"""Import a Quill desktop session (``meta.json`` v1/v2, audio, optional transcript)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..errors import bad_request
from ..state import AppState
from ..storage import MetaError, SAMPLE_RATE, read_inputs, safe_child
from ..util import normalize_iso
from .deps import get_state, meeting_view, require_auth, save_upload

router = APIRouter(prefix="/api/v1/imports", tags=["imports"], dependencies=[Depends(require_auth)])


def _iso_field(meta: Any, key: str) -> str | None:
    """A meta.json timestamp as stored UTC ISO, or None when absent or unparseable."""
    if isinstance(meta, dict) and meta.get(key):
        try:
            return normalize_iso(str(meta[key]))
        except ValueError:
            return None
    return None


async def _read_json_part(upload: UploadFile, name: str) -> Any:
    try:
        return json.loads((await upload.read()).decode("utf-8"))
    except ValueError as exc:
        raise bad_request(f"{name} is not valid JSON") from exc


@router.post("/quill", status_code=201)
async def import_quill(
    file: list[UploadFile] = File(...),
    title: str | None = Form(None),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Parts are matched by file name: ``meta.json``, ``transcript.json`` and the segment files.

    With ``transcript.json`` the transcript is ingested as is; otherwise the audio
    is transcribed in the background. ``.caf`` (AAC) segments are decoded by ffmpeg.
    """
    by_name = {(u.filename or "").rsplit("/", 1)[-1]: u for u in file}
    if "meta.json" not in by_name:
        raise bad_request("a part named meta.json is required")
    meta = await _read_json_part(by_name["meta.json"], "meta.json")
    transcript = await _read_json_part(by_name["transcript.json"], "transcript.json") if "transcript.json" in by_name else None

    started, ended = _iso_field(meta, "started"), _iso_field(meta, "ended")
    meta_duration_ms = int(float(meta.get("duration_seconds") or 0) * 1000) if isinstance(meta, dict) else 0
    row = state.meetings.create(title or (f"Imported session {started[:16].replace('T', ' ')}" if started else "Imported session"),
                                "import", started_at=started)  # fmt: skip
    directory = state.meetings.session_path(row)
    try:
        for name, upload in by_name.items():
            if name in ("meta.json", "transcript.json"):
                continue
            await save_upload(upload, safe_child(directory, name))
        (directory / "meta.json").write_text(json.dumps(meta))
        inputs, capture = read_inputs(directory)
    except (MetaError, ValueError) as exc:
        state.meetings.delete(row["id"])
        raise bad_request(str(exc), "invalid_session") from exc

    present = [i for i in inputs if (directory / i.file).is_file()]
    if not present and transcript is None:
        state.meetings.delete(row["id"])
        raise bad_request("none of the audio files named in meta.json were uploaded", "no_audio")

    tracks: dict[str, dict[str, Any]] = {}
    for item in present:
        t = tracks.setdefault(item.track_id, {"kind": item.kind, "speaker": item.speaker, "segments": []})
        t["segments"].append(
            {"file": item.file, "start_offset_ms": item.offset_ms, "end_offset_ms": item.offset_ms,
             "frames_written": 0, "sample_rate_hz": SAMPLE_RATE, "channels": 1}
        )  # fmt: skip
    for track_id, t in tracks.items():
        first, *rest = t["segments"]
        state.meetings.add_uploaded_track(row["id"], track_id, t["speaker"], first["file"], kind=t["kind"])
        state.meetings.save_track_segments(row["id"], track_id, [first, *rest], "complete", None)
    state.meetings.db.execute(
        "UPDATE meetings SET capture_status=?, ended_at=? WHERE id=?", (capture, ended, row["id"])
    )

    if transcript is not None:
        _ingest_transcript(state, row["id"], transcript, tracks, meta_duration_ms)
    else:
        state.jobs.queue_transcription(row["id"])
    return meeting_view(state, state.meetings.require(row["id"]))


def _ingest_transcript(
    state: AppState, meeting_id: str, doc: Any, tracks: dict[str, dict[str, Any]], meta_duration_ms: int
) -> None:
    if not isinstance(doc, dict) or not isinstance(doc.get("segments"), list):
        state.meetings.delete(meeting_id)
        raise bad_request("transcript.json needs a segments array", "invalid_transcript")
    by_speaker = {t["speaker"]: tid for tid, t in tracks.items()}
    items = []
    try:
        for seg in doc["segments"]:
            speaker = str(seg["speaker"])
            items.append(
                {
                    "track_id": str(seg.get("track") or by_speaker.get(speaker, "")),
                    "speaker": speaker, "start_ms": int(seg["start_ms"]), "end_ms": int(seg["end_ms"]),
                    "text": str(seg["text"]), "words": seg.get("words") or None,
                }
            )  # fmt: skip
    except (KeyError, TypeError, ValueError) as exc:
        state.meetings.delete(meeting_id)
        raise bad_request(f"bad segment in transcript.json: {exc}", "invalid_transcript") from exc
    state.meetings.replace_segments(meeting_id, items)
    state.meetings.complete(
        meeting_id, engine=str(doc.get("engine") or "imported"), model=str(doc.get("model") or "unknown"),
        duration_ms=max(meta_duration_ms, max((i["end_ms"] for i in items), default=0)),
    )  # fmt: skip
