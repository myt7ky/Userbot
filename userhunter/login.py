"""Вход в аккаунты, через которые бот будет проверять юзы.

Запуск: python -m userhunter.login
Входит по очереди во все аккаунты из SESSION_NAME (через запятую), которые ещё
не авторизованы. Telethon спросит номер телефона и код из Telegram.

Чтобы добавить второй аккаунт: впишите в .env  SESSION_NAME=checker,checker2
и снова запустите эту команду — она спросит номер только для нового.
"""
from telethon.sync import TelegramClient

from .config import load_config

cfg = load_config()
for name in cfg.sessions:
    client = TelegramClient(name, cfg.api_id, cfg.api_hash)
    client.connect()
    if client.is_user_authorized():
        me = client.get_me()
        print(f"✔ {name}: уже вошли как @{me.username or me.first_name}")
        client.disconnect()
        continue
    print(f"\n=== Вход для сессии «{name}»: введите номер аккаунта-проверяльщика ===")
    client.start()
    me = client.get_me()
    print(f"✔ {name}: сессия сохранена для {me.first_name} (@{me.username})")
    client.disconnect()
print("\nГотово! Запуск бота: python -m userhunter")
