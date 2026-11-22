"""Сквозные сценарии: так, как их пройдёт живой пользователь."""
from __future__ import annotations

import asyncio
import re

import pytest
from aiogram.methods import AnswerCallbackQuery, AnswerPreCheckoutQuery, RefundStarPayment, SendInvoice, SendMessage
from telethon.tl.functions.channels import UpdateUsernameRequest

from userhunter import keyboards as kb
from userhunter.generator import is_valid

from .conftest import ADMIN, USER

pytestmark = pytest.mark.asyncio


def buttons(method) -> list[str]:
    markup = method.reply_markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or markup.keyboard
    return [b.text for row in rows for b in row]


def callbacks(method) -> list[str]:
    return [b.callback_data for row in method.reply_markup.inline_keyboard for b in row]


def found_names(text: str) -> list[str]:
    return re.findall(r"<code>@([a-z0-9_]+)</code>", text)


async def test_start_shows_menu_and_free_attempt(h):
    await h.send(USER, "/start")
    msg = h.session.last(SendMessage, USER)
    assert "1 поиск бесплатно" in msg.text
    assert buttons(msg) == [kb.BTN_SEARCH, kb.BTN_CHECK, kb.BTN_BUY, kb.BTN_PROFILE, kb.BTN_HELP]
    assert h.db.balance(USER) == (1, 0, 0)


async def test_admin_sees_admin_button(h):
    await h.send(ADMIN, "/start")
    assert kb.BTN_ADMIN in buttons(h.session.last(SendMessage, ADMIN))


async def test_free_search_then_paywall_then_purchase(h):
    # 1. бесплатный поиск
    await h.send(USER, kb.BTN_SEARCH)
    styles = h.session.last(SendMessage, USER)
    assert "style:pron" in callbacks(styles) and "style:mask" in callbacks(styles)
    await h.press(USER, "style:pron")
    await h.wait_hunts()

    result = h.session.last(SendMessage, USER)
    names = found_names(result.text)
    assert len(names) == 3 and all(is_valid(n) for n in names)
    assert "Как поставить" in result.text
    assert "grab:" not in "".join(callbacks(result))  # обычному пользователю захват не показываем
    assert h.db.balance(USER) == (0, 0, 1)
    assert h.db.user_finds(USER)[0] == names[-1]

    # 2. попытки кончились → предложение купить
    await h.send(USER, kb.BTN_SEARCH)
    await h.press(USER, "style:word")
    paywall = h.session.last(SendMessage, USER)
    assert "Бесплатный поиск уже использован" in paywall.text
    assert callbacks(paywall) == ["buy:0", "buy:1"]
    assert not h.app.hunts

    # 3. покупка 5 поисков за 100 ⭐
    await h.press(USER, "buy:1")
    invoice = h.session.last(SendInvoice)
    assert invoice.currency == "XTR" and invoice.prices[0].amount == 100
    await h.pay(USER, invoice.payload, 100, "charge-1")
    assert h.session.last(AnswerPreCheckoutQuery).ok is True
    assert "Начислено <b>5 поисков</b>" in h.session.last(SendMessage, USER).text
    assert h.db.balance(USER)[1] == 5
    assert any("Покупка: 100 ⭐" in t for t in h.session.texts(ADMIN))  # админу пришло уведомление

    # повторная доставка того же платежа не начисляет дважды
    await h.pay(USER, invoice.payload, 100, "charge-1")
    assert h.db.balance(USER)[1] == 5

    # 4. платный поиск списывает одну попытку
    await h.press(USER, "style:pattern")
    await h.wait_hunts()
    assert h.db.balance(USER) == (0, 4, 2)


async def test_found_usernames_are_not_shown_twice(h):
    other = USER + 1
    await h.press(USER, "style:pron")
    await h.wait_hunts()
    first = set(found_names(h.session.last(SendMessage, USER).text))
    await h.press(other, "style:pron")
    await h.wait_hunts()
    second = set(found_names(h.session.last(SendMessage, other).text))
    assert first and second and not first & second


async def test_precheckout_rejects_tampered_price(h):
    await h.pay(USER, "pack:1:5:1", 1, "charge-x")  # 5 поисков за 1 звезду
    assert h.session.last(AnswerPreCheckoutQuery).ok is False


async def test_nothing_found_returns_attempt(h):
    h.account.is_free = lambda name: False
    await h.press(USER, "style:pron")
    await h.wait_hunts()
    assert "Попытка <b>возвращена</b>" in h.session.last(SendMessage, USER).text
    assert h.db.balance(USER) == (1, 0, 0)


async def test_stop_button(h):
    h.account.is_free = lambda name: False
    h.app.checker.delay = 0.01
    await h.press(USER, "style:pron")
    await asyncio.sleep(0.05)
    await h.press(USER, "stop")
    await h.wait_hunts()
    assert any("Остановлено вами" in t for t in h.session.texts(USER))
    assert h.db.balance(USER)[0] == 1  # ничего не нашли → попытка вернулась


async def test_second_search_while_running_is_refused(h):
    h.app.checker.delay = 0.01
    h.account.is_free = lambda name: False
    h.db.ensure_user(USER, "u", 1)
    h.db.add_credits(USER, 3)
    await h.press(USER, "style:pron")
    await h.press(USER, "style:word")
    assert "уже идёт поиск" in h.session.last(SendMessage, USER).text
    h.app.stop_hunt(USER)
    await h.wait_hunts()
    assert h.db.balance(USER) == (1, 3, 0)  # обе попытки на месте


async def test_queue_when_all_slots_busy(h):
    other = USER + 1
    h.app.checker.delay = 0.005
    await h.press(USER, "style:pron")
    await asyncio.sleep(0)  # первый поиск занял единственное место
    await h.press(other, "style:pron")
    await asyncio.sleep(0.01)
    assert any("вы в очереди" in t for t in h.session.texts(other))
    await h.wait_hunts()
    assert len(found_names(h.session.last(SendMessage, other).text)) == 3


async def test_mask_and_word_input(h):
    h.db.ensure_user(USER, "u", 0)
    h.db.add_credits(USER, 2)
    await h.press(USER, "style:mask")
    await h.send(USER, "ab")  # слишком коротко
    assert "5–32 символа" in h.session.last(SendMessage, USER).text
    await h.send(USER, "ABABA")
    await h.wait_hunts()
    names = found_names(h.session.last(SendMessage, USER).text)
    assert names and all(n[0] == n[2] == n[4] and n[1] == n[3] for n in names)

    await h.press(USER, "style:like")
    await h.send(USER, "pixel")
    await h.wait_hunts()
    assert all("pixel" in n for n in found_names(h.session.last(SendMessage, USER).text))


async def test_menu_button_escapes_input_state(h):
    await h.press(USER, "style:mask")
    await h.send(USER, kb.BTN_PROFILE)
    assert "Профиль" in h.session.last(SendMessage, USER).text
    assert not h.app.hunts


async def test_check_flow_and_daily_limit(h):
    await h.send(USER, kb.BTN_CHECK)
    await h.send(USER, "@Pixelhq fragx bad__name")
    text = h.session.texts(USER)[-1]
    assert "@pixelhq</code> — ✅ свободен" in text
    assert "@fragx</code> — 💎 продаётся на Fragment" in text
    assert "bad__name</code> — 🚫" in text

    assert h.db.checks_left(USER, 3) == 1  # некорректный юз лимит не тратит
    await h.send(USER, "/check one12 two12")  # хватает только на один
    assert "@one12" in h.session.texts(USER)[-1] and "two12" not in h.session.texts(USER)[-1]
    await h.send(USER, "/check again")
    assert "закончились" in h.session.last(SendMessage, USER).text


async def test_admin_no_charge_and_grab(h):
    await h.press(ADMIN, "style:pron")
    await h.wait_hunts()
    result = h.session.last(SendMessage, ADMIN)
    names = found_names(result.text)
    assert h.db.balance(ADMIN)[:2] == (1, 0)  # админу попытки не списываются
    assert f"grab:{names[0]}" in callbacks(result)

    await h.press(ADMIN, f"grab:{names[0]}")
    assert "🎯 занят на канал" in h.session.last(SendMessage, ADMIN).text
    assert any(isinstance(r, UpdateUsernameRequest) for r in h.account.requests)
    assert h.db.list_grabbed()[0][0] == names[0]


async def test_user_cannot_use_admin_things(h):
    await h.press(USER, "grab:abcde")
    assert not any(isinstance(r, UpdateUsernameRequest) for r in h.account.requests)
    await h.send(USER, f"/give {USER} 100")
    assert h.db.balance(USER)[1] == 0


async def test_admin_autograb(h):
    await h.send(ADMIN, kb.BTN_ADMIN)
    assert "Админка" in h.session.last(SendMessage, ADMIN).text
    await h.press(ADMIN, "adm:autograb")
    await h.press(ADMIN, "style:pron")
    await h.wait_hunts()
    assert h.session.last(SendMessage, ADMIN).text.count("🎯 занят на канал") == 3
    assert len(h.db.list_grabbed()) == 3


async def test_admin_give_and_refund(h):
    await h.send(USER, "/start")
    await h.send(ADMIN, f"/give {USER} 2")
    assert h.db.balance(USER)[1] == 2
    assert "начислено 2 поиска" in h.session.last(SendMessage, USER).text

    await h.pay(USER, "pack:0:1:25", 25, "ch-9")
    assert h.db.balance(USER)[1] == 3
    await h.send(ADMIN, "/refund ch-9")
    refund = [c for c in h.session.calls if isinstance(c, RefundStarPayment)]
    assert refund and refund[0].telegram_payment_charge_id == "ch-9"
    assert h.db.balance(USER)[1] == 2
    assert h.db.business_stats()["stars"] == 0


async def test_every_callback_is_answered(h):
    """Кнопки не должны «зависать» с часиками."""
    for data in ["style:pron", "stop", "menu:buy", "finds", "cancel"]:
        before = sum(isinstance(c, AnswerCallbackQuery) for c in h.session.calls)
        await h.press(USER, data)
        assert sum(isinstance(c, AnswerCallbackQuery) for c in h.session.calls) == before + 1, data
    await h.wait_hunts()


async def test_buttons_under_old_messages_work(h):
    await h.press(USER, "style:mask", old=True)
    assert "Поиск по маске" in h.session.last(SendMessage, USER).text
    await h.press(USER, "cancel", old=True)
    await h.press(USER, "style:pron", old=True)
    await h.wait_hunts()
    assert len(found_names(h.session.last(SendMessage, USER).text)) == 3
