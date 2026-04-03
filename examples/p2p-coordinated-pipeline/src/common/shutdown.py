"""
Graceful shutdown: HandlerCounter tracks in-flight stream handlers.

Workers wrap every stream handler in ``async with counter:`` so that
on shutdown the main loop can call ``await counter.wait_idle()`` to give
handlers a grace period before the nursery tears them down.

How it works in the worker's cancellation path:
  1. KeyboardInterrupt / SIGTERM raises trio.Cancelled in the
     ``while True: await trio.sleep(...)`` announcement loop.
  2. The ``except trio.Cancelled:`` block catches it before context managers
     unwind — background services (and therefore handlers) are still alive.
  3. ``wait_idle()`` shields itself from cancellation and polls until all
     handlers decrement the counter or the drain_timeout elapses.
  4. ``raise`` propagates Cancelled, unwinding background services normally.
"""
import logging

import trio

log = logging.getLogger(__name__)


class HandlerCounter:
    """
    Reference counter for in-flight stream handler coroutines.

    Usage::

        counter = HandlerCounter()

        async def negotiate_handler(stream):
            async with counter:
                await _handle_negotiate(stream, ...)

        # On shutdown:
        try:
            while True:
                await trio.sleep(cfg.reannounce_interval)
                ...
        except trio.Cancelled:
            log.info("Draining handlers...")
            await counter.wait_idle(drain_timeout=5.0)
            raise
    """

    def __init__(self) -> None:
        self._count: int = 0

    @property
    def count(self) -> int:
        return self._count

    async def __aenter__(self) -> "HandlerCounter":
        self._count += 1
        return self

    async def __aexit__(self, *_) -> None:
        self._count -= 1

    async def wait_idle(self, drain_timeout: float = 10.0) -> None:
        """
        Poll until all active handlers finish or ``drain_timeout`` elapses.

        Always runs inside ``trio.CancelScope(shield=True)`` so it can do
        async work even when the surrounding scope has been cancelled.
        """
        with trio.CancelScope(shield=True):
            with trio.move_on_after(drain_timeout):
                while self._count > 0:
                    await trio.sleep(0.1)
        if self._count > 0:
            log.warning(
                f"[HandlerCounter] {self._count} handler(s) still active after "
                f"{drain_timeout:.0f}s drain window — forcing shutdown"
            )
