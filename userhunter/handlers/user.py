"""Меню пользователя: поиск, проверка, профиль, помощь."""
from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from .. import keyboards as kb
from .. import texts
from ..core import App, show
from ..generator import MODES, is_valid

router = Router(name="user")

# ввод в состоянии: обычный текст, но не кнопка меню и не команда
TYPED = F.text & ~F.text.in_(kb.MENU_BUTTONS) & ~F.text.startswith("/")

MAX_CHECK_NAMES = 5


class Form(StatesGroup):
    word = State()
    mask = State()
    check = State()


# ---------- старт и меню ----------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    free, credits, _ = app.db.balance(event_from_user.id)
    await message.answer(
        texts.welcome(html.escape(event_from_user.first_name), free, credits, app.cfg),
        reply_markup=kb.main_menu(app.is_admin(event_from_user.id)),
    )


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def cmd_help(message: Message, state: FSMContext, app: App) -> None:
    await state.clear()
    await message.answer(texts.help_text(app.cfg), reply_markup=kb.to_search())


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await message.answer(texts.TERMS)


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, app: App) -> None:
    await message.answer(texts.pay_support(html.escape(app.cfg.support)))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED, reply_markup=kb.main_menu(app.is_admin(event_from_user.id)))


@router.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer(texts.CANCELLED)
    await show(call, texts.CANCELLED)


# ---------- поиск ----------
@router.message(Command("search", "hunt"))
@router.message(F.text == kb.BTN_SEARCH)
async def cmd_search(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    free, credits, _ = app.db.balance(event_from_user.id)
    if app.is_admin(event_from_user.id):
        balance = "♾ Админ: без ограничений"
    else:
        balance = f"Доступно: <b>{texts.searches(free + credits)}</b>"
    await message.answer(f"🔎 <b>Выберите стиль юза</b>\n{balance}", reply_markup=kb.styles())


@router.callback_query(F.data == "menu:search")
async def cb_menu_search(call: CallbackQuery, state: FSMContext, app: App, event_from_user: User) -> None:
    await call.answer()
    await cmd_search(call.message, state, app, event_from_user)


async def begin(message: Message, bot: Bot, app: App, user_id: int, title: str, **gen_kwargs) -> None:
    """Списывает попытку и запускает поиск — или показывает оплату."""
    if user_id in app.hunts:
        await message.answer(texts.ALREADY_RUNNING)
        return
    attempt = None
    if not app.is_admin(user_id):
        attempt = app.db.take_attempt(user_id)
        if attempt is None:
            _, _, done = app.db.balance(user_id)
            await message.answer(texts.paywall(free_used=done > 0), reply_markup=kb.packs(app.cfg.packs))
            return
    app.start_hunt(bot, message.chat.id, user_id, title, attempt, **gen_kwargs)


@router.callback_query(F.data.startswith("style:"))
async def cb_style(call: CallbackQuery, state: FSMContext, bot: Bot, app: App, event_from_user: User) -> None:
    style = call.data.split(":", 1)[1]
    await call.answer()
    if style == "like":
        await state.set_state(Form.word)
        await show(call, texts.WORD_HELP, kb.cancel())
    elif style == "mask":
        await state.set_state(Form.mask)
        await show(call, texts.MASK_HELP, kb.cancel())
    elif style in MODES:
        title = kb.STYLES.get(style, MODES[style][0])
        await show(call, f"Стиль: {title}")
        await begin(call.message, bot, app, event_from_user.id, title, mode=style)


@router.message(Form.word, TYPED)
async def got_word(message: Message, state: FSMContext, bot: Bot, app: App, event_from_user: User) -> None:
    word = message.text.strip().lstrip("@").lower()
    if not (word.isascii() and word.isalpha() and 3 <= len(word) <= 20):
        await message.answer("Нужно одно слово латиницей, 3–20 букв. Например: <code>pixel</code>",
                             reply_markup=kb.cancel())
        return
    await state.clear()
    await begin(message, bot, app, event_from_user.id, f"🔤 вариации «{word}»", base=word, max_misses=300)


@router.message(Form.mask, TYPED)
async def got_mask(message: Message, state: FSMContext, bot: Bot, app: App, event_from_user: User) -> None:
    mask = message.text.strip()
    if not (5 <= len(mask) <= 32 and mask.isascii() and " " not in mask):
        await message.answer("Маска — 5–32 символа без пробелов. Например: <code>cvcvc</code>",
                             reply_markup=kb.cancel())
        return
    await state.clear()
    await begin(message, bot, app, event_from_user.id, f"🧩 маска {mask}", mask=mask, max_misses=500)


@router.callback_query(F.data == "stop")
async def cb_stop(call: CallbackQuery, app: App, event_from_user: User) -> None:
    if app.stop_hunt(event_from_user.id):
        await call.answer("Останавливаю…")
    else:
        await call.answer("Поиск уже завершён")


# ---------- проверка ----------
@router.message(F.text == kb.BTN_CHECK)
async def btn_check(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.check)
    await message.answer(texts.CHECK_HELP, reply_markup=kb.cancel())


async def run_check(message: Message, app: App, user_id: int, raw: str) -> None:
    names = [n.lstrip("@").lower() for n in raw.replace(",", " ").split()][:MAX_CHECK_NAMES]
    if not names:
        await message.answer(texts.CHECK_HELP, reply_markup=kb.cancel())
        return
    admin = app.is_admin(user_id)
    skipped = 0
    if not admin:
        # некорректные юзы не тратят лимит — сразу ставим их в конец
        names.sort(key=lambda n: not is_valid(n))
        valid = sum(map(is_valid, names))
        allowed = app.db.use_checks(user_id, valid, app.cfg.free_checks_per_day) if valid else 0
        if not allowed and valid:
            await message.answer(
                "⏳ Бесплатные проверки на сегодня закончились. Завтра будут новые — "
                "а пока можно запустить 🔎 поиск: он проверит сотни вариантов за вас.",
                reply_markup=kb.to_search(),
            )
            return
        skipped = valid - allowed
        names = names[:allowed] + [n for n in names if not is_valid(n)]
    wait = await message.answer("⏳ Проверяю…")
    lines, free = await app.check_names(names)
    text = "\n".join(lines)
    if free:
        text += "\n\n" + texts.HOW_TO_SET
    if skipped:
        text += f"\n\n⏳ Ещё {skipped} не проверено: бесплатные проверки на сегодня закончились."
    await wait.edit_text(text, reply_markup=kb.grab_only(free) if admin else None)


@router.message(Command("check"))
async def cmd_check(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await btn_check(message, state)
        return
    await run_check(message, app, event_from_user.id, args[1])


@router.message(Form.check, TYPED)
async def got_check(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    await run_check(message, app, event_from_user.id, message.text)


# ---------- профиль ----------
@router.message(Command("profile", "balance"))
@router.message(F.text == kb.BTN_PROFILE)
async def cmd_profile(message: Message, state: FSMContext, app: App, event_from_user: User) -> None:
    await state.clear()
    uid = event_from_user.id
    free, credits, done = app.db.balance(uid)
    checks_left = app.db.checks_left(uid, app.cfg.free_checks_per_day)
    await message.answer(texts.profile(uid, free, credits, done, checks_left), reply_markup=kb.profile())


@router.callback_query(F.data == "finds")
async def cb_finds(call: CallbackQuery, app: App, event_from_user: User) -> None:
    await call.answer()
    names = app.db.user_finds(event_from_user.id)
    if not names:
        await call.message.answer("Пока пусто. Запустите первый поиск!", reply_markup=kb.to_search())
        return
    await call.message.answer(
        "<b>📜 Ваши последние находки</b>\n(свободны были в момент поиска — проверьте перед установкой)\n\n"
        + "\n".join(f"<code>@{n}</code>" for n in names)
    )
