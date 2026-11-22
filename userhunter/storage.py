"""SQLite-кэш проверок, чтобы не проверять один и тот же юз повторно."""
from __future__ import annotations

import sqlite3
import time

RECHECK_AFTER = 3 * 24 * 3600  # занятые юзы перепроверяем раз в 3 дня


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS checks (
                   username TEXT PRIMARY KEY,
                   status   TEXT NOT NULL,
                   score    INTEGER NOT NULL DEFAULT 0,
                   checked  INTEGER NOT NULL
               )"""
        )
        # каналы, на которые заняты юзы
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS grabbed (
                   username    TEXT PRIMARY KEY,
                   channel_id  INTEGER NOT NULL,
                   access_hash INTEGER NOT NULL,
                   created     INTEGER NOT NULL
               )"""
        )
        # созданные, но пустые каналы (юз не встал) — переиспользуем при следующем захвате
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS spare (
                   channel_id  INTEGER PRIMARY KEY,
                   access_hash INTEGER NOT NULL
               )"""
        )
        self.db.commit()

    def save(self, username: str, status: str, score: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO checks VALUES (?, ?, ?, ?)",
            (username, status, score, int(time.time())),
        )
        self.db.commit()

    def recently_checked(self, username: str) -> bool:
        row = self.db.execute(
            "SELECT checked FROM checks WHERE username = ?", (username,)
        ).fetchone()
        return bool(row) and time.time() - row[0] < RECHECK_AFTER

    def found(self, limit: int = 50) -> list[tuple[str, int, int]]:
        return self.db.execute(
            "SELECT username, score, checked FROM checks WHERE status = 'free' "
            "ORDER BY score DESC, checked DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def stats(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT status, COUNT(*) FROM checks GROUP BY status"))

    # ---------- захват на каналы ----------
    def add_grabbed(self, username: str, channel_id: int, access_hash: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO grabbed VALUES (?, ?, ?, ?)",
            (username, channel_id, access_hash, int(time.time())),
        )
        self.db.execute("UPDATE checks SET status = 'grabbed' WHERE username = ?", (username,))
        self.db.commit()

    def get_grabbed(self, username: str) -> tuple[int, int] | None:
        return self.db.execute(
            "SELECT channel_id, access_hash FROM grabbed WHERE username = ?", (username,)
        ).fetchone()

    def remove_grabbed(self, username: str) -> None:
        self.db.execute("DELETE FROM grabbed WHERE username = ?", (username,))
        self.db.commit()

    def list_grabbed(self) -> list[tuple[str, int]]:
        return self.db.execute("SELECT username, created FROM grabbed ORDER BY created DESC").fetchall()

    def push_spare(self, channel_id: int, access_hash: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO spare VALUES (?, ?)", (channel_id, access_hash))
        self.db.commit()

    def pop_spare(self) -> tuple[int, int] | None:
        row = self.db.execute("SELECT channel_id, access_hash FROM spare LIMIT 1").fetchone()
        if row:
            self.db.execute("DELETE FROM spare WHERE channel_id = ?", (row[0],))
            self.db.commit()
        return row
