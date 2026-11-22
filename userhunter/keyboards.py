from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton as IB,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .config import Pack
from .texts import searches

BTN_SEARCH = "🔎 Найти юз"
BTN_CHECK = "✅ Проверить юз"
BTN_BUY = "💎 Купить поиски"
BTN_PROFILE = "👤 Профиль"
BTN_HELP = "❓ Помощь"
BTN_ADMIN = "🛠 Админка"
MENU_BUTTONS = {BTN_SEARCH, BTN_CHECK, BTN_BUY, BTN_PROFILE, BTN_HELP, BTN_ADMIN}

# режим генератора → подпись кнопки. Порядок = порядок кнопок.
STYLES: dict[str, str] = {
    "brand": "💎 Как дорогие",
    "ru": "🇷🇺 Русские слова",
    "pron": "🗣 Читаемые",
    "word": "📖 Слова",
    "affix": "✨ Слово+",
    "pattern": "🔁 Повторы",
    "palindrome": "🪞 Палиндромы",
    "digits": "🔢 Слово+цифры",
    "short": "🎲 Случайные",
}


def _inline(rows: list[list[IB]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_CHECK)],
        [KeyboardButton(text=BTN_BUY), KeyboardButton(text=BTN_PROFILE)],
        [KeyboardButton(text=BTN_HELP)] + ([KeyboardButton(text=BTN_ADMIN)] if is_admin else []),
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Выберите действие 👇")


def styles() -> InlineKeyboardMarkup:
    buttons = [IB(text=title, callback_data=f"style:{key}") for key, title in STYLES.items()]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([IB(text="🔤 По слову", callback_data="style:like"), IB(text="🧩 По маске", callback_data="style:mask")])
    rows.append([IB(text="✖️ Отмена", callback_data="cancel")])
    return _inline(rows)


def packs(items: tuple[Pack, ...]) -> InlineKeyboardMarkup:
    rows = []
    for i, p in enumerate(items):
        per = round(p.stars / p.searches)
        suffix = f" · {per} ⭐/шт" if p.searches > 1 else ""
        rows.append([IB(text=f"{searches(p.searches)} — {p.stars} ⭐{suffix}", callback_data=f"buy:{i}")])
    return _inline(rows)


def cancel() -> InlineKeyboardMarkup:
    return _inline([[IB(text="✖️ Отмена", callback_data="cancel")]])


def stop() -> InlineKeyboardMarkup:
    return _inline([[IB(text="⏹ Остановить", callback_data="stop")]])


def after_hunt(grab_names: list[str] | None = None) -> InlineKeyboardMarkup:
    rows = [[IB(text=f"🎯 Занять @{n}", callback_data=f"grab:{n}")] for n in (grab_names or [])[:10]]
    rows.append([IB(text="🔁 Искать ещё", callback_data="menu:search")])
    return _inline(rows)


def grab_only(names: list[str]) -> InlineKeyboardMarkup | None:
    if not names:
        return None
    return _inline([[IB(text=f"🎯 Занять @{n}", callback_data=f"grab:{n}")] for n in names[:10]])


def profile() -> InlineKeyboardMarkup:
    return _inline([
        [IB(text="📜 Мои находки", callback_data="finds")],
        [IB(text="💎 Купить поиски", callback_data="menu:buy")],
    ])


def to_search() -> InlineKeyboardMarkup:
    return _inline([[IB(text="🔎 Найти юз", callback_data="menu:search")]])


def admin(autograb: bool) -> InlineKeyboardMarkup:
    return _inline([
        [IB(text=f"🎯 Автозахват: {'вкл ✅' if autograb else 'выкл'}", callback_data="adm:autograb")],
        [IB(text="📋 Занятые каналы", callback_data="adm:channels"), IB(text="🏆 Лучшие находки", callback_data="adm:found")],
        [IB(text="🔄 Обновить", callback_data="adm:refresh")],
    ])
