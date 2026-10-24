"""SQLite (WAL) access: schema, migrations, FTS5.

One shared connection guarded by a re-entrant lock. Queries are tiny and WAL
keeps readers cheap, so a pool would add complexity without throughput. Call
sites in async code use it directly; anything slow must not hold a transaction
open across an ``await``.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

MIGRATIONS: list[str] = [
    """
    CREATE TABLE meetings (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'scheduled',
        source TEXT NOT NULL DEFAULT 'api',
        started_at TEXT,
        ended_at TEXT,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        session_dir TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        calendar_url TEXT,
        user_notes_md TEXT NOT NULL DEFAULT '',
        ai_notes_md TEXT NOT NULL DEFAULT '',
        ai_notes_source TEXT,
        attendees_json TEXT NOT NULL DEFAULT '[]',
        error TEXT,
        engine TEXT,          -- ASR provenance of the stored transcript
        model TEXT,
        capture_status TEXT   -- complete | recovered | incomplete (from meta.json)
    );
    CREATE INDEX meetings_status ON meetings(status);

    -- A track id is the client's id ("t1", a Jitsi participant id), unique per meeting.
    -- segments_json lists the audio files written for it (one per registration).
    CREATE TABLE tracks (
        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        id TEXT NOT NULL,
        kind TEXT NOT NULL,
        speaker TEXT NOT NULL,
        label TEXT,
        participant_id TEXT,
        status TEXT NOT NULL DEFAULT 'live',
        started_offset_ms INTEGER NOT NULL DEFAULT 0,
        ended_offset_ms INTEGER,
        segments_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (meeting_id, id)
    );

    CREATE TABLE segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        track_id TEXT NOT NULL DEFAULT '',
        seq INTEGER NOT NULL,
        speaker TEXT NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        text TEXT NOT NULL,
        words_json TEXT,
        UNIQUE (meeting_id, seq)
    );
    CREATE INDEX segments_time ON segments(meeting_id, start_ms);

    CREATE VIRTUAL TABLE segments_fts USING fts5(
        text, content='segments', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER segments_ai AFTER INSERT ON segments BEGIN
        INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER segments_ad AFTER DELETE ON segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER segments_au AFTER UPDATE OF text ON segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
    END;

    CREATE TABLE folders (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        parent_folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE meeting_folders (
        folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        PRIMARY KEY (folder_id, meeting_id)
    );

    CREATE TABLE api_keys (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        prefix TEXT NOT NULL,
        sha256 TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at TEXT
    );

    CREATE TABLE webhooks (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        secret TEXT NOT NULL,
        events_json TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        flavor TEXT NOT NULL DEFAULT 'quill',     -- 'quill' (native) or 'granola' payloads
        scopes_json TEXT NOT NULL DEFAULT '[]',   -- stored for Granola clients, informational
        folder_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE templates (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        builtin INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX jobs_status ON jobs(status);
    """,
]


class Database:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
                self._conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")

    def close(self) -> None:
        """Fold the WAL into quill.db first: backups copy the data directory."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group statements atomically; nested use joins the outer transaction."""
        with self._lock:
            if self._conn.in_transaction:
                yield
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row else None


_TERM_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def fts_query(user_query: str) -> str | None:
    """Turn free text into a safe FTS5 expression.

    Every term is double-quoted (so ``AND``, ``-``, ``:`` or ``*`` typed by a
    user is data, not syntax) and the last one is a prefix match so search
    works while typing. Returns None when nothing searchable remains.
    """
    terms = _TERM_RE.findall(user_query)
    if not terms:
        return None
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    quoted[-1] += "*"
    return " ".join(quoted)
