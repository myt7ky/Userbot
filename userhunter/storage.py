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
