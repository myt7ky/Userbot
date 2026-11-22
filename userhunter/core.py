"""Состояние приложения и запуск поисков."""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from . import keyboards as kb
from . import texts
from .checker import Checker, Status
from .config import Config
from .db import Database
from .generator import candidates, coolness, is_valid
from .grabber import GRAB_TEXT, STOP_RESULTS, Grabber, GrabResult

log = logging.getLogger(__name__)

ADMIN_CHECKS_MULTIPLIER = 5  # админу разрешаем искать дольше


@dataclass
class App:
    cfg: Config
    checker: Checker
    db: Database
    grabber: Grabber
    autograb: dict[int, bool] = field(default_factory=dict)
    hunts: dict[int, asyncio.Task] = field(default_factory=dict)
    sem: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.sem = asyncio.Semaphore(self.cfg.max_parallel_hunts)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.cfg.admin_ids

    def autograb_on(self, user_id: int) -> bool:
        return self.autograb.get(user_id, self.cfg.auto_grab)

    # ---------- поиск ----------
    def start_hunt(self, bot: Bot, chat_id: int, user_id: int, title: str,
                   attempt: str | None, **gen_kwargs) -> None:
        """attempt — какая попытка списана ('free'/'paid'), None для админа."""
        task = asyncio.create_task(self._hunt(bot, chat_id, user_id, title, attempt, gen_kwargs))
        self.hunts[user_id] = task

    def stop_hunt(self, user_id: int) -> bool:
        task = self.hunts.get(user_id)
        if not task:
            return False
        task.cancel()
        return True

    async def _edit(self, bot: Bot, chat_id: int, message_id: int, text: str, markup=None) -> None:
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup)

    async def _hunt(self, bot: Bot, chat_id: int, user_id: int, title: str,
                    attempt: str | None, gen_kwargs: dict) -> None:
        admin = self.is_admin(user_id)
        count = self.cfg.results_per_search
        max_checks = self.cfg.max_checks_per_search * (ADMIN_CHECKS_MULTIPLIER if admin else 1)
        grab = admin and self.autograb_on(user_id)
        found: dict[str, GrabResult | None] = {}
        checked = 0
        note = ""
        failed = False
        message_id: int | None = None

        def progress(done: bool) -> str:
            head = "🏁 Поиск завершён" if done else "🔎 Ищу"
            text = f"{head}: {title}\nПроверено вариантов: {checked} · найдено: {len(found)}/{count}"
            if note:
                text += f"\n{note}"
            if not done and found:
                text += "\n\n" + "\n".join(f"✅ <code>@{n}</code>" for n in found)
            return text

        try:
            if self.sem.locked():
                msg = await bot.send_message(chat_id, texts.QUEUED, reply_markup=kb.stop())
                message_id = msg.message_id
            async with self.sem:
                if message_id is None:
                    msg = await bot.send_message(chat_id, progress(False), reply_markup=kb.stop())
                    message_id = msg.message_id
                else:
                    await self._edit(bot, chat_id, message_id, progress(False), kb.stop())

                for name in candidates(skip=self.db.recently_checked, **gen_kwargs):
                    if len(found) >= count or checked >= max_checks:
                        break
                    status = await self.checker.check(name)
                    checked += 1
                    self.db.save(name, status.value, coolness(name))
                    if status is Status.FREE:
                        found[name] = None
                        self.db.add_find(user_id, name)
                        if grab:
                            res = await self.grabber.grab(name)
                            found[name] = None if res in STOP_RESULTS else res
                            if res in STOP_RESULTS:
                                grab = False
                                note = f"⚠️ Автозахват выключен: {GRAB_TEXT[res]}"
                        await self._edit(bot, chat_id, message_id, progress(False), kb.stop())
                    elif checked % 15 == 0:
                        await self._edit(bot, chat_id, message_id, progress(False), kb.stop())
        except asyncio.CancelledError:
            note = "⏹ Остановлено вами."
        except Exception:
            log.exception("hunt failed")
            failed = True
            note = "⚠️ Поиск прервался из-за ошибки."
        finally:
            self.hunts.pop(user_id, None)

        # ---- итог ----
        if message_id is not None:
            await self._edit(bot, chat_id, message_id, progress(True))

        if not found:
            if attempt:
                self.db.return_attempt(user_id, attempt)
            text = texts.NOTHING_FOUND if not failed else (
                "⚠️ Поиск прервался из-за ошибки. Попытка возвращена на баланс, попробуйте ещё раз."
            )
            if not attempt and not failed:
                text = "😕 Ничего не нашлось. Попробуйте другой стиль."
            await bot.send_message(chat_id, text, reply_markup=kb.to_search())
            return

        self.db.count_search(user_id)
        lines = []
        for i, name in enumerate(found, 1):
            extra = f" — {GRAB_TEXT[found[name]]}" if found[name] else ""
            lines.append(f"{i}. <code>@{name}</code>{extra}")
        text = f"🎉 <b>Свободные юзы</b> ({html.escape(title)}):\n\n" + "\n".join(lines) + "\n\n" + texts.HOW_TO_SET
        grab_names = [n for n, r in found.items() if r is None] if admin else None
        await bot.send_message(chat_id, text, reply_markup=kb.after_hunt(grab_names))

    # ---------- ручная проверка ----------
    async def check_names(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Возвращает (строки результата, список свободных)."""
        lines, free = [], []
        for name in names:
            if not is_valid(name):
                lines.append(f"<code>{html.escape(name)}</code> — 🚫 так нельзя (5–32 символа: a-z, 0-9, _)")
                continue
            status = await self.checker.check(name)
            self.db.save(name, status.value, coolness(name))
            lines.append(f"<code>@{name}</code> — {STATUS_TEXT[status]}")
            if status is Status.FREE:
                free.append(name)
        return lines, free


STATUS_TEXT = {
    Status.FREE: "✅ свободен",
    Status.TAKEN: "❌ занят",
    Status.FRAGMENT: "💎 продаётся на Fragment",
    Status.INVALID: "🚫 недоступен",
}


async def show(call: CallbackQuery, text: str, markup=None) -> None:
    """Заменить текст сообщения с кнопкой. Сообщения старше 48 ч Telegram менять не даёт —
    тогда просто отправляем новое."""
    if isinstance(call.message, Message):
        try:
            await call.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await call.message.answer(text, reply_markup=markup)
