import pytest

from bot.store import Store


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    return Store(db_path)


class TestStore:
    def test_subscribe_returns_true_for_new(self, store):
        assert store.subscribe(123) is True

    def test_subscribe_returns_false_for_duplicate(self, store):
        assert store.subscribe(123) is True
        assert store.subscribe(123) is False

    def test_unsubscribe(self, store):
        store.subscribe(123)
        assert store.unsubscribe(123) is True
        assert store.unsubscribe(123) is False

    def test_get_subscriber(self, store):
        store.subscribe(123)
        row = store.get_subscriber(123)
        assert row is not None
        assert row["chat_id"] == 123

    def test_set_keywords(self, store):
        store.subscribe(123)
        store.set_keywords(123, "python, design")
        row = store.get_subscriber(123)
        assert row["keywords"] == "python, design"

    def test_filter_new(self, store):
        from bot.providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        c2 = Course(id="udemy:course2", title="Test2", url="https://example.com/course2", source="test")
        fresh = store.filter_new([c1, c2])
        assert len(fresh) == 2
        store.mark_seen([c1])
        fresh = store.filter_new([c1, c2])
        assert len(fresh) == 1
        assert fresh[0].id == "udemy:course2"

    def test_filter_new_dedupes_within_batch(self, store):
        from bot.providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        fresh = store.filter_new([c1, c1])
        assert len(fresh) == 1

    def test_mark_seen(self, store):
        from bot.providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        store.mark_seen([c1])
        assert store.seen_count() == 1

    def test_recent(self, store):
        from bot.providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        store.mark_seen([c1])
        rows = store.recent(limit=5)
        assert len(rows) == 1
        assert rows[0]["title"] == "Test"

    def test_seen_count(self, store):
        from bot.providers import Course
        store.mark_seen([Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")])
        assert store.seen_count() == 1

    def test_subscriber_count(self, store):
        store.subscribe(123)
        assert store.subscriber_count() == 1

    def test_prune(self, store):
        import sqlite3
        import time

        from bot.providers import Course
        store.subscribe(123)
        store.mark_seen([Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")])
        conn = sqlite3.connect(store.path)
        conn.execute("UPDATE seen SET first_seen = ? WHERE course_id = ?", (int(time.time()) - 100000, "udemy:course1"))
        conn.commit()
        conn.close()
        removed = store.prune(older_than_days=1)
        assert removed >= 1

    def test_all_subscribers(self, store):
        store.subscribe(123)
        store.subscribe(456)
        rows = store.all_subscribers()
        assert len(rows) == 2


class TestFrequency:
    def test_default_frequency_is_instant(self, store):
        store.subscribe(123)
        row = store.get_subscriber(123)
        assert row["frequency"] == "instant"

    def test_set_frequency(self, store):
        store.subscribe(123)
        store.set_frequency(123, "daily")
        assert store.get_subscriber(123)["frequency"] == "daily"

    def test_set_frequency_invalid_raises(self, store):
        store.subscribe(123)
        with pytest.raises(ValueError):
            store.set_frequency(123, "hourly")

    def test_subscribers_by_frequency(self, store):
        store.subscribe(1)
        store.subscribe(2)
        store.set_frequency(2, "paused")
        instant = store.subscribers_by_frequency("instant")
        paused = store.subscribers_by_frequency("paused")
        assert len(instant) == 1
        assert instant[0]["chat_id"] == 1
        assert len(paused) == 1
        assert paused[0]["chat_id"] == 2


class TestDigestQueue:
    def _course(self, cid="udemy:c1", title="Test"):
        from bot.providers import Course
        return Course(id=cid, title=title, url="https://example.com/" + cid, source="test")

    def test_queue_and_drain(self, store):
        store.queue_for_digest([self._course(), self._course("udemy:c2", "Two")])
        assert store.digest_count() == 2
        rows = store.drain_digest()
        assert len(rows) == 2
        assert store.digest_count() == 0

    def test_queue_dedupes(self, store):
        store.queue_for_digest([self._course()])
        store.queue_for_digest([self._course()])
        assert store.digest_count() == 1

    def test_drain_respects_limit(self, store):
        for i in range(10):
            store.queue_for_digest([self._course(f"udemy:c{i}")])
        rows = store.drain_digest(limit=3)
        assert len(rows) == 3
        assert store.digest_count() == 7

    def test_drain_empty(self, store):
        assert store.drain_digest() == []


class TestMetrics:
    def test_bump_and_get(self, store):
        store.bump_metric("polls")
        store.bump_metric("polls")
        assert store.get_metric("polls") == 2

    def test_get_missing_metric(self, store):
        assert store.get_metric("nonexistent") == 0

    def test_bump_custom_amount(self, store):
        store.bump_metric("courses", 5)
        assert store.get_metric("courses") == 5

    def test_all_metrics(self, store):
        store.bump_metric("a")
        store.bump_metric("b", 3)
        metrics = store.all_metrics()
        assert metrics["a"] == 1
        assert metrics["b"] == 3


class TestMigrations:
    def test_new_db_stamped_to_latest(self, tmp_path):
        import sqlite3

        from bot.store import LATEST_SCHEMA_VERSION
        path = str(tmp_path / "fresh.db")
        Store(path)
        conn = sqlite3.connect(path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == LATEST_SCHEMA_VERSION

    def test_old_db_gets_migrated(self, tmp_path):
        import sqlite3

        from bot.store import LATEST_SCHEMA_VERSION, Store
        path = str(tmp_path / "old.db")
        # Simulate a v1 database (no frequency column, no digest_queue).
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE subscribers (
                chat_id INTEGER PRIMARY KEY,
                keywords TEXT NOT NULL DEFAULT '',
                added_at INTEGER NOT NULL
            );
            CREATE TABLE seen (
                course_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                first_seen INTEGER NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO subscribers (chat_id, added_at) VALUES (42, 0)")
        conn.commit()
        conn.close()

        Store(path)

        conn = sqlite3.connect(path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(subscribers)").fetchall()]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        # Subscriber data survived.
        row = conn.execute("SELECT chat_id, frequency FROM subscribers WHERE chat_id=42").fetchone()
        conn.close()

        assert version == LATEST_SCHEMA_VERSION
        assert "frequency" in cols
        assert "digest_queue" in tables
        assert row is not None
        assert row[1] == "instant"
