"""Telegram front end: commands, the polling job, and the broadcast loop."""

from __future__ import annotations

import asyncio
import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import Config
from providers import (
    Course,
    Provider,
    RSSCouponProvider,
    TutorialBarProvider,
    UdemyAffiliateProvider,
)
from store import Store

log = logging.getLogger(__name__)

# Telegram allows ~30 messages/second to different chats. Stay well under it.
SEND_DELAY = 0.06

WELCOME = (
    "👋 <b>Free Udemy course alerts</b>\n\n"
    "You're subscribed. I'll message you whenever I find a new free course.\n\n"
    "<b>Commands</b>\n"
    "/latest - the most recent finds\n"
    "/filter python, design - only get courses matching these keywords\n"
    "/filter off - remove your filter\n"
    "/status - what I'm tracking\n"
    "/stop - unsubscribe"
)


def format_course(course: Course) -> str:
    """Render one course as a Telegram HTML message."""
    lines = [f"🎓 <b>{html.escape(course.title)}</b>"]

    if course.headline:
        headline = course.headline[:180]
        lines.append(html.escape(headline))

    meta = []
    if course.instructor:
        meta.append(f"👤 {html.escape(course.instructor)}")
    if course.rating:
        meta.append(f"⭐ {course.rating:.1f}")
    if course.students:
        meta.append(f"👥 {course.students:,}")
    if meta:
        lines.append(" · ".join(meta))

    if course.coupon_code:
        lines.append(f"🎟 Coupon: <code>{html.escape(course.coupon_code)}</code>")
    if course.expires:
        lines.append(f"⏳ {html.escape(str(course.expires))}")

    lines.append(f'\n<a href="{html.escape(course.url, quote=True)}">Enrol free →</a>')
    return "\n".join(lines)


def matches_filter(course: Course, keywords: str) -> bool:
    """A subscriber with no keywords gets everything; otherwise OR-match."""
    if not keywords:
        return True
    blob = course.search_blob()
    return any(kw.strip() in blob for kw in keywords.split(",") if kw.strip())


class CourseBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config.db_path)
        self.providers: list[Provider] = []

        if config.tutorialbar:
            self.providers.append(
                TutorialBarProvider(
                    categories=config.tutorialbar_categories or None
                )
            )
        if config.has_udemy_api:
            self.providers.append(
                UdemyAffiliateProvider(
                    client_id=config.udemy_client_id,
                    client_secret=config.udemy_client_secret,
                    language=config.language,
                )
            )
        if config.coupon_feeds:
            self.providers.append(RSSCouponProvider(config.coupon_feeds))

        if not self.providers:
            log.warning(
                "No providers configured - the bot will run but never find "
                "anything. Set TUTORIALBAR=true, UDEMY_CLIENT_ID/SECRET, or "
                "COUPON_FEEDS."
            )

    # ---------- commands ----------

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        is_new = self.store.subscribe(update.effective_chat.id)
        text = WELCOME if is_new else "You're already subscribed. " + WELCOME
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        removed = self.store.unsubscribe(update.effective_chat.id)
        await update.message.reply_text(
            "Unsubscribed. Send /start any time to come back."
            if removed
            else "You weren't subscribed."
        )

    async def cmd_filter(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self.store.get_subscriber(chat_id):
            await update.message.reply_text("Send /start first.")
            return

        arg = " ".join(ctx.args).strip()
        if not arg:
            row = self.store.get_subscriber(chat_id)
            current = row["keywords"] or "(none - you get everything)"
            await update.message.reply_text(
                f"Your filter: {current}\n\n"
                "Set one with: /filter python, machine learning\n"
                "Remove it with: /filter off"
            )
            return

        if arg.lower() in {"off", "clear", "none"}:
            self.store.set_keywords(chat_id, "")
            await update.message.reply_text("Filter removed - you'll get everything.")
            return

        self.store.set_keywords(chat_id, arg)
        await update.message.reply_text(f"Filter set: {arg}")

    async def cmd_latest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        rows = self.store.recent(limit=5)
        if not rows:
            await update.message.reply_text(
                "Nothing found yet - I'll message you as soon as I do."
            )
            return

        lines = ["<b>Most recent finds</b>\n"]
        for row in rows:
            lines.append(
                f'• <a href="{html.escape(row["url"], quote=True)}">'
                f'{html.escape(row["title"])}</a>'
            )
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        sources = ", ".join(p.name for p in self.providers) or "none configured"
        await update.message.reply_text(
            f"Sources: {sources}\n"
            f"Checking every {self.config.poll_interval // 60} min\n"
            f"Courses tracked: {self.store.seen_count():,}\n"
            f"Subscribers: {self.store.subscriber_count():,}"
        )

    # ---------- polling + broadcast ----------

    async def fetch_all(self) -> list[Course]:
        results = await asyncio.gather(
            *(p.fetch() for p in self.providers), return_exceptions=True
        )
        courses: list[Course] = []
        for provider, result in zip(self.providers, results):
            if isinstance(result, Exception):
                log.error("Provider %s raised: %s", provider.name, result)
                continue
            courses.extend(result)
        return courses

    async def poll_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Runs on a timer: fetch, dedupe, announce."""
        if not self.providers:
            return

        courses = await self.fetch_all()
        new = self.store.filter_new(courses)

        if not new:
            log.info("Poll complete: nothing new (%d checked)", len(courses))
            return

        # First ever poll: the whole catalogue looks new, and nobody wants
        # ninety messages as a greeting. Record it as the baseline and start
        # announcing from the next genuinely new course.
        if self.store.seen_count() == 0:
            self.store.mark_seen(new)
            log.info("Cold start: baselined %d courses without announcing", len(new))
            return

        # Only mark what we actually announce. Anything over the per-cycle cap
        # stays unseen and goes out next cycle instead of being dropped.
        to_send = new[: self.config.max_per_cycle]
        log.info(
            "Found %d new; announcing %d (%d queued for later)",
            len(new),
            len(to_send),
            len(new) - len(to_send),
        )

        for course in to_send:
            await self.broadcast(ctx, course)
        self.store.mark_seen(to_send)

    async def broadcast(self, ctx: ContextTypes.DEFAULT_TYPE, course: Course) -> None:
        text = format_course(course)
        sent = 0

        for row in self.store.all_subscribers():
            if not matches_filter(course, row["keywords"]):
                continue

            chat_id = row["chat_id"]
            try:
                await ctx.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
                )
                sent += 1
            except Forbidden:
                # User blocked the bot or deleted the chat - clean them up.
                log.info("Removing blocked subscriber %s", chat_id)
                self.store.unsubscribe(chat_id)
            except RetryAfter as exc:
                log.warning("Rate limited, sleeping %.1fs", exc.retry_after)
                await asyncio.sleep(exc.retry_after + 1)
                try:
                    await ctx.bot.send_message(
                        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
                    )
                    sent += 1
                except TelegramError as retry_exc:
                    log.error("Send to %s failed after retry: %s", chat_id, retry_exc)
            except TelegramError as exc:
                log.error("Send to %s failed: %s", chat_id, exc)

            await asyncio.sleep(SEND_DELAY)

        log.info("Sent %r to %d subscribers", course.title[:40], sent)

    async def prune_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        removed = self.store.prune(older_than_days=90)
        if removed:
            log.info("Pruned %d old course records", removed)

    # ---------- wiring ----------

    def build(self) -> Application:
        app = Application.builder().token(self.config.telegram_token).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_start))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(CommandHandler("filter", self.cmd_filter))
        app.add_handler(CommandHandler("latest", self.cmd_latest))
        app.add_handler(CommandHandler("status", self.cmd_status))

        app.job_queue.run_repeating(
            self.poll_job, interval=self.config.poll_interval, first=15
        )
        app.job_queue.run_repeating(self.prune_job, interval=86400, first=3600)

        return app
