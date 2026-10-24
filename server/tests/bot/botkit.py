"""Shared helpers for the bot tests (not named conftest: other test dirs have one).

`quill_double` is a stand-in for the server core: the real bot router plus the
three endpoints the bot talks to (create meeting, live ingest WebSocket,
stop), recording everything it receives so tests can assert on the wire.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from quill_server.bot.routes import router  # noqa: E402

TOKEN = "test-internal-token-0123456789"


def chromium_path() -> str | None:
    """Pre-installed Chromium for sandboxes where Playwright's own build is absent."""
    explicit = os.environ.get("QUILL_CHROMIUM_PATH")
    if explicit:
        return explicit
    for candidate in ("/opt/pw-browsers/chromium",):
        if Path(candidate).exists():
            return candidate
    return None


@dataclass
class Recorder:
    meetings: list[dict] = field(default_factory=list)
    stops: list[str] = field(default_factory=list)
    texts: dict[str, list[dict]] = field(default_factory=dict)  # meeting -> text frames
    frames: dict[str, list[bytes]] = field(default_factory=dict)  # meeting -> binary frames
    sockets_open: int = 0
    closed: dict[str, asyncio.Event] = field(default_factory=dict)
    ws: dict[str, WebSocket] = field(default_factory=dict)  # live sockets, so tests can close them

    def decoded(self, meeting: str) -> dict[str, list[int]]:
        """track_id -> concatenated PCM samples, from the binary frames."""
        import struct

        out: dict[str, list[int]] = {}
        for f in self.frames.get(meeting, []):
            n = f[0]
            tid = f[1 : 1 + n].decode()
            body = f[1 + n :]
            out.setdefault(tid, []).extend(struct.unpack(f"<{len(body) // 2}h", body))
        return out

    def types(self, meeting: str) -> list[str]:
        return [t.get("type") for t in self.texts.get(meeting, [])]


@dataclass
class Double:
    port: int
    token: str
    rec: Recorder
    app: FastAPI

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def build_double_app(rec: Recorder, token: str) -> FastAPI:
    app = FastAPI()
    app.state.config = SimpleNamespace(internal_token=token)
    app.include_router(router)

    @app.post("/api/v1/meetings")
    async def create(request: Request):
        if request.headers.get("authorization") != f"Bearer {token}":
            return {"error": {"code": "unauthorized"}}
        body = await request.json()
        mid = f"mtg{len(rec.meetings) + 1:04d}"
        rec.meetings.append({"id": mid, **body})
        rec.closed[mid] = asyncio.Event()
        return {"id": mid, "status": "scheduled"}

    @app.get("/test/blank")
    async def blank():
        return HTMLResponse("<!doctype html><title>blank</title>")

    @app.post("/api/v1/meetings/{mid}/stop")
    async def stop(mid: str):
        rec.stops.append(mid)
        return {"ok": True}

    @app.websocket("/api/v1/meetings/{mid}/live")
    async def live(ws: WebSocket, mid: str):
        if ws.query_params.get("internal") != token:
            await ws.close(code=4401)
            return
        await ws.accept()
        rec.sockets_open += 1
        rec.ws[mid] = ws
        rec.texts.setdefault(mid, [])
        rec.frames.setdefault(mid, [])
        await ws.send_text(json.dumps({"type": "ready", "meeting_id": mid, "session_offset_ms": 0}))
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    rec.frames[mid].append(msg["bytes"])
                elif msg.get("text") is not None:
                    data = json.loads(msg["text"])
                    rec.texts[mid].append(data)
                    if data.get("type") == "ping":
                        await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            pass
        finally:
            rec.sockets_open -= 1
            rec.closed.setdefault(mid, asyncio.Event()).set()

    return app


