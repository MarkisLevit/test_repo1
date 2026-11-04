"""Налаштування застосунку зі змінних оточення (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _parse_ids(value: str) -> frozenset[int]:
    ids = set()
    for part in value.replace(" ", "").split(","):
        if part:
            ids.add(int(part))
    return frozenset(ids)


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    webapp_url: str = ""
    admin_ids: frozenset[int] = frozenset()
    data_dir: Path = Path("data")
    dev_mode: bool = False
    run_bot: bool = True
    app_link: str = ""
    # Скільки секунд дійсний initData від Telegram
    init_data_ttl: int = 24 * 3600

    @property
    def db_path(self) -> Path:
        return self.data_dir / "warehouse.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            webapp_url=os.getenv("WEBAPP_URL", "").strip().rstrip("/"),
            admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "")),
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            dev_mode=_flag(os.getenv("DEV_MODE")),
            run_bot=_flag(os.getenv("RUN_BOT"), default=True),
            app_link=os.getenv("APP_LINK", "").strip(),
        )
