"""Telegram-бот для поиска красивых свободных юзернеймов."""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from dataclasses import dataclass, field

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from telethon import TelegramClient

from .checker import Checker, Status
from .config import Config, load_config
from .generator import MODES, candidates, coolness, is_valid
from .storage import Storage

log = logging.getLogger(__name__)
router = Router()

DEFAULT_COUNT = 10
MAX_COUNT = 50
ATTEMPTS_PER_RESULT = 300  # сдаёмся после count * N проверок без успеха

HELP = (
    "<b>🔎 Охотник за юзами</b>\n\n"
    "Генерирую красивые юзернеймы и проверяю, свободны ли они.\n\n"
    "<b>Команды</b>\n"
    "/hunt <code>режим [N]</code> — найти N свободных юзов (по умолчанию 10)\n"
    "/mask <code>маска [N]</code> — поиск по маске\n"
    "/like <code>слово [N]</code> — вариации слова (theword, wordhq, getword…)\n"
    "/check <code>юз1 юз2 …</code> — проверить конкретные юзы\n"
    "/found — лучшие найденные свободные юзы\n"
    "/stats — статистика проверок\n"
    "/stop — остановить поиск\n\n"
    "<b>Режимы:</b>\n"
    + "\n".join(f"<code>{k}</code> — {v[0]}" for k, v in MODES.items())
    + "\n\n<b>Маска:</b> <code>c</code> согласная, <code>v</code> гласная, <code>l</code> любая буква, "
    "<code>d</code> цифра, <code>A</code>/<code>B</code>… — одинаковые буквы, остальное как есть.\n"
    "Примеры: <code>/mask cvcvc</code>, <code>/mask ABABA</code>, <code>/mask neoll</code>"
)

STATUS_ICON = {
    Status.FREE: "✅ свободен",
    Status.TAKEN: "❌ занят",
    Status.FRAGMENT: "💎 продаётся на Fragment",
    Status.INVALID: "🚫 недоступен",
}


@dataclass
class Hunt:
    task: asyncio.Task
    found: list[str] = field(default_factory=list)


hunts: dict[int, Hunt] = {}


class AdminOnly(BaseMiddleware):
    """Проверки идут через ваш аккаунт — пускаем только своих."""

    def __init__(self, admins: frozenset[int]):
        self.admins = admins

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user and user.id in self.admins:
            return await handler(event, data)
        if isinstance(event, Message):
            await event.answer(
                f"⛔ Нет доступа. Ваш ID: <code>{user.id if user else '?'}</code> — "
                "добавьте его в ADMIN_IDS и перезапустите бота."
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)


def modes_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, (title, _) in MODES.items():
        row.append(InlineKeyboardButton(text=title, callback_data=f"hunt:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_count(arg: str | None) -> int:
    try:
        return max(1, min(MAX_COUNT, int(arg))) if arg else DEFAULT_COUNT
    except ValueError:
        return DEFAULT_COUNT


def link(name: str) -> str:
    return f'<a href="https://t.me/{name}">@{name}</a>'


async def run_hunt(
    message: Message, checker: Checker, storage: Storage, title: str, count: int, **gen_kwargs
) -> None:
    chat_id = message.chat.id
    hunt = hunts[chat_id]
    checked = 0
    progress = await message.answer(f"🔎 Ищу: {title}\nЦель: {count} свободных…")
    gen = candidates(skip=storage.recently_checked, **gen_kwargs)

    async def update_progress(done: bool = False) -> None:
        head = "🏁 Готово" if done else "🔎 Ищу"
        text = (
            f"{head}: {title}\nПроверено: {checked} · найдено: {len(hunt.found)}/{count}"
        )
        if hunt.found:
            text += "\n\n" + "\n".join(link(n) for n in hunt.found)
        with contextlib.suppress(TelegramBadRequest):
            await progress.edit_text(text, disable_web_page_preview=True)

    try:
        for name in gen:
            if len(hunt.found) >= count or checked >= count * ATTEMPTS_PER_RESULT:
                break
            status = await checker.check(name)
            checked += 1
            score = coolness(name)
            storage.save(name, status.value, score)
            if status is Status.FREE:
                hunt.found.append(name)
                await update_progress()
            elif checked % 15 == 0:
                await update_progress()
    except asyncio.CancelledError:
        await update_progress(done=True)
        await message.answer("⏹ Поиск остановлен.")
        raise
    except Exception:
        log.exception("hunt failed")
        await message.answer("⚠️ Поиск упал с ошибкой, подробности в логах.")
    else:
        await update_progress(done=True)
        if len(hunt.found) < count:
            await message.answer(
                "Варианты кончились или попадаются только занятые. "
                "Попробуйте другой режим или маску подлиннее."
            )
    finally:
        hunts.pop(chat_id, None)


async def start_hunt(message: Message, checker: Checker, storage: Storage, title: str, count: int, **kw) -> None:
    if message.chat.id in hunts:
        await message.answer("Уже ищу. /stop — чтобы остановить.")
        return
    # задача не начнёт выполняться до следующего await, так что hunts заполнится раньше
    task = asyncio.create_task(run_hunt(message, checker, storage, title, count, **kw))
    hunts[message.chat.id] = Hunt(task=task)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP, reply_markup=modes_keyboard())


@router.callback_query(F.data.startswith("hunt:"))
async def cb_hunt(call: CallbackQuery, checker: Checker, storage: Storage) -> None:
    mode = call.data.split(":", 1)[1]
    await call.answer()
    if mode in MODES:
        await start_hunt(call.message, checker, storage, MODES[mode][0], DEFAULT_COUNT, mode=mode)


@router.message(Command("hunt"))
async def cmd_hunt(message: Message, command: CommandObject, checker: Checker, storage: Storage) -> None:
    args = (command.args or "").split()
    mode = args[0] if args else "pron"
    if mode not in MODES:
        await message.answer("Не знаю такой режим. Выберите:", reply_markup=modes_keyboard())
        return
    await start_hunt(message, checker, storage, MODES[mode][0], parse_count(args[1] if len(args) > 1 else None), mode=mode)


@router.message(Command("mask"))
async def cmd_mask(message: Message, command: CommandObject, checker: Checker, storage: Storage) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer("Пример: <code>/mask cvcvc 10</code>")
        return
    mask = args[0]
    if not 5 <= len(mask) <= 32:
        await message.answer("Маска должна быть длиной 5–32 символа.")
        return
    await start_hunt(
        message, checker, storage, f"маска <code>{html.escape(mask)}</code>",
        parse_count(args[1] if len(args) > 1 else None), mask=mask,
    )


@router.message(Command("like"))
async def cmd_like(message: Message, command: CommandObject, checker: Checker, storage: Storage) -> None:
    args = (command.args or "").lower().split()
    if not args or not args[0].replace("_", "").isalnum() or not args[0].isascii():
        await message.answer("Пример: <code>/like pixel 10</code> (латиница)")
        return
    await start_hunt(
        message, checker, storage, f"вариации «{html.escape(args[0])}»",
        parse_count(args[1] if len(args) > 1 else None), base=args[0], max_misses=300,
    )


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject, checker: Checker, storage: Storage) -> None:
    names = [n.lstrip("@").lower() for n in (command.args or "").replace(",", " ").split()][:20]
    if not names:
        await message.answer("Пример: <code>/check durov pixelhq</code>")
        return
    lines = []
    for name in names:
        if not is_valid(name):
            lines.append(f"<code>{html.escape(name)}</code> — 🚫 некорректный юз (5–32 символа, a-z 0-9 _)")
            continue
        status = await checker.check(name)
        storage.save(name, status.value, coolness(name))
        lines.append(f"{link(name)} — {STATUS_ICON[status]}")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    hunt = hunts.get(message.chat.id)
    if not hunt:
        await message.answer("Сейчас ничего не ищу.")
        return
    hunt.task.cancel()


@router.message(Command("found"))
async def cmd_found(message: Message, storage: Storage) -> None:
    rows = storage.found(30)
    if not rows:
        await message.answer("Пока ничего не найдено. Начните с /hunt")
        return
    text = "<b>🏆 Лучшие найденные</b> (могли уже занять — перепроверьте /check)\n\n"
    text += "\n".join(f"{link(n)} · {s}" for n, s, _ in rows)
    await message.answer(text, disable_web_page_preview=True)


@router.message(Command("stats"))
async def cmd_stats(message: Message, storage: Storage) -> None:
    st = storage.stats()
    await message.answer(
        f"Проверено всего: {sum(st.values())}\n"
        f"✅ свободных: {st.get('free', 0)}\n❌ занятых: {st.get('taken', 0)}\n"
        f"💎 на Fragment: {st.get('fragment', 0)}\n🚫 недоступных: {st.get('invalid', 0)}"
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg: Config = load_config()
    if not cfg.admin_ids:
        raise SystemExit("Заполните ADMIN_IDS в .env — иначе ботом сможет пользоваться кто угодно.")

    client = TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Сессия не авторизована. Сначала выполните: python -m userhunter.login")

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(checker=Checker(client, cfg.check_delay), storage=Storage(cfg.db_path))
    dp.message.middleware(AdminOnly(cfg.admin_ids))
    dp.callback_query.middleware(AdminOnly(cfg.admin_ids))
    dp.include_router(router)

    me = await client.get_me()
    log.info("Проверяю через аккаунт %s, бот запускается…", me.username or me.id)
    try:
        await dp.start_polling(bot)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
