"""Configuration, read from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Config:
    telegram_token: str
    udemy_client_id: str | None
    udemy_client_secret: str | None
    coupon_feeds: list[str]
    db_path: str
    poll_interval: int          # seconds between checks
    max_per_cycle: int          # cap announcements so nobody gets 200 messages
    language: str
    admin_chat_id: int | None

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and "
                "fill it in (get a token from @BotFather on Telegram)."
            )

        feeds = [
            u.strip()
            for u in os.getenv("COUPON_FEEDS", "").split(",")
            if u.strip()
        ]
        admin = os.getenv("ADMIN_CHAT_ID", "").strip()

        return cls(
            telegram_token=token,
            udemy_client_id=os.getenv("UDEMY_CLIENT_ID") or None,
            udemy_client_secret=os.getenv("UDEMY_CLIENT_SECRET") or None,
            coupon_feeds=feeds,
            db_path=os.getenv("DB_PATH", "bot.db"),
            poll_interval=_int("POLL_INTERVAL", 3600),
            max_per_cycle=_int("MAX_PER_CYCLE", 10),
            language=os.getenv("LANGUAGE", "en"),
            admin_chat_id=int(admin) if admin.lstrip("-").isdigit() else None,
        )

    @property
    def has_udemy_api(self) -> bool:
        return bool(self.udemy_client_id and self.udemy_client_secret)
