"""Быстрый предварительный отсев занятых юзов через публичные страницы t.me.

Если на t.me/<имя> есть профиль, канал, группа или бот — юз точно занят,
и тратить на него медленную проверку через аккаунт не нужно. Всё, что
отсев пропустил (или не смог проверить), всё равно проверяет аккаунт,
так что ошибка отсева может только замедлить поиск, но не выдать
занятый юз как свободный.

При старте отсев сам проверяет себя на заведомо занятом и заведомо
свободном юзе. Если t.me недоступен или изменил вёрстку — отсев
выключается и бот работает как раньше.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import string

import aiohttp

log = logging.getLogger(__name__)

EXISTS = re.compile(r'class="tgme_page_title"')


class WebPrefilter:
    def __init__(self, concurrency: int = 8, timeout: float = 8, base_url: str = "https://t.me"):
        self.base_url = base_url.rstrip("/")
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None
        self.enabled = False

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        gibberish = "".join(random.choices(string.ascii_lowercase, k=24))
        taken, free = await asyncio.gather(self.exists("telegram"), self.exists(gibberish))
        self.enabled = taken is True and free is False
        if self.enabled:
            log.info("Быстрый отсев через t.me включён")
        else:
            log.warning("Быстрый отсев через t.me недоступен (taken=%s, free=%s) — проверяю только аккаунтом",
                        taken, free)

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def exists(self, name: str) -> bool | None:
        """True — юз точно занят, False — страницы нет, None — не удалось узнать."""
        if not self.session:
            return None
        try:
            async with self.sem, self.session.get(f"{self.base_url}/{name}") as resp:
                if resp.status != 200:
                    return None
                return bool(EXISTS.search(await resp.text()))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def maybe_free(self, names: list[str]) -> list[str]:
        """Оставляет только те юзы, которые могут быть свободны."""
        if not self.enabled:
            return names
        results = await asyncio.gather(*(self.exists(n) for n in names))
        return [n for n, taken in zip(names, results) if taken is not True]
