import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_id: int
    api_hash: str
    admin_ids: frozenset[int]
    check_delay: float
    session_name: str
    db_path: str


def load_config() -> Config:
    def need(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise SystemExit(f"Не задана переменная окружения {name} (см. .env.example)")
        return value

    admins = frozenset(
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
    )
    return Config(
        bot_token=need("BOT_TOKEN"),
        api_id=int(need("API_ID")),
        api_hash=need("API_HASH"),
        admin_ids=admins,
        check_delay=max(0.5, float(os.getenv("CHECK_DELAY", "3"))),
        session_name=os.getenv("SESSION_NAME", "checker"),
        db_path=os.getenv("DB_PATH", "usernames.db"),
    )
