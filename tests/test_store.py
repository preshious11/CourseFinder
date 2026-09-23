import pytest

from store import Store


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
        from providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        c2 = Course(id="udemy:course2", title="Test2", url="https://example.com/course2", source="test")
        fresh = store.filter_new([c1, c2])
        assert len(fresh) == 2
        store.mark_seen([c1])
        fresh = store.filter_new([c1, c2])
        assert len(fresh) == 1
        assert fresh[0].id == "udemy:course2"

    def test_filter_new_dedupes_within_batch(self, store):
        from providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        fresh = store.filter_new([c1, c1])
        assert len(fresh) == 1

    def test_mark_seen(self, store):
        from providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        store.mark_seen([c1])
        assert store.seen_count() == 1

    def test_recent(self, store):
        from providers import Course
        c1 = Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")
        store.mark_seen([c1])
        rows = store.recent(limit=5)
        assert len(rows) == 1
        assert rows[0]["title"] == "Test"

    def test_seen_count(self, store):
        from providers import Course
        store.mark_seen([Course(id="udemy:course1", title="Test", url="https://example.com/course1", source="test")])
        assert store.seen_count() == 1

    def test_subscriber_count(self, store):
        store.subscribe(123)
        assert store.subscriber_count() == 1

    def test_prune(self, store):
        import sqlite3
        import time

        from providers import Course
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
