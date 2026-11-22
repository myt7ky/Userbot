"""Генераторы «красивых» юзернеймов.

Правила Telegram: 5–32 символа, a-z / 0-9 / _, начинается с буквы,
не заканчивается на _ и не содержит двух _ подряд.
(4-символьные юзы продаются только на Fragment, поэтому минимум — 5.)
"""
from __future__ import annotations

import random
import re
import string
from pathlib import Path
from typing import Callable, Iterator

VOWELS = "aeiou"
CONSONANTS = "bcdfghjklmnprstvwxz"  # без q и y — с ними хуже читается
LETTERS = string.ascii_lowercase
DIGITS = string.digits

_VALID = re.compile(r"^[a-z](?!.*__)[a-z0-9_]{3,30}[a-z0-9]$")


def is_valid(name: str) -> bool:
    return bool(_VALID.match(name))


def _load_words() -> list[str]:
    path = Path(__file__).with_name("words.txt")
    words = {
        w.strip().lower()
        for w in path.read_text(encoding="utf-8").split()
        if w.strip() and not w.startswith("#")
    }
    return sorted(w for w in words if w.isalpha() and 5 <= len(w) <= 12)


WORDS: list[str] = _load_words()


# ---------- маски ----------
# c — согласная, v — гласная, l — любая буква, d — цифра,
# A, B, C... — одна и та же случайная буква везде, где стоит этот символ,
# остальное — как есть (литерал).
def from_mask(mask: str) -> str:
    bound: dict[str, str] = {}
    out = []
    for ch in mask:
        if ch == "c":
            out.append(random.choice(CONSONANTS))
        elif ch == "v":
            out.append(random.choice(VOWELS))
        elif ch == "l":
            out.append(random.choice(LETTERS))
        elif ch == "d":
            out.append(random.choice(DIGITS))
        elif ch.isupper():
            if ch not in bound:
                used = set(bound.values())
                bound[ch] = random.choice([x for x in LETTERS if x not in used] or LETTERS)
            out.append(bound[ch])
        else:
            out.append(ch)
    return "".join(out)


# ---------- режимы ----------
def gen_pronounceable() -> str:
    """Читаемые 5–6 букв: kamor, lumina, zoreb."""
    return from_mask(random.choice(["cvcvc", "cvccv", "vcvcv", "cvcvcv", "cvvcv", "cvcvv"]))


def gen_short() -> str:
    """Любые 5 букв."""
    return from_mask("lllll")


def gen_pattern() -> str:
    """Повторы и симметрия: ABABA, AABBA, ABCBA, ABBBA..."""
    return from_mask(random.choice(
        ["ABABA", "AABBA", "ABCBA", "ABBBA", "AABAA", "ABBAB", "ABCAB", "AAABB", "ABABAB", "ABCCBA"]
    ))


def gen_palindrome() -> str:
    """Читаемые палиндромы: kayak-подобные."""
    # cvc + v + cvc → "rotor"-подобные (5); cvcv + cvc → 7
    half = from_mask(random.choice(["cv", "cvc", "vcv"]))
    return half + random.choice(VOWELS + CONSONANTS) + half[::-1]


def gen_word() -> str:
    """Словарные английские слова."""
    return random.choice(WORDS)


_AFFIX_PRE = ["the", "its", "get", "my", "mr", "im", "go", "hey", "real", "only", "just", "x"]
_AFFIX_SUF = ["x", "s", "y", "o", "hq", "io", "ly", "er", "ix", "on", "_x", "dev", "app", "bot", "zz"]


def gen_word_affix(base: str | None = None) -> str:
    """Слово + приставка/суффикс: thecoder, pixelhq, getmint."""
    w = base or random.choice([w for w in WORDS if len(w) <= 7] or WORDS)
    if random.random() < 0.5:
        return random.choice(_AFFIX_PRE) + w
    return w + random.choice(_AFFIX_SUF)


def gen_word_digit() -> str:
    """Слово + «красивые» цифры: nova777, ghost01."""
    w = random.choice([w for w in WORDS if len(w) <= 6] or WORDS)
    return w + random.choice(["0", "1", "7", "00", "01", "13", "69", "77", "99", "777", "1337", "404"])


MODES: dict[str, tuple[str, Callable[[], str]]] = {
    "word": ("📖 Словарные слова", gen_word),
    "pron": ("🗣 Читаемые 5–6 букв", gen_pronounceable),
    "pattern": ("🔁 Повторы ABABA/AABBA", gen_pattern),
    "palindrome": ("🪞 Палиндромы", gen_palindrome),
    "affix": ("✨ Слово + приставка", gen_word_affix),
    "digits": ("🔢 Слово + цифры", gen_word_digit),
    "short": ("🎲 Случайные 5 букв", gen_short),
}


def candidates(
    mode: str | None = None,
    *,
    mask: str | None = None,
    base: str | None = None,
    skip: Callable[[str], bool] = lambda _: False,
    max_misses: int = 5000,
) -> Iterator[str]:
    """Бесконечный поток уникальных валидных кандидатов.

    Останавливается, если `max_misses` раз подряд не удалось придумать
    ничего нового (например, словарь исчерпан).
    """
    if mask:
        make: Callable[[], str] = lambda: from_mask(mask)
    elif base:
        make = lambda: gen_word_affix(base)
    else:
        make = MODES[mode or "pron"][1]

    seen: set[str] = set()
    misses = 0
    while misses < max_misses:
        name = make()
        if name in seen or not is_valid(name) or skip(name):
            misses += 1
            continue
        seen.add(name)
        misses = 0
        yield name


def coolness(name: str) -> int:
    """Грубая оценка «крутости» 0–100 — чтобы сортировать находки."""
    score = 60
    score -= (len(name) - 5) * 6  # короче — лучше
    if name in WORDS:
        score += 30
    if any(c.isdigit() for c in name):
        score -= 15
    if "_" in name:
        score -= 20
    if re.search(r"[^aeiou\d_]{3,}", name):  # 3+ согласных подряд — плохо читается
        score -= 20
    else:
        score += 10
    if name == name[::-1]:
        score += 10
    if len(set(name)) <= 2:
        score += 5
    return max(0, min(100, score))
