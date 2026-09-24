"""Graceful shutdown handling: flush in-flight broadcasts and close cleanly.

Installs signal handlers so SIGINT/SIGTERM trigger an orderly shutdown
instead of killing the process mid-broadcast.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

log = logging.getLogger(__name__)


class ShutdownManager:
    """Coordinates graceful shutdown: register callbacks, then trigger them."""

    def __init__(self) -> None:
        self._callbacks: list[Any] = []
        self._event: asyncio.Event | None = None
        self._shutting_down = False

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    def on_shutdown(self, callback: Any) -> None:
        """Register a zero-arg callable (sync or async) to run on shutdown."""
        self._callbacks.append(callback)

    def _ensure_event(self) -> asyncio.Event:
        if self._event is None:
            self._event = asyncio.Event()
            if self._shutting_down:
                self._event.set()
        return self._event

    async def wait(self) -> None:
        """Block until shutdown is triggered (returns immediately if already)."""
        await self._ensure_event().wait()

    def trigger(self) -> None:
        """Signal shutdown and run all registered callbacks."""
        if self._shutting_down:
            return
        self._shutting_down = True
        log.info("Graceful shutdown initiated")
        if self._event is None:
            self._event = asyncio.Event()
        self._event.set()
        for cb in self._callbacks:
            try:
                result = cb()
                if asyncio.iscoroutine(result):
                    asyncio.get_event_loop().create_task(result)
            except Exception:
                log.exception("Shutdown callback failed")

    def install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers that call trigger()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        for sig in (signal.SIGINT, signal.SIGTERM):
            if loop is not None:
                try:
                    loop.add_signal_handler(sig, self.trigger)
                    continue
                except (NotImplementedError, RuntimeError):
                    pass
            try:
                signal.signal(sig, lambda *_: self.trigger())
            except (ValueError, OSError):
                pass
