"""Админка: статистика, начисления, возвраты, захват юзов на каналы."""
from __future__ import annotations

import html
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .. import keyboards as kb
from .. import texts
from ..core import App, show
from ..generator import MODES, is_valid
from ..grabber import GRAB_TEXT, STOP_RESULTS

router = Router(name="admin")


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, event_from_user: User | None = None,
                       app: App | None = None) -> bool:
        return bool(event_from_user and app and app.is_admin(event_from_user.id))


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

ADMIN_HELP = (
    "\n\n<b>Команды</b>\n"
    "/give <code>user_id N</code> — начислить N поисков (минус — списать)\n"
    "/refund <code>charge_id</code> — вернуть звёзды за покупку\n"
    "/hunt <code>режим</code> — поиск без списания (" + ", ".join(MODES) + ")\n"
    "/grab <code>юз…</code> — занять юзы на канал\n"
    "/release <code>юз…</code> — удалить канал, освободить юз"
)


def panel_text(app: App) -> str:
    b = app.db.business_stats()
    c = app.db.check_stats()
    return (
        "<b>🛠 Админка</b>\n\n"
        f"👥 Пользователей: {b['users']}\n"
        f"💰 Платящих: {b['payers']} · покупок: {b['payments']} · выручка: <b>{b['stars']} ⭐</b>\n"
        f"🔎 Поисков: {b['searches']} · выдано юзов: {b['finds']}\n"
        f"🔁 Сейчас идёт поисков: {len(app.hunts)}\n"
        f"🧮 Проверено юзов: {sum(c.values())} (свободных {c.get('free', 0)}, "
        f"Fragment {c.get('fragment', 0)})\n"
        f"🎯 Занято на каналы: {len(app.db.list_grabbed())}"
        + ADMIN_HELP
    )


@router.message(Command("admin"))
@router.message(F.text == kb.BTN_ADMIN)
async def cmd_admin(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    await message.answer(panel_text(app), reply_markup=kb.admin(app.autograb_on(event_from_user.id)))


@router.callback_query(F.data == "adm:refresh")
async def cb_refresh(call: CallbackQuery, app: App, event_from_user: User) -> None:
    await call.answer("Обновлено")
    await show(call, panel_text(app), kb.admin(app.autograb_on(event_from_user.id)))


@router.callback_query(F.data == "adm:autograb")
async def cb_autograb(call: CallbackQuery, app: App, event_from_user: User) -> None:
    value = not app.autograb_on(event_from_user.id)
    app.autograb[event_from_user.id] = value
    await call.answer(
        "Автозахват включён: найденные в ваших поисках юзы сразу займу на каналы" if value
        else "Автозахват выключен", show_alert=value,
    )
    await show(call, panel_text(app), kb.admin(value))


@router.callback_query(F.data == "adm:channels")
async def cb_channels(call: CallbackQuery, app: App) -> None:
    await call.answer()
    rows = app.db.list_grabbed()
    if not rows:
        await call.message.answer("Пока ничего не занято.")
        return
    await call.message.answer(
        f"<b>🎯 Занятые юзы ({len(rows)})</b>\n\n"
        + "\n".join(f"<code>@{n}</code> · {time.strftime('%d.%m.%Y', time.localtime(ts))}" for n, ts in rows)
        + "\n\nЗабрать себе: канал → Изменить → стереть юзернейм → сразу поставить его у себя. "
        "Или передать права владельца на канал."
    )


@router.callback_query(F.data == "adm:found")
async def cb_found(call: CallbackQuery, app: App) -> None:
    await call.answer()
    rows = app.db.found(20)
    if not rows:
        await call.message.answer("Пока пусто.")
        return
    await call.message.answer(
        "<b>🏆 Лучшие свободные</b> (на момент проверки)\n\n"
        + "\n".join(f"<code>@{n}</code> · {s}" for n, s, _ in rows),
        reply_markup=kb.grab_only([n for n, _, _ in rows]),
    )


@router.message(Command("give"))
async def cmd_give(message: Message, command: CommandObject, app: App) -> None:
    try:
        uid, n = (int(x) for x in (command.args or "").split())
    except ValueError:
        await message.answer("Формат: <code>/give user_id N</code>")
        return
    if not app.db.add_credits(uid, n):
        await message.answer("Пользователь не найден (он должен хотя бы раз нажать /start).")
        return
    free, credits, _ = app.db.balance(uid)
    await message.answer(f"Готово. У <code>{uid}</code> теперь {texts.searches(free + credits)}.")
    if n > 0:
        try:
            await message.bot.send_message(uid, f"🎁 Вам начислено {texts.searches(n)}!", reply_markup=kb.to_search())
        except Exception:
            pass


@router.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject, bot: Bot, app: App) -> None:
    charge_id = (command.args or "").strip()
    row = app.db.get_payment(charge_id) if charge_id else None
    if not row:
        await message.answer("Формат: <code>/refund charge_id</code> (ID приходит в уведомлении о покупке)")
        return
    user_id, stars, credits, refunded = row
    if refunded:
        await message.answer("Этот платёж уже возвращён.")
        return
    try:
        await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    except Exception as e:
        await message.answer(f"Telegram не принял возврат: {html.escape(str(e))}")
        return
    app.db.mark_refunded(charge_id)
    await message.answer(f"↩️ Возвращено {stars} ⭐ пользователю <code>{user_id}</code>, списано до {credits} поисков.")


@router.message(Command("hunt"))
async def cmd_hunt(message: Message, command: CommandObject, bot: Bot, app: App, event_from_user: User) -> None:
    mode = (command.args or "").strip()
    if mode not in MODES:
        await message.answer("Выберите стиль:", reply_markup=kb.styles())
        return
    if event_from_user.id in app.hunts:
        await message.answer(texts.ALREADY_RUNNING)
        return
    app.start_hunt(bot, message.chat.id, event_from_user.id, MODES[mode][0], None, mode=mode)


async def grab_names(app: App, names: list[str]) -> str:
    lines = []
    for name in names:
        if not is_valid(name):
            lines.append(f"<code>{html.escape(name)}</code> — 🚫 некорректный юз")
            continue
        res = await app.grabber.grab(name)
        lines.append(f"<code>@{name}</code> — {GRAB_TEXT[res]}")
        if res in STOP_RESULTS:
            break
    return "\n".join(lines)


def _names(args: str | None) -> list[str]:
    return [n.lstrip("@").lower() for n in (args or "").replace(",", " ").split()][:10]


@router.message(Command("grab"))
async def cmd_grab(message: Message, command: CommandObject, app: App) -> None:
    names = _names(command.args)
    if not names:
        await message.answer("Пример: <code>/grab pixelhq kamor</code>")
        return
    wait = await message.answer("⏳ Занимаю…")
    await wait.edit_text(await grab_names(app, names))


@router.callback_query(F.data.startswith("grab:"))
async def cb_grab(call: CallbackQuery, app: App) -> None:
    name = call.data.split(":", 1)[1]
    await call.answer(f"Занимаю @{name}…")
    await call.message.answer(await grab_names(app, [name]))


@router.message(Command("release"))
async def cmd_release(message: Message, command: CommandObject, app: App) -> None:
    names = _names(command.args)
    if not names:
        await message.answer("Пример: <code>/release pixelhq</code> — удалит канал, юз станет свободным.")
        return
    lines = []
    for name in names:
        ok = await app.grabber.release(name)
        lines.append(f"@{name} — {'🗑 канал удалён, юз свободен' if ok else 'не найден среди занятых'}")
    await message.answer("\n".join(lines))
