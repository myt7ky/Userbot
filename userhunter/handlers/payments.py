"""Покупка поисков за Telegram Stars (XTR).

Цифровые товары в ботах Telegram продаются только за звёзды — провайдер
и provider_token не нужны. Звёзды копятся на балансе бота, их можно
вывести через Fragment (подробнее в README).
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery, User

from .. import keyboards as kb
from .. import texts
from ..config import Pack
from ..core import App

log = logging.getLogger(__name__)
router = Router(name="payments")


def make_payload(i: int, pack: Pack) -> str:
    return f"pack:{i}:{pack.searches}:{pack.stars}"


def parse_payload(app: App, payload: str) -> Pack | None:
    """Пакет из payload, если он совпадает с текущими ценами."""
    try:
        kind, i, searches, stars = payload.split(":")
        pack = app.cfg.packs[int(i)]
    except (ValueError, IndexError):
        return None
    if kind != "pack" or pack.searches != int(searches) or pack.stars != int(stars):
        return None
    return pack


async def show_packs(message: Message, app: App, user_id: int) -> None:
    free, credits, _ = app.db.balance(user_id)
    await message.answer(
        f"💎 <b>Купить поиски</b>\n\nСейчас на балансе: <b>{texts.searches(free + credits)}</b>\n"
        f"Один поиск = до {app.cfg.results_per_search} свободных юзов. "
        "Если поиск ничего не найдёт — попытка вернётся.\n\nОплата звёздами Telegram ⭐",
        reply_markup=kb.packs(app.cfg.packs),
    )


@router.message(Command("buy"))
@router.message(F.text == kb.BTN_BUY)
async def cmd_buy(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    await show_packs(message, app, event_from_user.id)


@router.callback_query(F.data == "menu:buy")
async def cb_menu_buy(call: CallbackQuery, app: App, event_from_user: User) -> None:
    await call.answer()
    await show_packs(call.message, app, event_from_user.id)


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, bot: Bot, app: App) -> None:
    try:
        i = int(call.data.split(":", 1)[1])
        pack = app.cfg.packs[i]
    except (ValueError, IndexError):
        await call.answer("Этот пакет больше недоступен", show_alert=True)
        return
    await call.answer()
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title=f"{texts.searches(pack.searches)} юзов",
        description=(
            f"{texts.searches(pack.searches)} красивых свободных юзернеймов — "
            f"до {app.cfg.results_per_search} юзов за поиск. Поиски не сгорают."
        ),
        payload=make_payload(i, pack),
        currency="XTR",
        prices=[LabeledPrice(label=texts.searches(pack.searches), amount=pack.stars)],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, app: App) -> None:
    pack = parse_payload(app, query.invoice_payload)
    if pack is None or query.currency != "XTR" or query.total_amount != pack.stars:
        await query.answer(ok=False, error_message="Цены обновились — откройте 💎 Купить поиски ещё раз.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def paid(message: Message, app: App, event_from_user: User) -> None:
    sp = message.successful_payment
    pack = parse_payload(app, sp.invoice_payload)
    searches = pack.searches if pack else int(sp.invoice_payload.split(":")[2])
    is_new = app.db.add_payment(sp.telegram_payment_charge_id, event_from_user.id, sp.total_amount, searches)
    if not is_new:
        return
    log.info("Оплата: user=%s stars=%s searches=%s", event_from_user.id, sp.total_amount, searches)
    free, credits, _ = app.db.balance(event_from_user.id)
    await message.answer(
        f"✅ Оплата прошла! Начислено <b>{texts.searches(searches)}</b>.\n"
        f"Теперь на балансе: <b>{texts.searches(free + credits)}</b>",
        reply_markup=kb.to_search(),
    )
    for admin_id in app.cfg.admin_ids:
        try:
            await message.bot.send_message(
                admin_id,
                f"💰 Покупка: {sp.total_amount} ⭐ → {texts.searches(searches)}\n"
                f"Пользователь: <code>{event_from_user.id}</code> {event_from_user.full_name}\n"
                f"Charge ID: <code>{sp.telegram_payment_charge_id}</code>",
            )
        except Exception:  # админ мог не запускать бота
            pass
