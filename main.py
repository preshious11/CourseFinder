"""Entry point. Run with: python -m bot.main from the parent directory."""

import asyncio
import logging

from .config import Config
from .telegram_bot import CourseBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    config = Config.from_env()
    bot = CourseBot(config)
    app = bot.build()

    logging.info(
        "Starting bot | sources=%s | interval=%ss",
        [p.name for p in bot.providers] or "none",
        config.poll_interval,
    )
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
