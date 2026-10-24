"""Jitsi notetaker bot manager (docs/server-spec.md section 8).

Public contract (the server core imports this)::

    BotManager(
        config=None,                       # quill_server.config.Config or None -> os.environ
        live_hub=None,                     # reserved; the bot ingests over the loopback WebSocket
        *,
        create_meeting: Callable[[str, str], Awaitable[str]] | None = None,
        on_state: Callable[[dict], Any] | None = None,
    )
    async join(url: str, title: str | None = None, display_name: str | None = None,
               password: str | None = None) -> str          # meeting_id
    async leave(meeting_id: str) -> dict                    # final state dict
    list() -> list[dict]                                    # active first, then recent finished
    get(meeting_id: str) -> dict | None
    async shutdown() -> None                                # leave every bot, close Chromium

``create_meeting(title, source) -> meeting_id`` makes the meeting row. Without
it the manager calls ``POST http://127.0.0.1:{QUILL_PORT}/api/v1/meetings``
with ``Authorization: Bearer {QUILL_INTERNAL_TOKEN}`` and body
``{"title": ..., "source": "bot"}``, and reads ``id`` from the JSON answer.

``on_state(state_dict)`` is called (sync or async) on every state change so the
core can mirror ``state`` / ``error`` onto the meeting. A bot that fails before
the conference is joined leaves its meeting in ``scheduled``: the core decides
whether to delete or mark it.

State dict keys: ``meeting_id, state, error, reason, host, room, title,
display_name, participants, created_at, joined_at, ended_at``. ``state`` is one
of ``starting, lobby, joined, leaving, ended, failed``. The password and the
JWT are never in it.

Errors are ``BotError`` (``.code``, ``.message``). Codes: ``bot_disabled``,
``invalid_url``, ``host_not_allowed``, ``already_joined``, ``limit_reached``,
``misconfigured``, ``launch_failed``, ``meeting_create_failed``, ``not_found``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from typing import Any, Awaitable, Callable

import httpx

from .session import BotSession
from .settings import BotSettings
from .urls import BotError, JitsiTarget, parse_jitsi_url

log = logging.getLogger("quill.bot")

CreateMeeting = Callable[[str, str], Awaitable[str]]

KEEP_FINISHED = 20
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_NAME = 64


def clean_name(value: str | None, fallback: str) -> str:
    """Display names go into XMPP presence: strip control chars, bound the length."""
    name = _CONTROL.sub("", value or "").strip()[:MAX_NAME].strip()
    return name or fallback


class BotManager:
    def __init__(
        self,
        config: Any = None,
        live_hub: Any = None,
        *,
        create_meeting: CreateMeeting | None = None,
        on_state: Callable[[dict[str, Any]], Any] | None = None,
        settings: BotSettings | None = None,
    ) -> None:
        self.settings = settings or BotSettings.resolve(config)
        self.live_hub = live_hub
        self._create_meeting = create_meeting
        self._on_state = on_state
        self._playwright: Any = None
        self._pw_lock = asyncio.Lock()
        self._sessions: dict[str, BotSession] = {}
        self._starting: set[BotSession] = set()
        self._closed = False

    # ---- public API ---------------------------------------------------------

    async def join(
        self,
        url: str,
        title: str | None = None,
        display_name: str | None = None,
        password: str | None = None,
    ) -> str:
        """Validate, start Chromium, create the meeting, send the bot in. Returns the meeting id.

        Returns once the browser is running, not once Jitsi is joined: poll
        ``list()`` for ``joined`` / ``lobby`` / ``failed``.
        """
        if self._closed:
            raise BotError("bot_disabled", "the bot manager is shut down")
        cfg = self.settings
        target = parse_jitsi_url(url, cfg.allowed_hosts)  # raises before anything is launched
        if not cfg.internal_token:
            raise BotError("misconfigured", "QUILL_INTERNAL_TOKEN is not set")
        self._check_capacity(target)

        session = BotSession(
            settings=cfg,
            target=target,
            title=(title or "").strip()[:200] or f"Jitsi: {target.room}",
            display_name=clean_name(display_name, cfg.bot_name),
            password=password,
            on_change=self._changed,
        )
        self._starting.add(session)  # counts toward the limit while Chromium starts
        try:
            try:
                pw = await self._get_playwright()
                await session.launch(pw)
            except Exception as exc:
                log.exception("bot launch failed")
                raise BotError("launch_failed", f"could not start the browser: {exc}") from exc
            try:
                meeting_id = await self._make_meeting(session.title)
            except BaseException:
                await session.close_browser()
                raise
            session.meeting_id = meeting_id
            self._sessions[meeting_id] = session
            await session.open_page(announce=cfg.announce)
            self._changed(session)
            self._prune()
            return meeting_id
        finally:
            self._starting.discard(session)

    async def leave(self, meeting_id: str) -> dict[str, Any]:
        """Ask a bot to leave and wait (bounded) until its browser is closed."""
        session = self._sessions.get(meeting_id)
        if session is None:
            raise BotError("not_found", f"no bot for meeting {meeting_id}")
        session.request_leave("leave")
        await session.wait_done(60)
        return session.as_dict()

    def list(self) -> list[dict[str, Any]]:
        sessions = sorted(self._sessions.values(), key=lambda s: (not s.active, -s.created_at))
        return [s.as_dict() for s in sessions]

    def get(self, meeting_id: str) -> dict[str, Any] | None:
        session = self._sessions.get(meeting_id)
        return session.as_dict() if session else None

    async def shutdown(self) -> None:
        """Leave every bot and stop Playwright. Safe to call twice."""
        self._closed = True
        sessions = list(self._sessions.values())
        await asyncio.gather(*(s.cancel() for s in sessions), return_exceptions=True)
        async with self._pw_lock:
            pw, self._playwright = self._playwright, None
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                log.exception("playwright stop failed")

    # ---- internals ----------------------------------------------------------

    def _check_capacity(self, target: JitsiTarget) -> None:
        live = [s for s in (*self._sessions.values(), *self._starting) if s.active]
        if any(s.target.key == target.key for s in live):
            raise BotError("already_joined", "a bot is already in that meeting")
        if len(live) >= self.settings.max_concurrent:
            raise BotError("limit_reached", f"at most {self.settings.max_concurrent} bots can run at once")

    async def _get_playwright(self) -> Any:
        async with self._pw_lock:
            if self._playwright is None:
                from playwright.async_api import async_playwright  # heavy import, only when used

                self._playwright = await async_playwright().start()
            return self._playwright

    async def _make_meeting(self, title: str) -> str:
        try:
            if self._create_meeting is not None:
                return str(await self._create_meeting(title, "bot"))
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.settings.local_base}/api/v1/meetings",
                    json={"title": title, "source": "bot"},
                    headers={"Authorization": f"Bearer {self.settings.internal_token}"},
                )
                resp.raise_for_status()
                body = resp.json()
            meeting = body.get("meeting", body) if isinstance(body, dict) else {}
            return str(meeting["id"])
        except Exception as exc:
            raise BotError("meeting_create_failed", f"could not create the meeting: {exc}") from exc

    def _changed(self, session: BotSession) -> None:
        if self._on_state is None or session.meeting_id is None:
            return
        try:
            result = self._on_state(session.as_dict())
            if inspect.isawaitable(result):
                asyncio.ensure_future(result).add_done_callback(_log_task_error)
        except Exception:
            log.exception("on_state hook failed")

    def _prune(self) -> None:
        finished = [s for s in self._sessions.values() if not s.active]
        finished.sort(key=lambda s: s.ended_at or 0)
        for old in finished[: max(0, len(finished) - KEEP_FINISHED)]:
            self._sessions.pop(old.meeting_id or "", None)


def _log_task_error(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("on_state hook failed: %s", task.exception())
