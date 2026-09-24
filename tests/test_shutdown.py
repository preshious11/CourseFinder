import asyncio

from bot.shutdown import ShutdownManager


class TestShutdownManager:
    def test_trigger_runs_callbacks(self):
        sm = ShutdownManager()
        called = []
        sm.on_shutdown(lambda: called.append(1))
        sm.trigger()
        assert called == [1]
        assert sm.shutting_down is True

    def test_trigger_twice_runs_once(self):
        sm = ShutdownManager()
        called = []
        sm.on_shutdown(lambda: called.append(1))
        sm.trigger()
        sm.trigger()
        assert len(called) == 1

    def test_callback_exception_is_swallowed(self):
        sm = ShutdownManager()
        sm.on_shutdown(lambda: 1 / 0)
        sm.trigger()  # should not raise
        assert sm.shutting_down is True

    def test_wait_returns_after_trigger(self):
        async def scenario():
            sm = ShutdownManager()
            sm.trigger()
            await asyncio.wait_for(sm.wait(), timeout=1)
        asyncio.run(scenario())

    def test_on_shutdown_accepts_async(self):
        async def scenario():
            sm = ShutdownManager()
            flag = asyncio.Event()

            async def cb():
                flag.set()

            sm.on_shutdown(cb)
            sm.trigger()
            # callback is scheduled as a task; give the loop a tick
            await asyncio.sleep(0)
            assert flag.is_set()

        asyncio.run(scenario())
