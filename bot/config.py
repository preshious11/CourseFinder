"""Configuration, read from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent.parent

load_dotenv()


def _resolve(path: str) -> str:
    """Anchor a relative path to the package, not the working directory.

    The bot is documented to run as `python -m bot` (equivalently
    `python -m bot.main`) from the parent directory, but it also gets run from
    inside the package. With a bare relative DB_PATH those two produce
    *different* databases, so the bot quietly loses its subscribers depending
    on where it was launched from.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str(PACKAGE_DIR / candidate)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Config:
    telegram_token: str
    udemy_client_id: str | None
    udemy_client_secret: str | None
    coupon_feeds: list[str]
    tutorialbar: bool           # the no-credentials default source
    tutorialbar_categories: list[str]

    # Multi-platform /search. Each flag gates one platform; a platform that is
    # off, or whose credentials are absent, is skipped rather than failing the
    # whole search.
    search_coursera: bool
    search_edx: bool
    search_mit: bool
    search_freecodecamp: bool
    search_catalogue_pages: int   # how deep to scan catalogues that lack search
    search_timeout: float
    db_path: str
    poll_interval: int          # seconds between checks
    max_per_cycle: int          # cap announcements so nobody gets 200 messages
    language: str
    admin_chat_id: int | None

    @classmethod
    def from_env(cls) -> Config:
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
        categories = [
            c.strip()
            for c in os.getenv("TUTORIALBAR_CATEGORIES", "").split(",")
            if c.strip()
        ]

        return cls(
            telegram_token=token,
            udemy_client_id=os.getenv("UDEMY_CLIENT_ID") or None,
            udemy_client_secret=os.getenv("UDEMY_CLIENT_SECRET") or None,
            coupon_feeds=feeds,
            tutorialbar=_bool("TUTORIALBAR", True),
            tutorialbar_categories=categories,
            search_coursera=_bool("SEARCH_COURSERA", True),
            search_edx=_bool("SEARCH_EDX", True),
            search_mit=_bool("SEARCH_MIT", True),
            search_freecodecamp=_bool("SEARCH_FREECODECAMP", True),
            search_catalogue_pages=_int("SEARCH_CATALOGUE_PAGES", 5),
            search_timeout=float(_int("SEARCH_TIMEOUT", 25)),
            db_path=_resolve(os.getenv("DB_PATH", "bot.db")),
            poll_interval=_int("POLL_INTERVAL", 3600),
            max_per_cycle=_int("MAX_PER_CYCLE", 10),
            language=os.getenv("LANGUAGE", "en"),
            admin_chat_id=int(admin) if admin.lstrip("-").isdigit() else None,
        )

    @property
    def has_udemy_api(self) -> bool:
        return bool(self.udemy_client_id and self.udemy_client_secret)
