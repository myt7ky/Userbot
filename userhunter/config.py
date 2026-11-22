import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Pack:
    searches: int  # сколько поисков даёт пакет
    stars: int     # цена в Telegram Stars


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_id: int
    api_hash: str
    admin_ids: frozenset[int]
    check_delay: float = 3.0
    session_name: str = "checker"
    db_path: str = "usernames.db"
    auto_grab: bool = False
    grab_about: str = ""
    # ---- монетизация ----
    free_searches: int = 1           # бесплатных поисков новому пользователю
    results_per_search: int = 5      # сколько свободных юзов выдаёт один поиск
    max_checks_per_search: int = 200  # после стольких проверок поиск сдаётся
    free_checks_per_day: int = 10    # бесплатных ручных проверок в сутки
    max_parallel_hunts: int = 2      # одновременных поисков (остальные ждут в очереди)
    packs: tuple[Pack, ...] = field(default_factory=lambda: (Pack(1, 10), Pack(5, 40), Pack(15, 100)))
    support: str = ""                # контакт поддержки для /paysupport
    web_prefilter: bool = True       # быстрый отсев занятых юзов через t.me

    @property
    def sessions(self) -> list[str]:
        """SESSION_NAME может содержать несколько аккаунтов через запятую."""
        return [s.strip() for s in self.session_name.split(",") if s.strip()]


def parse_packs(raw: str) -> tuple[Pack, ...]:
    """"1:25,5:100" → (Pack(1, 25), Pack(5, 100))"""
    packs = []
    for part in raw.replace(" ", "").split(","):
        if part:
            searches, stars = part.split(":")
            packs.append(Pack(int(searches), int(stars)))
    return tuple(packs)


def load_config() -> Config:
    def need(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise SystemExit(f"Не задана переменная окружения {name} (см. .env.example)")
        return value

    def num(name: str, default: int) -> int:
        return int(os.getenv(name, str(default)))

    admins = frozenset(int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x)
    return Config(
        bot_token=need("BOT_TOKEN"),
        api_id=int(need("API_ID")),
        api_hash=need("API_HASH"),
        admin_ids=admins,
        check_delay=max(0.5, float(os.getenv("CHECK_DELAY", "3"))),
        session_name=os.getenv("SESSION_NAME", "checker"),
        db_path=os.getenv("DB_PATH", "usernames.db"),
        auto_grab=os.getenv("AUTO_GRAB", "0").strip().lower() in ("1", "true", "yes", "on"),
        grab_about=os.getenv("GRAB_ABOUT", ""),
        free_searches=num("FREE_SEARCHES", 1),
        results_per_search=num("RESULTS_PER_SEARCH", 5),
        max_checks_per_search=num("MAX_CHECKS_PER_SEARCH", 200),
        free_checks_per_day=num("FREE_CHECKS_PER_DAY", 10),
        max_parallel_hunts=max(1, num("MAX_PARALLEL_HUNTS", 2)),
        packs=parse_packs(os.getenv("PACKS", "1:10,5:40,15:100")),
        support=os.getenv("SUPPORT", ""),
        web_prefilter=os.getenv("WEB_PREFILTER", "1").strip().lower() in ("1", "true", "yes", "on"),
    )
