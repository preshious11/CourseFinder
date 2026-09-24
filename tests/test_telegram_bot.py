import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.error import Conflict, TelegramError

from bot.config import Config
from bot.providers import Course
from bot.telegram_bot import (
    CourseBot,
    format_course,
    format_digest,
    format_search_results,
    handle_error,
)


def _make_config(**overrides) -> Config:
    defaults = dict(
        telegram_token="test",
        udemy_client_id=None, udemy_client_secret=None,
        coupon_feeds=[], tutorialbar=True,
        tutorialbar_categories=[],
        search_coursera=True, search_edx=True,
        search_mit=True, search_freecodecamp=True,
        search_catalogue_pages=5, search_timeout=25,
        db_path=":memory:", poll_interval=3600,
        max_per_cycle=10, language="en", admin_chat_id=None,
        webhook_url=None, webhook_port=8443, health_port=None,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _make_bot(tmp_path=None, **overrides) -> CourseBot:
    if tmp_path is not None:
        db = os.path.join(str(tmp_path), "test.db")
    else:
        db = ":memory:"
    cfg = _make_config(db_path=db, **overrides)
    return CourseBot(cfg)


class TestFormatCourse:
    def test_basic(self):
        c = Course(id="1", title="Python 101", url="https://x.com/c", source="test")
        out = format_course(c)
        assert "Python 101" in out
        assert "Enrol free" in out

    def test_escapes_html(self):
        c = Course(id="1", title="<script>alert(1)</script>", url="https://x.com/c", source="test")
        out = format_course(c)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_includes_coupon(self):
        c = Course(id="1", title="T", url="https://x.com/c", source="test", coupon_code="ABC123")
        out = format_course(c)
        assert "ABC123" in out

    def test_includes_price_and_duration(self):
        c = Course(
            id="1", title="T", url="https://x.com/c", source="test",
            price="Free", duration="2h", level="Beginner",
        )
        out = format_course(c)
        assert "Free" in out
        assert "2h" in out
        assert "Beginner" in out


class TestFormatSearchResults:
    def test_labels_platform(self):
        c = Course(id="1", title="Python", url="https://x.com/c", source="coursera")
        out = format_search_results("python", [c])
        assert "Python" in out
        assert "Coursera" in out

    def test_multiple(self):
        courses = [
            Course(id="1", title="A", url="https://x.com/1", source="coursera"),
            Course(id="2", title="B", url="https://x.com/2", source="edx"),
        ]
        out = format_search_results("q", courses)
        assert "A" in out
        assert "B" in out
        assert "Coursera" in out
        assert "edX" in out


class TestFormatDigest:
    def test_header_and_links(self):
        rows = [
            {"title": "Course One", "url": "https://x.com/1", "source": "test", "course_id": "1"},
            {"title": "Course Two", "url": "https://x.com/2", "source": "test", "course_id": "2"},
        ]
        out = format_digest(rows, "en")
        assert "Daily digest" in out
        assert "Course One" in out
        assert "Course Two" in out
        assert "https://x.com/1" in out

    def test_spanish_header(self):
        rows = [{"title": "X", "url": "https://x.com/x", "source": "t", "course_id": "x"}]
        out = format_digest(rows, "es")
        assert "Resumen diario" in out

    def test_shows_age_of_queued_items(self):
        import time
        rows = [{
            "title": "Old Deal",
            "url": "https://x.com/o",
            "source": "t",
            "course_id": "o",
            "queued_at": int(time.time()) - 5 * 3600,  # 5 hours ago
        }]
        out = format_digest(rows, "en")
        assert "5h ago" in out

    def test_missing_queued_at_no_crash(self):
        rows = [{"title": "T", "url": "https://x.com/t", "source": "t", "course_id": "t"}]
        out = format_digest(rows, "en")
        assert "T" in out


class TestDigestMaxAge:
    def test_send_digests_drops_stale(self, tmp_path):
        import time
        bot = _make_bot(tmp_path)
        bot.store.subscribe(1)
        bot.store.set_frequency(1, "daily")
        c = Course(id="udemy:stale", title="Stale", url="https://x.com/s", source="test")
        bot.store.queue_for_digest([c])
        with bot.store._conn() as conn:
            conn.execute(
                "UPDATE digest_queue SET queued_at = ?",
                (int(time.time()) - 3 * 86400,),  # 3 days old > 48h max
            )
        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()
        asyncio.run(bot._send_digests(ctx, "daily"))
        ctx.bot.send_message.assert_not_awaited()
        assert bot.store.digest_count() == 0

    def test_send_digests_fresh_item_goes_out(self, tmp_path):
        bot = _make_bot(tmp_path)
        bot.store.subscribe(1)
        bot.store.set_frequency(1, "daily")
        c = Course(id="udemy:fresh", title="Fresh", url="https://x.com/f", source="test")
        bot.store.queue_for_digest([c])
        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()
        asyncio.run(bot._send_digests(ctx, "daily"))
        ctx.bot.send_message.assert_awaited_once()
        assert bot.store.digest_count() == 0


class TestRateLimitIntegration:
    def test_bot_has_rate_limiter(self):
        bot = _make_bot()
        assert bot.rate_limiter is not None
        assert bot.rate_limiter.max_calls == 10

    def test_bot_has_search_cache(self):
        bot = _make_bot()
        assert bot.search_cache is not None

    def test_bot_has_metrics(self):
        bot = _make_bot()
        assert bot.metrics is not None
        bot.metrics.inc("x")
        assert bot.metrics.get("x") == 1


class TestSearchCacheIntegration:
    def test_cache_hit_on_repeated_search_key(self):
        bot = _make_bot()
        bot.search_cache.set("python|*", ["a", "b"])
        assert bot.search_cache.get("python|*") == ["a", "b"]


class TestFrequencyIntegration:
    def test_set_and_get_frequency(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_config(db_path=os.path.join(td, "t.db"))
            bot = CourseBot(cfg)
            bot.store.subscribe(1)
            bot.store.set_frequency(1, "daily")
            assert bot.store.get_subscriber(1)["frequency"] == "daily"

    def test_digest_queue_integration(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_config(db_path=os.path.join(td, "t.db"))
            bot = CourseBot(cfg)
            c = Course(id="udemy:x", title="X", url="https://x.com/x", source="test")
            bot.store.queue_for_digest([c])
            assert bot.store.digest_count() == 1
            rows = bot.store.drain_digest()
            assert len(rows) == 1
            assert bot.store.digest_count() == 0


class TestHealthStats:
    def test_health_stats_shape(self, tmp_path):
        bot = _make_bot(tmp_path)
        stats = bot.health_stats()
        assert "subscribers" in stats
        assert "courses" in stats
        assert "digest_queued" in stats
        assert "uptime_s" in stats


def _welcome_update(chat_id=1, username="alice", first_name="Alice"):
    update = MagicMock(spec=Update)
    update.effective_chat.id = chat_id
    user = MagicMock()
    user.username = username
    user.first_name = first_name
    update.effective_user = user
    update.message = AsyncMock()
    return update


def _welcome_ctx(bot_username="TestBot"):
    ctx = MagicMock()
    ctx.bot.get_me = AsyncMock(return_value=MagicMock(username=bot_username))
    return ctx


class TestWelcomeUsername:
    def test_shows_live_subscriber_username(self, tmp_path):
        bot = _make_bot(tmp_path)
        update = _welcome_update(username="alice")
        asyncio.run(bot.cmd_start(update, _welcome_ctx()))
        text = update.message.reply_text.call_args[0][0]
        assert "@alice" in text

    def test_different_users_get_their_own_name(self, tmp_path):
        bot = _make_bot(tmp_path)
        u1 = _welcome_update(chat_id=1, username="alice")
        asyncio.run(bot.cmd_start(u1, _welcome_ctx()))
        t1 = u1.message.reply_text.call_args[0][0]
        u2 = _welcome_update(chat_id=2, username="bob")
        asyncio.run(bot.cmd_start(u2, _welcome_ctx()))
        t2 = u2.message.reply_text.call_args[0][0]
        assert "@alice" in t1 and "@bob" not in t1
        assert "@bob" in t2 and "@alice" not in t2

    def test_no_username_falls_back_to_first_name(self, tmp_path):
        bot = _make_bot(tmp_path)
        update = _welcome_update(username=None, first_name="Alice")
        asyncio.run(bot.cmd_start(update, _welcome_ctx()))
        text = update.message.reply_text.call_args[0][0]
        assert "Alice" in text
        assert "@" not in text.split("\n")[0]  # no dangling @

    def test_no_username_no_first_name_falls_back(self, tmp_path):
        bot = _make_bot(tmp_path)
        update = _welcome_update(username=None, first_name=None)
        asyncio.run(bot.cmd_start(update, _welcome_ctx()))
        text = update.message.reply_text.call_args[0][0]
        assert "there" in text

    def test_already_subscribed_also_uses_live_name(self, tmp_path):
        bot = _make_bot(tmp_path)
        first = _welcome_update(chat_id=5, username="old_name")
        asyncio.run(bot.cmd_start(first, _welcome_ctx()))
        second = _welcome_update(chat_id=5, username="new_name")
        asyncio.run(bot.cmd_start(second, _welcome_ctx()))
        text = second.message.reply_text.call_args[0][0]
        assert "already subscribed" in text
        assert "@new_name" in text

    def test_no_hardcoded_title_or_name(self, tmp_path):
        bot = _make_bot(tmp_path)
        update = _welcome_update(username="zzz_unique")
        asyncio.run(bot.cmd_start(update, _welcome_ctx()))
        text = update.message.reply_text.call_args[0][0]
        assert "Free Course Bot" not in text
        assert "preshious" not in text.lower()
        assert "@zzz_unique" in text

    def test_inline_example_uses_bot_username_not_hardcoded(self, tmp_path):
        bot = _make_bot(tmp_path)
        update = _welcome_update(username="alice")
        asyncio.run(bot.cmd_start(update, _welcome_ctx(bot_username="RealBot")))
        text = update.message.reply_text.call_args[0][0]
        assert "@RealBot" in text
        assert "this_bot" not in text


class TestBuild:
    def test_registers_all_handlers(self, tmp_path):
        bot = _make_bot(tmp_path)
        app = bot.build()
        handlers = app.handlers.get(0, [])  # group 0
        assert len(handlers) >= 11

    def test_jobs_scheduled(self, tmp_path):
        bot = _make_bot(tmp_path)
        app = bot.build()
        jobs = app.job_queue.jobs()
        # poll, prune, daily digest, weekly digest
        assert len(jobs) >= 4

    def test_error_handler_is_async(self):
        import inspect
        assert inspect.iscoroutinefunction(handle_error)


class TestHandleError:
    def _ctx(self, error):
        ctx = MagicMock()
        ctx.error = error
        return ctx

    def _update_with_message(self):
        update = MagicMock(spec=Update)
        msg = AsyncMock()
        update.effective_message = msg
        return update, msg

    def test_conflict_does_not_reply(self, caplog):
        update, msg = self._update_with_message()
        ctx = self._ctx(Conflict("terminated by other getUpdates request"))
        asyncio.run(handle_error(update, ctx))
        msg.reply_text.assert_not_awaited()

    def test_generic_error_replies_awaited(self):
        update, msg = self._update_with_message()
        ctx = self._ctx(TelegramError("boom"))
        asyncio.run(handle_error(update, ctx))
        msg.reply_text.assert_awaited_once()

    def test_no_update_no_crash(self):
        ctx = self._ctx(TelegramError("boom"))
        asyncio.run(handle_error(None, ctx))  # must not raise

    def test_reply_failure_is_swallowed(self):
        update = MagicMock(spec=Update)
        msg = AsyncMock()
        msg.reply_text.side_effect = TelegramError("gone")
        update.effective_message = msg
        ctx = self._ctx(TelegramError("boom"))
        asyncio.run(handle_error(update, ctx))  # must not raise
