"""Granola-compatible API (spec section 5.1): `/v1/notes`, `/v1/folders`, ...

Mirrors the field shapes of Granola's public OpenAPI document so a client built
for Granola only changes its base URL and key. Quill ids already have Granola's
shape (``not_``, ``fol_``, ``whe_`` + 14 base62 characters).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..errors import ApiError, bad_request, not_found
from ..folders import canonical_folder_id
from ..meetings import Row, decode_cursor, encode_cursor
from ..state import AppState
from ..util import normalize_id, parse_iso
from ..webhooks import GRANOLA_EVENTS
from .deps import get_state, require_auth

router = APIRouter(tags=["granola"], dependencies=[Depends(require_auth)])

MAX_INLINE_TRANSCRIPT = 2000
Scope = Literal["personal", "public", "workspace"]
GranolaEvent = Literal["note.access_granted", "note.edited", "note.generated"]


# --- id handling ---


def _note_id(value: str) -> str:
    cid = normalize_id(value, "not")
    if cid is None:
        raise bad_request("note_id must look like not_ followed by 14 letters or digits")
    return cid


def _folder_id(value: str) -> str:
    cid = canonical_folder_id(value)
    if cid is None:
        raise bad_request("folder ids look like fol_ followed by 14 letters or digits")
    return cid


# --- shapes ---


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _plain(markdown: str) -> str:
    """Markdown to plain text for `summary_text` / `private_notes_text`."""
    text = re.sub(r"```[a-zA-Z0-9]*\n?", "", markdown)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|`|~~)", "", text)
    text = re.sub(r"(?<![\w*])[*_]([^*_\n]+)[*_](?![\w*])", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _owner(state: AppState) -> dict[str, Any]:
    return {"name": state.config.speaker_name, "email": state.config.owner_email}


def _summary(state: AppState, row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "object": "note",
        "title": row["title"],
        "owner": _owner(state),
        "created_at": row.get("sort_ts") or row["started_at"] or row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": None,
    }


def _folder(f: Row) -> dict[str, Any]:
    return {
        "id": f["id"], "object": "folder", "name": f["name"],
        "parent_folder_id": f["parent_folder_id"], "space_id": None,
    }  # fmt: skip


def _attendees(state: AppState, row: Row) -> list[dict[str, Any]]:
    people = [{"name": a["name"] or None, "email": a["email"]} for a in state.meetings.view(row)["attendees"]]
    seen = {(p["name"] or "").lower() for p in people}
    for track in state.meetings.tracks(row["id"]):
        name = track["label"] or track["speaker"]
        if track["kind"] == "participant" and name and name.lower() not in seen and name != state.config.bot_name:
            seen.add(name.lower())
            people.append({"name": name, "email": ""})
    return people


def _calendar_event(row: Row, attendees: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not row["calendar_url"]:
        return None
    return {
        "event_title": row["title"],
        "invitees": [{"email": a["email"]} for a in attendees if a["email"]],
        "organiser": None,
        "calendar_event_id": row["calendar_url"],
        "scheduled_start_time": row["started_at"],
        "scheduled_end_time": row["ended_at"],
    }


def _speaker(state: AppState, kind: str | None, name: str) -> dict[str, Any]:
    if kind is None:  # transcripts ingested without a track: infer from the desktop labels
        kind = {"me": "mic", "them": "system"}.get(name.lower(), "participant")
    if kind == "mic":
        return {"source": "microphone", "attribution": "me"}
    if kind == "system":
        return {"source": "speaker", "attribution": "them"}
    if kind == "upload":
        return {"source": "speaker", "diarization_label": name}
    return {
        "source": "speaker",
        "name": name,
        "attribution": "me" if name == state.config.speaker_name else "them",
    }


def _transcript_items(state: AppState, row: Row) -> list[dict[str, Any]]:
    kinds = {t["id"]: t["kind"] for t in state.meetings.tracks(row["id"])}
    base = parse_iso(row["started_at"] or row["created_at"])
    return [
        {
            "speaker": _speaker(state, kinds.get(s["track_id"]), s["speaker"]),
            "text": s["text"],
            "start_time": _stamp(base + timedelta(milliseconds=s["start_ms"])),
            "end_time": _stamp(base + timedelta(milliseconds=s["end_ms"])),
        }
        for s in state.meetings.segments(row["id"])
    ]


def _note(state: AppState, row: Row, with_transcript: bool) -> dict[str, Any]:
    attendees = _attendees(state, row)
    notes, mine = row["ai_notes_md"], row["user_notes_md"]
    transcript = None
    if with_transcript:
        transcript = _transcript_items(state, row)
        if len(transcript) > MAX_INLINE_TRANSCRIPT:
            raise ApiError(
                413, "TRANSCRIPT_TOO_LARGE",
                f"the transcript has {len(transcript)} items; page through /v1/notes/{row['id']}/transcript",
            )  # fmt: skip
    return {
        **_summary(state, row),
        "web_url": f"{state.config.public_url}/#/meetings/{row['id']}",
        "calendar_event": _calendar_event(row, attendees),
        "attachments": [],
        "attendees": attendees,
        "folder_membership": [_folder(f) for f in state.folders.of_meeting(row["id"], with_ancestors=True)],
        "space_membership": [],
        "summary_text": _plain(notes) if notes else "",
        "summary_markdown": notes or None,
        "private_notes_text": _plain(mine) if mine else None,
        "private_notes_markdown": mine or None,
        "transcript": transcript,
    }


# --- notes ---

PageSize30 = Annotated[int, Query(ge=1, le=30)]


@router.get("/v1/notes")
def list_notes(
    created_before: str | None = None,
    created_after: str | None = None,
    updated_after: str | None = None,
    folder_id: str | None = None,
    space_id: Annotated[str | None, Query(pattern=r"^[Ss][Pp][Aa]_[a-zA-Z0-9]{14}$")] = None,
    cursor: str | None = None,
    page_size: PageSize30 = 10,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    if space_id:  # Quill has no spaces, so no note is in one
        return {"notes": [], "hasMore": False, "cursor": None}
    rows, next_cursor = state.meetings.list_meetings(
        status="ready", folder_id=_folder_id(folder_id) if folder_id else None, created_after=created_after,
        created_before=created_before, updated_after=updated_after, limit=page_size, cursor=cursor,
    )  # fmt: skip
    return {"notes": [_summary(state, r) for r in rows], "hasMore": next_cursor is not None, "cursor": next_cursor}


@router.get("/v1/notes/{note_id}")
def get_note(
    note_id: str, include: Literal["transcript"] | None = None, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    row = state.meetings.row(_note_id(note_id))
    if row is None:
        raise not_found("note")
    return _note(state, row, include == "transcript")


@router.get("/v1/notes/{note_id}/transcript")
def get_note_transcript(
    note_id: str,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    row = state.meetings.row(_note_id(note_id))
    if row is None:
        raise not_found("note")
    start = 0
    if cursor:
        (start,) = decode_cursor(cursor, 1)
        if not isinstance(start, int) or start < 0:
            raise bad_request("invalid cursor", "bad_cursor")
    items = _transcript_items(state, row)
    page = items[start : start + page_size]
    more = start + page_size < len(items)
    return {"transcript": page, "hasMore": more, "cursor": encode_cursor(start + page_size) if more else None}


# --- folders and spaces ---


@router.get("/v1/folders")
def list_folders(
    cursor: str | None = None, page_size: PageSize30 = 10, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    folders = state.folders.list_all()  # alphabetical, case-insensitive
    start = 0
    if cursor:
        key, last_id = decode_cursor(cursor, 2)
        start = next((i + 1 for i, f in enumerate(folders) if (f["name"].lower(), f["id"]) == (key, last_id)), len(folders))
    page = folders[start : start + page_size]
    more = start + page_size < len(folders)
    last = page[-1] if page else None
    return {
        "folders": [_folder(f) for f in page],
        "hasMore": more,
        "cursor": encode_cursor(last["name"].lower(), last["id"]) if more and last else None,
    }


@router.get("/v1/spaces")
def list_spaces(cursor: str | None = None, page_size: PageSize30 = 10) -> dict[str, Any]:
    return {"spaces": [], "hasMore": False, "cursor": None}


# --- webhook endpoints ---


class CreateEndpoint(BaseModel):
    url: str
    scopes: list[Scope] = Field(min_length=1)
    events: list[GranolaEvent] = Field(default_factory=lambda: list(GRANOLA_EVENTS), min_length=1)
    folder_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


class UpdateEndpoint(BaseModel):
    url: str | None = None
    scopes: list[Scope] | None = Field(default=None, min_length=1)
    events: list[GranolaEvent] | None = Field(default=None, min_length=1)
    folder_ids: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None


def _endpoint(state: AppState, ep: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ep["id"],
        "object": "webhook_endpoint",
        "url": ep["url"],
        "url_redacted": False,
        "events": ep["events"],
        "folder_ids": ep["folder_ids"],
        "scopes": ep["scopes"],
        "created_by": _owner(state),
        "enabled": ep["enabled"],
        "created_at": ep["created_at"],
    }


def _check_url(url: str) -> str:
    from .admin import valid_url

    return valid_url(url)


def _check_folders(state: AppState, folder_ids: list[str]) -> list[str]:
    return [state.folders.require(_folder_id(f))["id"] for f in folder_ids]


def _endpoint_or_404(state: AppState, endpoint_id: str) -> dict[str, Any]:
    ep = state.webhooks.get(endpoint_id, "granola")
    if ep is None:
        raise not_found("webhook endpoint")
    return ep


@router.post("/v1/webhook-endpoints", status_code=201)
def create_endpoint(body: CreateEndpoint, state: AppState = Depends(get_state)) -> dict[str, Any]:
    folder_ids = _check_folders(state, body.folder_ids or [])
    ep = state.webhooks.create(
        _check_url(body.url), list(body.events), flavor="granola", scopes=list(body.scopes), folder_ids=folder_ids
    )
    return {**_endpoint(state, ep), "signing_secret": ep["secret"]}


@router.get("/v1/webhook-endpoints")
def list_endpoints(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"webhook_endpoints": [_endpoint(state, e) for e in state.webhooks.list_by_flavor("granola")]}


@router.patch("/v1/webhook-endpoints/{endpoint_id}")
def update_endpoint(endpoint_id: str, body: UpdateEndpoint, state: AppState = Depends(get_state)) -> dict[str, Any]:
    ep = _endpoint_or_404(state, endpoint_id)
    fields: dict[str, Any] = {}
    if body.url is not None:
        fields["url"] = _check_url(body.url)
    if body.scopes is not None:
        fields["scopes"] = list(body.scopes)
    if body.events is not None:
        fields["events"] = list(body.events)
    if body.folder_ids is not None:
        fields["folder_ids"] = _check_folders(state, body.folder_ids)
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    state.webhooks.update(ep["id"], **fields)
    return _endpoint(state, _endpoint_or_404(state, ep["id"]))


@router.delete("/v1/webhook-endpoints/{endpoint_id}")
def delete_endpoint(endpoint_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    ep = _endpoint_or_404(state, endpoint_id)
    state.webhooks.delete(ep["id"], "granola")
    return {"id": ep["id"], "object": "webhook_endpoint", "deleted": True}
