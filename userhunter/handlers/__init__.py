from aiogram import Router

from . import admin, payments, user


def build_router() -> Router:
    root = Router(name="root")
    # порядок важен: админские команды раньше пользовательских
    for sub in (admin.router, payments.router, user.router):
        sub._parent_router = None  # позволяет собрать диспетчер повторно (в тестах)
        root.include_router(sub)
    return root
