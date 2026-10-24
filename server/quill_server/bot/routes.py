"""Serves the bot page to the headless browser, and only to it.

`GET /bot/page?internal=<QUILL_INTERNAL_TOKEN>` is the HTML; its scripts are
`GET /bot/page/<file>?internal=...`. The token is compared in constant time.
Without a configured token the routes refuse everything: an empty secret must
never be a valid secret.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

PAGE_DIR = Path(__file__).resolve().parent / "page"

ASSETS: dict[str, str] = {
    "bot.js": "text/javascript; charset=utf-8",
    "protocol.js": "text/javascript; charset=utf-8",
    "pipeline.js": "text/javascript; charset=utf-8",
    "pcm-worklet.js": "text/javascript; charset=utf-8",
}

# The token is in the URL, and the page loads scripts from another origin
# (the Jitsi server), so never leak the URL as a Referer.
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

router = APIRouter(prefix="/bot", tags=["bot"], include_in_schema=False)


def _expected_token(request: Request) -> str:
    config = getattr(request.app.state, "config", None)
    token = getattr(config, "internal_token", None) if config is not None else None
    return str(token) if token else os.environ.get("QUILL_INTERNAL_TOKEN", "")


def token_ok(supplied: str | None, expected: str) -> bool:
    """Constant-time check; an empty expected or supplied token never matches."""
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def _forbidden() -> JSONResponse:
    return JSONResponse(
        {"error": {"code": "forbidden", "message": "internal token required"}},
        status_code=403,
        headers=_HEADERS,
    )


def _serve(request: Request, filename: str, media_type: str) -> Response:
    if not token_ok(request.query_params.get("internal"), _expected_token(request)):
        return _forbidden()
    body = (PAGE_DIR / filename).read_bytes()
    return Response(body, media_type=media_type, headers=_HEADERS)


@router.get("/page")
async def bot_page(request: Request) -> Response:
    return _serve(request, "bot.html", "text/html; charset=utf-8")


@router.get("/page/{asset}")
async def bot_asset(asset: str, request: Request) -> Response:
    media_type = ASSETS.get(asset)
    if media_type is None:
        # Same answer as a bad token so the route list is not probeable.
        if not token_ok(request.query_params.get("internal"), _expected_token(request)):
            return _forbidden()
        return JSONResponse({"error": {"code": "not_found", "message": "no such asset"}}, status_code=404, headers=_HEADERS)
    return _serve(request, asset, media_type)
