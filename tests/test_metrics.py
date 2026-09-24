from bot.metrics import Metrics


class TestMetrics:
    def test_inc_and_get(self):
        m = Metrics()
        m.inc("polls")
        m.inc("polls")
        assert m.get("polls") == 2

    def test_get_missing(self):
        m = Metrics()
        assert m.get("nope") == 0

    def test_inc_amount(self):
        m = Metrics()
        m.inc("courses", 5)
        assert m.get("courses") == 5

    def test_snapshot(self):
        m = Metrics()
        m.inc("a")
        m.inc("b", 2)
        snap = m.snapshot()
        assert snap["a"] == 1
        assert snap["b"] == 2

    def test_uptime(self):
        m = Metrics()
        assert m.uptime_seconds() >= 0

    def test_reset(self):
        m = Metrics()
        m.inc("x")
        m.reset()
        assert m.get("x") == 0
        assert m.snapshot() == {}
