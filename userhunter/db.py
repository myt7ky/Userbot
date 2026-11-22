"""SQLite: кэш проверок, пользователи и баланс, платежи, занятые каналы."""
from __future__ import annotations

import sqlite3
import time

RECHECK_AFTER = 3 * 24 * 3600  # проверенные юзы не трогаем 3 дня (и не показываем другим)

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    username TEXT PRIMARY KEY,
    status   TEXT NOT NULL,
    score    INTEGER NOT NULL DEFAULT 0,
    checked  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    free_left   INTEGER NOT NULL DEFAULT 0,
    credits     INTEGER NOT NULL DEFAULT 0,
    searches    INTEGER NOT NULL DEFAULT 0,
    checks_day  TEXT,
    checks_used INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    charge_id TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    stars     INTEGER NOT NULL,
    credits   INTEGER NOT NULL,
    created   INTEGER NOT NULL,
    refunded  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS finds (
    user_id  INTEGER NOT NULL,
    username TEXT NOT NULL,
    created  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS finds_user ON finds(user_id, created);
-- каналы, на которые заняты юзы (функция админа)
CREATE TABLE IF NOT EXISTS grabbed (
    username    TEXT PRIMARY KEY,
    channel_id  INTEGER NOT NULL,
    access_hash INTEGER NOT NULL,
    created     INTEGER NOT NULL
);
-- созданные, но пустые каналы (юз не встал) — переиспользуем при следующем захвате
CREATE TABLE IF NOT EXISTS spare (
    channel_id  INTEGER PRIMARY KEY,
    access_hash INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- кэш проверок ----------
    def save(self, username: str, status: str, score: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO checks VALUES (?, ?, ?, ?)",
            (username, status, score, int(time.time())),
        )
        self.db.commit()

    def recently_checked(self, username: str) -> bool:
        row = self.db.execute("SELECT checked FROM checks WHERE username = ?", (username,)).fetchone()
        return bool(row) and time.time() - row[0] < RECHECK_AFTER

    def found(self, limit: int = 50) -> list[tuple[str, int, int]]:
        return self.db.execute(
            "SELECT username, score, checked FROM checks WHERE status = 'free' "
            "ORDER BY score DESC, checked DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def check_stats(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT status, COUNT(*) FROM checks GROUP BY status"))

    # ---------- пользователи и баланс ----------
    def ensure_user(self, user_id: int, name: str, free: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO users (id, name, free_left, created) VALUES (?, ?, ?, ?)",
            (user_id, name, free, int(time.time())),
        )
        self.db.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        self.db.commit()

    def balance(self, user_id: int) -> tuple[int, int, int]:
        """(бесплатных, платных, всего поисков сделано)"""
        row = self.db.execute(
            "SELECT free_left, credits, searches FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return tuple(row) if row else (0, 0, 0)

    def take_attempt(self, user_id: int) -> str | None:
        """Списывает попытку: сначала бесплатную, потом платную. None — нечего списывать."""
        with self.db:
            if self.db.execute(
                "UPDATE users SET free_left = free_left - 1 WHERE id = ? AND free_left > 0", (user_id,)
            ).rowcount:
                return "free"
            if self.db.execute(
                "UPDATE users SET credits = credits - 1 WHERE id = ? AND credits > 0", (user_id,)
            ).rowcount:
                return "paid"
        return None

    def return_attempt(self, user_id: int, kind: str) -> None:
        column = "free_left" if kind == "free" else "credits"
        with self.db:
            self.db.execute(f"UPDATE users SET {column} = {column} + 1 WHERE id = ?", (user_id,))

    def add_credits(self, user_id: int, n: int) -> bool:
        with self.db:
            return bool(self.db.execute(
                "UPDATE users SET credits = MAX(0, credits + ?) WHERE id = ?", (n, user_id)
            ).rowcount)

    def count_search(self, user_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE users SET searches = searches + 1 WHERE id = ?", (user_id,))

    def use_checks(self, user_id: int, wanted: int, per_day: int) -> int:
        """Сколько ручных проверок из `wanted` можно сделать сегодня (и списывает их)."""
        today = time.strftime("%Y-%m-%d")
        with self.db:
            row = self.db.execute(
                "SELECT checks_day, checks_used FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            used = row[1] if row and row[0] == today else 0
            allowed = max(0, min(wanted, per_day - used))
            self.db.execute(
                "UPDATE users SET checks_day = ?, checks_used = ? WHERE id = ?",
                (today, used + allowed, user_id),
            )
        return allowed

    def checks_left(self, user_id: int, per_day: int) -> int:
        row = self.db.execute(
            "SELECT checks_day, checks_used FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        used = row[1] if row and row[0] == time.strftime("%Y-%m-%d") else 0
        return max(0, per_day - used)

    def add_find(self, user_id: int, username: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO finds VALUES (?, ?, ?)", (user_id, username, int(time.time())))

    def user_finds(self, user_id: int, limit: int = 30) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT username FROM finds WHERE user_id = ? ORDER BY created DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        )]

    # ---------- платежи ----------
    def add_payment(self, charge_id: str, user_id: int, stars: int, credits: int) -> bool:
        """Записывает платёж и начисляет поиски. False — этот платёж уже был учтён."""
        with self.db:
            try:
                self.db.execute(
                    "INSERT INTO payments (charge_id, user_id, stars, credits, created) VALUES (?, ?, ?, ?, ?)",
                    (charge_id, user_id, stars, credits, int(time.time())),
                )
            except sqlite3.IntegrityError:
                return False
            self.db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (credits, user_id))
        return True

    def get_payment(self, charge_id: str) -> tuple[int, int, int, int] | None:
        """(user_id, stars, credits, refunded)"""
        return self.db.execute(
            "SELECT user_id, stars, credits, refunded FROM payments WHERE charge_id = ?", (charge_id,)
        ).fetchone()

    def mark_refunded(self, charge_id: str) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT user_id, credits FROM payments WHERE charge_id = ? AND refunded = 0", (charge_id,)
            ).fetchone()
            if row:
                self.db.execute("UPDATE payments SET refunded = 1 WHERE charge_id = ?", (charge_id,))
                self.db.execute(
                    "UPDATE users SET credits = MAX(0, credits - ?) WHERE id = ?", (row[1], row[0])
                )

    def business_stats(self) -> dict[str, int]:
        q = lambda sql: self.db.execute(sql).fetchone()[0] or 0  # noqa: E731
        return {
            "users": q("SELECT COUNT(*) FROM users"),
            "searches": q("SELECT SUM(searches) FROM users"),
            "payers": q("SELECT COUNT(DISTINCT user_id) FROM payments WHERE refunded = 0"),
            "payments": q("SELECT COUNT(*) FROM payments WHERE refunded = 0"),
            "stars": q("SELECT SUM(stars) FROM payments WHERE refunded = 0"),
            "finds": q("SELECT COUNT(*) FROM finds"),
        }

    # ---------- захват на каналы ----------
    def add_grabbed(self, username: str, channel_id: int, access_hash: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO grabbed VALUES (?, ?, ?, ?)",
                (username, channel_id, access_hash, int(time.time())),
            )
            self.db.execute("UPDATE checks SET status = 'grabbed' WHERE username = ?", (username,))

    def get_grabbed(self, username: str) -> tuple[int, int] | None:
        return self.db.execute(
            "SELECT channel_id, access_hash FROM grabbed WHERE username = ?", (username,)
        ).fetchone()

    def remove_grabbed(self, username: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM grabbed WHERE username = ?", (username,))

    def list_grabbed(self) -> list[tuple[str, int]]:
        return self.db.execute("SELECT username, created FROM grabbed ORDER BY created DESC").fetchall()

    def push_spare(self, channel_id: int, access_hash: int) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO spare VALUES (?, ?)", (channel_id, access_hash))

    def pop_spare(self) -> tuple[int, int] | None:
        with self.db:
            row = self.db.execute("SELECT channel_id, access_hash FROM spare LIMIT 1").fetchone()
            if row:
                self.db.execute("DELETE FROM spare WHERE channel_id = ?", (row[0],))
        return row
