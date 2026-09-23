"""Telegram front end: commands, inline mode, polling, and broadcast."""

from __future__ import annotations

import asyncio
import html
import logging
import time

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
)

from bot.config import Config
from bot.providers import (
    PROVIDER_LABELS,
    Course,
    Provider,
    RSSCouponProvider,
    TutorialBarProvider,
    UdemyAffiliateProvider,
)
from bot.search_providers import (
    CourseraProvider,
    EdXProvider,
    FreeCodeCampProvider,
    MITProvider,
    search_all_providers,
)
from bot.store import Store

log = logging.getLogger(__name__)

SEND_DELAY = 0.06

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0

WELCOME = (
    "👋 <b>Free Udemy course alerts</b>\n\n"
    "You're subscribed. I'll message you whenever I find a new free course.\n\n"
    "<b>Commands</b>\n"
    "/search python - search courses across every platform\n"
    "/search python coursera - search a specific platform\n"
    "/latest - the most recent finds\n"
    "/latest python - filter recent by keyword\n"
    "/filter python, design - only get courses matching these keywords\n"
    "/filter off - remove your filter (get everything)\n"
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


def format_search_results(query: str, courses: list[Course]) -> str:
    """Render multi-platform search results, labelled by platform."""
    title = query.strip().title()
    lines = [f"📚 <b>{html.escape(title)} Courses</b>\n"]

    for index, course in enumerate(courses, start=1):
        lines.append(f"<b>{index}. {html.escape(course.title)}</b>")
        lines.append(f"🏫 {html.escape(course.provider)}")

        meta = []
        if course.instructor:
            meta.append(f"👤 {html.escape(course.instructor)}")
        if course.rating:
            meta.append(f"⭐ {course.rating:.1f}")
        if course.price:
            meta.append(f"💰 {html.escape(course.price)}")
        if course.duration:
            meta.append(f"⏱ {html.escape(course.duration)}")
        if course.level:
            meta.append(f"📶 {html.escape(course.level)}")
        if meta:
            lines.append(" · ".join(meta))

        lines.append(
            f'🔗 <a href="{html.escape(course.url, quote=True)}">Open Course</a>\n'
        )

    return "\n".join(lines)


def matches_filter(course: Course, keywords: str) -> bool:
    """A subscriber with no keywords gets everything; otherwise OR-match."""
    if not keywords:
        return True
    blob = course.search_blob()
    return any(kw.strip() in blob for kw in keywords.split(",") if kw.strip())


def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log it, don't crash the polling loop."""
    log.error("Unhandled error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            update.effective_message.reply_text(
                "Something went wrong. Try again shortly.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass


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

        pages = config.search_catalogue_pages
        self.search_providers: list[Provider] = list(self.providers)
        if config.search_mit:
            self.search_providers.append(MITProvider())
        if config.search_coursera:
            self.search_providers.append(CourseraProvider(pages=pages))
        if config.search_edx:
            self.search_providers.append(EdXProvider(pages=pages))
        if config.search_freecodecamp:
            self.search_providers.append(FreeCodeCampProvider())

    def _is_admin(self, chat_id: int) -> bool:
        return self.config.admin_chat_id is not None and chat_id == self.config.admin_chat_id

    def _resolve_platform(self, platform: str) -> str:
        """Normalise a user-supplied platform name to a provider name."""
        platform = platform.lower().strip()
        aliases = {
            "coursera": "coursera", "course": "coursera",
            "edx": "edx", "edX": "edx",
            "mit": "mit", "openlearning": "mit",
            "freecodecamp": "freecodecamp", "freecodecamp.org": "freecodecamp",
            "tutorialbar": "tutorialbar",
            "udemy": "udemy-api",
        }
        return aliases.get(platform, platform)

    def _filter_by_platform(self, courses: list[Course], platform: str) -> list[Course]:
        """Return only courses from a specific platform."""
        target = self._resolve_platform(platform)
        return [c for c in courses if c.source == target or c.provider.lower() == target]

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        is_new = self.store.subscribe(update.effective_chat.id)
        text = WELCOME if is_new else "You're already subscribed. " + WELCOME
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Commands:\n"
            "/start - Subscribe\n"
            "/stop - Unsubscribe\n"
            "/filter keywords - Set keyword filter\n"
            "/filter off - Remove filter\n"
            "/search query [platform] - Search courses\n"
            "/latest [keyword] - Most recent finds\n"
            "/status - Bot status\n"
            "Type /filter off to receive all courses.\n"
            "You can filter by platform too: /search python coursera",
            parse_mode=ParseMode.HTML,
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
            await update.message.reply_text("Filter removed — you'll get everything.")
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

        args = ctx.args
        platform = None
        if args:
            platform = args[-1] if not args[0].lower().startswith("/") else None
            if platform and self._resolve_platform(platform) in {
                p.name for p in self.search_providers
            } | {p.source for p in self.providers}:
                pass
            else:
                platform = None

        lines = ["<b>Most recent finds</b>\n"]
        for row in rows:
            if platform:
                continue
            lines.append(
                f'• <a href="{html.escape(row["url"], quote=True)}">'
                f'{html.escape(row["title"])}</a>'
            )
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/search <query> [platform] - look across configured platforms."""
        args = ctx.args
        if not args:
            await update.message.reply_text(
                "What should I look for?\n\n"
                "Example: /search python\n"
                "By platform: /search python coursera\n"
                f"I'll check {self._platform_names()}."
            )
            return

        platform = None
        query_parts = []
        for arg in args:
            resolved = self._resolve_platform(arg)
            if resolved in {p.name for p in self.search_providers} | {p.source for p in self.providers}:
                platform = resolved
            else:
                query_parts.append(arg)

        if not query_parts:
            await update.message.reply_text(
                "What should I search for?\n\n"
                "Example: /search python\n"
                "By platform: /search python coursera"
            )
            return

        query = " ".join(query_parts).strip()
        await ctx.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )

        courses = await search_all_providers(
            self.search_providers,
            query,
            limit_per_provider=self.config.max_per_cycle,
            timeout=self.config.search_timeout,
        )

        if platform:
            courses = self._filter_by_platform(courses, platform)

        if not courses:
            plat = f" on {PROVIDER_LABELS.get(platform, platform)}" if platform else ""
            await update.message.reply_text(
                f"No courses found{plat} for “{query}”.\n"
                "Try a broader term, or check /status to see which platforms "
                "are switched on."
            )
            return

        text = format_search_results(query, courses)
        while len(text) > 4000 and len(courses) > 1:
            courses = courses[:-1]
            text = format_search_results(query, courses)

        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        sources = ", ".join(p.name for p in self.providers) or "none configured"
        await update.message.reply_text(
            f"Sources: {sources}\n"
            f"Search platforms: {self._platform_names() or 'none'}\n"
            f"Checking every {self.config.poll_interval // 60} min\n"
            f"Courses tracked: {self.store.seen_count():,}\n"
            f"Subscribers: {self.store.subscriber_count():,}"
        )

    async def cmd_inline(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline queries: type @bot_name query to search."""
        query = ctx.args[0] if ctx.args else ""
        if not query:
            await ctx.answer_inline_query([])
            return

        courses = await search_all_providers(
            self.search_providers,
            query,
            limit_per_provider=3,
            timeout=self.config.search_timeout,
        )

        results = []
        for course in courses[:5]:
            title = course.title[:50]
            description = (
                f"{course.provider} · {html.escape(course.instructor)}"
                if course.instructor
                else course.provider
            )
            text = format_course(course)
            results.append(
                InlineQueryResultArticle(
                    id=course.id,
                    title=title,
                    input_message_content=InputTextMessageContent(
                        message_text=text, parse_mode=ParseMode.HTML
                    ),
                    description=description[:50],
                )
            )

        await ctx.answer_inline_query(results, cache_time=60)

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

        if self.store.seen_count() == 0:
            self.store.mark_seen(new)
            log.info("Cold start: baselined %d courses without announcing", len(new))
            return

        to_send = new[: self.config.max_per_cycle]
        log.info(
            "Found %d new; announcing %d (%d queued for later)",
            len(new), len(to_send), len(new) - len(to_send),
        )

        for course in to_send:
            await self.broadcast(ctx, course)
        self.store.mark_seen(to_send)

    async def broadcast(self, ctx: ContextTypes.DEFAULT_TYPE, course: Course) -> None:
        """Send a course to all matching subscribers with exponential backoff."""
        text = format_course(course)
        sent = 0

        for row in self.store.all_subscribers():
            if not matches_filter(course, row["keywords"]):
                continue

            chat_id = row["chat_id"]
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await ctx.bot.send_message(
                        chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
                    )
                    sent += 1
                    break
                except Forbidden:
                    log.info("Removing blocked subscriber %s", chat_id)
                    self.store.unsubscribe(chat_id)
                    break
                except RetryAfter as exc:
                    log.warning("Rate limited, sleeping %.1fs", exc.retry_after)
                    time.sleep(exc.retry_after + 1)
                    try:
                        await ctx.bot.send_message(
                            chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
                        )
                        sent += 1
                        break
                    except TelegramError:
                        if attempt == MAX_RETRIES:
                            log.error("Send to %s failed after %d retries", chat_id, MAX_RETRIES)
                        continue
                except TelegramError as exc:
                    wait = INITIAL_BACKOFF * (2 ** (attempt - 1))
                    log.warning(
                        "Send to %s failed (attempt %d/%d), retrying in %.1fs",
                        chat_id, attempt, MAX_RETRIES, wait,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(wait)
                    else:
                        log.error("Send to %s failed after %d retries: %s", chat_id, MAX_RETRIES, exc)

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
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(CommandHandler("filter", self.cmd_filter))
        app.add_handler(CommandHandler("latest", self.cmd_latest))
        app.add_handler(CommandHandler("search", self.cmd_search))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(InlineQueryHandler(self.cmd_inline))
        app.add_error_handler(handle_error)

        app.job_queue.run_repeating(
            self.poll_job, interval=self.config.poll_interval, first=15
        )
        app.job_queue.run_repeating(self.prune_job, interval=86400, first=3600)

        return app
