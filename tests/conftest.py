"""Тестовый стенд: настоящий Dispatcher aiogram + поддельные Telegram Bot API и аккаунт Telethon."""
from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace as NS
from typing import Any, Callable

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendInvoice, SendMessage, TelegramMethod
from aiogram.types import Message, Update
from telethon import errors
from telethon.tl.functions.account import CheckUsernameRequest
from telethon.tl.functions.channels import CreateChannelRequest, UpdateUsernameRequest

from userhunter.bot import build_dispatcher, make_bot
from userhunter.checker import Checker
from userhunter.config import Config, Pack
from userhunter.core import App
from userhunter.db import Database
from userhunter.grabber import Grabber

ADMIN = 1000
USER = 2000


class FakeSession(BaseSession):
    """Вместо HTTP к api.telegram.org — записываем вызовы и отдаём правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._ids = itertools.count(100)

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendInvoice, EditMessageText)):
            mid = getattr(method, "message_id", None) or next(self._ids)
            return Message.model_validate(
                {"message_id": mid, "date": 0, "chat": {"id": method.chat_id, "type": "private"},
                 "text": getattr(method, "text", None)},
                context={"bot": bot},
            )
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass

    # --- помощники для проверок ---
    def texts(self, chat_id: int | None = None) -> list[str]:
        return [c.text for c in self.calls
                if isinstance(c, (SendMessage, EditMessageText)) and (chat_id is None or c.chat_id == chat_id)]

    def last(self, kind: type = SendMessage, chat_id: int | None = None):
        for c in reversed(self.calls):
            if isinstance(c, kind) and (chat_id is None or c.chat_id == chat_id):
                return c
        raise AssertionError(f"нет вызова {kind.__name__}")


class FakeTelegramAccount:
    """Поддельный Telethon-клиент: решает, какие юзы свободны."""

    def __init__(self) -> None:
        self.is_free: Callable[[str], bool] = lambda name: True
        self.channels = itertools.count(1)
        self.requests: list[Any] = []

    async def __call__(self, req: Any) -> Any:
        self.requests.append(req)
        if isinstance(req, CheckUsernameRequest):
            if req.username.startswith("frag"):
                raise errors.UsernamePurchaseAvailableError(request=None)
            return self.is_free(req.username)
        if isinstance(req, CreateChannelRequest):
            cid = next(self.channels)
            return NS(chats=[NS(id=cid, access_hash=cid * 10)])
        if isinstance(req, UpdateUsernameRequest):
            return True
        return True


class Harness:
    def __init__(self, cfg: Config) -> None:
        self.account = FakeTelegramAccount()
        self.session = FakeSession()
        self.bot = make_bot("42:TEST")
        self.bot.session = self.session
        checker = Checker(self.account, delay=0)
        self.db = Database(":memory:")
        self.app = App(cfg=cfg, checker=checker, db=self.db, grabber=Grabber(checker, self.db))
        self.dp = build_dispatcher(self.app)
        self._uid = itertools.count(1)

    @staticmethod
    def _user(uid: int) -> dict:
        return {"id": uid, "is_bot": False, "first_name": f"User{uid}", "language_code": "ru"}

    async def feed(self, payload: dict) -> None:
        upd = Update.model_validate({"update_id": next(self._uid), **payload}, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, upd)

    async def send(self, uid: int, text: str) -> None:
        await self.feed({"message": {
            "message_id": next(self._uid), "date": 0, "chat": {"id": uid, "type": "private"},
            "from": self._user(uid), "text": text,
        }})

    async def press(self, uid: int, data: str, old: bool = False) -> None:
        """old=True — кнопка под сообщением старше 48 ч (Telegram присылает его с date=0)."""
        await self.feed({"callback_query": {
            "id": str(next(self._uid)), "from": self._user(uid), "chat_instance": "ci", "data": data,
            "message": {"message_id": 1, "date": 0 if old else 1_700_000_000, "chat": {"id": uid, "type": "private"}, "text": "…"},
        }})

    async def pay(self, uid: int, payload: str, stars: int, charge_id: str) -> None:
        await self.feed({"pre_checkout_query": {
            "id": f"pc-{charge_id}", "from": self._user(uid), "currency": "XTR",
            "total_amount": stars, "invoice_payload": payload,
        }})
        await self.feed({"message": {
            "message_id": next(self._uid), "date": 0, "chat": {"id": uid, "type": "private"},
            "from": self._user(uid),
            "successful_payment": {"currency": "XTR", "total_amount": stars, "invoice_payload": payload,
                                   "telegram_payment_charge_id": charge_id, "provider_payment_charge_id": ""},
        }})

    async def wait_hunts(self) -> None:
        while self.app.hunts:
            await asyncio.gather(*self.app.hunts.values(), return_exceptions=True)


@pytest.fixture
def cfg() -> Config:
    return Config(
        bot_token="42:TEST", api_id=1, api_hash="x", admin_ids=frozenset({ADMIN}),
        check_delay=0, free_searches=1, results_per_search=3, max_checks_per_search=40,
        free_checks_per_day=3, max_parallel_hunts=1, packs=(Pack(1, 25), Pack(5, 100)),
        support="@support",
    )


@pytest.fixture
def h(cfg: Config) -> Harness:
    return Harness(cfg)
