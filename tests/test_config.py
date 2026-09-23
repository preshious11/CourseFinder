import os

import pytest


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for key in list(os.environ.keys()):
        if key.startswith(("TELEGRAM", "UDEMY", "COUPON", "TUTORIAL", "SEARCH", "DB_", "POLL_", "MAX_", "LANGUAGE", "ADMIN", "TUTORIALBAR")):
            monkeypatch.delenv(key, raising=False)


def test_from_env_raises_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from config import Config
    with pytest.raises(SystemExit):
        Config.from_env()


def test_from_env_minimal(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from config import Config
    cfg = Config.from_env()
    assert cfg.telegram_token == "test-token"
    assert cfg.coupon_feeds == []
    assert cfg.tutorialbar is True
    assert cfg.search_coursera is True
    assert cfg.search_edx is True
    assert cfg.has_udemy_api is False
    assert cfg.admin_chat_id is None


def test_from_env_with_udemy_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("UDEMY_CLIENT_ID", "my-id")
    monkeypatch.setenv("UDEMY_CLIENT_SECRET", "my-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from config import Config
    cfg = Config.from_env()
    assert cfg.udemy_client_id == "my-id"
    assert cfg.udemy_client_secret == "my-secret"
    assert cfg.has_udemy_api is True


def test_from_env_with_admin_chat_id(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_CHAT_ID", "12345")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from config import Config
    cfg = Config.from_env()
    assert cfg.admin_chat_id == 12345


def test_from_env_with_negative_admin_chat_id(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_CHAT_ID", "-123")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from config import Config
    cfg = Config.from_env()
    assert cfg.admin_chat_id == -123


def test_from_env_with_coupon_feeds(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("COUPON_FEEDS", "https://example.com/feed1,https://example.com/feed2")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from config import Config
    cfg = Config.from_env()
    assert len(cfg.coupon_feeds) == 2
    assert "https://example.com/feed1" in cfg.coupon_feeds


def test_from_env_with_tutorialbar_categories(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TUTORIALBAR_CATEGORIES", "python,design")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from config import Config
    cfg = Config.from_env()
    assert cfg.tutorialbar_categories == ["python", "design"]


def test_from_env_with_custom_poll_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("POLL_INTERVAL", "1800")
    from config import Config
    cfg = Config.from_env()
    assert cfg.poll_interval == 1800


def test_from_env_with_max_per_cycle(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MAX_PER_CYCLE", "20")
    from config import Config
    cfg = Config.from_env()
    assert cfg.max_per_cycle == 20
