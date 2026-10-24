"""Jitsi notetaker bot (docs/server-spec.md section 8). See README.md."""

from .manager import BotManager
from .urls import BotError, BotUrlError

__all__ = ["BotManager", "BotError", "BotUrlError"]
