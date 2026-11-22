import random

import pytest

from userhunter.config import Pack, parse_packs
from userhunter.db import Database
from userhunter.generator import MODES, candidates, from_mask, is_valid
from userhunter.texts import searches


@pytest.mark.parametrize("mode", list(MODES))
def test_generators_produce_valid_unique_names(mode):
    random.seed(0)
    gen = candidates(mode)
    names = [next(gen) for _ in range(200)] if mode != "word" else list(gen)[:200]
    assert all(is_valid(n) for n in names)
    assert len(set(names)) == len(names)


def test_is_valid():
    assert is_valid("abcde") and is_valid("a_b_c") and is_valid("x1234")
    for bad in ["abcd", "1abcd", "abcd_", "ab__cd", "ABCDE", "a" * 33, "_abcd"]:
        assert not is_valid(bad), bad


def test_mask():
    random.seed(1)
    n = from_mask("ABABA")
    assert n[0] == n[2] == n[4] and n[1] == n[3] and n[0] != n[1]
    assert from_mask("neo12") == "neo12"


def test_parse_packs():
    assert parse_packs("1:25, 5:100") == (Pack(1, 25), Pack(5, 100))


def test_plural():
    assert [searches(n) for n in (1, 2, 5, 11, 21, 22)] == [
        "1 поиск", "2 поиска", "5 поисков", "11 поисков", "21 поиск", "22 поиска"]


def test_billing_order_and_returns():
    db = Database(":memory:")
    db.ensure_user(1, "u", 1)
    db.add_credits(1, 1)
    assert db.take_attempt(1) == "free"
    assert db.take_attempt(1) == "paid"
    assert db.take_attempt(1) is None
    db.return_attempt(1, "paid")
    assert db.balance(1)[:2] == (0, 1)
    db.ensure_user(1, "renamed", 1)  # повторный /start не дарит новый бесплатный поиск
    assert db.balance(1)[:2] == (0, 1)


def test_payment_idempotent_and_refund():
    db = Database(":memory:")
    db.ensure_user(1, "u", 0)
    assert db.add_payment("c1", 1, 100, 5)
    assert not db.add_payment("c1", 1, 100, 5)
    assert db.balance(1)[1] == 5
    db.take_attempt(1)
    db.mark_refunded("c1")
    assert db.balance(1)[1] == 0  # не уходит в минус
    db.mark_refunded("c1")
    assert db.get_payment("c1")[3] == 1


def test_daily_checks():
    db = Database(":memory:")
    db.ensure_user(1, "u", 0)
    assert db.use_checks(1, 2, 3) == 2
    assert db.use_checks(1, 5, 3) == 1
    assert db.use_checks(1, 1, 3) == 0
    assert db.checks_left(1, 3) == 0
