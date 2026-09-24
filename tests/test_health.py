import json
import urllib.request

from bot.health import HealthServer


class TestHealthServer:
    def test_healthz_returns_ok(self):
        server = HealthServer(port=0)  # ephemeral — no port-clash on re-runs
        server.start()
        try:
            assert server.port > 0
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/healthz", timeout=3
            ) as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                assert data["status"] == "ok"
        finally:
            server.stop()

    def test_stats_fn_is_included(self):
        server = HealthServer(port=0, stats_fn=lambda: {"subscribers": 3})
        server.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/healthz", timeout=3
            ) as resp:
                data = json.loads(resp.read())
                assert data["subscribers"] == 3
        finally:
            server.stop()

    def test_404_on_unknown_path(self):
        server = HealthServer(port=0)
        server.start()
        try:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.port}/nope", timeout=3
                )
                raise AssertionError("expected 404")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            server.stop()

    def test_stop_is_safe_when_not_started(self):
        server = HealthServer(port=0)
        server.stop()  # should not raise
