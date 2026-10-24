"""FastAPI dependencies and small request helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request, UploadFile

from ..auth import COOKIE_NAME, Principal
from ..errors import ApiError
from ..state import AppState

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def get_state(request: Request) -> AppState:
    return request.app.state.quill


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "missing or invalid credentials")


def require_auth(request: Request) -> Principal:
    """Bearer API key (or the bootstrap key), or the signed admin cookie."""
    state = get_state(request)
    principal = state.auth.check_token(bearer_token(request))
    if principal is None and state.auth.valid_cookie(request.cookies.get(COOKIE_NAME)):
        principal = Principal("session")
    if principal is None:
        raise unauthorized()
    return principal


def require_auth_or_internal(request: Request) -> Principal:
    """Like :func:`require_auth`, and also the bot's internal token as Bearer.

    Only for routes the bot calls on its own meeting (a fallback ``stop``).
    """
    state = get_state(request)
    if state.auth.check_internal(bearer_token(request)):
        return Principal("internal")
    return require_auth(request)


def require_session(request: Request) -> Principal:
    """Admin cookie only: used for managing API keys, so a leaked key cannot mint more."""
    state = get_state(request)
    if not state.auth.valid_cookie(request.cookies.get(COOKIE_NAME)):
        raise ApiError(401, "session_required", "sign in as admin to do this")
    return Principal("session")


def parse_include(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


async def save_upload(upload: UploadFile, dest: Path, limit: int = MAX_UPLOAD_BYTES) -> int:
    """Copy an upload to ``dest``; removes the partial file and raises 413 past ``limit``."""
    size = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await upload.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise ApiError(413, "file_too_large", f"uploads are limited to {limit // (1024 * 1024)} MB")
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return size


def meeting_view(state: AppState, row: dict[str, Any], include: list[str] | None = None) -> dict[str, Any]:
    """Meeting JSON plus the bot's state and error for bot meetings."""
    view = state.meetings.view(row, include or [])
    bot = state.bot_state(row["id"]) if row["source"] == "bot" else None
    if bot is not None:
        view["bot"] = bot
    return view
