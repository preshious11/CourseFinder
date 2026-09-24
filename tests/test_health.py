import json
import urllib.request

from bot.health import HealthServer


class TestHealthServer:
    def test_healthz_returns_ok(self):
        server = HealthServer(port=0)  # pick a free port
        # port=0 doesn't work with ThreadingHTTPServer bind; use a fixed high port
        server = HealthServer(port=18765)
        server.start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:18765/healthz", timeout=3) as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                assert data["status"] == "ok"
        finally:
            server.stop()

    def test_stats_fn_is_included(self):
        server = HealthServer(port=18766, stats_fn=lambda: {"subscribers": 3})
        server.start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:18766/healthz", timeout=3) as resp:
                data = json.loads(resp.read())
                assert data["subscribers"] == 3
        finally:
            server.stop()

    def test_404_on_unknown_path(self):
        server = HealthServer(port=18767)
        server.start()
        try:
            try:
                urllib.request.urlopen("http://127.0.0.1:18767/nope", timeout=3)
                raise AssertionError("expected 404")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            server.stop()

    def test_stop_is_safe_when_not_started(self):
        server = HealthServer(port=18768)
        server.stop()  # should not raise
