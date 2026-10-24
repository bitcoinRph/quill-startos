from __future__ import annotations

import json
import os
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from quill_server.app import create_app
from quill_server.housekeeping import delete_old_audio
from quill_server.state import AppState
from quill_server.util import iso, utcnow

from .conftest import (API_KEY, FakeEngine, frames, install_fake_bot, make_config, requires_vad, seed_meeting,
                       speech_pcm, state_of, wait_for)  # fmt: skip

TOKEN = f"?token={API_KEY}"


# --- bot routes ---


def test_bot_routes_with_manager(make_client):
    client = make_client(jitsi_domains=("meet.example.com",))
    bot = install_fake_bot(client)
    r = client.post("/api/v1/bot/join", json={"url": "https://meet.example.com/standup", "title": "Standup", "password": "pw"})
    assert r.status_code == 201
    meeting = r.json()
    assert meeting["source"] == "bot" and meeting["bot"] == {"state": "starting", "error": None}
    assert bot.joins == [("https://meet.example.com/standup", "Standup", None, "pw")]

    bot.bots[meeting["id"]].update(state="failed", error="lobby denied")
    got = client.get(f"/api/v1/meetings/{meeting['id']}").json()
    assert got["bot"] == {"state": "failed", "error": "lobby denied"}
    listed = client.get("/api/v1/bot").json()
    assert listed["enabled"] is True and listed["domains"] == ["meet.example.com"] and listed["bots"][0]["state"] == "failed"

    bad = client.post("/api/v1/bot/join", json={"url": "https://evil.example.org/x"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_bot_request"
    assert client.post(f"/api/v1/bot/{meeting['id']}/leave").json()["bot"]["state"] == "ended"
    other = client.post("/api/v1/meetings", json={}).json()["id"]
    assert client.post(f"/api/v1/bot/{other}/leave").status_code == 404


def test_bot_unavailable_returns_503(client):
    r = client.post("/api/v1/bot/join", json={"url": "https://meet.example.com/x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "bot_unavailable"
    assert client.get("/api/v1/bot").json() == {"enabled": False, "domains": [], "bots": []}


def test_bot_import_failure_is_503_not_a_crash(make_client, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "quill_server.bot.manager", None)  # makes the import fail
    client = make_client(jitsi_domains=("meet.example.com",))
    assert client.post("/api/v1/bot/join", json={"url": "https://meet.example.com/x"}).status_code == 503


# --- static UI ---


def test_spa_fallback_and_api_404s_stay_json(tmp_path, make_client):
    web = tmp_path / "web"
    (web / "js").mkdir(parents=True)
    (web / "dev").mkdir()
    (web / "dev" / "mock_server.mjs").write_text("secret tooling")
    (web / "index.html").write_text("<h1>quill ui</h1>")
    (web / "js" / "app.js").write_text("export {}")
    client = make_client(web_dir=web)
    anon = {"Authorization": ""}
    assert client.get("/", headers=anon).text == "<h1>quill ui</h1>"
    assert client.get("/meetings/not_abc", headers=anon).text == "<h1>quill ui</h1>"  # client-side route
    assert client.get("/js/app.js", headers=anon).text == "export {}"
    assert client.get("/js/missing.js", headers=anon).status_code == 404
    assert client.get("/dev/mock_server.mjs", headers=anon).status_code == 404  # dev tooling is not served
    assert client.get("/dev/", headers=anon).status_code == 404
    api404 = client.get("/api/v1/nothing", headers=anon)
    assert api404.status_code == 404 and api404.json()["error"]["code"] == "not_found"
    assert client.get("/health", headers=anon).json()["status"] == "ok"
    assert client.get("/openapi.json", headers=anon).json()["info"]["title"] == "Quill"


def test_no_web_dir_is_fine(tmp_path, make_client):
    client = make_client(web_dir=tmp_path / "absent")
    assert client.get("/").status_code == 404 and client.get("/health").status_code == 200


# --- shutdown ---


def test_sigterm_checkpoints_wal(tmp_path):
    state = AppState.create(make_config(tmp_path), engine=FakeEngine())
    with TestClient(create_app(state=state), headers={"Authorization": f"Bearer {API_KEY}"}) as c:
        for i in range(20):
            c.post("/api/v1/meetings", json={"title": f"m{i}"})
        wal = tmp_path / "data" / "quill.db-wal"
        assert wal.exists() and wal.stat().st_size > 0
    assert not wal.exists() or wal.stat().st_size == 0  # rsync of /data copies a complete quill.db


# --- SSE ---


async def test_sse_stream_and_replay(live_server):
    base, state = live_server
    mid = state.meetings.create("sse", "api")["id"]
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as http:
        events = []
        async with http.stream("GET", f"/api/v1/meetings/{mid}/events") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            lines = r.aiter_lines()
            async for line in lines:
                if line.startswith("event:"):
                    events.append(line.split(": ", 1)[1])
                    break
            seg = state.meetings.add_segment(mid, track_id="t", speaker="Ann", start_ms=1, end_ms=2, text="live words", words=None)
            state.bus.publish(mid, {"type": "segment", "segment": {k: seg[k] for k in ("seq", "track_id", "speaker", "start_ms", "end_ms", "text")}})
            state.bus.publish(mid, {"type": "notes", "source": "agent"})
            data = {}
            async for line in lines:
                if line.startswith("event:"):
                    events.append(line.split(": ", 1)[1])
                elif line.startswith("data:"):
                    data[events[-1]] = json.loads(line[5:])
                if "notes" in data:
                    break
        assert events == ["status", "segment", "notes"]
        assert data["segment"]["segment"]["text"] == "live words" and data["notes"] == {"type": "notes", "source": "agent"}

        replay = []
        async with http.stream("GET", f"/api/v1/meetings/{mid}/events", headers={"Last-Event-ID": "0"}) as r:
            async for line in r.aiter_lines():
                replay.append(line)
                if any(l.startswith("id: ") for l in replay) and line.startswith("data:") and "live words" in line:
                    break
        assert "id: 1" in replay and "event: segment" in replay


# --- lifecycle webhooks, compression, retention ---


@requires_vad
def test_meeting_started_and_ended_webhooks(make_client):
    got = []

    def handler(request):
        got.append(json.loads(request.content)["type"])
        return httpx.Response(200)

    client = make_client(webhook_client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client.post("/api/v1/webhooks", json={"url": "http://h/x"})
    mid = client.post("/api/v1/meetings", json={}).json()["id"]
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "track", "track_id": "t", "kind": "participant", "speaker": "A"})
        for f in list(frames(speech_pcm(), "t", 200))[:10]:
            ws.send_bytes(f)
        ws.send_json({"type": "stop"})
        while True:
            m = ws.receive_json()
            if m.get("status") == "ready":
                break
    assert wait_for(lambda: len(got) >= 3)
    assert got[0] == "meeting.started" and set(got) == {"meeting.started", "meeting.ended", "transcript.ready"}


@requires_vad
def test_finished_wavs_are_compressed_to_opus(make_client):
    client = make_client(compress_audio=True)
    mid = client.post("/api/v1/meetings", json={}).json()["id"]
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "track", "track_id": "t", "kind": "participant", "speaker": "A"})
        for f in frames(speech_pcm(), "t", 200):
            ws.send_bytes(f)
        ws.send_json({"type": "stop"})
        while ws.receive_json().get("status") != "ready":
            pass
    d = state_of(client).meetings.session_path(state_of(client).meetings.require(mid))
    assert wait_for(lambda: (d / "t.ogg").exists() and not (d / "t.wav").exists())
    track = client.get(f"/api/v1/meetings/{mid}?include=tracks").json()["tracks"][0]
    assert track["segments"][0]["file"] == "t.ogg"
    assert json.loads((d / "meta.json").read_text())["tracks"][0]["segments"][0]["file"] == "t.ogg"
    audio = client.get(f"/api/v1/meetings/{mid}/audio/t")
    assert audio.status_code == 200 and audio.content[:4] == b"OggS"


def test_retention_deletes_audio_but_keeps_transcripts(make_client):
    client = make_client(audio_retention_days=7)
    s = state_of(client)
    old = seed_meeting(client, [("me", 0, 500, "keep this text")], "old")
    fresh = seed_meeting(client, [("me", 0, 500, "fresh")], "fresh")
    for mid, age in ((old, 30), (fresh, 1)):
        s.meetings.add_uploaded_track(mid, "u1", "me", "u1-a.wav")
        (s.meetings.session_path(s.meetings.require(mid)) / "u1-a.wav").write_bytes(b"RIFF....")
        s.db.execute("UPDATE meetings SET ended_at=? WHERE id=?", (iso(utcnow() - timedelta(days=age)), mid))
    assert delete_old_audio(s.config, s.meetings) == 1
    assert not (s.meetings.session_path(s.meetings.require(old)) / "u1-a.wav").exists()
    assert (s.meetings.session_path(s.meetings.require(fresh)) / "u1-a.wav").exists()
    assert client.get(f"/api/v1/meetings/{old}/audio/u1").json()["error"]["code"] == "audio_unavailable"
    assert "keep this text" in client.get(f"/api/v1/meetings/{old}/transcript", params={"format": "txt"}).text
    assert client.post(f"/api/v1/meetings/{old}/retranscribe").status_code == 409


def test_delete_removes_session_directory(client):
    mid = seed_meeting(client, [("me", 0, 500, "bye")])
    s = state_of(client)
    d = s.meetings.session_path(s.meetings.require(mid))
    assert d.is_dir()
    client.delete(f"/api/v1/meetings/{mid}")
    assert not d.exists()
    assert client.get("/api/v1/search", params={"q": "bye"}).json()["hits"] == []  # FTS rows went with it


def test_config_from_env_parses_everything():
    from quill_server.config import Config

    c = Config.from_env({
        "QUILL_DATA_DIR": "/d", "QUILL_PORT": "9000", "QUILL_ASR_ENGINE": "OpenAI", "QUILL_REMOTE_ASR_URL": "http://asr:8081/",
        "QUILL_JITSI_DOMAINS": "Meet.Example.com, jitsi.local ,", "QUILL_AUTO_NOTES": "true", "QUILL_BOT_ANNOUNCE": "false",
        "QUILL_AUDIO_RETENTION_DAYS": "30", "QUILL_ASR_THREADS": "0", "QUILL_OWNER_EMAIL": "me@x.io",
        "QUILL_LLM_BASE_URL": "http://llm/v1/", "QUILL_LLM_MODEL": "m",
    })  # fmt: skip
    assert (c.port, c.asr_engine, c.remote_asr_url, c.auto_notes, c.bot_announce) == (9000, "openai", "http://asr:8081", True, False)
    assert c.jitsi_domains == ("meet.example.com", "jitsi.local") and c.audio_retention_days == 30
    assert c.llm_base_url == "http://llm/v1" and c.llm_enabled and c.threads == min(4, os.cpu_count() or 1)
    assert Config.from_env({}).bot_announce is True and Config.from_env({}).session_secret != Config.from_env({}).session_secret


def test_bot_error_mapping():
    from quill_server.bot.urls import BotError, BotUrlError
    from quill_server.errors import bot_error

    e = bot_error(BotUrlError("host_not_allowed", "nope"))
    assert (e.status, e.code) == (400, "host_not_allowed")
    assert bot_error(BotError("not_found", "x")).status == 404
    assert bot_error(BotError("limit_reached", "x")).status == 409
    assert bot_error(BotError("launch_failed", "x")).status == 502
    assert bot_error(RuntimeError("boom")).status == 502


@requires_vad
def test_internal_token_may_only_stop(client):
    mid = client.post("/api/v1/meetings", json={}).json()["id"]
    internal = {"Authorization": "Bearer internal-token"}
    assert client.get(f"/api/v1/meetings/{mid}", headers=internal).status_code == 401
    assert client.post("/api/v1/meetings", headers=internal, json={}).status_code == 401
    assert client.post(f"/api/v1/meetings/{mid}/stop", headers=internal).status_code == 202  # scheduled -> failed
    assert client.get(f"/api/v1/meetings/{mid}").json()["status"] == "failed"


def test_failed_bot_marks_its_scheduled_meeting_failed(client):
    state = state_of(client)
    mid = client.post("/api/v1/meetings", json={"title": "Bot"}).json()["id"]
    state._on_bot_state({"meeting_id": mid, "state": "joined", "error": None})
    assert client.get(f"/api/v1/meetings/{mid}").json()["status"] == "scheduled"
    state._on_bot_state({"meeting_id": mid, "state": "failed", "error": "lobby denied"})
    body = client.get(f"/api/v1/meetings/{mid}").json()
    assert (body["status"], body["error"]) == ("failed", "lobby denied")
