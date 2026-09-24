"""Entry point. Run with: python -m bot from the project root."""

import logging

from bot.config import Config
from bot.health import HealthServer
from bot.shutdown import ShutdownManager
from bot.telegram_bot import CourseBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    config = Config.from_env()
    bot = CourseBot(config)
    app = bot.build()

    shutdown = ShutdownManager()
    health: HealthServer | None = None
    if config.health_port:
        health = HealthServer(config.health_port, stats_fn=bot.health_stats)
        health.start()
        shutdown.on_shutdown(health.stop)

    logging.info(
        "Starting bot | sources=%s | interval=%ss | mode=%s",
        [p.name for p in bot.providers] or "none",
        config.poll_interval,
        "webhook" if config.webhook_url else "polling",
    )

    # python-telegram-bot manages its own event loop via Application.
    if config.webhook_url:
        app.run_webhook(
            listen="0.0.0.0",
            port=config.webhook_port,
            url_path=config.telegram_token,
            webhook_url=f"{config.webhook_url}/{config.telegram_token}",
        )
    else:
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
