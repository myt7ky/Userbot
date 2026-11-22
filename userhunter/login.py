"""Одноразовый вход в аккаунт, через который бот будет проверять юзы.

Запуск: python -m userhunter.login
Telethon спросит номер телефона и код из Telegram, затем сохранит файл *.session.
"""
from telethon.sync import TelegramClient

from .config import load_config

cfg = load_config()
with TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash) as client:
    me = client.get_me()
    print(f"Готово! Сессия сохранена для {me.first_name} (@{me.username}). Теперь: python -m userhunter")
