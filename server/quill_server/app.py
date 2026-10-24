"""FastAPI app factory: API, MCP, bot page and the static web UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .api import admin, bot, granola, imports, live, meetings, openai_compat, system
from .config import Config
from .errors import ApiError
from .mcp_server import McpGate, build_mcp, mcp_http_app
from .state import AppState

log = logging.getLogger("quill.app")

VERSION = "0.1.3"
# Request paths that belong to the API; they must 404 as JSON, never fall back to the UI shell.
API_PREFIXES = ("api", "v1", "mcp", "bot", "health", "openapi.json", "docs", "redoc")
GRANOLA_PREFIXES = ("/v1/notes", "/v1/folders", "/v1/spaces", "/v1/webhook-endpoints")
_STATUS_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 413: "payload_too_large", 422: "validation_error",
}  # fmt: skip


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class SpaFiles(StaticFiles):
    """Static files with ``index.html`` for unknown UI routes (client-side routing)."""

    async def get_response(self, path: str, scope: Scope):
        if path.split("/", 1)[0] == "dev":  # web/dev holds developer tooling, never user-facing
            raise StarletteHTTPException(404)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            first = path.split("/", 1)[0]
            last = path.rsplit("/", 1)[-1]
            if exc.status_code != 404 or first in API_PREFIXES or "." in last:
                raise
            return await super().get_response("index.html", scope)


def _web_dir(config: Config) -> Path | None:
    candidate = config.web_dir or Path(__file__).resolve().parent.parent / "web"
    return candidate if (candidate / "index.html").is_file() else None


def create_app(config: Config | None = None, state: AppState | None = None) -> FastAPI:
    state = state or AppState.create(config or Config.from_env())
    mcp, _ = build_mcp(state)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await state.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await state.stop()

    app = FastAPI(title="Quill", version=VERSION, lifespan=lifespan)
    app.state.quill = state
    app.state.config = state.config  # read by the bot page router

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, _STATUS_CODES.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{where}: {first.get('msg', 'invalid request')}" if where else str(first.get("msg", "invalid request"))
        # Granola documents 400 for malformed input; the native API uses 422.
        status = 400 if request.url.path.startswith(GRANOLA_PREFIXES) else 422
        return error_response(status, "bad_request" if status == 400 else "validation_error", message)

    for module in (system, meetings, live, admin, imports, bot, openai_compat, granola):
        app.include_router(module.router)
    app.add_route("/mcp", McpGate(mcp_http_app(mcp), state))

    try:
        from .bot.routes import router as bot_page_router

        app.include_router(bot_page_router)
    except ImportError as exc:
        log.info("bot page not available: %s", exc)

    web = _web_dir(state.config)
    if web is not None:
        app.mount("/", SpaFiles(directory=web, html=True), name="web")
    return app
