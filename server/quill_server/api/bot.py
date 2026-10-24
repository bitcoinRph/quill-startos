"""Jitsi bot routes. The manager is imported lazily so a missing bot package
or an empty ``QUILL_JITSI_DOMAINS`` never stops the server from starting."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..errors import ApiError, bot_error
from ..state import AppState
from .deps import get_state, meeting_view, require_auth

router = APIRouter(prefix="/api/v1/bot", tags=["bot"], dependencies=[Depends(require_auth)])


class JoinBody(BaseModel):
    url: str
    title: str | None = None
    display_name: str | None = None
    password: str | None = None


def _bot(state: AppState) -> Any:
    bot = state.get_bot()
    if bot is None:
        raise ApiError(503, "bot_unavailable", "the Jitsi bot is disabled (QUILL_JITSI_DOMAINS is empty) or not installed")
    return bot


@router.post("/join", status_code=201)
async def join(body: JoinBody, state: AppState = Depends(get_state)) -> dict[str, Any]:
    bot = _bot(state)
    try:
        meeting_id = await bot.join(body.url, body.title, body.display_name, body.password)
    except Exception as exc:  # BotError / BotUrlError (e.g. host not in QUILL_JITSI_DOMAINS)
        raise bot_error(exc) from exc
    return meeting_view(state, state.meetings.require(meeting_id))


@router.post("/{meeting_id}/leave")
async def leave(meeting_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    bot = _bot(state)
    row = state.meetings.require(meeting_id)
    try:
        await bot.leave(row["id"])
    except Exception as exc:
        raise bot_error(exc) from exc
    return meeting_view(state, state.meetings.require(row["id"]))


@router.get("")
def list_bots(state: AppState = Depends(get_state)) -> dict[str, Any]:
    bot = state.get_bot()
    return {"enabled": bot is not None, "domains": list(state.config.jitsi_domains), "bots": bot.list() if bot else []}
