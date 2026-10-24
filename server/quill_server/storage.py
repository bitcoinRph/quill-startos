"""Session directories (spec section 4): naming, meta.json, crash-safe WAV.

``meta.json`` is the shared contract with the desktop apps. The writer always
emits schema v2; the reader also understands v1, exactly like
``SessionMeta.readInputs`` in the macOS app.
"""

from __future__ import annotations

import json
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from .util import atomic_write_json

SAMPLE_RATE = 16000
WAV_HEADER_BYTES = 44


def session_dir_name(created: datetime, meeting_id: str) -> str:
    return f"{created.strftime('%Y.%m.%d-%H%M')}-{meeting_id[-8:].lower()}"


def create_session_dir(sessions_dir: Path, created: datetime, meeting_id: str) -> Path:
    path = sessions_dir / session_dir_name(created, meeting_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_session_dir(path: Path, sessions_dir: Path) -> None:
    """Delete a session directory, refusing anything outside ``sessions_dir``."""
    resolved = path.resolve()
    if sessions_dir.resolve() not in resolved.parents:
        raise ValueError(f"{path} is not inside the sessions directory")
    shutil.rmtree(resolved, ignore_errors=True)


def safe_child(directory: Path, name: str) -> Path:
    """Join a client-supplied file name to ``directory`` without escaping it."""
    base = Path(name.replace("\\", "/")).name
    if not base or base.startswith("."):
        raise ValueError(f"unsafe file name: {name!r}")
    return directory / base


# --- WAV ---------------------------------------------------------------------


def _wav_header(data_bytes: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_bytes)
    )


class WavWriter:
    """Append-only mono PCM16 WAV whose header can be refreshed while open.

    The header is rewritten by :meth:`update_header` on a timer, so a crash
    loses at most one interval of header accuracy; :func:`repair_wav` fixes the
    sizes from the file length afterwards.
    """

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
        self.path = path
        self.sample_rate = sample_rate
        self.data_bytes = 0
        self._fh: BinaryIO | None = open(path, "wb")
        self._fh.write(_wav_header(0, sample_rate))
        self._fh.flush()

    @property
    def frames(self) -> int:
        return self.data_bytes // 2

    def write(self, pcm: bytes) -> None:
        if self._fh is None:
            raise ValueError("WAV writer is closed")
        usable = len(pcm) - (len(pcm) % 2)
        if usable:
            self._fh.write(pcm[:usable])
            self.data_bytes += usable

    def update_header(self) -> None:
        if self._fh is None:
            return
        self._fh.flush()
        end = self._fh.tell()
        self._fh.seek(0)
        self._fh.write(_wav_header(self.data_bytes, self.sample_rate))
        self._fh.seek(end)
        self._fh.flush()

    def close(self) -> None:
        if self._fh is None:
            return
        self.update_header()
        self._fh.close()
        self._fh = None


def repair_wav(path: Path) -> int:
    """Rewrite a WAV header from the real file size; returns the sample count."""
    size = path.stat().st_size
    if size < WAV_HEADER_BYTES:
        return 0
    data = (size - WAV_HEADER_BYTES) // 2 * 2
    with open(path, "r+b") as fh:
        fh.write(_wav_header(data))
        fh.truncate(WAV_HEADER_BYTES + data)
    return data // 2


# --- meta.json ---------------------------------------------------------------


@dataclass(frozen=True)
class TrackInput:
    """One audio file to transcribe, who speaks on it, and its session offset."""

    file: str
    speaker: str
    offset_ms: int
    track_id: str = ""
    kind: str = "upload"


class MetaError(Exception):
    pass


def read_inputs(session_dir: Path) -> tuple[list[TrackInput], str | None]:
    """Parse a Quill ``meta.json`` (v1 or v2) into ordered transcription inputs.

    Returns the inputs and the capture status (None for v1, which predates
    completeness tracking).
    """
    meta_path = session_dir / "meta.json"
    try:
        doc = json.loads(meta_path.read_text("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("meta.json is not an object")
    except (OSError, ValueError) as exc:
        raise MetaError(f"can't parse {meta_path}: {exc}") from exc

    if int(doc.get("schema_version") or 0) >= 2:
        inputs: list[TrackInput] = []
        tracks = doc.get("tracks") or []
        kinds = [str(t.get("kind") or "track") for t in tracks]
        for ti, track in enumerate(tracks):
            # Desktop sessions have one track per kind ("mic", "system"); keep those as ids.
            tid = str(track.get("track_id") or (kinds[ti] if kinds.count(kinds[ti]) == 1 else f"{kinds[ti]}-{ti}"))
            for seg in track.get("segments") or []:
                inputs.append(
                    TrackInput(
                        file=str(seg["file"]),
                        speaker=str(track.get("label") or track.get("speaker") or "them"),
                        offset_ms=int(seg.get("start_offset_ms") or 0),
                        track_id=tid,
                        kind=str(track.get("kind") or "upload"),
                    )
                )
        return inputs, doc.get("status")

    files = doc.get("files")
    if not isinstance(files, dict):
        raise MetaError(f"can't parse {meta_path}: no files map")
    offsets = doc.get("start_offset_ms") if isinstance(doc.get("start_offset_ms"), dict) else {}
    speakers = {"mic": "me", "system": "them"}
    inputs = [
        TrackInput(str(files[kind]), speakers[kind], int(offsets.get(kind) or 0), kind, kind)
        for kind in ("mic", "system")
        if kind in files
    ]
    return inputs, None


def write_meta(
    session_dir: Path,
    *,
    started: str | None,
    ended: str | None,
    duration_ms: int,
    status: str,
    tracks: list[dict[str, Any]],
) -> None:
    """Write schema v2 ``meta.json`` atomically. ``tracks`` rows come from the DB."""
    out_tracks = []
    for t in tracks:
        entry: dict[str, Any] = {
            "track_id": t["id"],
            "kind": t["kind"],
            "speaker": t["speaker"],
            "status": t.get("capture_status", "complete"),
            "segments": [
                {
                    "file": s["file"],
                    "start_offset_ms": s["start_offset_ms"],
                    "end_offset_ms": s["end_offset_ms"],
                    "frames_written": s["frames_written"],
                    "sample_rate_hz": s.get("sample_rate_hz", SAMPLE_RATE),
                    "channels": s.get("channels", 1),
                }
                for s in t["segments"]
            ],
            "interruptions": [],
            "warnings": [],
        }
        if t.get("label"):
            entry["label"] = t["label"]
        if t.get("participant_id"):
            entry["participant_id"] = t["participant_id"]
        out_tracks.append(entry)
    atomic_write_json(
        session_dir / "meta.json",
        {
            "schema_version": 2,
            "started": started or "",
            "ended": ended or "",
            "duration_seconds": round(duration_ms / 1000),
            "status": status,
            "tracks": out_tracks,
        },
    )


AUDIO_EXTENSIONS = ("wav", "ogg", "caf", "m4a", "mp3", "flac", "opus", "webm")


def next_segment_file(session_dir: Path, base: str, ext: str = "wav") -> str:
    """``base.wav``, then ``base-002.wav``, ... never reusing a name, even after
    a finished segment was transcoded to another extension."""
    n = 1
    while True:
        stem = base if n == 1 else f"{base}-{n:03d}"
        if not any((session_dir / f"{stem}.{e}").exists() for e in AUDIO_EXTENSIONS):
            return f"{stem}.{ext}"
        n += 1
