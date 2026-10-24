"""WebSocket live ingest endpoint (spec section 6); the protocol lives in ``live.py``."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import COOKIE_NAME
from ..errors import ApiError
from ..live import LiveConnection

router = APIRouter()


def _authorized(ws: WebSocket) -> bool:
    auth = ws.app.state.quill.auth
    header = ws.headers.get("authorization", "")
    bearer = header.split(" ", 1)[1].strip() if header.lower().startswith("bearer ") else None
    return bool(
        auth.valid_cookie(ws.cookies.get(COOKIE_NAME))
        or auth.check_token(ws.query_params.get("token") or bearer)
        or auth.check_internal(ws.query_params.get("internal"))
    )


@router.websocket("/api/v1/meetings/{meeting_id}/live")
async def live(ws: WebSocket, meeting_id: str) -> None:
    state = ws.app.state.quill
    if not _authorized(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    send_lock = asyncio.Lock()

    async def send(msg: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json(msg)

    try:
        session = state.hub.session(meeting_id)
    except ApiError as exc:
        await send({"type": "error", "code": exc.code, "message": exc.message})
        await ws.close(code=4404 if exc.status == 404 else 4409)
        return

    sub = state.bus.subscribe(session.id)
    conn = LiveConnection(session, send)

    async def forward() -> None:
        while (event := await sub.get()) is not None:
            if event["type"] in ("segment", "status"):
                await send(event)

    await send({"type": "ready", "meeting_id": session.id, "session_offset_ms": session.clock_ms()})
    forwarder = asyncio.create_task(forward())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") is not None:
                await conn.on_text(message["text"])
            elif message.get("bytes") is not None:
                await conn.on_binary(message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        conn.close()
        sub.close()
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await forwarder
