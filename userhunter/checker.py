"""Проверка доступности юзернейма через MTProto (account.checkUsername).

Bot API не умеет проверять свободность юза, поэтому проверка идёт
от имени вашего обычного аккаунта (Telethon-сессия). Сам аккаунт ничего
не меняет — только спрашивает «свободен ли юз».
"""
from __future__ import annotations

import asyncio
import enum
import logging

from telethon import TelegramClient, errors
from telethon.tl.functions.account import CheckUsernameRequest

log = logging.getLogger(__name__)


class Status(enum.Enum):
    FREE = "free"            # можно занять бесплатно
    TAKEN = "taken"          # занят
    FRAGMENT = "fragment"    # продаётся на fragment.com
    INVALID = "invalid"      # запрещён / зарезервирован


class Checker:
    def __init__(self, client: TelegramClient, delay: float):
        self.client = client
        self.delay = delay
        self.lock = asyncio.Lock()  # один запрос за раз на весь бот — так меньше флуда

    async def check(self, username: str) -> Status:
        async with self.lock:
            while True:
                try:
                    ok = await self.client(CheckUsernameRequest(username))
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
