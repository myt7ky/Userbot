"""Telegram-бот для поиска красивых свободных юзернеймов."""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
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
from .grabber import GRAB_TEXT, STOP_RESULTS, Grabber, GrabResult
from .storage import Storage

log = logging.getLogger(__name__)
router = Router()

DEFAULT_COUNT = 10
MAX_COUNT = 50
ATTEMPTS_PER_RESULT = 300  # сдаёмся после count * N проверок без успеха

HELP = (
    "<b>🔎 Охотник за юзами</b>\n\n"
    "Генерирую красивые юзернеймы, проверяю, свободны ли они, и могу сразу занять их на канал.\n\n"
    "<b>Поиск</b>\n"
    "/hunt <code>режим [N]</code> — найти N свободных юзов (по умолчанию 10)\n"
    "/mask <code>маска [N]</code> — поиск по маске\n"
    "/like <code>слово [N]</code> — вариации слова (theword, wordhq, getword…)\n"
    "/check <code>юз1 юз2 …</code> — проверить конкретные юзы\n"
    "/stop — остановить поиск\n\n"
    "<b>Захват</b>\n"
    "/autograb — вкл/выкл автозахват найденных юзов на каналы\n"
    "/grab <code>юз1 юз2 …</code> — занять юзы на канал прямо сейчас\n"
    "/channels — занятые юзы\n"
    "/release <code>юз</code> — удалить канал и освободить юз\n\n"
    "<b>Прочее</b>\n"
    "/found — лучшие найденные свободные · /stats — статистика\n\n"
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
class App:
    checker: Checker
    storage: Storage
    grabber: Grabber
    autograb_default: bool
    autograb: dict[int, bool] = field(default_factory=dict)
    hunts: dict[int, asyncio.Task] = field(default_factory=dict)

    def autograb_on(self, chat_id: int) -> bool:
        return self.autograb.get(chat_id, self.autograb_default)


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


def grab_keyboard(names: list[str]) -> InlineKeyboardMarkup | None:
    """Кнопки «🎯 Занять @юз» (не больше 10, чтобы не захламлять)."""
    names = names[:10]
    if not names:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 Занять @{n}", callback_data=f"grab:{n}")] for n in names
    ])


def parse_count(arg: str | None) -> int:
    try:
        return max(1, min(MAX_COUNT, int(arg))) if arg else DEFAULT_COUNT
    except ValueError:
        return DEFAULT_COUNT


def link(name: str) -> str:
    return f'<a href="https://t.me/{name}">@{name}</a>'


def parse_names(args: str | None, limit: int = 20) -> list[str]:
    return [n.lstrip("@").lower() for n in (args or "").replace(",", " ").split()][:limit]


async def run_hunt(message: Message, app: App, title: str, count: int, **gen_kwargs) -> None:
    chat_id = message.chat.id
    found: dict[str, GrabResult | None] = {}  # юз → результат захвата (None — не пытались)
    checked = 0
    grab = app.autograb_on(chat_id)
    grab_note = ""
    progress = await message.answer(
        f"🔎 Ищу: {title}\nЦель: {count} свободных…" + ("\n🎯 Автозахват включён" if grab else "")
    )
    gen = candidates(skip=app.storage.recently_checked, **gen_kwargs)

    def line(name: str) -> str:
        res = found[name]
        return link(name) + (f" — {GRAB_TEXT[res]}" if res else "")

    async def update_progress(done: bool = False) -> None:
        head = "🏁 Готово" if done else "🔎 Ищу"
        text = f"{head}: {title}\nПроверено: {checked} · найдено: {len(found)}/{count}"
        if grab_note:
            text += f"\n{grab_note}"
        if found:
            text += "\n\n" + "\n".join(line(n) for n in found)
        kb = grab_keyboard([n for n, r in found.items() if r is None]) if done else None
        with contextlib.suppress(TelegramBadRequest):
            await progress.edit_text(text, disable_web_page_preview=True, reply_markup=kb)

    try:
        for name in gen:
            if len(found) >= count or checked >= count * ATTEMPTS_PER_RESULT:
                break
            status = await app.checker.check(name)
            checked += 1
            app.storage.save(name, status.value, coolness(name))
            if status is Status.FREE:
                found[name] = None
                if grab:
                    res = await app.grabber.grab(name)
                    found[name] = res
                    if res in STOP_RESULTS:
                        grab = False
                        grab_note = f"⚠️ Автозахват выключен: {GRAB_TEXT[res]}"
                        found[name] = None  # можно будет занять кнопкой позже
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
        if len(found) < count:
            await message.answer(
                "Варианты кончились или попадаются только занятые. "
                "Попробуйте другой режим или маску подлиннее."
            )
    finally:
        app.hunts.pop(chat_id, None)


async def start_hunt(message: Message, app: App, title: str, count: int, **kw) -> None:
    if message.chat.id in app.hunts:
        await message.answer("Уже ищу. /stop — чтобы остановить.")
        return
    # задача не начнёт выполняться до следующего await, так что hunts заполнится раньше
    app.hunts[message.chat.id] = asyncio.create_task(run_hunt(message, app, title, count, **kw))


# ---------- поиск ----------
@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message, app: App) -> None:
    state = "включён ✅" if app.autograb_on(message.chat.id) else "выключен"
    await message.answer(HELP + f"\n\n🎯 Автозахват сейчас {state}", reply_markup=modes_keyboard())


@router.callback_query(F.data.startswith("hunt:"))
async def cb_hunt(call: CallbackQuery, app: App) -> None:
    mode = call.data.split(":", 1)[1]
    await call.answer()
    if mode in MODES:
        await start_hunt(call.message, app, MODES[mode][0], DEFAULT_COUNT, mode=mode)


@router.message(Command("hunt"))
async def cmd_hunt(message: Message, command: CommandObject, app: App) -> None:
    args = (command.args or "").split()
    mode = args[0] if args else "pron"
    if mode not in MODES:
        await message.answer("Не знаю такой режим. Выберите:", reply_markup=modes_keyboard())
        return
    await start_hunt(message, app, MODES[mode][0], parse_count(args[1] if len(args) > 1 else None), mode=mode)


@router.message(Command("mask"))
async def cmd_mask(message: Message, command: CommandObject, app: App) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer("Пример: <code>/mask cvcvc 10</code>")
        return
    mask = args[0]
    if not 5 <= len(mask) <= 32:
        await message.answer("Маска должна быть длиной 5–32 символа.")
        return
    await start_hunt(
        message, app, f"маска <code>{html.escape(mask)}</code>",
        parse_count(args[1] if len(args) > 1 else None), mask=mask,
    )


@router.message(Command("like"))
async def cmd_like(message: Message, command: CommandObject, app: App) -> None:
    args = (command.args or "").lower().split()
    if not args or not args[0].replace("_", "").isalnum() or not args[0].isascii():
        await message.answer("Пример: <code>/like pixel 10</code> (латиница)")
        return
    await start_hunt(
        message, app, f"вариации «{html.escape(args[0])}»",
        parse_count(args[1] if len(args) > 1 else None), base=args[0], max_misses=300,
    )


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject, app: App) -> None:
    names = parse_names(command.args)
    if not names:
        await message.answer("Пример: <code>/check durov pixelhq</code>")
        return
    lines, free = [], []
    for name in names:
        if not is_valid(name):
            lines.append(f"<code>{html.escape(name)}</code> — 🚫 некорректный юз (5–32 символа, a-z 0-9 _)")
            continue
        status = await app.checker.check(name)
        app.storage.save(name, status.value, coolness(name))
        lines.append(f"{link(name)} — {STATUS_ICON[status]}")
        if status is Status.FREE:
            free.append(name)
    await message.answer("\n".join(lines), disable_web_page_preview=True, reply_markup=grab_keyboard(free))


@router.message(Command("stop"))
async def cmd_stop(message: Message, app: App) -> None:
    task = app.hunts.get(message.chat.id)
    if not task:
        await message.answer("Сейчас ничего не ищу.")
        return
    task.cancel()


# ---------- захват ----------
@router.message(Command("autograb"))
async def cmd_autograb(message: Message, command: CommandObject, app: App) -> None:
    arg = (command.args or "").strip().lower()
    if arg in ("on", "вкл", "1"):
        value = True
    elif arg in ("off", "выкл", "0"):
        value = False
    else:
        value = not app.autograb_on(message.chat.id)
    app.autograb[message.chat.id] = value
    if value:
        await message.answer(
            "🎯 Автозахват <b>включён</b>. Каждый найденный свободный юз сразу займу на новый публичный канал.\n"
            "Учтите лимит Telegram: ~10 публичных юзов на обычный аккаунт (~20 с Premium). "
            "Ненужные освобождайте через /release. Действует со следующего поиска."
        )
    else:
        await message.answer("Автозахват выключен. Занимать можно кнопками под результатами или /grab.")


async def grab_names(app: App, names: list[str]) -> str:
    lines = []
    for name in names:
        if not is_valid(name):
            lines.append(f"<code>{html.escape(name)}</code> — 🚫 некорректный юз")
            continue
        res = await app.grabber.grab(name)
        lines.append(f"{link(name)} — {GRAB_TEXT[res]}")
        if res in STOP_RESULTS:
            break
    return "\n".join(lines)


@router.message(Command("grab"))
async def cmd_grab(message: Message, command: CommandObject, app: App) -> None:
    names = parse_names(command.args, limit=10)
    if not names:
        await message.answer("Пример: <code>/grab pixelhq kamor</code>")
        return
    wait = await message.answer("⏳ Занимаю…")
    await wait.edit_text(await grab_names(app, names), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("grab:"))
async def cb_grab(call: CallbackQuery, app: App) -> None:
    name = call.data.split(":", 1)[1]
    await call.answer(f"Занимаю @{name}…")
    await call.message.answer(await grab_names(app, [name]), disable_web_page_preview=True)


@router.message(Command("channels"))
async def cmd_channels(message: Message, app: App) -> None:
    rows = app.storage.list_grabbed()
    if not rows:
        await message.answer("Пока ничего не занято. Включите /autograb или используйте /grab.")
        return
    text = f"<b>🎯 Занятые юзы ({len(rows)})</b>\n\n" + "\n".join(
        f"{link(n)} · {time.strftime('%d.%m.%Y', time.localtime(ts))}" for n, ts in rows
    )
    text += (
        "\n\nЧтобы забрать юз себе: в канале → Изменить → стереть юзернейм → сразу поставить его "
        "в своём профиле/канале. Или передайте права владельца на канал."
    )
    await message.answer(text, disable_web_page_preview=True)


@router.message(Command("release"))
async def cmd_release(message: Message, command: CommandObject, app: App) -> None:
    names = parse_names(command.args, limit=10)
    if not names:
        await message.answer("Пример: <code>/release pixelhq</code> — удалит канал, юз станет свободным.")
        return
    lines = []
    for name in names:
        ok = await app.grabber.release(name)
        lines.append(f"@{name} — {'🗑 канал удалён, юз свободен' if ok else 'не найден среди занятых'}")
    await message.answer("\n".join(lines))


# ---------- прочее ----------
@router.message(Command("found"))
async def cmd_found(message: Message, app: App) -> None:
    rows = app.storage.found(30)
    if not rows:
        await message.answer("Пока ничего не найдено. Начните с /hunt")
        return
    text = "<b>🏆 Лучшие найденные</b> (могли уже занять — перепроверьте /check)\n\n"
    text += "\n".join(f"{link(n)} · {s}" for n, s, _ in rows)
    await message.answer(
        text, disable_web_page_preview=True, reply_markup=grab_keyboard([n for n, _, _ in rows])
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, app: App) -> None:
    st = app.storage.stats()
    await message.answer(
        f"Проверено всего: {sum(st.values())}\n"
        f"✅ свободных: {st.get('free', 0)}\n🎯 занято вами: {len(app.storage.list_grabbed())}\n"
        f"❌ занятых: {st.get('taken', 0)}\n"
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

    checker = Checker(client, cfg.check_delay)
    storage = Storage(cfg.db_path)
    app = App(
        checker=checker,
        storage=storage,
        grabber=Grabber(checker, storage, about=cfg.grab_about),
        autograb_default=cfg.auto_grab,
    )

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(app=app)
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
