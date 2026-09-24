import time

from bot.rate_limit import RateLimiter


class TestRateLimiter:
    def test_allows_under_limit(self):
        rl = RateLimiter(max_calls=3, window=60)
        assert rl.allow("u1") is True
        assert rl.allow("u1") is True
        assert rl.allow("u1") is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_calls=2, window=60)
        rl.allow("u1")
        rl.allow("u1")
        assert rl.allow("u1") is False

    def test_keys_are_independent(self):
        rl = RateLimiter(max_calls=1, window=60)
        assert rl.allow("a") is True
        assert rl.allow("b") is True
        assert rl.allow("a") is False

    def test_window_expiry(self):
        rl = RateLimiter(max_calls=1, window=0.01)
        assert rl.allow("u") is True
        assert rl.allow("u") is False
        time.sleep(0.02)
        assert rl.allow("u") is True

    def test_remaining(self):
        rl = RateLimiter(max_calls=3, window=60)
        rl.allow("u")
        assert rl.remaining("u") == 2
        rl.allow("u")
        rl.allow("u")
        assert rl.remaining("u") == 0

    def test_retry_after_zero_when_allowed(self):
        rl = RateLimiter(max_calls=5, window=60)
        assert rl.retry_after("u") == 0.0

    def test_retry_after_positive_when_blocked(self):
        rl = RateLimiter(max_calls=1, window=60)
        rl.allow("u")
        assert rl.retry_after("u") > 0

    def test_reset_single_key(self):
        rl = RateLimiter(max_calls=1, window=60)
        rl.allow("u")
        rl.reset("u")
        assert rl.allow("u") is True

    def test_reset_all(self):
        rl = RateLimiter(max_calls=1, window=60)
        rl.allow("a")
        rl.reset()
        assert rl.allow("a") is True
