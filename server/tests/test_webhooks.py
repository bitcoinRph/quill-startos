from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx

from quill_server.db import Database
from quill_server.webhooks import RETRY_BACKOFF, Webhooks, new_secret, sign, verify

from .conftest import seed_meeting, state_of, wait_for


def test_signature_is_standard_webhooks():
    key = b"0123456789abcdef0123456789abcdef"
    secret = "whsec_" + base64.b64encode(key).decode()
    body = b'{"id":"evt_1"}'
    expected = "v1," + base64.b64encode(hmac.new(key, b"msg_1.1700000000." + body, hashlib.sha256).digest()).decode()
    assert sign(secret, "msg_1", 1700000000, body) == expected
    headers = {"webhook-id": "msg_1", "webhook-timestamp": str(int(time.time())), "webhook-signature": ""}
    headers["webhook-signature"] = "v1,bogus " + sign(secret, "msg_1", headers["webhook-timestamp"], body)
    assert verify(secret, headers, body)  # space-separated list, any valid entry passes
    assert not verify(secret, headers, body + b" ")
    headers["webhook-timestamp"] = "1"
    assert not verify(secret, headers, body)  # outside the replay window


def test_new_secret_format():
    s = new_secret()
    assert s.startswith("whsec_") and len(base64.b64decode(s[6:])) == 32


def make_hooks(handler, sleeps):
    async def sleep(seconds):
        sleeps.append(seconds)

    return Webhooks(
        Database(":memory:"),
        folders_of=lambda _: set(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False),
        sleep=sleep,
    )


async def test_retries_with_backoff_then_succeeds_and_signs_each_try():
    seen, sleeps = [], []
    statuses = iter([500, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["webhook-id"], request.headers["webhook-timestamp"],
                     request.headers["webhook-signature"], request.content))  # fmt: skip
        return httpx.Response(next(statuses))

    hooks = make_hooks(handler, sleeps)
    ep = hooks.create("http://hook.example/x", ["notes.ready"])
    payload = {"id": "evt_abc", "type": "notes.ready", "created_at": "t", "data": {}}
    assert await hooks.deliver(ep, payload) is True
    assert sleeps == [5.0, 30.0] and len(seen) == 3
    assert {s[0] for s in seen} == {"evt_abc"}  # stable id across retries
    for msg_id, ts, sig, body in seen:
        assert sig == sign(ep["secret"], msg_id, ts, body)
    assert json.loads(seen[0][3]) == payload


async def test_gives_up_after_five_retries_and_skips_non_retryable():
    sleeps = []
    hooks = make_hooks(lambda r: httpx.Response(503), sleeps)
    ep = hooks.create("http://h/x", ["notes.ready"])
    assert await hooks.deliver(ep, {"id": "e", "type": "t", "created_at": "", "data": {}}) is False
    assert sleeps == list(RETRY_BACKOFF) == [5.0, 30.0, 120.0, 600.0, 3600.0]

    sleeps.clear()
    hooks = make_hooks(lambda r: httpx.Response(404), sleeps)
    ep = hooks.create("http://h/x", ["notes.ready"])
    assert await hooks.deliver(ep, {"id": "e", "type": "t", "created_at": "", "data": {}}) is False
    assert sleeps == []


async def test_redirects_are_not_followed():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://internal.lan/secret"})

    hooks = make_hooks(handler, [])
    ep = hooks.create("http://h/x", ["notes.ready"])
    assert await hooks.deliver(ep, {"id": "e", "type": "t", "created_at": "", "data": {}}) is False
    assert calls == ["http://h/x"]


def test_native_webhook_crud_and_secret_once(client):
    created = client.post("/api/v1/webhooks", json={"url": "http://h/x", "events": ["notes.ready"]})
    assert created.status_code == 201
    body = created.json()
    assert body["id"].startswith("whe_") and body["secret"].startswith("whsec_")
    listed = client.get("/api/v1/webhooks").json()["webhooks"]
    assert listed[0]["id"] == body["id"] and "secret" not in listed[0]
    assert client.post("/api/v1/webhooks", json={"url": "ftp://x"}).status_code == 400
    assert client.post("/api/v1/webhooks", json={"url": "http://x", "events": ["nope"]}).status_code == 400
    assert client.delete(f"/api/v1/webhooks/{body['id'].replace('whe_', 'WHE_')}").status_code == 204
    assert client.get("/api/v1/webhooks").json()["webhooks"] == []


def test_events_are_delivered_to_native_endpoints(make_client):
    got = []

    def handler(request):
        got.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(200)

    client = make_client(webhook_client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ep = client.post("/api/v1/webhooks", json={"url": "http://h/x", "events": ["notes.ready", "transcript.ready"]}).json()
    other = client.post("/api/v1/webhooks", json={"url": "http://h/y", "events": ["meeting.started"]}).json()
    mid = seed_meeting(client, [("me", 0, 1000, "hello")])
    client.put(f"/api/v1/meetings/{mid}/notes", json={"markdown": "# Notes", "source": "agent"})
    assert wait_for(lambda: len(got) >= 2)
    types = sorted(b["type"] for _, b in got)
    assert types == ["notes.ready", "transcript.ready"]
    headers, payload = got[0]
    assert set(payload) == {"id", "type", "created_at", "data"} and payload["data"]["meeting_id"] == mid
    assert payload["data"]["url"] == f"https://quill.test/#/meetings/{mid}"
    secret = state_of(client).db.one("SELECT secret FROM webhooks WHERE id=?", (ep["id"],))["secret"]
    assert headers["webhook-signature"] == sign(secret, headers["webhook-id"], headers["webhook-timestamp"],
                                                json.dumps(payload, separators=(",", ":")).encode())  # fmt: skip
    assert other["id"] != ep["id"]
