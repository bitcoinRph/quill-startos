"""Meetings: CRUD, audio, transcripts, events, notes."""

from __future__ import annotations

import json
import mimetypes
from typing import Annotated, Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import transcripts
from ..errors import ApiError, bad_request, conflict, not_found
from ..state import AppState
from ..storage import safe_child
from ..util import slug
from .deps import get_state, meeting_view, parse_include, require_auth, require_auth_or_internal, save_upload

_PREFIX = "/api/v1/meetings"
authed = APIRouter(prefix=_PREFIX, tags=["meetings"], dependencies=[Depends(require_auth)])
# The bot may stop its own meeting over REST when its page cannot send `stop`.
bot_ok = APIRouter(prefix=_PREFIX, tags=["meetings"], dependencies=[Depends(require_auth_or_internal)])
router = APIRouter()

Limit = Annotated[int, Query(ge=1, le=100)]


class CreateMeeting(BaseModel):
    title: str | None = None
    source: str = "api"
    attendees: list[Any] | None = None
    calendar_url: str | None = None


class PatchMeeting(BaseModel):
    title: str | None = None
    user_notes_md: str | None = None
    attendees: list[Any] | None = None
    calendar_url: str | None = None
    speakers: dict[str, str] | None = None


class GenerateNotes(BaseModel):
    template_id: str | None = None


class SaveNotes(BaseModel):
    markdown: str
    source: Literal["agent", "user", "llm"] = "user"


@authed.get("")
def list_meetings(
    q: str | None = None,
    status: str | None = None,
    source: str | None = None,
    folder_id: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    rows, next_cursor = state.meetings.list_meetings(
        q=q, status=status, source=source, folder_id=folder_id, created_after=created_after,
        created_before=created_before, limit=limit, cursor=cursor,
    )  # fmt: skip
    return {
        "meetings": [meeting_view(state, r) for r in rows],
        "has_more": next_cursor is not None,
        "cursor": next_cursor,
    }


@authed.post("", status_code=201)
def create_meeting(body: CreateMeeting, state: AppState = Depends(get_state)) -> dict[str, Any]:
    row = state.meetings.create(body.title, body.source, body.attendees, body.calendar_url)
    return meeting_view(state, row)


@authed.get("/{meeting_id}")
def get_meeting(meeting_id: str, include: str | None = None, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return meeting_view(state, state.meetings.require(meeting_id), parse_include(include))


@authed.patch("/{meeting_id}")
def patch_meeting(meeting_id: str, body: PatchMeeting, state: AppState = Depends(get_state)) -> dict[str, Any]:
    row = state.meetings.update(
        meeting_id, title=body.title, user_notes_md=body.user_notes_md, attendees=body.attendees,
        calendar_url=body.calendar_url,
    )  # fmt: skip
    if body.speakers:
        row = state.meetings.rename_speakers(meeting_id, body.speakers)
    return meeting_view(state, row)


@authed.delete("/{meeting_id}", status_code=204)
async def delete_meeting(meeting_id: str, state: AppState = Depends(get_state)) -> Response:
    row = state.meetings.require(meeting_id)
    session = state.hub.sessions.pop(row["id"], None)
    if session is not None:
        session.abandon()
    state.meetings.delete(row["id"])
    return Response(status_code=204)


@bot_ok.post("/{meeting_id}/stop", status_code=202)
async def stop_meeting(meeting_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    row = state.meetings.require(meeting_id)
    await state.hub.stop(row["id"])
    return meeting_view(state, state.meetings.require(row["id"]))


@authed.post("/{meeting_id}/audio", status_code=202)
async def upload_audio(
    meeting_id: str,
    file: list[UploadFile] = File(...),
    speaker: list[str] = Form(default_factory=list),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """One track per file; ``speaker`` values pair with files by position."""
    row = state.meetings.require(meeting_id)
    if row["status"] == "live" or row["id"] in state.hub.sessions:
        raise conflict("the meeting is live; stop it before uploading audio", "meeting_live")
    directory = state.meetings.session_path(row)
    existing = {t["id"] for t in state.meetings.tracks(row["id"])}
    added = 0
    for i, upload in enumerate(file):
        n = 1
        while f"u{n}" in existing:
            n += 1
        track_id = f"u{n}"
        existing.add(track_id)
        original = upload.filename or f"audio-{n}"
        stem, dot, ext = original.rpartition(".")
        name = f"{track_id}-{slug(stem if dot else original)}.{slug(ext) if dot else 'bin'}"
        await save_upload(upload, safe_child(directory, name))
        label = speaker[i].strip() if i < len(speaker) and speaker[i].strip() else (stem or original)
        state.meetings.add_uploaded_track(row["id"], track_id, label, name)
        added += 1
    if not added:
        raise bad_request("no files uploaded")
    state.jobs.queue_transcription(row["id"])
    return meeting_view(state, state.meetings.require(row["id"]))


@authed.get("/{meeting_id}/audio/{track_id}")
def get_audio(meeting_id: str, track_id: str, segment: int = 0, state: AppState = Depends(get_state)) -> FileResponse:
    """Serves one segment file of a track. ``X-Quill-Offset-Ms`` is where it starts on the session clock."""
    row = state.meetings.require(meeting_id)
    track = next((t for t in state.meetings.tracks(row["id"]) if t["id"] == track_id), None)
    if track is None or not 0 <= segment < len(track["segments"]):
        raise not_found("track")
    seg = track["segments"][segment]
    path = state.meetings.session_path(row) / seg["file"]
    if not path.is_file():
        raise ApiError(404, "audio_unavailable", "the audio for this track was deleted or never saved")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"X-Quill-Offset-Ms": str(seg["start_offset_ms"])})


@authed.get("/{meeting_id}/transcript")
def get_transcript(
    meeting_id: str,
    format: Literal["json", "md", "txt", "srt", "vtt", "compact"] = "json",
    since_seq: int | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    speaker: str | None = None,
    state: AppState = Depends(get_state),
) -> Response:
    row = state.meetings.require(meeting_id)
    segs = transcripts.filter_segments(
        state.meetings.segments(row["id"], by_seq=since_seq is not None),
        since_seq=since_seq, start_ms=start_ms, end_ms=end_ms, speaker=speaker,
    )  # fmt: skip
    if format == "json":
        doc = transcripts.canonical_json(segs, row["engine"] or "", row["model"] or "", row["updated_at"], with_seq=True)
        doc.update(
            meeting_id=row["id"], status=row["status"],
            next_seq=max([s["seq"] for s in segs], default=since_seq or 0),
        )  # fmt: skip
        return Response(json.dumps(doc, ensure_ascii=False), media_type="application/json")
    text = transcripts.render_text(
        format, segs, title=row["title"], engine=row["engine"] or "", model=row["model"] or "",
        capture_status=row["capture_status"],
    )  # fmt: skip
    return Response(text, media_type=transcripts.CONTENT_TYPES[format])


@authed.get("/{meeting_id}/events")
async def meeting_events(
    meeting_id: str, request: Request, since_seq: int | None = None, state: AppState = Depends(get_state)
) -> EventSourceResponse:
    """SSE of `segment`, `status` and `notes` events. Reconnects replay from ``Last-Event-ID``."""
    row = state.meetings.require(meeting_id)
    last = since_seq
    header = request.headers.get("last-event-id", "")
    if last is None and header.isdigit():
        last = int(header)
    sub = state.bus.subscribe(row["id"])  # subscribe before replaying so nothing is missed

    async def stream() -> AsyncIterator[dict[str, str]]:
        try:
            sent = last or 0
            current = state.meetings.require(row["id"])
            yield {"event": "status", "data": json.dumps({"type": "status", "status": current["status"], "lag_ms": 0})}
            for seg in state.meetings.segments(row["id"], since_seq=sent, by_seq=True) if last is not None else []:
                sent = seg["seq"]
                yield _sse_segment(seg)
            while True:
                event = await sub.get()
                if event is None:
                    return
                if event["type"] == "segment":
                    if event["segment"]["seq"] <= sent:
                        continue
                    sent = event["segment"]["seq"]
                    yield _sse_segment(event["segment"])
                else:
                    yield {"event": event["type"], "data": json.dumps(event)}
        finally:
            sub.close()

    return EventSourceResponse(stream())


def _sse_segment(seg: dict[str, Any]) -> dict[str, str]:
    public = {k: seg[k] for k in ("seq", "track_id", "speaker", "start_ms", "end_ms", "text")}
    return {"event": "segment", "id": str(seg["seq"]), "data": json.dumps({"type": "segment", "segment": public})}


@authed.post("/{meeting_id}/notes/generate")
async def generate_notes(
    meeting_id: str, body: GenerateNotes | None = None, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    markdown = await state.notes.generate(meeting_id, body.template_id if body else None)
    return {"markdown": markdown, "source": "llm"}


@authed.put("/{meeting_id}/notes")
def save_notes(meeting_id: str, body: SaveNotes, state: AppState = Depends(get_state)) -> dict[str, Any]:
    state.meetings.save_notes(meeting_id, body.markdown, body.source)
    return meeting_view(state, state.meetings.require(meeting_id), ["notes"])


@authed.post("/{meeting_id}/retranscribe", status_code=202)
def retranscribe(meeting_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    row = state.meetings.require(meeting_id)
    if row["status"] in ("live", "processing"):
        raise conflict(f"meeting is {row['status']}", "busy")
    directory = state.meetings.session_path(row)
    has_audio = any(
        (directory / seg["file"]).is_file() for t in state.meetings.tracks(row["id"]) for seg in t["segments"]
    )
    if not has_audio:
        raise conflict("no stored audio to transcribe (deleted by retention, or never saved)", "no_audio")
    state.jobs.queue_transcription(row["id"])
    return meeting_view(state, state.meetings.require(row["id"]))


router.include_router(authed)
router.include_router(bot_ok)
