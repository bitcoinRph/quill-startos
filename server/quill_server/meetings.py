"""Meeting records: lifecycle, segments, tracks, notes, and the session files.

The database is the source of truth; ``transcript.json``/``.md``, ``meta.json``
and the notes files are written beside the audio so a session directory stays a
self-contained, desktop-compatible artifact.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from . import transcripts
from .config import Config
from .db import Database, fts_query
from .errors import bad_request, not_found
from .events import EventBus
from .folders import Folders
from .storage import create_session_dir, remove_session_dir, write_meta
from .util import atomic_write, atomic_write_json, iso, new_id, normalize_id, normalize_iso, utcnow
from .webhooks import Webhooks

log = logging.getLogger("quill.meetings")

STATUSES = ("scheduled", "live", "processing", "ready", "failed")
SOURCES = ("bot", "browser", "upload", "import", "api")
SORT_TS = "COALESCE(m.started_at, m.created_at)"

Row = dict[str, Any]


def normalize_attendees(value: Sequence[Any] | None) -> list[dict[str, str]]:
    """Accept names, emails or ``{name, email}`` objects; store ``{name, email}``."""
    out: list[dict[str, str]] = []
    for item in value or []:
        if isinstance(item, dict):
            name, email = str(item.get("name") or ""), str(item.get("email") or "")
        else:
            text = str(item).strip()
            name, email = ("", text) if "@" in text and " " not in text else (text, "")
        if name or email:
            out.append({"name": name, "email": email})
    return out


def encode_cursor(*parts: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode().rstrip("=")


def decode_cursor(cursor: str, arity: int) -> list[Any]:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if isinstance(parts, list) and len(parts) == arity:
            return parts
    except ValueError:
        pass
    raise bad_request("invalid cursor", "bad_cursor")


def date_bound(value: str) -> str:
    try:
        return normalize_iso(value)
    except ValueError as exc:
        raise bad_request(f"invalid date: {value!r}") from exc


class MeetingService:
    def __init__(
        self, config: Config, db: Database, bus: EventBus, webhooks: Webhooks, folders: Folders
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.webhooks = webhooks
        self.folders = folders
        # Set by the app: runs after a transcript becomes ready (auto notes).
        self.after_ready: Callable[[str], Awaitable[None]] | None = None
        self._background: set[asyncio.Task] = set()

    # --- lookup ---

    def row(self, meeting_id: str) -> Row | None:
        cid = normalize_id(meeting_id, "not")
        return self.db.one("SELECT * FROM meetings WHERE id=?", (cid,)) if cid else None

    def require(self, meeting_id: str) -> Row:
        row = self.row(meeting_id)
        if row is None:
            raise not_found("meeting")
        return row

    def session_path(self, row: Row) -> Path:
        return self.config.sessions_dir / row["session_dir"]

    # --- create / update / delete ---

    def create(
        self,
        title: str | None = None,
        source: str = "api",
        attendees: Sequence[Any] | None = None,
        calendar_url: str | None = None,
        started_at: str | None = None,
    ) -> Row:
        if source not in SOURCES:
            raise bad_request(f"source must be one of {', '.join(SOURCES)}")
        now = utcnow()
        meeting_id = new_id("not")
        directory = create_session_dir(self.config.sessions_dir, now, meeting_id)
        self.db.execute(
            "INSERT INTO meetings(id, title, status, source, started_at, session_dir, created_at, updated_at, "
            "calendar_url, attendees_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                meeting_id,
                (title or "").strip() or f"Meeting {now.strftime('%Y-%m-%d %H:%M')}",
                "scheduled",
                source,
                started_at,
                directory.name,
                iso(now),
                iso(now),
                calendar_url,
                json.dumps(normalize_attendees(attendees)),
            ),
        )
        return self.require(meeting_id)

    def update(self, meeting_id: str, **fields: Any) -> Row:
        row = self.require(meeting_id)
        sets: dict[str, Any] = {}
        if fields.get("title") is not None:
            title = str(fields["title"]).strip()
            if not title:
                raise bad_request("title cannot be empty")
            sets["title"] = title
        if fields.get("user_notes_md") is not None:
            sets["user_notes_md"] = str(fields["user_notes_md"])
        if fields.get("attendees") is not None:
            sets["attendees_json"] = json.dumps(normalize_attendees(fields["attendees"]))
        if fields.get("calendar_url") is not None:
            sets["calendar_url"] = fields["calendar_url"]
        if sets:
            self._set(row["id"], sets)
            if "user_notes_md" in sets:
                atomic_write(self.session_path(row) / "user-notes.md", sets["user_notes_md"])
            if row["status"] == "ready":
                self.write_files(row["id"])
        return self.require(row["id"])

    def _set(self, meeting_id: str, sets: dict[str, Any]) -> None:
        sets = {**sets, "updated_at": iso()}
        cols = ", ".join(f"{k}=?" for k in sets)
        self.db.execute(f"UPDATE meetings SET {cols} WHERE id=?", (*sets.values(), meeting_id))

    def delete(self, meeting_id: str) -> None:
        row = self.require(meeting_id)
        self.db.execute("DELETE FROM meetings WHERE id=?", (row["id"],))
        remove_session_dir(self.session_path(row), self.config.sessions_dir)

    def rename_speakers(self, meeting_id: str, mapping: dict[str, str]) -> Row:
        row = self.require(meeting_id)
        clean = {k: v.strip() for k, v in mapping.items() if isinstance(v, str) and v.strip()}
        with self.db.transaction():
            for old, new in clean.items():
                self.db.execute("UPDATE segments SET speaker=? WHERE meeting_id=? AND speaker=?", (new, row["id"], old))
                self.db.execute(
                    "UPDATE tracks SET speaker=CASE WHEN speaker=? THEN ? ELSE speaker END, "
                    "label=CASE WHEN label=? THEN ? ELSE label END WHERE meeting_id=?",
                    (old, new, old, new, row["id"]),
                )
            self._set(row["id"], {})
        if row["status"] == "ready":
            self.write_files(row["id"])
        return self.require(row["id"])

    # --- listing ---

    def list_meetings(
        self,
        *,
        q: str | None = None,
        status: str | Sequence[str] | None = None,
        source: str | None = None,
        folder_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[Row], str | None]:
        where, params = ["1=1"], []
        if q and q.strip():
            fts = fts_query(q)
            like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clause = "m.title LIKE ? ESCAPE '\\'"
            params.append(like)
            if fts:
                clause += (
                    " OR m.id IN (SELECT s.meeting_id FROM segments s WHERE s.id IN "
                    "(SELECT rowid FROM segments_fts WHERE segments_fts MATCH ?))"
                )
                params.append(fts)
            where.append(f"({clause})")
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            where.append(f"m.status IN ({','.join('?' * len(statuses))})")
            params += statuses
        if source:
            where.append("m.source=?")
            params.append(source)
        if folder_id:
            folder = self.folders.require(folder_id)
            ids = sorted(self.folders.descendants(folder["id"]))
            where.append(
                f"m.id IN (SELECT meeting_id FROM meeting_folders WHERE folder_id IN ({','.join('?' * len(ids))}))"
            )
            params += ids
        if created_after:
            where.append(f"{SORT_TS} > ?")
            params.append(date_bound(created_after))
        if created_before:
            where.append(f"{SORT_TS} < ?")
            params.append(date_bound(created_before))
        if updated_after:
            where.append("m.updated_at > ?")
            params.append(date_bound(updated_after))
        if cursor:
            ts, last_id = decode_cursor(cursor, 2)
            where.append(f"({SORT_TS} < ? OR ({SORT_TS} = ? AND m.id < ?))")
            params += [ts, ts, last_id]
        rows = self.db.query(
            f"SELECT m.*, {SORT_TS} AS sort_ts FROM meetings m WHERE {' AND '.join(where)} "
            f"ORDER BY {SORT_TS} DESC, m.id DESC LIMIT ?",
            (*params, limit + 1),
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, (encode_cursor(rows[-1]["sort_ts"], rows[-1]["id"]) if more and rows else None)

    # --- views ---

    def speakers(self, meeting_id: str) -> list[str]:
        rows = self.db.query(
            "SELECT speaker FROM segments WHERE meeting_id=? GROUP BY speaker ORDER BY MIN(start_ms)", (meeting_id,)
        )
        return [r["speaker"] for r in rows]

    def view(self, row: Row, include: Sequence[str] = ()) -> Row:
        out: Row = {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "source": row["source"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "calendar_url": row["calendar_url"],
            "attendees": json.loads(row["attendees_json"]),
            "speakers": self.speakers(row["id"]),
            "folder_ids": [f["id"] for f in self.folders.of_meeting(row["id"])],
            "has_user_notes": bool(row["user_notes_md"]),
            "has_ai_notes": bool(row["ai_notes_md"]),
            "ai_notes_source": row["ai_notes_source"],
            "engine": row["engine"],
            "model": row["model"],
            "capture_status": row["capture_status"],
            "error": row["error"],
        }
        if "notes" in include:
            out["user_notes_md"] = row["user_notes_md"]
            out["ai_notes_md"] = row["ai_notes_md"]
        if "tracks" in include:
            out["tracks"] = self.tracks(row["id"])
        if "transcript" in include:
            out["transcript"] = self.segments(row["id"])
        return out

    # --- tracks ---

    @staticmethod
    def _track(r: Row) -> Row:
        return {
            "id": r["id"],
            "kind": r["kind"],
            "speaker": r["speaker"],
            "label": r["label"],
            "participant_id": r["participant_id"],
            "status": r["status"],
            "started_offset_ms": r["started_offset_ms"],
            "ended_offset_ms": r["ended_offset_ms"],
            "segments": json.loads(r["segments_json"]),
        }

    def tracks(self, meeting_id: str) -> list[Row]:
        rows = self.db.query(
            "SELECT * FROM tracks WHERE meeting_id=? ORDER BY started_offset_ms, id", (meeting_id,)
        )
        return [self._track(r) for r in rows]

    def upsert_track(
        self,
        meeting_id: str,
        track_id: str,
        *,
        kind: str,
        speaker: str,
        label: str | None,
        participant_id: str | None,
        offset_ms: int,
        segment: dict[str, Any],
    ) -> None:
        """Create the track, or append a segment file when a participant rejoins."""
        with self.db.transaction():
            existing = self.db.one("SELECT segments_json FROM tracks WHERE meeting_id=? AND id=?", (meeting_id, track_id))
            if existing is None:
                self.db.execute(
                    "INSERT INTO tracks(meeting_id, id, kind, speaker, label, participant_id, status, "
                    "started_offset_ms, segments_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (meeting_id, track_id, kind, speaker, label, participant_id, "live", offset_ms, json.dumps([segment])),
                )
            else:
                files = json.loads(existing["segments_json"]) + [segment]
                self.db.execute(
                    "UPDATE tracks SET status='live', speaker=?, label=?, segments_json=? WHERE meeting_id=? AND id=?",
                    (speaker, label, json.dumps(files), meeting_id, track_id),
                )

    def save_track_segments(
        self, meeting_id: str, track_id: str, segments: list[dict[str, Any]], status: str, ended_ms: int | None
    ) -> None:
        self.db.execute(
            "UPDATE tracks SET segments_json=?, status=?, ended_offset_ms=COALESCE(?, ended_offset_ms) "
            "WHERE meeting_id=? AND id=?",
            (json.dumps(segments), status, ended_ms, meeting_id, track_id),
        )

    def update_track_identity(self, meeting_id: str, track_id: str, speaker: str, label: str | None) -> None:
        self.db.execute(
            "UPDATE tracks SET speaker=?, label=? WHERE meeting_id=? AND id=?", (speaker, label, meeting_id, track_id)
        )

    def add_uploaded_track(self, meeting_id: str, track_id: str, speaker: str, file: str, kind: str = "upload") -> None:
        segment = {
            "file": file, "start_offset_ms": 0, "end_offset_ms": 0,
            "frames_written": 0, "sample_rate_hz": 16000, "channels": 1,
        }  # fmt: skip
        self.upsert_track(
            meeting_id, track_id, kind=kind, speaker=speaker, label=None, participant_id=None,
            offset_ms=0, segment=segment,
        )  # fmt: skip
        self.db.execute("UPDATE tracks SET status='complete' WHERE meeting_id=? AND id=?", (meeting_id, track_id))

    # --- segments ---

    @staticmethod
    def _segment(r: Row) -> Row:
        return {
            "seq": r["seq"],
            "track_id": r["track_id"],
            "speaker": r["speaker"],
            "start_ms": r["start_ms"],
            "end_ms": r["end_ms"],
            "text": r["text"],
            "words": json.loads(r["words_json"]) if r["words_json"] else None,
        }

    def segments(self, meeting_id: str, *, since_seq: int | None = None, by_seq: bool = False) -> list[Row]:
        order = "seq" if by_seq else "start_ms, seq"
        rows = self.db.query(
            f"SELECT * FROM segments WHERE meeting_id=? AND seq>? ORDER BY {order}", (meeting_id, since_seq or 0)
        )
        return [self._segment(r) for r in rows]

    def add_segment(
        self,
        meeting_id: str,
        *,
        track_id: str,
        speaker: str,
        start_ms: int,
        end_ms: int,
        text: str,
        words: list[dict[str, Any]] | None,
    ) -> Row:
        with self.db.transaction():
            seq = self.db.scalar("SELECT COALESCE(MAX(seq), 0) + 1 FROM segments WHERE meeting_id=?", (meeting_id,))
            self.db.execute(
                "INSERT INTO segments(meeting_id, track_id, seq, speaker, start_ms, end_ms, text, words_json) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (meeting_id, track_id, seq, speaker, start_ms, end_ms, text, json.dumps(words) if words else None),
            )
        return {
            "seq": seq, "track_id": track_id, "speaker": speaker,
            "start_ms": start_ms, "end_ms": end_ms, "text": text, "words": words,
        }  # fmt: skip

    def replace_segments(self, meeting_id: str, items: Sequence[Row]) -> None:
        """Replace the transcript, numbering ``seq`` in time order."""
        ordered = sorted(items, key=lambda s: (s["start_ms"], s["end_ms"]))
        with self.db.transaction():
            self.db.execute("DELETE FROM segments WHERE meeting_id=?", (meeting_id,))
            for seq, s in enumerate(ordered, start=1):
                words = s.get("words")
                self.db.execute(
                    "INSERT INTO segments(meeting_id, track_id, seq, speaker, start_ms, end_ms, text, words_json) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (meeting_id, s.get("track_id", ""), seq, s["speaker"], s["start_ms"], s["end_ms"], s["text"],
                     json.dumps(words) if words else None),
                )  # fmt: skip

    # --- status / completion ---

    def set_status(self, meeting_id: str, status: str, *, error: str | None = None, lag_ms: int = 0) -> None:
        sets: Row = {"status": status, "error": error}
        row = self.require(meeting_id)
        if status == "live" and not row["started_at"]:
            sets["started_at"] = iso()
        self._set(row["id"], sets)
        self.bus.publish(row["id"], {"type": "status", "status": status, "lag_ms": lag_ms})

    def event_data(self, row: Row, **extra: Any) -> Row:
        base = {"meeting_id": row["id"], "title": row["title"], "status": row["status"], "source": row["source"]}
        if self.config.public_url:
            base["url"] = f"{self.config.public_url}/#/meetings/{row['id']}"
        return {**base, **extra}

    def emit(self, event: str, meeting_id: str, *, first_notes: bool = False, **extra: Any) -> None:
        row = self.require(meeting_id)
        self.webhooks.emit(event, row["id"], self.event_data(row, **extra), first_notes=first_notes)

    def complete(
        self,
        meeting_id: str,
        *,
        engine: str,
        model: str,
        duration_ms: int | None = None,
        capture_status: str | None = None,
    ) -> None:
        """Transcript is final: write the session files, mark ready, notify."""
        row = self.require(meeting_id)
        segs = self.segments(row["id"])
        if duration_ms is None:
            duration_ms = max([s["end_ms"] for s in segs], default=0)
        now = iso()
        self._set(
            row["id"],
            {
                "status": "ready", "error": None, "engine": engine, "model": model,
                "duration_ms": duration_ms,
                "started_at": row["started_at"] or row["created_at"],
                "ended_at": row["ended_at"] or now,
                "capture_status": capture_status or row["capture_status"] or "complete",
            },
        )  # fmt: skip
        self.write_files(row["id"])
        self.bus.publish(row["id"], {"type": "status", "status": "ready", "lag_ms": 0})
        self.emit("transcript.ready", row["id"])
        if self.after_ready is not None:
            self._spawn(lambda: self.after_ready(row["id"]))  # type: ignore[misc]

    def fail(self, meeting_id: str, error: str) -> None:
        self.set_status(meeting_id, "failed", error=error)

    def _spawn(self, make: Callable[[], Awaitable[None]]) -> None:
        """Start a background task on the app loop; safe from sync endpoint threads."""

        def start() -> None:
            task = asyncio.get_running_loop().create_task(make())  # type: ignore[arg-type]
            self._background.add(task)
            task.add_done_callback(self._background.discard)

        self.bus.run_on_loop(start)

    async def drain_background(self) -> None:
        await asyncio.gather(*self._background, return_exceptions=True)

    # --- files ---

    def write_files(self, meeting_id: str) -> None:
        """(Re)write transcript.json/md, meta.json and the notes files atomically."""
        row = self.require(meeting_id)
        directory = self.session_path(row)
        segs = self.segments(row["id"])
        engine, model = row["engine"] or "", row["model"] or ""
        doc = transcripts.canonical_json(segs, engine, model, row["updated_at"])
        atomic_write_json(directory / "transcript.json", doc)
        atomic_write(
            directory / "transcript.md",
            transcripts.render_md(row["title"], engine, model, segs, row["capture_status"]),
        )
        tracks = [
            {**t, "capture_status": "complete" if t["status"] in ("live", "complete") else t["status"]}
            for t in self.tracks(row["id"])
        ]
        write_meta(
            directory,
            started=row["started_at"],
            ended=row["ended_at"],
            duration_ms=row["duration_ms"],
            status=row["capture_status"] or "complete",
            tracks=tracks,
        )
        if row["ai_notes_md"]:
            atomic_write(directory / "notes.md", row["ai_notes_md"])
        if row["user_notes_md"]:
            atomic_write(directory / "user-notes.md", row["user_notes_md"])

    # --- notes ---

    def save_notes(self, meeting_id: str, markdown: str, source: str) -> bool:
        """Store AI notes; returns True when these are the first notes (``note.generated``)."""
        if source not in ("agent", "user", "llm"):
            raise bad_request("source must be agent, user or llm")
        row = self.require(meeting_id)
        first = not row["ai_notes_md"]
        self._set(row["id"], {"ai_notes_md": markdown, "ai_notes_source": source})
        atomic_write(self.session_path(row) / "notes.md", markdown)
        self.bus.publish(row["id"], {"type": "notes", "source": source})
        self.emit("notes.ready", row["id"], source=source, first_notes=first)
        return first
