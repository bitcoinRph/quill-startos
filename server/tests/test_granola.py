"""Conformance of the Granola-compatible API against Granola's own OpenAPI document.

Set GRANOLA_OPENAPI to a copy of https://docs.granola.ai/api-reference/openapi.json;
the tests are skipped when it is missing.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from quill_server.util import parse_iso
from quill_server.webhooks import verify

from .conftest import seed_meeting, state_of, wait_for

OPENAPI = Path(os.environ.get("GRANOLA_OPENAPI", "/nonexistent"))
pytestmark = pytest.mark.skipif(not OPENAPI.is_file(), reason="GRANOLA_OPENAPI is not set to Granola's openapi.json")

DATE_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$")


def _fix_nullable_allof(node: Any) -> Any:
    """Granola writes nullable objects as ``allOf: [X, {type: [object, null]}]``, which no value of
    ``null`` can satisfy because X is an object. Read it as the intended ``X | null``."""
    if isinstance(node, dict):
        parts = node.get("allOf")
        if parts and len(parts) == 2 and parts[1].get("type") == ["object", "null"] and "$ref" in parts[0]:
            return {"anyOf": [_fix_nullable_allof(parts[0]), {"type": "null"}]}
        return {k: _fix_nullable_allof(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_fix_nullable_allof(v) for v in node]
    return node


@pytest.fixture(scope="module")
def spec() -> dict:
    return _fix_nullable_allof(json.loads(OPENAPI.read_text()))


@pytest.fixture
def validate(spec):
    """validate(schema_name, payload): OpenAPI 3.1 is JSON Schema 2020-12, so ``type: [x, "null"]``
    and the internal ``#/components/schemas/...`` references work as written."""

    def check(name: str, payload: Any) -> None:
        schema = {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
        assert not errors, f"{name}: " + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
        _assert_date_times(payload)

    return check


def _assert_date_times(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("created_at", "updated_at", "start_time", "end_time", "deleted_at") and isinstance(v, str):
                assert DATE_TIME.match(v), f"{k}={v!r} is not an RFC 3339 date-time"
                parse_iso(v)
            _assert_date_times(v)
    elif isinstance(node, list):
        for v in node:
            _assert_date_times(v)


def test_validator_is_not_vacuous(validate):
    with pytest.raises(AssertionError):
        validate("Note", {"id": "bad"})
    with pytest.raises(AssertionError):
        validate("ListNotesOutput", {"notes": [], "hasMore": False})  # cursor is required, even when null


def add_track(client, mid: str, track_id: str, kind: str, speaker: str) -> None:
    s = state_of(client)
    s.meetings.add_uploaded_track(mid, track_id, speaker, f"{track_id}.wav", kind=kind)


def mixed_meeting(client) -> str:
    mid = seed_meeting(client, [("x", 0, 1, "placeholder")], "Quarterly sync")
    s = state_of(client)
    for tid, kind, who in (("m", "mic", "me"), ("s", "system", "them"), ("p", "participant", "Alice"),
                           ("me2", "participant", "Me"), ("u", "upload", "Speaker A")):  # fmt: skip
        add_track(client, mid, tid, kind, who)
    rows = [("m", "me", 1000, 2000, "mic words"), ("s", "them", 3000, 4000, "system words"),
            ("p", "Alice", 5000, 6000, "jitsi words"), ("me2", "Me", 7000, 8000, "my jitsi words"),
            ("u", "Speaker A", 9000, 10000, "upload words")]  # fmt: skip
    s.meetings.replace_segments(mid, [{"track_id": t, "speaker": sp, "start_ms": a, "end_ms": b, "text": x} for t, sp, a, b, x in rows])
    s.db.execute("UPDATE meetings SET started_at='2026-03-04T10:00:00Z', calendar_url='cal-123' WHERE id=?", (mid,))
    return mid


def test_list_notes_schema_and_paging(client, validate):
    ids = [seed_meeting(client, [("me", 0, 500, "hi")], f"N{i}") for i in range(4)]
    state_of(client).meetings.create("Scheduled one")  # not ready: never listed
    page = client.get("/v1/notes", params={"page_size": 3}).json()
    validate("ListNotesOutput", page)
    assert len(page["notes"]) == 3 and page["hasMore"] is True and page["cursor"]
    last = client.get("/v1/notes", params={"page_size": 3, "cursor": page["cursor"]}).json()
    validate("ListNotesOutput", last)
    assert len(last["notes"]) == 1 and last["hasMore"] is False and last["cursor"] is None
    assert sorted(n["id"] for n in page["notes"] + last["notes"]) == sorted(ids)
    n = page["notes"][0]
    assert n["object"] == "note" and n["owner"] == {"name": "Me", "email": "owner@example.com"} and n["deleted_at"] is None
    assert len(client.get("/v1/notes").json()["notes"]) == 4  # default page size 10
    assert client.get("/v1/notes", params={"page_size": 31}).status_code == 400
    assert client.get("/v1/notes", params={"page_size": 0}).status_code == 400
    assert client.get("/v1/notes", params={"cursor": "%%%"}).status_code == 400
    assert client.get("/v1/notes", params={"created_after": "2999-01-01"}).json() == {"notes": [], "hasMore": False, "cursor": None}
    assert len(client.get("/v1/notes", params={"updated_after": "2000-01-01T00:00:00Z"}).json()["notes"]) == 4
    assert client.get("/v1/notes", params={"space_id": "spa_8kTq2mWxLp4Rz7"}).json()["notes"] == []
    assert client.get("/v1/notes", params={"space_id": "bad"}).status_code == 400
    assert client.get("/v1/notes", headers={"Authorization": ""}).status_code == 401


def test_list_notes_folder_filter_includes_children(client, validate):
    a = seed_meeting(client, [("me", 0, 500, "a")], "A")
    seed_meeting(client, [("me", 0, 500, "b")], "B")
    parent = client.post("/api/v1/folders", json={"name": "Parent"}).json()["id"]
    child = client.post("/api/v1/folders", json={"name": "Child", "parent_folder_id": parent}).json()["id"]
    client.post(f"/api/v1/folders/{child}/meetings/{a}")
    res = client.get("/v1/notes", params={"folder_id": parent.replace("fol_", "FOL_")}).json()
    validate("ListNotesOutput", res)
    assert [n["id"] for n in res["notes"]] == [a]
    assert client.get("/v1/notes", params={"folder_id": "fol_" + "x" * 14}).status_code == 404
    assert client.get("/v1/notes", params={"folder_id": "nope"}).status_code == 400


def test_get_note_schema_and_mappings(client, validate):
    mid = mixed_meeting(client)
    client.patch(f"/api/v1/meetings/{mid}", json={"attendees": [{"name": "Ann", "email": "ann@example.com"}], "user_notes_md": "- **mine**"})
    client.put(f"/api/v1/meetings/{mid}/notes", json={"markdown": "## Summary\n\nWe **ship** [it](http://x).\n\n- item", "source": "agent"})
    top = client.post("/api/v1/folders", json={"name": "Top"}).json()["id"]
    sub = client.post("/api/v1/folders", json={"name": "Sub", "parent_folder_id": top}).json()["id"]
    client.post(f"/api/v1/folders/{sub}/meetings/{mid}")

    note = client.get(f"/v1/notes/{mid.replace('not_', 'NOT_')}").json()
    validate("Note", note)
    assert note["transcript"] is None and note["id"] == mid and note["title"] == "Quarterly sync"
    assert note["web_url"] == f"https://quill.test/#/meetings/{mid}"
    assert note["attachments"] == [] and note["space_membership"] == []
    assert note["created_at"] == "2026-03-04T10:00:00Z"
    assert note["summary_markdown"].startswith("## Summary") and note["summary_text"] == "Summary\n\nWe ship it.\n\n- item"
    assert note["private_notes_markdown"] == "- **mine**" and note["private_notes_text"] == "- mine"
    assert {a["name"] for a in note["attendees"]} == {"Ann", "Alice", "Me"} and all("email" in a for a in note["attendees"])
    assert [f["id"] for f in note["folder_membership"]] == [sub, top]  # the folder plus its ancestors, by name
    assert next(f for f in note["folder_membership"] if f["id"] == sub)["parent_folder_id"] == top
    cal = note["calendar_event"]
    assert cal["calendar_event_id"] == "cal-123" and cal["invitees"] == [{"email": "ann@example.com"}]

    full = client.get(f"/v1/notes/{mid}", params={"include": "transcript"}).json()
    validate("Note", full)
    speakers = [t["speaker"] for t in full["transcript"]]
    assert speakers == [
        {"source": "microphone", "attribution": "me"},
        {"source": "speaker", "attribution": "them"},
        {"source": "speaker", "name": "Alice", "attribution": "them"},
        {"source": "speaker", "name": "Me", "attribution": "me"},
        {"source": "speaker", "diarization_label": "Speaker A"},
    ]
    first = full["transcript"][0]
    assert first["text"] == "mic words" and first["start_time"] == "2026-03-04T10:00:01.000Z" and first["end_time"] == "2026-03-04T10:00:02.000Z"
    assert client.get(f"/v1/notes/{mid}", params={"include": "bogus"}).status_code == 400


def test_note_without_notes_or_calendar(client, validate):
    mid = seed_meeting(client, [("me", 0, 500, "hi")])
    note = client.get(f"/v1/notes/{mid}").json()
    validate("Note", note)
    assert note["summary_markdown"] is None and note["summary_text"] == "" and note["private_notes_text"] is None
    assert note["calendar_event"] is None and note["folder_membership"] == []


def test_note_errors(client):
    assert client.get("/v1/notes/not_zzzzzzzzzzzzzz").status_code == 404
    assert client.get("/v1/notes/garbage").status_code == 400
    assert client.get("/v1/notes/not_zzzzzzzzzzzzzz/transcript").status_code == 404


def test_transcript_too_large_and_paging(client, validate):
    n = 2001
    mid = seed_meeting(client, [("me", i * 10, i * 10 + 5, f"w{i}") for i in range(n)])
    r = client.get(f"/v1/notes/{mid}", params={"include": "transcript"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "TRANSCRIPT_TOO_LARGE"
    assert client.get(f"/v1/notes/{mid}").status_code == 200  # without include it is fine
    items, cursor, pages = [], None, 0
    while True:
        page = client.get(f"/v1/notes/{mid}/transcript", params={"page_size": 100, **({"cursor": cursor} if cursor else {})}).json()
        validate("GetTranscriptOutput", page)
        items += page["transcript"]
        pages += 1
        cursor = page["cursor"]
        if not page["hasMore"]:
            assert cursor is None
            break
    assert len(items) == n and pages == 21 and items[-1]["text"] == f"w{n - 1}"
    default = client.get(f"/v1/notes/{mid}/transcript").json()
    assert len(default["transcript"]) == 50
    assert client.get(f"/v1/notes/{mid}/transcript", params={"page_size": 101}).status_code == 400
    assert client.get(f"/v1/notes/{mid}/transcript", params={"cursor": "nope"}).status_code == 400
    ok = seed_meeting(client, [("me", 0, 5, "x")] * 2000)
    assert client.get(f"/v1/notes/{ok}", params={"include": "transcript"}).status_code == 200  # exactly 2000 is inline


def test_folders_and_spaces(client, validate):
    for name in ("banana", "Apple", "cherry"):
        client.post("/api/v1/folders", json={"name": name})
    parent = client.get("/v1/folders").json()["folders"][0]["id"]
    client.post("/api/v1/folders", json={"name": "Blueberry", "parent_folder_id": parent})
    page = client.get("/v1/folders", params={"page_size": 2}).json()
    validate("ListFoldersOutput", page)
    assert [f["name"] for f in page["folders"]] == ["Apple", "banana"] and page["hasMore"] and page["cursor"]
    rest = client.get("/v1/folders", params={"page_size": 2, "cursor": page["cursor"]}).json()
    validate("ListFoldersOutput", rest)
    assert [f["name"] for f in rest["folders"]] == ["Blueberry", "cherry"] and rest["hasMore"] is False and rest["cursor"] is None
    blue = next(f for f in rest["folders"] if f["name"] == "Blueberry")
    assert blue["parent_folder_id"] == parent and blue["space_id"] is None and blue["object"] == "folder"
    assert client.get("/v1/folders", params={"page_size": 31}).status_code == 400
    spaces = client.get("/v1/spaces").json()
    validate("ListSpacesOutput", spaces)
    assert spaces == {"spaces": [], "hasMore": False, "cursor": None}


def test_webhook_endpoint_crud_schema(client, validate):
    folder = client.post("/api/v1/folders", json={"name": "F"}).json()["id"]
    body = {"url": "http://lan.example:8080/hook", "scopes": ["personal", "public"], "folder_ids": [folder.replace("fol_", "FOL_")]}
    created = client.post("/v1/webhook-endpoints", json=body)
    assert created.status_code == 201
    created = created.json()
    validate("CreateWebhookEndpointOutput", created)
    assert created["signing_secret"].startswith("whsec_") and created["events"] == ["note.access_granted", "note.edited", "note.generated"]
    assert created["folder_ids"] == [folder] and created["enabled"] is True and created["url_redacted"] is False
    listed = client.get("/v1/webhook-endpoints").json()
    assert set(listed) == {"webhook_endpoints"} and "signing_secret" not in listed["webhook_endpoints"][0]
    for ep in listed["webhook_endpoints"]:
        validate("WebhookEndpoint", ep)
    patched = client.patch(f"/v1/webhook-endpoints/{created['id'].replace('whe_', 'WHE_')}", json={"enabled": False, "events": ["note.edited"], "folder_ids": []})
    assert patched.status_code == 200
    validate("WebhookEndpoint", patched.json())
    assert patched.json()["enabled"] is False and patched.json()["events"] == ["note.edited"] and patched.json()["folder_ids"] == []
    assert client.post("/v1/webhook-endpoints", json={"url": "http://x", "scopes": []}).status_code == 400
    assert client.post("/v1/webhook-endpoints", json={"url": "http://x", "scopes": ["bogus"]}).status_code == 400
    assert client.post("/v1/webhook-endpoints", json={"url": "http://x", "scopes": ["public"], "events": ["note.nope"]}).status_code == 400
    assert client.patch("/v1/webhook-endpoints/whe_zzzzzzzzzzzzzz", json={}).status_code == 404
    gone = client.delete(f"/v1/webhook-endpoints/{created['id']}").json()
    assert gone == {"id": created["id"], "object": "webhook_endpoint", "deleted": True}
    assert client.get("/v1/webhook-endpoints").json() == {"webhook_endpoints": []}
    # the native API does not see Granola endpoints and vice versa
    client.post("/v1/webhook-endpoints", json={"url": "http://x", "scopes": ["public"]})
    assert client.get("/api/v1/webhooks").json()["webhooks"] == []


def test_granola_style_deliveries(make_client):
    got: list[tuple[dict, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        got.append((dict(request.headers), request.content))
        return httpx.Response(204)

    client = make_client(webhook_client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    folder = client.post("/api/v1/folders", json={"name": "Clients"}).json()["id"]
    other = client.post("/api/v1/folders", json={"name": "Other"}).json()["id"]
    everything = client.post("/v1/webhook-endpoints", json={"url": "http://h/all", "scopes": ["personal"]}).json()
    only_folder = client.post("/v1/webhook-endpoints", json={"url": "http://h/f", "scopes": ["personal"], "folder_ids": [other]}).json()
    paused = client.post("/v1/webhook-endpoints", json={"url": "http://h/p", "scopes": ["personal"]}).json()
    client.patch(f"/v1/webhook-endpoints/{paused['id']}", json={"enabled": False})
    mid = seed_meeting(client, [("me", 0, 500, "hi")])
    client.post(f"/api/v1/folders/{folder}/meetings/{mid}")

    client.put(f"/api/v1/meetings/{mid}/notes", json={"markdown": "first", "source": "agent"})
    assert wait_for(lambda: len(got) >= 1)
    client.put(f"/api/v1/meetings/{mid}/notes", json={"markdown": "second", "source": "user"})
    assert wait_for(lambda: len(got) >= 2)
    assert len(got) == 2  # not the folder-restricted one, not the paused one
    (h1, b1), (h2, b2) = sorted(got, key=lambda g: json.loads(g[1])["occurred_at"] + json.loads(g[1])["event_type"])
    p1, p2 = json.loads(b1), json.loads(b2)
    by_type = {p["event_type"]: p for p in (p1, p2)}
    assert set(by_type) == {"note.generated", "note.edited"}
    gen, edited = by_type["note.generated"], by_type["note.edited"]
    assert set(gen) == {"event_id", "event_type", "note_id", "occurred_at"} and gen["note_id"] == mid
    assert edited["data"] == {"changed_fields": ["summary"]}
    for headers, body in got:
        assert verify(everything["signing_secret"], headers, body)
        assert not verify(only_folder["signing_secret"], headers, body)
    assert headers["webhook-signature"].startswith("v1,")
