import time

from bot.search_cache import SearchCache


class TestSearchCache:
    def test_set_and_get(self):
        c = SearchCache(ttl=60)
        c.set("q1", [1, 2, 3])
        assert c.get("q1") == [1, 2, 3]

    def test_get_missing(self):
        c = SearchCache()
        assert c.get("nope") is None

    def test_ttl_expiry(self):
        c = SearchCache(ttl=0.01)
        c.set("k", "v")
        assert c.get("k") == "v"
        time.sleep(0.02)
        assert c.get("k") is None

    def test_overwrite(self):
        c = SearchCache()
        c.set("k", "old")
        c.set("k", "new")
        assert c.get("k") == "new"

    def test_eviction_at_max_entries(self):
        c = SearchCache(ttl=60, max_entries=3)
        for i in range(5):
            c.set(f"k{i}", i)
        assert len(c) <= 3

    def test_clear(self):
        c = SearchCache()
        c.set("a", 1)
        c.clear()
        assert len(c) == 0
        assert c.get("a") is None

    def test_len(self):
        c = SearchCache()
        assert len(c) == 0
        c.set("a", 1)
        assert len(c) == 1
