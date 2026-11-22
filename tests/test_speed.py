"""Ускорение: отсев через t.me и несколько аккаунтов."""
from __future__ import annotations

import asyncio
import re
import time

import pytest
from aiohttp import web
from aiogram.methods import SendMessage
from telethon.tl.functions.account import CheckUsernameRequest

from userhunter.checker import Checker
from userhunter.prefilter import WebPrefilter

from .conftest import USER, FakeTelegramAccount

pytestmark = pytest.mark.asyncio

TAKEN_PAGE = '<div class="tgme_page_title"><span dir="auto">Someone</span></div>'
EMPTY_PAGE = '<div class="tgme_page_description">If you have Telegram, you can contact</div>'


def is_free(name: str) -> bool:
    return name.endswith(("a", "e"))  # «свободны» ~10% вариантов


@pytest.fixture
async def fake_tme():
    """Поддельный t.me: страница профиля есть у занятых юзов и у «telegram»."""
    async def page(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name == "broken":
            return web.Response(status=500)
        free = name != "telegram" and (is_free(name) or len(name) == 24)  # 24 буквы — калибровочная абракадабра
        return web.Response(text=EMPTY_PAGE if free else TAKEN_PAGE, content_type="text/html")

    app = web.Application()
    app.router.add_get("/{name}", page)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


async def test_prefilter_calibrates_and_filters(fake_tme):
    pf = WebPrefilter(base_url=fake_tme)
    await pf.start()
    assert pf.enabled
    assert await pf.exists("telegram") is True
    assert await pf.exists("broken") is None  # ошибка сайта → «не знаю»
    assert await pf.maybe_free(["kama", "kamo", "broken"]) == ["kama", "broken"]
    await pf.close()


async def test_prefilter_disables_itself_when_site_is_down():
    pf = WebPrefilter(base_url="http://127.0.0.1:9", timeout=1)
    await pf.start()
    assert not pf.enabled
    assert await pf.maybe_free(["abcde"]) == ["abcde"]  # всё отдаём на проверку аккаунтом
    await pf.close()


async def test_hunt_with_prefilter_uses_far_fewer_account_checks(h, fake_tme):
    h.account.is_free = is_free
    h.app.cfg.__dict__["max_checks_per_search"] = 500  # frozen dataclass: меняем только в тесте

    await h.press(USER, "style:pron")
    await h.wait_hunts()
    without = sum(isinstance(r, CheckUsernameRequest) for r in h.account.requests)

    h.app.prefilter = WebPrefilter(base_url=fake_tme)
    await h.app.prefilter.start()
    h.account.requests.clear()
    h.db.add_credits(USER, 1)
    await h.press(USER, "style:pron")
    await h.wait_hunts()
    with_pf = sum(isinstance(r, CheckUsernameRequest) for r in h.account.requests)
    await h.app.prefilter.close()

    names = re.findall(r"<code>@([a-z]+)</code>", h.session.last(SendMessage, USER).text)
    assert len(names) == 3 and all(is_free(n) for n in names)
    assert with_pf <= 4 < without, (with_pf, without)  # через аккаунт идут почти только свободные


async def test_several_accounts_check_in_parallel():
    accounts = [FakeTelegramAccount() for _ in range(3)]
    one = Checker(accounts[0], delay=0.05)
    three = Checker(accounts, delay=0.05)
    names = [f"name{i:02d}x" for i in range(9)]

    t = time.monotonic()
    await asyncio.gather(*(one.check(n) for n in names))
    slow = time.monotonic() - t

    t = time.monotonic()
    await asyncio.gather(*(three.check(n) for n in names))
    fast = time.monotonic() - t

    assert fast < slow / 2
    assert all(len(a.requests) >= 3 for a in accounts)  # нагрузка распределилась
