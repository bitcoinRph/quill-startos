"""Per-meeting fan-out to WebSocket and SSE listeners.

Events are the same dicts the WebSocket sends (``{"type": "segment", ...}``).
Publishing happens on the event loop only; a listener that stops draining is
dropped instead of letting memory grow without bound.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable

Event = dict[str, Any]
QUEUE_LIMIT = 2000


class Subscription:
    def __init__(self, bus: "EventBus", meeting_id: str) -> None:
        self._bus = bus
        self.meeting_id = meeting_id
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(QUEUE_LIMIT)

    async def get(self) -> Event | None:
        """Next event, or None when the bus dropped this listener."""
        return await self.queue.get()

    def close(self) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def run_on_loop(self, fn: Callable[..., None], *args: Any) -> None:
        """Run ``fn`` on the app's event loop, from any thread (sync endpoints run in a pool)."""
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if self._loop is None or on_loop:
            fn(*args)
        else:
            self._loop.call_soon_threadsafe(fn, *args)

    def subscribe(self, meeting_id: str) -> Subscription:
        sub = Subscription(self, meeting_id)
        self._subs[meeting_id].add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.meeting_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.meeting_id]

    def publish(self, meeting_id: str, event: Event) -> None:
        self.run_on_loop(self._publish, meeting_id, event)

    def _publish(self, meeting_id: str, event: Event) -> None:
        for sub in list(self._subs.get(meeting_id, ())):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                self.unsubscribe(sub)
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def listeners(self, meeting_id: str) -> int:
        return len(self._subs.get(meeting_id, ()))
