"""Small helpers shared across modules: ids, timestamps, atomic writes."""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE62 = string.ascii_letters + string.digits


def new_id(prefix: str) -> str:
    """``<prefix>_`` + 14 base62 characters, the shape Granola's API uses.

    Notes are ``not``, folders ``fol``, webhook endpoints ``whe``; internal rows
    (jobs, keys, templates) use their own prefixes. Ids carry no time order, so
    list pagination breaks ties on the id itself.
    """
    return f"{prefix}_" + "".join(secrets.choice(_BASE62) for _ in range(14))


def normalize_id(value: str, prefix: str) -> str | None:
    """Canonicalize the prefix case (``NOT_x`` -> ``not_x``); None if malformed."""
    if len(value) != len(prefix) + 15 or value[: len(prefix) + 1].lower() != prefix + "_":
        return None
    rest = value[len(prefix) + 1 :]
    return f"{prefix}_{rest}" if rest.isalnum() and rest.isascii() else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    """Second-resolution UTC timestamp, the only format stored in the database."""
    return (dt or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Accept dates and datetimes (naive = UTC); raises ValueError otherwise."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_iso(value: str) -> str:
    return iso(parse_iso(value))


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write via temp file + ``os.replace`` so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode() if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slug(value: str, fallback: str = "track") -> str:
    """Filesystem-safe fragment; never empty, never starts with a dot."""
    cleaned = _SLUG_RE.sub("-", value).strip("-.")[:48]
    return cleaned or fallback


def clock(ms: int, always_hours: bool = False) -> str:
    """``m:ss`` below one hour, ``h:mm:ss`` from one hour (matches macOS)."""
    total = max(0, ms) // 1000
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if (h > 0 or always_hours) else f"{m}:{s:02d}"
