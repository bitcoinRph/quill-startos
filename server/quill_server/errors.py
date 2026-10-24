"""API errors: ``{"error": {"code", "message"}}`` with a matching HTTP status."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def not_found(what: str = "meeting") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def bad_request(message: str, code: str = "bad_request") -> ApiError:
    return ApiError(400, code, message)


def conflict(message: str, code: str = "conflict") -> ApiError:
    return ApiError(409, code, message)


_BOT_STATUS = {"not_found": 404, "already_joined": 409, "limit_reached": 409, "bot_disabled": 503}


def bot_error(exc: Exception) -> ApiError:
    """Map what the bot manager raises to an API error.

    The manager's ``BotError`` carries a stable ``code``; bad URLs and hosts that
    are not allowed arrive as ``BotUrlError`` (client's fault, 400). A plain
    ``ValueError``/``KeyError`` is handled the same way for simpler managers.
    """
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc)
    if code:
        client_fault = type(exc).__name__ == "BotUrlError"
        return ApiError(_BOT_STATUS.get(code, 400 if client_fault else 502), code, message)
    if isinstance(exc, ValueError):
        return ApiError(400, "invalid_bot_request", message)
    if isinstance(exc, KeyError):
        return ApiError(404, "not_found", "no bot is attached to that meeting")
    return ApiError(502, "bot_failed", f"the bot failed: {message}")
