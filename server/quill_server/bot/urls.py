"""Jitsi URL parsing and the host allowlist.

The server runs a browser, and a browser on a server can reach the LAN. So the
only thing the bot ever navigates to is a host the operator listed in
`QUILL_JITSI_DOMAINS`, matched exactly, over https. The browser never sees the
URL the caller sent: we parse it, check it, and build our own from the parsed
parts. That removes the whole class of parser-differential tricks
(`https://good.com\\@evil.com`, `%`-encoded hosts, `user@host`, odd IP forms).

Allowlist entries are `host` (https on port 443) or `host:port`. IPv6 literals
use brackets: `[::1]` or `[::1]:8443`. There are no wildcards.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlsplit

log = logging.getLogger("quill.bot")

DEFAULT_PORT = 443
MAX_URL_LENGTH = 2048
MAX_ROOM_LENGTH = 200

_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f]")  # raw URL: no whitespace either
_ROOM_BAD = re.compile(r"[\x00-\x1f\x7f-\x9f]")  # a decoded room name may hold a space
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


class BotError(Exception):
    """A bot request that cannot be served. `code` is stable for API clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BotUrlError(BotError):
    """The meeting URL is malformed or its host is not allowed."""


@dataclass(frozen=True)
class AllowedHost:
    hostname: str
    port: int = DEFAULT_PORT

    @property
    def netloc(self) -> str:
        return format_netloc(self.hostname, self.port)


@dataclass(frozen=True)
class JitsiTarget:
    """What the bot may open, rebuilt from parsed parts (never the raw URL)."""

    hostname: str
    port: int
    room: str
    jwt: str | None = None

    @property
    def netloc(self) -> str:
        return format_netloc(self.hostname, self.port)

    @property
    def base_url(self) -> str:
        return f"https://{self.netloc}"

    @property
    def key(self) -> tuple[str, int, str]:
        """Identity of the room, used to refuse a second bot in the same room."""
        return (self.hostname, self.port, self.room.casefold())


def format_netloc(hostname: str, port: int) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    return host if port == DEFAULT_PORT else f"{host}:{port}"


def _normalize_ip(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _parse_entry(raw: str) -> AllowedHost | None:
    entry = raw.strip().lower()
    if not entry:
        return None
    if any(c in entry for c in "/\\@?#% ") or "://" in entry:
        return None
    port = DEFAULT_PORT
    if entry.startswith("["):
        end = entry.find("]")
        if end < 0:
            return None
        host, rest = entry[1:end], entry[end + 1 :]
        if rest:
            if not rest.startswith(":") or not rest[1:].isdigit():
                return None
            port = int(rest[1:])
        ip = _normalize_ip(host)
        if ip is None or ":" not in ip:
            return None
        host = ip
    else:
        host, sep, port_s = entry.partition(":")
        if sep:
            if not port_s.isdigit():
                return None
            port = int(port_s)
        ip = _normalize_ip(host)
        host = ip if ip is not None else host
        if ip is None and not _HOSTNAME.match(host):
            return None
    if not 0 < port < 65536:
        return None
    return AllowedHost(host, port)


def parse_allowlist(value: str | Iterable[str] | None) -> tuple[AllowedHost, ...]:
    """Parse `QUILL_JITSI_DOMAINS` (comma list) or an iterable of entries.

    Bad entries are dropped with a warning: a typo must never widen access.
    """
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    hosts: list[AllowedHost] = []
    for item in items:
        if not str(item).strip():
            continue
        parsed = _parse_entry(str(item))
        if parsed is None:
            log.warning("ignoring invalid QUILL_JITSI_DOMAINS entry %r", item)
        elif parsed not in hosts:
            hosts.append(parsed)
    return tuple(hosts)


def parse_jitsi_url(url: str, allowed: Iterable[AllowedHost]) -> JitsiTarget:
    """Validate a meeting URL against the allowlist.

    Accepts `https://<host>[:port]/<room>[?jwt=...]`. Rejects every other
    scheme, userinfo, ports not listed, hosts not listed (IP literals included),
    and paths that are not exactly one room segment (Jitsi tenant paths are not
    supported). Messages never echo the query string, which can hold a JWT.
    """
    allowed = tuple(allowed)
    if not allowed:
        raise BotError("bot_disabled", "the Jitsi bot is disabled: QUILL_JITSI_DOMAINS is empty")
    if not isinstance(url, str) or not url.strip():
        raise BotUrlError("invalid_url", "meeting URL is required")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise BotUrlError("invalid_url", "meeting URL is too long")
    if _CONTROL.search(url) or "\\" in url:
        raise BotUrlError("invalid_url", "meeting URL contains forbidden characters")

    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise BotUrlError("invalid_url", "meeting URL is not valid") from None

    if parts.scheme != "https":
        raise BotUrlError("invalid_url", "only https meeting URLs are allowed")
    if "@" in parts.netloc:
        raise BotUrlError("invalid_url", "meeting URL must not contain credentials")
    if not hostname:
        raise BotUrlError("invalid_url", "meeting URL has no host")
    hostname = _normalize_ip(hostname) or hostname.lower()
    effective_port = DEFAULT_PORT if port is None else port

    if AllowedHost(hostname, effective_port) not in allowed:
        listed_hosts = {a.hostname for a in allowed}
        if hostname not in listed_hosts:
            raise BotUrlError("host_not_allowed", f"host {hostname!r} is not in QUILL_JITSI_DOMAINS")
        raise BotUrlError("host_not_allowed", f"port {effective_port} is not allowed for host {hostname!r}")

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 1 or parts.path.count("//") > 0:
        raise BotUrlError("invalid_url", "meeting URL must be https://<host>/<room>")
    room = unquote(segments[0])
    if (
        not room
        or len(room) > MAX_ROOM_LENGTH
        or room in {".", ".."}
        or "/" in room
        or _ROOM_BAD.search(room)
    ):
        raise BotUrlError("invalid_url", "meeting room name is not valid")

    jwt_values = parse_qs(parts.query).get("jwt")
    jwt = jwt_values[0] if jwt_values and jwt_values[0] else None
    return JitsiTarget(hostname=hostname, port=effective_port, room=room, jwt=jwt)
