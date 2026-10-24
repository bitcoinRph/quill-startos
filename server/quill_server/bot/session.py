"""One bot = one headless Chromium page running `page/bot.js`.

The page does the Jitsi work and reports state; this class owns what must not
depend on the page behaving: the hard time limits, the alone timer, the
network allowlist, and closing the browser on every exit path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlsplit

import httpx

from .settings import BotSettings
from .urls import JitsiTarget

log = logging.getLogger("quill.bot")

STARTING, LOBBY, JOINED, LEAVING, ENDED, FAILED = "starting", "lobby", "joined", "leaving", "ended", "failed"
FINAL_STATES = frozenset({ENDED, FAILED})

START_TIMEOUT_S = 90.0  # config.js, library, XMPP and the first join
LOBBY_TIMEOUT_S = 600.0  # nobody admitted the bot
PAGE_LEAVE_TIMEOUT_S = 25.0
CLOSE_TIMEOUT_S = 10.0
TICK_S = 1.0

CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    # A bot window is never foreground; do not let Chromium slow it down.
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

StateHook = Callable[["BotSession"], Any]


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


class BotSession:
    def __init__(
        self,
        *,
        settings: BotSettings,
        target: JitsiTarget,
        title: str,
        display_name: str,
        password: str | None,
        on_change: StateHook | None = None,
    ) -> None:
        self.settings = settings
        self.target = target
        self.title = title
        self.display_name = display_name
        self._password = password or None
        self._on_change = on_change

        self.meeting_id: str | None = None
        self.state = STARTING
        self.error: str | None = None
        self.reason: str | None = None
        self.others = 0
        self.created_at = time.time()
        self.joined_at: float | None = None
        self.ended_at: float | None = None
        self.console: deque[str] = deque(maxlen=60)

        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lobby_since: float | None = None
        self._alone_since: float | None = None
        self._ingest_open = False
        self._stop_sent = False
        self._ingest_closed_early = False
        self._page_finished = False
        self._page_result: tuple[str, str | None, str | None] = (ENDED, None, None)
        self._dead: str | None = None
        self._leave_reason: str | None = None
        self._wake = asyncio.Event()
        self._done = asyncio.Event()
        self._finalizing = False
        self._task: asyncio.Task | None = None

    # ---- introspection ------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state not in FINAL_STATES

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe state. Never includes the password or the JWT."""
        return {
            "meeting_id": self.meeting_id,
            "state": self.state,
            "error": self.error,
            "reason": self.reason,
            "host": self.target.netloc,
            "room": self.target.room,
            "title": self.title,
            "display_name": self.display_name,
            "participants": self.others,
            "created_at": _iso(self.created_at),
            "joined_at": _iso(self.joined_at),
            "ended_at": _iso(self.ended_at),
        }

    def _set_state(self, state: str, error: str | None = None, reason: str | None = None) -> None:
        if self.state in FINAL_STATES or (state == self.state and error is None):
            return
        self.state = state
        if error is not None:
            self.error = error
        if reason is not None:
            self.reason = reason
        now = time.time()
        if state == JOINED and self.joined_at is None:
            self.joined_at = now
            self._alone_since = now if self.others == 0 else None
        if state == LOBBY and self._lobby_since is None:
            self._lobby_since = now
        if state in FINAL_STATES:
            self.ended_at = now
        log.info("bot %s -> %s%s", self.meeting_id, state, f" ({error})" if error else "")
        if self._on_change is not None:
            try:
                self._on_change(self)
            except Exception:  # a broken hook must not wedge the bot
                log.exception("bot state hook failed")

    # ---- start --------------------------------------------------------------

    async def launch(self, playwright: Any) -> None:
        """Start Chromium. Raises on failure; nothing is left running then."""
        kwargs: dict[str, Any] = {"headless": True, "args": list(CHROMIUM_ARGS)}
        if self.settings.chromium_path:
            kwargs["executable_path"] = self.settings.chromium_path
        self._browser = await playwright.chromium.launch(**kwargs)
        try:
            self._browser.on("disconnected", lambda *_: self._mark_dead("browser exited"))
            if self.settings.insecure_tls:
                log.warning("QUILL_BOT_TEST_INSECURE_TLS is on: TLS errors are ignored (tests only)")
            self._context = await self._browser.new_context(ignore_https_errors=self.settings.insecure_tls)
            await self._context.route("**/*", self._route)
            self._page = await self._context.new_page()
            self._page.on("crash", lambda *_: self._mark_dead("page crashed"))
            self._page.on("console", lambda m: self.console.append(m.text))
            self._page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
            await self._context.expose_function("quillBotEvent", self._on_page_event)
            secrets = {
                "password": self._password,
                "jwt": self.target.jwt,
                "iceAddressMap": dict(self.settings.ice_address_map),
            }
            await self._page.add_init_script(f"window.__quillSecrets = {json.dumps(secrets)};")
        except BaseException:
            await self.close_browser()
            raise

    async def open_page(self, announce: bool) -> None:
        """Navigate to the bot page (needs `meeting_id`) and start supervising."""
        assert self.meeting_id and self._page
        query = urlencode(
            {
                "internal": self.settings.internal_token,
                "host": self.target.netloc,
                "room": self.target.room,
                "meeting": self.meeting_id,
                "name": self.display_name,
                "announce": "1" if announce else "0",
            }
        )
        self._task = asyncio.create_task(self._run(f"{self.settings.local_base}/bot/page?{query}"))

    # ---- network allowlist --------------------------------------------------

    def _request_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme in {"data", "blob", "about"}:
            return True
        try:
            host, port = parts.hostname, parts.port
        except ValueError:
            return False
        if parts.scheme == "https":
            return host == self.target.hostname and (port or 443) == self.target.port
        if parts.scheme == "http":
            return host == "127.0.0.1" and port == self.settings.port
        return False

    async def _route(self, route: Any) -> None:
        """HTTP(S) only goes to the Jitsi host and to Quill on loopback.

        WebSocket and WebRTC are not filtered by Playwright routing; they go to
        where the allowlisted Jitsi server's own config tells the library.
        """
        url = route.request.url
        allowed = self._request_allowed(url)
        if not allowed:
            log.warning("bot %s blocked request to %s", self.meeting_id, urlsplit(url).netloc or url[:40])
        try:
            await (route.continue_() if allowed else route.abort("blockedbyclient"))
        except Exception:
            pass  # the page is already gone

    # ---- page events (called from Playwright's loop) -------------------------

    def _mark_dead(self, why: str) -> None:
        if self.state not in FINAL_STATES and not self._finalizing:
            self._dead = why
            self._wake.set()

    def _on_page_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "state":
            state = event.get("state")
            if state in {LOBBY, JOINED, LEAVING}:
                self._set_state(state, reason=event.get("reason"))
            elif state in FINAL_STATES:
                self._page_finished = True
                self._page_result = (state, event.get("error"), event.get("reason"))
            self._wake.set()
        elif kind == "participants":
            self.others = int(event.get("others", 0))
            if self.state == JOINED:
                if self.others > 0:
                    self._alone_since = None
                elif self._alone_since is None:
                    self._alone_since = time.time()
        elif kind == "ingest":
            ev = event.get("event")
            if ev == "open":
                self._ingest_open = True
            elif ev == "stop_sent":
                self._stop_sent = True
            elif ev == "closed" and not self._finalizing:
                self._ingest_closed_early = True
                self._wake.set()

    # ---- supervision --------------------------------------------------------

    async def _run(self, page_url: str) -> None:
        started = time.monotonic()
        try:
            try:
                await self._page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
            except Exception as exc:
                await self._finalize(FAILED, "error", f"could not open the bot page: {exc}")
                return
            max_s = self.settings.max_minutes * 60
            alone_s = self.settings.alone_minutes * 60
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), TICK_S)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                now = time.monotonic()
                wall = time.time()
                if self._page_finished:
                    state, error, reason = self._page_result
                    await self._finalize(state, reason or "page_finished", error, page_done=True)
                    return
                if self._leave_reason:
                    await self._finalize(ENDED, self._leave_reason)
                    return
                if self._dead:
                    await self._finalize(FAILED, "error", self._dead)
                    return
                if self._ingest_closed_early:
                    await self._finalize(ENDED, "server_closed")
                    return
                if max_s > 0 and now - started >= max_s:
                    await self._finalize(ENDED, "max_duration")
                    return
                if self.state == STARTING and now - started >= START_TIMEOUT_S:
                    await self._finalize(FAILED, "error", "timed out joining the Jitsi conference")
                    return
                if self.state == LOBBY and self._lobby_since and wall - self._lobby_since >= LOBBY_TIMEOUT_S:
                    await self._finalize(FAILED, "error", "nobody admitted the bot from the lobby")
                    return
                if (
                    alone_s > 0
                    and self.state == JOINED
                    and self._alone_since
                    and wall - self._alone_since >= alone_s
                ):
                    await self._finalize(ENDED, "alone")
                    return
        except asyncio.CancelledError:
            await asyncio.shield(self._finalize(ENDED, "shutdown"))
            raise
        except Exception as exc:  # a bug must still release the browser
            log.exception("bot supervisor crashed")
            await self._finalize(FAILED, "error", f"internal error: {exc}")

    def request_leave(self, reason: str) -> None:
        if self._leave_reason is None and self.active:
            self._leave_reason = reason
            self._wake.set()

    async def wait_done(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._done.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ---- exit ---------------------------------------------------------------

    async def _finalize(
        self, final: str, reason: str | None, error: str | None = None, page_done: bool = False
    ) -> None:
        """Every exit path ends here: ask the page to wrap up, then close Chromium."""
        if self._finalizing:
            return
        self._finalizing = True
        try:
            if not page_done:
                if final != FAILED:
                    self._set_state(LEAVING, reason=reason)
                await self._ask_page_to_leave(reason or "leave")
            if self._ingest_open and not self._stop_sent:
                await self._rest_stop()
        finally:
            await self.close_browser()
            if self.state not in FINAL_STATES:
                if final == FAILED:
                    self._log_page_tail()
                    self._set_state(FAILED, error=error or "failed", reason=reason)
                else:
                    self._set_state(final, reason=reason)
            elif error and not self.error:
                self.error = error
            self._done.set()

    def _log_page_tail(self) -> None:
        """Failures are hard to diagnose from the state alone; keep the bot's own log lines."""
        own = [line for line in self.console if line.startswith("[quill-bot]")]
        if own:
            log.warning("bot %s page log (tail): %s", self.meeting_id, " | ".join(own[-8:]))

    async def _ask_page_to_leave(self, reason: str) -> None:
        if self._page is None or self._dead:
            return
        try:
            await asyncio.wait_for(
                self._page.evaluate("(r) => window.QuillBot ? window.QuillBot.leave(r) : null", reason),
                PAGE_LEAVE_TIMEOUT_S,
            )
        except Exception as exc:
            log.warning("bot %s: page did not leave cleanly: %s", self.meeting_id, exc)

    async def _rest_stop(self) -> None:
        """The page could not send `stop`; end the meeting through the API instead."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self.settings.local_base}/api/v1/meetings/{self.meeting_id}/stop",
                    headers={"Authorization": f"Bearer {self.settings.internal_token}"},
                )
        except Exception as exc:
            log.warning("bot %s: fallback stop failed: %s", self.meeting_id, exc)

    async def close_browser(self) -> None:
        """Close context and browser; each step is bounded and failures are ignored."""
        for obj in (self._context, self._browser):
            if obj is None:
                continue
            try:
                await asyncio.wait_for(obj.close(), CLOSE_TIMEOUT_S)
            except Exception:
                pass
        self._page = self._context = self._browser = None

    async def cancel(self) -> None:
        """Hard stop for shutdown: leave gracefully, then make sure the task is gone."""
        self.request_leave("shutdown")
        if self._task and not await self.wait_done(PAGE_LEAVE_TIMEOUT_S + CLOSE_TIMEOUT_S * 2):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            await self.close_browser()
