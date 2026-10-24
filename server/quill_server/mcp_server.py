"""MCP server (spec section 7): compact-text tools over streamable HTTP at ``/mcp``.

The tool bodies live on :class:`QuillTools` so tests can call them directly.
Output is deliberately terse text: agents pay for every token they read, so
long results are paged (``offset``/``next_offset``, ``since_seq``/``next_seq``,
``cursor``) instead of returned whole.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from . import transcripts
from .api.deps import bearer_token
from .api.system import search_hits
from .errors import ApiError, bot_error
from .state import AppState
from .util import clock

INSTRUCTIONS = (
    "Quill records and transcribes meetings. Find meetings with list_meetings or search_meetings, read "
    "them with get_meeting and get_transcript (page with next_offset), follow a running meeting with "
    "get_live_transcript (page with next_seq), and write notes back with save_notes."
)


def _fmt_duration(ms: int) -> str:
    minutes = ms // 60000
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _tool_error(exc: ApiError) -> ToolError:
    return ToolError(f"{exc.code}: {exc.message}")


def tool_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn API errors into model-readable tool errors, sync or async."""
    import functools
    import inspect

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*a: Any, **kw: Any) -> Any:
            try:
                return await fn(*a, **kw)
            except ApiError as exc:
                raise _tool_error(exc) from exc

        return awrapper

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except ApiError as exc:
            raise _tool_error(exc) from exc

    return wrapper


class QuillTools:
    def __init__(self, state: AppState) -> None:
        self.s = state

    @tool_errors
    def list_meetings(
        self,
        query: str | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> str:
        """List meetings, newest first, one line each: id | date | duration | title | status. `query` searches titles and transcripts; dates are ISO. If more remain, the last line gives a `cursor` to pass back."""
        rows, nxt = self.s.meetings.list_meetings(
            q=query, status=status, created_after=created_after, created_before=created_before,
            limit=max(1, min(limit, 100)), cursor=cursor,
        )  # fmt: skip
        lines = [
            f"{r['id']} | {(r['sort_ts'] or '')[:16].replace('T', ' ')} | {_fmt_duration(r['duration_ms'])} | "
            f"{r['title']} | {r['status']}"
            for r in rows
        ]
        if not lines:
            return "no meetings"
        if nxt:
            lines.append(f"cursor={nxt}")
        return "\n".join(lines)

    @tool_errors
    def get_meeting(self, meeting_id: str) -> str:
        """Meeting details: title, times, attendees, speakers, status (plus bot state), your notes and the AI notes. The transcript is separate: use get_transcript."""
        row = self.s.meetings.require(meeting_id)
        v = self.s.meetings.view(row, ["notes"])
        out = [
            f"id: {v['id']}",
            f"title: {v['title']}",
            f"status: {v['status']}" + (f" ({v['error']})" if v["error"] else ""),
            f"started: {v['started_at'] or '-'}  ended: {v['ended_at'] or '-'}  duration: {_fmt_duration(v['duration_ms'])}",
            "attendees: " + (", ".join(a["name"] or a["email"] for a in v["attendees"]) or "-"),
            "speakers: " + (", ".join(v["speakers"]) or "-"),
        ]
        bot = self.s.bot_state(row["id"]) if row["source"] == "bot" else None
        if bot:
            out.append(f"bot: {bot['state']}" + (f" ({bot['error']})" if bot.get("error") else ""))
        out.append(f"\n## User notes\n{v['user_notes_md'].strip() or '(none)'}")
        out.append(f"\n## AI notes ({v['ai_notes_source'] or 'none'})\n{v['ai_notes_md'].strip() or '(none)'}")
        return "\n".join(out)

    @tool_errors
    def get_transcript(
        self,
        meeting_id: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        speaker: str | None = None,
        offset: int = 0,
        max_chars: int = 20000,
    ) -> str:
        """Transcript as `[h:mm:ss] Speaker: text` lines. Filter by start_ms/end_ms/speaker. Long transcripts are paged: when the last line says next_offset=N, call again with offset=N. Never re-fetch what you already have."""
        row = self.s.meetings.require(meeting_id)
        segs = transcripts.filter_segments(
            self.s.meetings.segments(row["id"]), start_ms=start_ms, end_ms=end_ms, speaker=speaker
        )
        text = transcripts.render_compact(segs)
        offset = max(0, offset)
        window = text[offset : offset + max(200, max_chars)]
        if offset + len(window) < len(text):
            cut = window.rfind("\n")
            window = window[: cut + 1] if cut > 0 else window
            return f"{window}-- next_offset={offset + len(window)} of {len(text)} chars"
        return window if window else "(no transcript lines)"

    @tool_errors
    def search_meetings(self, query: str, limit: int = 10) -> str:
        """Full-text search across all transcripts. One hit per line: meeting id | title | [h:mm:ss] speaker: snippet (matches in **bold**). Follow up with get_transcript using start_ms."""
        hits = search_hits(self.s, query, max(1, min(limit, 50)))
        if not hits:
            return "no matches"
        return "\n".join(
            f"{h['meeting_id']} | {h['title']} | [{clock(h['start_ms'], True)}] {h['speaker']}: {h['snippet']}"
            for h in hits
        )

    @tool_errors
    async def join_meeting(
        self, url: str, title: str | None = None, display_name: str | None = None, password: str | None = None
    ) -> str:
        """Send the notetaker bot into a Jitsi meeting (the host must be allowed by the server). Returns the meeting id; follow it with get_live_transcript and check the bot with get_meeting."""
        bot = self.s.get_bot()
        if bot is None:
            raise ToolError("bot_unavailable: the Jitsi bot is disabled on this server (no QUILL_JITSI_DOMAINS)")
        try:
            meeting_id = await bot.join(url, title, display_name, password)
        except Exception as exc:
            raise _tool_error(bot_error(exc)) from exc
        state = self.s.bot_state(meeting_id)
        return f"meeting_id={meeting_id} bot={state['state'] if state else 'starting'}"

    @tool_errors
    async def leave_meeting(self, meeting_id: str) -> str:
        """Make the bot leave the meeting and finish the recording. Returns the meeting status (processing, then ready once the transcript is done)."""
        bot = self.s.get_bot()
        if bot is None:
            raise ToolError("bot_unavailable: the Jitsi bot is disabled on this server")
        row = self.s.meetings.require(meeting_id)
        try:
            await bot.leave(row["id"])
        except Exception as exc:
            raise _tool_error(bot_error(exc)) from exc
        return f"status={self.s.meetings.require(row['id'])['status']}"

    @tool_errors
    def get_live_transcript(self, meeting_id: str, since_seq: int = 0, max_chars: int = 8000) -> str:
        """New transcript lines of a running (or finished) meeting since `since_seq`. The last line reports next_seq (pass it next time) and the meeting status, so poll cheaply."""
        row = self.s.meetings.require(meeting_id)
        lines, used, next_seq = [], 0, since_seq
        for seg in self.s.meetings.segments(row["id"], since_seq=since_seq, by_seq=True):
            line = transcripts.compact_line(seg)
            if lines and used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
            next_seq = seg["seq"]
        lag = self.s.hub.sessions[row["id"]].pending_ms if row["id"] in self.s.hub.sessions else 0
        footer = f"-- next_seq={next_seq} status={row['status']}" + (f" lag_ms={lag}" if lag else "")
        return "\n".join([*lines, footer])

    @tool_errors
    def save_notes(self, meeting_id: str, markdown: str) -> str:
        """Save your notes for a meeting (replaces earlier AI notes; stored with source `agent`). Write Granola-style markdown: Summary, Key points and decisions, Action items (owner, due date if stated), Open questions."""
        self.s.meetings.save_notes(meeting_id, markdown, "agent")
        return "ok"

    @tool_errors
    async def generate_notes(self, meeting_id: str, template: str | None = None) -> str:
        """Have the server's own LLM write the notes from your notes and the transcript, using a template id or name from list_templates (default `general`). If no LLM is configured it fails: write the notes yourself and call save_notes."""
        return await self.s.notes.generate(meeting_id, template)

    @tool_errors
    def list_templates(self) -> str:
        """Note templates (id, name, prompt) usable with generate_notes or as a guide for writing your own notes."""
        return "\n\n".join(f"{t['id']} | {t['name']}\n{t['prompt']}" for t in self.s.notes.list_templates())

    @tool_errors
    def get_status(self) -> str:
        """Server status: transcription engine and model, queue depth, live meetings and the Jitsi domains the bot may join."""
        h = self.s.health()
        return (
            f"engine={h['engine']} model={h['model']} model_ready={h['model_ready']} queue_depth={h['queue_depth']} "
            f"live_meetings={h['live_meetings']} bot_domains={','.join(self.s.config.jitsi_domains) or 'none'}"
        )

    @tool_errors
    def meeting_notes_prompt(self, meeting_id: str, template: str | None = None) -> str:
        row = self.s.meetings.require(meeting_id)
        tpl = self.s.notes.get_template(template)
        transcript = transcripts.render_compact(self.s.meetings.segments(row["id"]))
        return self.s.notes.prompt_for(row["id"], tpl, transcript) + (
            "\n\nWhen done, save the result with the save_notes tool."
        )


def build_mcp(state: AppState) -> tuple[MCPServer, QuillTools]:
    tools = QuillTools(state)
    mcp = MCPServer("quill", instructions=INSTRUCTIONS)
    for name in (
        "list_meetings", "get_meeting", "get_transcript", "search_meetings", "join_meeting", "leave_meeting",
        "get_live_transcript", "save_notes", "generate_notes", "list_templates", "get_status",
    ):  # fmt: skip
        mcp.add_tool(getattr(tools, name), name=name)
    mcp.prompt(
        name="meeting_notes",
        description="Template plus transcript for writing meeting notes yourself; save them with save_notes.",
    )(tools.meeting_notes_prompt)
    return mcp, tools


class McpGate:
    """Bearer-auth in front of the MCP ASGI app; anything else gets 401."""

    def __init__(self, app: ASGIApp, state: AppState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            from starlette.requests import Request

            if self.state.auth.check_token(bearer_token(Request(scope))) is None:
                body = json.dumps({"error": {"code": "unauthorized", "message": "Bearer API key required"}}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b'Bearer realm="quill"'),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def mcp_http_app(mcp: MCPServer) -> ASGIApp:
    """The streamable-HTTP ASGI app, stateless JSON so clients survive restarts."""
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


