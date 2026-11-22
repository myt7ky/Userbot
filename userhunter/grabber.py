"""Захват свободного юза: создаём публичный канал и ставим ему этот юзернейм.

Канал создаётся от имени аккаунта-проверяльщика. Потом юз можно перенести
куда нужно: в настройках канала стереть юзернейм и сразу поставить его себе
(или другому каналу), либо отдать канал через «Передать права владельца».
"""
from __future__ import annotations

import asyncio
import contextlib
import enum
import logging

from telethon import errors
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    DeleteChannelRequest,
    EditTitleRequest,
    UpdateUsernameRequest,
)
from telethon.tl.types import InputChannel

from .checker import Checker
from .storage import Storage

log = logging.getLogger(__name__)

MAX_INLINE_FLOOD = 120  # FloodWait дольше этого не ждём, а сообщаем пользователю


class GrabResult(enum.Enum):
    OK = "ok"
    TAKEN = "taken"              # успели занять раньше нас / недоступен
    FRAGMENT = "fragment"        # продаётся на Fragment
    LIMIT_PUBLIC = "limit_public"      # лимит публичных юзов на аккаунте
    LIMIT_CHANNELS = "limit_channels"  # лимит каналов на аккаунте
    FLOOD = "flood"              # Telegram попросил подождать слишком долго
    ALREADY = "already"          # уже заняли этот юз раньше


GRAB_TEXT = {
    GrabResult.OK: "🎯 занят на канал",
    GrabResult.TAKEN: "😕 не успели: уже занят или недоступен",
    GrabResult.FRAGMENT: "💎 продаётся на Fragment",
    GrabResult.LIMIT_PUBLIC: "⛔ лимит публичных юзов на аккаунте (освободите через /release)",
    GrabResult.LIMIT_CHANNELS: "⛔ лимит каналов на аккаунте",
    GrabResult.FLOOD: "⏳ Telegram просит подождать, попробуйте позже",
    GrabResult.ALREADY: "🎯 уже занят вами",
}

STOP_RESULTS = {GrabResult.LIMIT_PUBLIC, GrabResult.LIMIT_CHANNELS, GrabResult.FLOOD}


class Grabber:
    def __init__(self, checker: Checker, storage: Storage, about: str = ""):
        self.checker = checker
        self.client = checker.client
        self.storage = storage
        self.about = about

    async def _call(self, request):
        """Запрос с ожиданием коротких FloodWait."""
        while True:
            try:
                return await self.client(request)
            except errors.FloodWaitError as e:
                if e.seconds > MAX_INLINE_FLOOD:
                    raise
                log.warning("FloodWait %s s при захвате", e.seconds)
                await asyncio.sleep(e.seconds + 1)

    async def _get_channel(self, title: str) -> InputChannel:
        spare = self.storage.pop_spare()
        if spare:
            channel = InputChannel(*spare)
            with contextlib.suppress(errors.RPCError):
                await self._call(EditTitleRequest(channel, title))
            return channel
        res = await self._call(CreateChannelRequest(title=title, about=self.about, broadcast=True))
        chat = res.chats[0]
        return InputChannel(chat.id, chat.access_hash)

    async def grab(self, username: str) -> GrabResult:
        if self.storage.get_grabbed(username):
            return GrabResult.ALREADY
        async with self.checker.lock:
            try:
                return await self._grab(username)
            finally:
                await asyncio.sleep(self.checker.delay)

    async def _grab(self, username: str) -> GrabResult:
        try:
            channel = await self._get_channel(username)
        except errors.ChannelsTooMuchError:
            return GrabResult.LIMIT_CHANNELS
        except errors.FloodWaitError:
            return GrabResult.FLOOD

        try:
            await self._call(UpdateUsernameRequest(channel, username))
        except Exception as e:
            # юз не встал — канал оставляем пустым про запас
            self.storage.push_spare(channel.channel_id, channel.access_hash)
            if isinstance(e, errors.ChannelsAdminPublicTooMuchError):
                return GrabResult.LIMIT_PUBLIC
            if isinstance(e, errors.UsernamePurchaseAvailableError):
                return GrabResult.FRAGMENT
            if isinstance(e, errors.FloodWaitError):
                return GrabResult.FLOOD
            if isinstance(e, (errors.UsernameOccupiedError, errors.UsernameInvalidError)):
                return GrabResult.TAKEN
            raise
        self.storage.add_grabbed(username, channel.channel_id, channel.access_hash)
        log.info("Занят @%s", username)
        return GrabResult.OK

    async def release(self, username: str) -> bool:
        """Удаляет канал — юз снова становится свободным."""
        row = self.storage.get_grabbed(username)
        if not row:
            return False
        async with self.checker.lock:
            with contextlib.suppress(errors.ChannelInvalidError, errors.ChannelPrivateError):
                await self._call(DeleteChannelRequest(InputChannel(*row)))
        self.storage.remove_grabbed(username)
        return True
