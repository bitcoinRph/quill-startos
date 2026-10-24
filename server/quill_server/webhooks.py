"""Outbound webhooks, signed with Standard Webhooks (https://www.standardwebhooks.com/).

Two endpoint flavors share one table: ``quill`` endpoints (native API) receive
``{id, type, created_at, data}``; ``granola`` endpoints (the Granola-compatible
API) receive Granola's ``{event_id, event_type, note_id, occurred_at}``.
Deliveries run as background tasks; a restart drops retries that were pending,
which is acceptable for notifications that can be re-derived from the API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Awaitable, Callable

import httpx

from .db import Database
from .util import iso, new_id, normalize_id

log = logging.getLogger("quill.webhooks")

NATIVE_EVENTS = ("meeting.started", "meeting.ended", "transcript.ready", "notes.ready")
GRANOLA_EVENTS = ("note.access_granted", "note.edited", "note.generated")
RETRY_BACKOFF = (5.0, 30.0, 120.0, 600.0, 3600.0)
RETRY_STATUS = {408, 429}
TIMEOUT_S = 15.0


def new_secret() -> str:
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


def sign(secret: str, msg_id: str, timestamp: int | str, body: bytes) -> str:
    """``v1,<base64 HMAC-SHA256 of "<id>.<timestamp>.<body>">``; the key is the
    base64 part of ``whsec_<key>``."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def verify(secret: str, headers: dict[str, str], body: bytes, tolerance_s: int = 300) -> bool:
    """Receiver-side check, kept here so tests and docs show the exact algorithm."""
    msg_id, ts, sig = headers["webhook-id"], headers["webhook-timestamp"], headers["webhook-signature"]
    if abs(time.time() - int(ts)) > tolerance_s:
        return False
    expected = sign(secret, msg_id, ts, body)
    return any(hmac.compare_digest(expected, part) for part in sig.split(" "))


def _retryable(status: int) -> bool:
    return status in RETRY_STATUS or status >= 500


class Webhooks:
    def __init__(
        self,
        db: Database,
        *,
        folders_of: Callable[[str], set[str]],
        run_on_loop: Callable[..., None] = lambda fn, *a: fn(*a),
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: tuple[float, ...] = RETRY_BACKOFF,
    ) -> None:
        self._db = db
        self._folders_of = folders_of
        self._run_on_loop = run_on_loop
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        )
        self._sleep = sleep
        self._backoff = backoff
        self._tasks: set[asyncio.Task] = set()

    # --- endpoints (CRUD) ---

    def create(
        self,
        url: str,
        events: list[str],
        *,
        flavor: str = "quill",
        scopes: list[str] | None = None,
        folder_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": new_id("whe"),
            "url": url,
            "secret": new_secret(),
            "events_json": json.dumps(events),
            "active": 1,
            "flavor": flavor,
            "scopes_json": json.dumps(scopes or []),
            "folder_ids_json": json.dumps(folder_ids or []),
            "created_at": iso(),
        }
        cols = ", ".join(row)
        self._db.execute(f"INSERT INTO webhooks({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        return self._decode(row)

    def list_by_flavor(self, flavor: str) -> list[dict[str, Any]]:
        rows = self._db.query("SELECT * FROM webhooks WHERE flavor=? ORDER BY created_at, id", (flavor,))
        return [self._decode(r) for r in rows]

    def get(self, endpoint_id: str, flavor: str) -> dict[str, Any] | None:
        endpoint_id = normalize_id(endpoint_id, "whe") or endpoint_id
        row = self._db.one("SELECT * FROM webhooks WHERE id=? AND flavor=?", (endpoint_id, flavor))
        return self._decode(row) if row else None

    def update(self, endpoint_id: str, **fields: Any) -> None:
        mapping = {"events": "events_json", "scopes": "scopes_json", "folder_ids": "folder_ids_json"}
        sets, values = [], []
        for key, value in fields.items():
            if key in mapping:
                sets.append(f"{mapping[key]}=?")
                values.append(json.dumps(value))
            elif key == "enabled":
                sets.append("active=?")
                values.append(1 if value else 0)
            elif key == "url":
                sets.append("url=?")
                values.append(value)
        if sets:
            self._db.execute(f"UPDATE webhooks SET {', '.join(sets)} WHERE id=?", (*values, endpoint_id))

    def delete(self, endpoint_id: str, flavor: str) -> bool:
        endpoint_id = normalize_id(endpoint_id, "whe") or endpoint_id
        return self._db.execute("DELETE FROM webhooks WHERE id=? AND flavor=?", (endpoint_id, flavor)).rowcount > 0

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "url": row["url"],
            "secret": row["secret"],
            "events": json.loads(row["events_json"]),
            "enabled": bool(row["active"]),
            "flavor": row["flavor"],
            "scopes": json.loads(row["scopes_json"]),
            "folder_ids": json.loads(row["folder_ids_json"]),
            "created_at": row["created_at"],
        }

    # --- delivery ---

    def emit(self, event: str, meeting_id: str, data: dict[str, Any], *, first_notes: bool = False) -> None:
        """Queue deliveries for every matching endpoint (callable from any thread).

        ``first_notes`` tells a Granola endpoint whether ``notes.ready`` is the
        first summary (``note.generated``) or a later change (``note.edited``).
        """
        now = iso()
        for ep in [self._decode(r) for r in self._db.query("SELECT * FROM webhooks WHERE active=1")]:
            if ep["flavor"] == "quill":
                if event not in ep["events"]:
                    continue
                payload = {"id": new_id("evt"), "type": event, "created_at": now, "data": data}
            else:
                if event != "notes.ready":
                    continue
                granola_event = "note.generated" if first_notes else "note.edited"
                if granola_event not in ep["events"] or not self._in_folders(ep["folder_ids"], meeting_id):
                    continue
                payload = {
                    "event_id": new_id("evt"),
                    "event_type": granola_event,
                    "note_id": meeting_id,
                    "occurred_at": now,
                }
                if granola_event == "note.edited":
                    payload["data"] = {"changed_fields": ["summary"]}
            self._run_on_loop(self._start, ep, payload)

    def _start(self, endpoint: dict[str, Any], payload: dict[str, Any]) -> None:
        task = asyncio.get_running_loop().create_task(self.deliver(endpoint, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _in_folders(self, wanted: list[str], meeting_id: str) -> bool:
        return not wanted or bool(set(wanted) & self._folders_of(meeting_id))

    async def deliver(self, endpoint: dict[str, Any], payload: dict[str, Any]) -> bool:
        """POST with retries; returns whether a 2xx was eventually received."""
        body = json.dumps(payload, separators=(",", ":")).encode()
        msg_id = payload.get("id") or payload["event_id"]
        async with self._client_factory() as client:
            for attempt in range(len(self._backoff) + 1):
                ts = int(time.time())
                headers = {
                    "content-type": "application/json",
                    "webhook-id": msg_id,
                    "webhook-timestamp": str(ts),
                    "webhook-signature": sign(endpoint["secret"], msg_id, ts, body),
                }
                try:
                    resp = await client.post(endpoint["url"], content=body, headers=headers)
                    if 200 <= resp.status_code < 300:
                        return True
                    if not _retryable(resp.status_code):
                        log.warning("webhook %s rejected with %s", endpoint["id"], resp.status_code)
                        return False
                except httpx.HTTPError as exc:
                    log.warning("webhook %s failed: %s", endpoint["id"], exc)
                if attempt < len(self._backoff):
                    await self._sleep(self._backoff[attempt])
        return False

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
