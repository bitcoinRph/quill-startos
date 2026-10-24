"""Health, sign-in and search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from ..auth import COOKIE_MAX_AGE, COOKIE_NAME
from ..db import fts_query
from ..errors import ApiError
from ..state import AppState
from .deps import get_state, require_auth

router = APIRouter()


class LoginBody(BaseModel):
    password: str


@router.get("/health", tags=["system"])
def health(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return state.health()


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/v1/auth/me", tags=["auth"])
def me(request: Request, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Lets the UI choose between the sign-in form and the app without a 401."""
    return {
        "signin_enabled": state.auth.signin_enabled,
        "authenticated": state.auth.valid_cookie(request.cookies.get(COOKIE_NAME)),
    }


@router.post("/api/v1/auth/login", tags=["auth"])
def login(body: LoginBody, request: Request, response: Response, state: AppState = Depends(get_state)) -> dict[str, Any]:
    if not state.auth.signin_enabled:
        raise ApiError(503, "signin_disabled", "no admin password is set (QUILL_ADMIN_PASSWORD)")
    who = _client(request)
    if state.auth.limiter.blocked(who):
        raise ApiError(429, "too_many_attempts", "too many failed sign-ins; try again in a few minutes")
    if not state.auth.check_password(body.password):
        state.auth.limiter.fail(who)
        raise ApiError(401, "invalid_password", "wrong password")
    state.auth.limiter.reset(who)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE_NAME, state.auth.issue_cookie(), max_age=COOKIE_MAX_AGE, httponly=True, samesite="strict", secure=secure,
        path="/",
    )  # fmt: skip
    return {"ok": True}


@router.post("/api/v1/auth/logout", tags=["auth"])
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


def search_hits(state: AppState, q: str, limit: int) -> list[dict[str, Any]]:
    """Full-text hits across all transcripts. Matches are wrapped in ``**bold**``."""
    expr = fts_query(q)
    if expr is None:
        return []
    rows = state.db.query(
        "SELECT s.meeting_id, m.title, s.seq, s.start_ms, s.speaker, "
        "snippet(segments_fts, 0, '**', '**', '...', 16) AS snippet "
        "FROM segments_fts JOIN segments s ON s.id = segments_fts.rowid "
        "JOIN meetings m ON m.id = s.meeting_id "
        "WHERE segments_fts MATCH ? ORDER BY rank LIMIT ?",
        (expr, limit),
    )
    return rows


@router.get("/api/v1/search", tags=["search"], dependencies=[Depends(require_auth)])
def search(q: str, limit: int = 10, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"hits": search_hits(state, q, max(1, min(limit, 100)))}
