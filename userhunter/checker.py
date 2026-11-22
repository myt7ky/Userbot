"""Проверка доступности юзернейма через MTProto (account.checkUsername).

Bot API не умеет проверять свободность юза, поэтому проверка идёт
от имени обычных аккаунтов (Telethon-сессии). Аккаунты ничего не меняют —
только спрашивают «свободен ли юз». Аккаунтов может быть несколько:
каждый проверяет со своей паузой, вместе — параллельно.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from typing import Any, Sequence

from telethon import errors
from telethon.tl.functions.account import CheckUsernameRequest

log = logging.getLogger(__name__)


class Status(enum.Enum):
    FREE = "free"            # можно занять бесплатно
    TAKEN = "taken"          # занят
    FRAGMENT = "fragment"    # продаётся на fragment.com
    INVALID = "invalid"      # запрещён / зарезервирован


class Checker:
    def __init__(self, clients: Any | Sequence[Any], delay: float):
        self.clients = list(clients) if isinstance(clients, (list, tuple)) else [clients]
        self.client = self.clients[0]  # основной аккаунт — на нём же создаются каналы при захвате
        self.delay = delay
        self.lock = asyncio.Lock()     # для операций захвата на основном аккаунте
        self._idle: asyncio.Queue = asyncio.Queue()
        for c in self.clients:
            self._idle.put_nowait(c)

    @property
    def workers(self) -> int:
        return len(self.clients)

    async def check(self, username: str) -> Status:
        client = await self._idle.get()  # ждём свободный аккаунт
        try:
            while True:
                try:
                    ok = await client(CheckUsernameRequest(username))
                    status = Status.FREE if ok else Status.TAKEN
                    break
                except errors.FloodWaitError as e:
                    log.warning("FloodWait %s s", e.seconds)
                    await asyncio.sleep(e.seconds + 1)
                except errors.UsernamePurchaseAvailableError:
                    status = Status.FRAGMENT
                    break
                except errors.UsernameOccupiedError:
                    status = Status.TAKEN
                    break
                except errors.UsernameInvalidError:
                    status = Status.INVALID
                    break
            await asyncio.sleep(self.delay)
            return status
        finally:
            self._idle.put_nowait(client)
