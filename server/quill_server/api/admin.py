"""Keys, webhooks, templates and folders."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from ..errors import bad_request, not_found
from ..state import AppState
from ..webhooks import NATIVE_EVENTS
from .deps import get_state, require_auth, require_session

router = APIRouter(prefix="/api/v1", tags=["admin"])


# --- API keys (cookie session only) ---


class NewKey(BaseModel):
    name: str = ""


@router.get("/keys", dependencies=[Depends(require_session)])
def list_keys(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"keys": state.auth.list_keys()}


@router.post("/keys", status_code=201, dependencies=[Depends(require_session)])
def create_key(body: NewKey, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """The secret is in this response only; only its SHA-256 is stored."""
    public, secret = state.auth.create_key(body.name)
    return {**public, "key": secret}


@router.delete("/keys/{key_id}", status_code=204, dependencies=[Depends(require_session)])
def revoke_key(key_id: str, state: AppState = Depends(get_state)) -> Response:
    state.auth.revoke_key(key_id)
    return Response(status_code=204)


# --- webhooks ---


class NewWebhook(BaseModel):
    url: str
    events: list[str] | None = None


def valid_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise bad_request("url must be an http(s) URL")
    return url


def _webhook_view(ep: dict[str, Any], with_secret: bool = False) -> dict[str, Any]:
    out = {k: ep[k] for k in ("id", "url", "events", "created_at")}
    out["active"] = ep["enabled"]
    if with_secret:
        out["secret"] = ep["secret"]
    return out


@router.get("/webhooks", dependencies=[Depends(require_auth)])
def list_webhooks(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"webhooks": [_webhook_view(w) for w in state.webhooks.list_by_flavor("quill")]}


@router.post("/webhooks", status_code=201, dependencies=[Depends(require_auth)])
def create_webhook(body: NewWebhook, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """The signing secret (``whsec_...``) is in this response only."""
    events = body.events or list(NATIVE_EVENTS)
    unknown = [e for e in events if e not in NATIVE_EVENTS]
    if unknown:
        raise bad_request(f"unknown events: {', '.join(unknown)}; valid: {', '.join(NATIVE_EVENTS)}")
    return _webhook_view(state.webhooks.create(valid_url(body.url), events), with_secret=True)


@router.delete("/webhooks/{webhook_id}", status_code=204, dependencies=[Depends(require_auth)])
def delete_webhook(webhook_id: str, state: AppState = Depends(get_state)) -> Response:
    if not state.webhooks.delete(webhook_id, "quill"):
        raise not_found("webhook")
    return Response(status_code=204)


# --- templates ---


class TemplateBody(BaseModel):
    name: str
    prompt: str


@router.get("/templates", dependencies=[Depends(require_auth)])
def list_templates(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"templates": [{**t, "builtin": bool(t["builtin"])} for t in state.notes.list_templates()]}


@router.post("/templates", status_code=201, dependencies=[Depends(require_auth)])
def create_template(body: TemplateBody, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return state.notes.save_template(None, body.name, body.prompt)


@router.put("/templates/{template_id}", dependencies=[Depends(require_auth)])
def update_template(template_id: str, body: TemplateBody, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return state.notes.save_template(template_id, body.name, body.prompt)


@router.delete("/templates/{template_id}", status_code=204, dependencies=[Depends(require_auth)])
def delete_template(template_id: str, state: AppState = Depends(get_state)) -> Response:
    state.notes.delete_template(template_id)
    return Response(status_code=204)


# --- folders ---


class NewFolder(BaseModel):
    name: str
    parent_folder_id: str | None = None


@router.get("/folders", dependencies=[Depends(require_auth)])
def list_folders(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"folders": state.folders.list_all()}


@router.post("/folders", status_code=201, dependencies=[Depends(require_auth)])
def create_folder(body: NewFolder, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return state.folders.create(body.name, body.parent_folder_id)


@router.delete("/folders/{folder_id}", status_code=204, dependencies=[Depends(require_auth)])
def delete_folder(folder_id: str, state: AppState = Depends(get_state)) -> Response:
    state.folders.delete(folder_id)
    return Response(status_code=204)


@router.post("/folders/{folder_id}/meetings/{meeting_id}", status_code=204, dependencies=[Depends(require_auth)])
def add_to_folder(folder_id: str, meeting_id: str, state: AppState = Depends(get_state)) -> Response:
    state.folders.add_meeting(folder_id, state.meetings.require(meeting_id)["id"])
    return Response(status_code=204)


@router.delete("/folders/{folder_id}/meetings/{meeting_id}", status_code=204, dependencies=[Depends(require_auth)])
def remove_from_folder(folder_id: str, meeting_id: str, state: AppState = Depends(get_state)) -> Response:
    state.folders.remove_meeting(folder_id, state.meetings.require(meeting_id)["id"])
    return Response(status_code=204)
