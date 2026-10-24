"""Bot settings: read from the server config object, else from os.environ.

The server core owns `quill_server.config.Config`. The bot must also run from
bare environment variables (tests, a standalone check), so every field is
looked up as an attribute first and as `QUILL_*` second.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from .urls import AllowedHost, parse_allowlist

TRUE = {"1", "true", "yes", "on"}

DEFAULT_BOT_NAME = "Quill Notetaker"
DEFAULT_MAX_CONCURRENT = 3


def _flag(value: Any, default: bool) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE


def _number(value: Any, default: float) -> float:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _address_map(value: Any) -> tuple[tuple[str, str], ...]:
    """Parse exact IPv4 rewrites: ``advertised=reachable`` pairs separated by commas."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ()
    entries = value if isinstance(value, (list, tuple)) else str(value).split(",")
    result: dict[str, str] = {}
    for entry in entries:
        left, sep, right = str(entry).strip().partition("=")
        if not sep:
            raise ValueError(f"invalid ICE address mapping {entry!r}; expected advertised=reachable")
        try:
            source = str(ipaddress.IPv4Address(left.strip()))
            target = str(ipaddress.IPv4Address(right.strip()))
        except ipaddress.AddressValueError as exc:
            raise ValueError(f"invalid IPv4 address in ICE mapping {entry!r}") from exc
        if source == target:
            raise ValueError(f"ICE address mapping must change the address: {entry!r}")
        if source in result:
            raise ValueError(f"duplicate ICE source address: {source}")
        result[source] = target
    return tuple(result.items())


@dataclass(frozen=True)
class BotSettings:
    allowed_hosts: tuple[AllowedHost, ...] = ()
    bot_name: str = DEFAULT_BOT_NAME
    announce: bool = True
    max_minutes: float = 240.0
    alone_minutes: float = 3.0
    port: int = 8080
    internal_token: str = ""
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    chromium_path: str | None = None
    # Test-only: accept a self-signed Jitsi certificate. Never on by default.
    insecure_tls: bool = False
    ice_address_map: tuple[tuple[str, str], ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.allowed_hosts)

    @property
    def local_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def resolve(cls, config: Any = None, env: Mapping[str, str] | None = None) -> "BotSettings":
        e = os.environ if env is None else env

        def pick(attr: str, var: str) -> Any:
            value = getattr(config, attr, None) if config is not None else None
            return value if value is not None else e.get(var)

        domains = pick("jitsi_domains", "QUILL_JITSI_DOMAINS")
        token = pick("internal_token", "QUILL_INTERNAL_TOKEN") or ""
        return cls(
            allowed_hosts=parse_allowlist(domains),
            bot_name=(pick("bot_name", "QUILL_BOT_NAME") or DEFAULT_BOT_NAME).strip() or DEFAULT_BOT_NAME,
            announce=_flag(pick("bot_announce", "QUILL_BOT_ANNOUNCE"), True),
            max_minutes=_number(pick("bot_max_minutes", "QUILL_BOT_MAX_MINUTES"), 240.0),
            alone_minutes=_number(pick("bot_alone_minutes", "QUILL_BOT_ALONE_MINUTES"), 3.0),
            port=int(_number(pick("port", "QUILL_PORT"), 8080)),
            internal_token=str(token),
            max_concurrent=max(1, int(_number(pick("bot_max_concurrent", "QUILL_BOT_MAX_CONCURRENT"), DEFAULT_MAX_CONCURRENT))),
            chromium_path=(pick("chromium_path", "QUILL_CHROMIUM_PATH") or None),
            insecure_tls=_flag(pick("bot_insecure_tls", "QUILL_BOT_TEST_INSECURE_TLS"), False),
            ice_address_map=_address_map(pick("bot_ice_address_map", "QUILL_BOT_ICE_ADDRESS_MAP")),
        )


def new_token() -> str:
    return secrets.token_urlsafe(32)
