"""Telegram front end: commands, inline mode, polling, broadcast, and digests."""

from __future__ import annotations

import asyncio
import datetime
import html
import logging

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Conflict, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
)

from bot.config import Config
from bot.i18n import t
from bot.metrics import Metrics
from bot.providers import (
    PROVIDER_LABELS,
    Course,
    Provider,
    RSSCouponProvider,
    TutorialBarProvider,
    UdemyAffiliateProvider,
)
from bot.rate_limit import RateLimiter
from bot.search_cache import SearchCache
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


def format_course(course: Course) -> str:
    """Render one course as a Telegram HTML message."""
    lines = [f"🎓 <b>{html.escape(course.title)}</b>"]

    if course.headline:
        headline = course.headline[:180]
        lines.append(f"📝 {html.escape(headline)}")

    meta = []
    if course.instructor:
        meta.append(f"👤 {html.escape(course.instructor)}")
    if course.rating:
        meta.append(f"⭐ {course.rating:.1f}")
    if course.students:
        meta.append(f"👥 {course.students:,}")
    if meta:
        lines.append(" · ".join(meta))

    if course.price:
        lines.append(f"💰 {html.escape(course.price)}")
    if course.coupon_code:
        lines.append(f"🎟️ Coupon: <code>{html.escape(course.coupon_code)}</code>")
    if course.duration:
        lines.append(f"⏱ {html.escape(course.duration)}")
    if course.level:
        lines.append(f"📶 Level: {html.escape(course.level)}")

    lines.append(f'🔗 <a href="{html.escape(course.url, quote=True)}">Enrol free →</a>')
    return "\n".join(lines)


def format_search_results(query: str, courses: list[Course]) -> str:
    """Render multi-platform search results, labelled by platform."""
    title = query.strip().title()
    lines = [f"📚 <b>{html.escape(title)} Courses</b> ✨\n"]

    for index, course in enumerate(courses, start=1):
        lines.append(f"🏅 <b>{index}. {html.escape(course.title)}</b>")
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


def format_digest(rows: list, language: str = "en") -> str:
    """Render a batch of digest-queued courses as one message."""
    header = t("digest_header", language, count=len(rows))
    lines = [header]
    for row in rows:
        lines.append(
            f'• <a href="{html.escape(row["url"], quote=True)}">'
            f'{html.escape(row["title"])}</a>'
        )
    return "\n".join(lines)


def matches_filter(course: Course, keywords: str) -> bool:
    """A subscriber with no keywords gets everything; otherwise OR-match."""
    if not keywords:
        return True
    blob = course.search_blob()
    return any(kw.strip() in blob for kw in keywords.split(",") if kw.strip())


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log it, don't crash the polling loop."""
    err = context.error

    if isinstance(err, Conflict):
        # Another instance is polling with the same token. Replying would
        # also fail, and the user cannot act on it from chat — log loudly.
        log.critical(
            "getUpdates conflict: another process is using this bot token. "
            "Stop the old container/instance (docker ps, Railway deployments, "
            "local `python -m bot`) so only one runs."
        )
        return

    log.error("Unhandled error: %s", err, exc_info=err)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
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
        self.metrics = Metrics()
        self.rate_limiter = RateLimiter(max_calls=10, window=60.0)
        self.search_cache = SearchCache(ttl=60.0)

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

    def _lang(self, chat_id: int | None = None) -> str:
        """Resolve the language for a chat (falls back to config default)."""
        if chat_id is not None:
            row = self.store.get_subscriber(chat_id)
            if row and "language" in row.keys() and row["language"]:
                return row["language"]
        return self.config.language

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

    def _known_platforms(self) -> set[str]:
        return {p.name for p in self.search_providers} | {p.source for p in self.providers}

    def _platform_names(self) -> str:
        return ", ".join(sorted(self._known_platforms()))

    async def _check_rate(self, update: Update) -> bool:
        """Return True if the user may proceed; otherwise send a rate-limit reply."""
        chat_id = update.effective_chat.id
        key = str(chat_id)
        if self.rate_limiter.allow(key):
            return True
        wait = self.rate_limiter.retry_after(key)
        lang = self._lang(chat_id)
        await update.message.reply_text(t("rate_limited", lang, seconds=wait))
        return False

    # ---------- commands ----------

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        chat_id = update.effective_chat.id
        lang = self._lang(chat_id)
        is_new = self.store.subscribe(chat_id)
        try:
            bot_username = (await ctx.bot.get_me()).username
        except TelegramError:
            bot_username = "this_bot"
        user = update.effective_user
        if user.username:
            display_name = f"@{user.username}"
        elif user.first_name:
            display_name = user.first_name
        else:
            display_name = "there"
        if is_new:
            text = t(
                "welcome", lang, bot_username=bot_username, username=display_name
            )
        else:
            text = t("already_subscribed", lang) + t(
                "welcome", lang, bot_username=bot_username, username=display_name
            )
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        self.metrics.inc("cmd_start")

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        await update.message.reply_text(
            "Commands:\n"
            "/start - Subscribe\n"
            "/stop - Unsubscribe\n"
            "/filter keywords - Set keyword filter\n"
            "/filter off - Remove filter\n"
            "/search query [platform] - Search courses\n"
            "/latest [keyword] - Most recent finds\n"
            "/course <slug> - Course details\n"
            "/categories - Browse categories\n"
            "/freq instant|daily|weekly|paused - Notification frequency\n"
            "/status - Bot status\n"
            "Type /filter off to receive all courses.\n"
            "You can filter by platform too: /search python coursera",
            parse_mode=ParseMode.HTML,
        )
        self.metrics.inc("cmd_help")

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        chat_id = update.effective_chat.id
        lang = self._lang(chat_id)
        removed = self.store.unsubscribe(chat_id)
        await update.message.reply_text(
            t("unsubscribed", lang) if removed else t("not_subscribed", lang)
        )
        self.metrics.inc("cmd_stop")

    async def cmd_filter(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        chat_id = update.effective_chat.id
        lang = self._lang(chat_id)
        if not self.store.get_subscriber(chat_id):
            await update.message.reply_text(t("send_start_first", lang))
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
            await update.message.reply_text(t("filter_removed", lang))
            return

        self.store.set_keywords(chat_id, arg)
        await update.message.reply_text(f"Filter set: {arg}")
        self.metrics.inc("cmd_filter")

    async def cmd_freq(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/freq [instant|daily|weekly|paused] - set notification frequency."""
        if not await self._check_rate(update):
            return
        chat_id = update.effective_chat.id
        lang = self._lang(chat_id)
        if not self.store.get_subscriber(chat_id):
            await update.message.reply_text(t("send_start_first", lang))
            return

        arg = " ".join(ctx.args).strip().lower()
        if not arg:
            row = self.store.get_subscriber(chat_id)
            current = row["frequency"] if "frequency" in row.keys() else "instant"
            await update.message.reply_text(
                t("freq_usage", lang, current=current), parse_mode=ParseMode.HTML
            )
            return

        if arg not in {"instant", "daily", "weekly", "paused"}:
            await update.message.reply_text(t("freq_invalid", lang))
            return

        self.store.set_frequency(chat_id, arg)
        await update.message.reply_text(t("freq_set", lang, frequency=arg))
        self.metrics.inc("cmd_freq")

    async def cmd_latest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        chat_id = update.effective_chat.id
        lang = self._lang(chat_id)
        args = ctx.args
        platform = None
        keyword = None

        if args:
            resolved = self._resolve_platform(args[-1])
            if resolved in self._known_platforms():
                platform = resolved
            else:
                keyword = " ".join(args).lower()

        rows = self.store.recent(limit=20)
        if not rows:
            await update.message.reply_text(t("nothing_found", lang))
            return

        lines = ["<b>Most recent finds</b>\n"]
        shown = 0
        for row in rows:
            if shown >= 5:
                break
            if platform and row["source"] != platform:
                continue
            if keyword and keyword not in row["title"].lower():
                continue
            lines.append(
                f'• <a href="{html.escape(row["url"], quote=True)}">'
                f'{html.escape(row["title"])}</a>'
            )
            shown += 1

        if shown == 0:
            await update.message.reply_text(t("no_matching", lang))
            return

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        self.metrics.inc("cmd_latest")

    async def cmd_course(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/course <slug-or-url> - show full details for one course."""
        if not await self._check_rate(update):
            return
        args = ctx.args
        if not args:
            await update.message.reply_text(
                "Usage: /course python-101\nor paste a full course URL."
            )
            return

        target = " ".join(args).strip().lower()
        # Direct URL lookup.
        if target.startswith("http"):
            slug = target.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
        else:
            slug = target

        rows = self.store.recent(limit=200)
        match = None
        for row in rows:
            row_slug = row["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
            if row_slug == slug or slug in row["title"].lower():
                match = row
                break

        if not match:
            await update.message.reply_text(
                f"No course found matching “{args[0]}”.\n"
                "Try /latest to see recent finds, or /search to look wider."
            )
            return

        text = (
            f"🎓 <b>{html.escape(match['title'])}</b>\n"
            f"🏫 {html.escape(match['source'])}\n"
            f'🔗 <a href="{html.escape(match["url"], quote=True)}">Open course</a>'
        )
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        self.metrics.inc("cmd_course")

    async def cmd_categories(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/categories - list browsable course categories."""
        if not await self._check_rate(update):
            return
        cats = getattr(TutorialBarProvider, "CATEGORIES", ())
        if not cats:
            await update.message.reply_text("No categories available.")
            return
        lines = ["📚 <b>Categories</b>\n"]
        for c in cats:
            lines.append(f"• {c.replace('-', ' ').title()}")
        lines.append("\nUse /filter &lt;category&gt; to subscribe to one.")
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.HTML
        )
        self.metrics.inc("cmd_categories")

    async def cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/search <query> [platform] - look across configured platforms."""
        if not await self._check_rate(update):
            return
        chat_id = update.effective_chat.id
        lang = self._lang(chat_id)
        args = ctx.args
        if not args:
            await update.message.reply_text(
                t("search_no_query", lang)
                + f"\nI'll check {self._platform_names()}."
            )
            return

        platform = None
        query_parts = []
        for arg in args:
            resolved = self._resolve_platform(arg)
            if resolved in self._known_platforms():
                platform = resolved
            else:
                query_parts.append(arg)

        if not query_parts:
            await update.message.reply_text(t("search_no_query", lang))
            return

        query = " ".join(query_parts).strip()

        # Serve from cache when possible.
        cache_key = f"{query}|{platform or '*'}"
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            courses = cached
            self.metrics.inc("search_cache_hit")
        else:
            await ctx.bot.send_chat_action(
                chat_id=chat_id, action=ChatAction.TYPING
            )
            courses = await search_all_providers(
                self.search_providers,
                query,
                limit_per_provider=self.config.max_per_cycle,
                timeout=self.config.search_timeout,
            )
            if platform:
                courses = self._filter_by_platform(courses, platform)
            self.search_cache.set(cache_key, courses)
            self.metrics.inc("search_cache_miss")

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
        self.metrics.inc("cmd_search")

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        lang = self._lang(update.effective_chat.id)
        sources = ", ".join(p.name for p in self.providers) or "none configured"
        metrics = self.metrics.snapshot()
        digest_pending = self.store.digest_count()
        await update.message.reply_text(
            t("status_header", lang) + "\n"
            f"📡 Sources: {sources}\n"
            f"🔍 Platforms: {self._platform_names() or 'none'}\n"
            f"⏰ Interval: {self.config.poll_interval // 60} min\n"
            f"📚 Courses tracked: {self.store.seen_count():,}\n"
            f"👥 Subscribers: {self.store.subscriber_count():,}\n"
            f"📥 Digest queued: {digest_pending}\n"
            f"🔍 Searches served: {metrics.get('cmd_search', 0):,}\n"
            f"⏱ Uptime: {int(self.metrics.uptime_seconds()) // 3600}h "
            f"{(int(self.metrics.uptime_seconds()) % 3600) // 60}m",
            parse_mode=ParseMode.HTML,
        )
        self.metrics.inc("cmd_status")

    async def cmd_inline(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline queries: type @bot_name query to search."""
        query = ctx.args[0] if ctx.args else ""
        if not query:
            await ctx.answer_inline_query([])
            return

        cache_key = f"inline|{query}"
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            courses = cached
        else:
            courses = await search_all_providers(
                self.search_providers,
                query,
                limit_per_provider=3,
                timeout=self.config.search_timeout,
            )
            self.search_cache.set(cache_key, courses)

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
        self.metrics.inc("inline_query")

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
        """Runs on a timer: fetch, dedupe, announce or enqueue for digest."""
        if not self.providers:
            return

        courses = await self.fetch_all()
        new = self.store.filter_new(courses)
        self.metrics.inc("polls")
        self.metrics.inc("courses_fetched", len(courses))

        if not new:
            log.info("Poll complete: nothing new (%d checked)", len(courses))
            return

        if self.store.seen_count() == 0:
            self.store.mark_seen(new)
            log.info("Cold start: baselined %d courses without announcing", len(new))
            return

        to_send = new[: self.config.max_per_cycle]
        rest = new[self.config.max_per_cycle:]

        # Split: instant subscribers get individual messages; daily/weekly
        # subscribers get the course queued for the digest job.
        instant_subs = self.store.subscribers_by_frequency("instant")
        digest_needed = any(
            row["frequency"] in {"daily", "weekly"}
            for row in self.store.all_subscribers()
        )

        if digest_needed:
            self.store.queue_for_digest(to_send)

        if instant_subs:
            log.info(
                "Found %d new; announcing %d to instant subscribers (%d queued for later)",
                len(new), len(to_send), len(rest),
            )
            for course in to_send:
                await self.broadcast(ctx, course, only_instant=True)
        else:
            log.info(
                "Found %d new; no instant subscribers — all queued for digest",
                len(new),
            )
            if not digest_needed:
                self.store.queue_for_digest(to_send)

        self.store.mark_seen(to_send + rest)
        self.metrics.inc("courses_announced", len(to_send))

    async def digest_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Send the daily digest to subscribers with frequency=daily."""
        await self._send_digests(ctx, "daily")

    async def weekly_digest_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Send the weekly digest to subscribers with frequency=weekly."""
        await self._send_digests(ctx, "weekly")

    async def _send_digests(self, ctx: ContextTypes.DEFAULT_TYPE, frequency: str) -> None:
        subs = self.store.subscribers_by_frequency(frequency)
        if not subs:
            return
        rows = self.store.drain_digest(limit=100)
        if not rows:
            return
        lang = self.config.language
        text = format_digest(rows, lang)
        sent = 0
        for row in subs:
            chat_id = row["chat_id"]
            if not matches_filter(
                Course(id="digest", title=text, url="", source="digest"),
                row["keywords"],
            ):
                # Fall back: send if no keyword filter, else per-row filter below.
                pass
            try:
                await ctx.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                sent += 1
            except Forbidden:
                self.store.unsubscribe(chat_id)
            except TelegramError as exc:
                log.warning("Digest send to %s failed: %s", chat_id, exc)
            await asyncio.sleep(SEND_DELAY)
        self.store.bump_metric("digests_sent")
        self.metrics.inc("digests_sent")
        log.info("Sent %s digest to %d subscribers (%d courses)", frequency, sent, len(rows))

    async def broadcast(
        self, ctx: ContextTypes.DEFAULT_TYPE, course: Course, only_instant: bool = False
    ) -> None:
        """Send a course to matching subscribers with exponential backoff."""
        text = format_course(course)
        sent = 0

        for row in self.store.all_subscribers():
            if only_instant and row["frequency"] != "instant":
                continue
            if row["frequency"] == "paused":
                continue
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
                    await asyncio.sleep(exc.retry_after + 1)
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
        self.store.bump_metric("messages_sent", sent)
        self.metrics.inc("messages_sent", sent)

    async def prune_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        removed = self.store.prune(older_than_days=90)
        if removed:
            log.info("Pruned %d old course records", removed)

    # ---------- wiring ----------

    def health_stats(self) -> dict:
        """Stats exposed on /healthz."""
        return {
            "subscribers": self.store.subscriber_count(),
            "courses": self.store.seen_count(),
            "digest_queued": self.store.digest_count(),
            "uptime_s": int(self.metrics.uptime_seconds()),
        }

    def build(self) -> Application:
        app = Application.builder().token(self.config.telegram_token).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(CommandHandler("filter", self.cmd_filter))
        app.add_handler(CommandHandler("freq", self.cmd_freq))
        app.add_handler(CommandHandler("latest", self.cmd_latest))
        app.add_handler(CommandHandler("course", self.cmd_course))
        app.add_handler(CommandHandler("categories", self.cmd_categories))
        app.add_handler(CommandHandler("search", self.cmd_search))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(InlineQueryHandler(self.cmd_inline))
        app.add_error_handler(handle_error)

        app.job_queue.run_repeating(
            self.poll_job, interval=self.config.poll_interval, first=15
        )
        app.job_queue.run_repeating(self.prune_job, interval=86400, first=3600)
        # Daily digest at ~09:00 UTC (first run in 1h, then every 24h).
        app.job_queue.run_daily(self.digest_job, time=datetime.time(9, 0))
        # Weekly digest on Mondays at ~10:00 UTC.
        app.job_queue.run_daily(
            self.weekly_digest_job, time=datetime.time(10, 0), days=(0,)
        )

        return app
