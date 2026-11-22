"""Точка входа: сборка бота, Telethon-клиента и обработчиков."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, TelegramObject
from telethon import TelegramClient

from .checker import Checker
from .config import Config, load_config
from .core import App
from .db import Database
from .grabber import Grabber
from .handlers import build_router

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="search", description="🔎 Найти юз"),
    BotCommand(command="check", description="✅ Проверить юз"),
    BotCommand(command="buy", description="💎 Купить поиски"),
    BotCommand(command="profile", description="👤 Профиль"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="terms", description="📄 Условия"),
    BotCommand(command="paysupport", description="💬 Поддержка по оплате"),
]


class RegisterUser(BaseMiddleware):
    """Заводит пользователя в базе при первом же действии (с бесплатными поисками)."""

    def __init__(self, app: App):
        self.app = app

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user and not user.is_bot:
            self.app.db.ensure_user(user.id, user.full_name, self.app.cfg.free_searches)
        return await handler(event, data)


def build_dispatcher(app: App) -> Dispatcher:
    dp = Dispatcher(app=app)
    dp.update.outer_middleware(RegisterUser(app))
    dp.include_router(build_router())
    return dp


def make_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg: Config = load_config()
    if not cfg.admin_ids:
        raise SystemExit("Заполните ADMIN_IDS в .env — нужен хотя бы один администратор.")

    client = TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Сессия не авторизована. Сначала выполните: python -m userhunter.login")

    checker = Checker(client, cfg.check_delay)
    db = Database(cfg.db_path)
    app = App(cfg=cfg, checker=checker, db=db, grabber=Grabber(checker, db, about=cfg.grab_about))

    bot = make_bot(cfg.bot_token)
    dp = build_dispatcher(app)
    await bot.set_my_commands(COMMANDS)

    me = await client.get_me()
    log.info("Проверяю через аккаунт %s, бот запускается…", me.username or me.id)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
