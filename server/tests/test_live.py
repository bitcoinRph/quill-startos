from __future__ import annotations

import json
import struct
import time

import numpy as np
import pytest
import soundfile as sf

from quill_server.storage import WAV_HEADER_BYTES, WavWriter, repair_wav

from .conftest import API_KEY, FakeEngine, frames, requires_vad, speech_pcm, state_of, wait_status

TOKEN = f"?token={API_KEY}"


def read_until(ws, predicate, limit=400):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if predicate(msg):
            return seen
    raise AssertionError(f"condition not reached; last messages: {seen[-5:]}")


def new_meeting(client, **body) -> str:
    return client.post("/api/v1/meetings", json=body or {"title": "Live"}).json()["id"]


def test_wav_header_is_valid_after_periodic_update_without_close(tmp_path):
    w = WavWriter(tmp_path / "t.wav")
    w.write(np.arange(16000, dtype="<i2").tobytes())
    w.update_header()  # what the 5 s timer does; the process then "crashes"
    w.write(np.arange(8000, dtype="<i2").tobytes())  # written after the last header update
    data, rate = sf.read(tmp_path / "t.wav", dtype="int16")
    assert rate == 16000 and len(data) >= 16000  # readable, header covers at least the checkpoint


def test_repair_wav_fixes_header_from_file_size(tmp_path):
    p = tmp_path / "c.wav"
    w = WavWriter(p)
    w.write(b"\x01\x00" * 12345)
    w._fh.flush()  # header still says 0 bytes
    assert struct.unpack("<I", p.read_bytes()[40:44])[0] == 0
    assert repair_wav(p) == 12345
    assert struct.unpack("<I", p.read_bytes()[40:44])[0] == 24690
    assert p.stat().st_size == WAV_HEADER_BYTES + 24690
    assert len(sf.read(p, dtype="int16")[0]) == 12345


@requires_vad
def test_live_session_end_to_end(client):
    mid = new_meeting(client)
    pcm = speech_pcm()
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ready = ws.receive_json()
        assert ready == {"type": "ready", "meeting_id": mid, "session_offset_ms": 0}
        ws.send_json({"type": "track", "track_id": "t1", "kind": "participant", "speaker": "Alice",
                      "label": "Alice", "participant_id": "abc", "sample_rate": 16000})  # fmt: skip
        for f in frames(pcm, "t1", 100):
            ws.send_bytes(f)
        ws.send_json({"type": "ping"})
        ws.send_json({"type": "track_end", "track_id": "t1"})
        ws.send_json({"type": "note", "markdown": "my note"})
        ws.send_json({"type": "stop"})
        msgs = read_until(ws, lambda m: m.get("type") == "status" and m["status"] == "ready")
    types = [m["type"] for m in msgs]
    assert "pong" in types and "segment" in types
    segs = [m["segment"] for m in msgs if m["type"] == "segment"]
    assert [s["seq"] for s in segs] == list(range(1, len(segs) + 1))
    assert set(segs[0]) == {"seq", "track_id", "speaker", "start_ms", "end_ms", "text"}
    assert segs[0]["track_id"] == "t1" and segs[0]["speaker"] == "Alice" and segs[0]["text"].startswith("fake chunk")
    assert any(m["type"] == "status" and m["status"] == "processing" for m in msgs)

    meeting = client.get(f"/api/v1/meetings/{mid}?include=tracks,notes").json()
    assert meeting["status"] == "ready" and meeting["user_notes_md"] == "my note"
    assert meeting["started_at"] and meeting["ended_at"] and meeting["duration_ms"] > 3000
    (track,) = meeting["tracks"]
    assert track["kind"] == "participant" and track["participant_id"] == "abc" and track["status"] == "complete"
    seg = track["segments"][0]
    assert seg["file"] == "t1.wav" and seg["frames_written"] == len(pcm) // 2 and seg["start_offset_ms"] == 0

    row = state_of(client).meetings.require(mid)
    d = state_of(client).meetings.session_path(row)
    assert sorted(p.name for p in d.iterdir()) == [
        "meta.json", "t1.wav", "transcript.json", "transcript.md", "user-notes.md",
    ]  # fmt: skip
    assert len(sf.read(d / "t1.wav", dtype="int16")[0]) == len(pcm) // 2  # header final after close
    meta = json.loads((d / "meta.json").read_text())
    assert meta["schema_version"] == 2 and meta["tracks"][0]["segments"][0]["file"] == "t1.wav"
    assert meta["tracks"][0]["label"] == "Alice" and meta["status"] == "complete"
    doc = json.loads((d / "transcript.json").read_text())
    assert doc["engine"] == "fake" and doc["segments"][0]["track"] == "t1" and doc["segments"][0]["words"]
    assert (d / "transcript.md").read_text().startswith(f"# {meeting['title']}\n\nengine: fake (fake-1)\n\n**[0:00] Alice:**")


@requires_vad
def test_each_track_gets_its_own_offset_and_rejoin_opens_new_file(client):
    mid = new_meeting(client)
    pcm = speech_pcm()
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "track", "track_id": "a", "kind": "participant", "speaker": "A"})
        first = list(frames(pcm, "a", 500))
        ws.send_bytes(first[0])
        # b joins while a is silent for a while: its offset is the session clock at its first frame
        ws.send_json({"type": "track", "track_id": "b", "kind": "participant", "speaker": "B"})
        time.sleep(0.4)
        ws.send_bytes(next(frames(pcm, "b", 500)))
        ws.send_json({"type": "track_end", "track_id": "a"})
        ws.send_json({"type": "track", "track_id": "a", "kind": "participant", "speaker": "A"})  # rejoin
        ws.send_bytes(first[1])
        ws.send_json({"type": "stop"})
        read_until(ws, lambda m: m.get("type") == "status" and m["status"] == "ready")
    tracks = {t["id"]: t for t in client.get(f"/api/v1/meetings/{mid}?include=tracks").json()["tracks"]}
    assert tracks["a"]["segments"][0]["start_offset_ms"] == 0
    assert tracks["b"]["segments"][0]["start_offset_ms"] >= 300
    assert [s["file"] for s in tracks["a"]["segments"]] == ["a.wav", "a-002.wav"]
    assert tracks["a"]["segments"][1]["start_offset_ms"] > tracks["a"]["segments"][0]["start_offset_ms"]


@requires_vad
def test_protocol_errors(client):
    mid = new_meeting(client)
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "track", "track_id": "x", "kind": "participant", "sample_rate": 48000})
        assert ws.receive_json()["code"] == "bad_sample_rate"
        ws.send_json({"type": "track", "track_id": "", "kind": "mic"})
        assert ws.receive_json()["code"] == "bad_track"
        ws.send_json({"type": "track", "track_id": "x", "kind": "weird"})
        assert ws.receive_json()["code"] == "bad_track"
        ws.send_bytes(bytes([1]) + b"zzzz")  # frame for an unregistered track
        assert ws.receive_json()["code"] == "unknown_track"
        ws.send_bytes(bytes([0]))
        assert ws.receive_json()["code"] == "bad_frame"
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["code"] == "unknown_type"
        ws.send_json({"type": "stop"})  # nothing recorded
        read_until(ws, lambda m: m.get("type") == "status" and m["status"] == "failed")
    assert client.get(f"/api/v1/meetings/{mid}").json()["error"] == "no audio received"


def test_ws_auth_cookie_token_internal(client):
    mid = new_meeting(client)
    anon = f"/api/v1/meetings/{mid}/live"
    from starlette.websockets import WebSocketDisconnect

    client.headers.pop("Authorization")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(anon):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(anon + "?internal=wrong"):
            pass
    with client.websocket_connect(anon + "?internal=internal-token") as ws:
        assert ws.receive_json()["type"] == "ready"
    with client.websocket_connect(anon + f"?token={API_KEY}") as ws:
        assert ws.receive_json()["type"] == "ready"
    client.post("/api/v1/auth/login", json={"password": "correct horse"})
    with client.websocket_connect(anon) as ws:  # cookie
        assert ws.receive_json()["type"] == "ready"


def test_closed_meeting_rejects_live(client):
    mid = new_meeting(client)
    state_of(client).db.execute("UPDATE meetings SET status='ready' WHERE id=?", (mid,))
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        assert ws.receive_json()["code"] == "meeting_closed"


@requires_vad
def test_rest_stop_and_sse_replay(client):
    mid = new_meeting(client)
    pcm = speech_pcm()
    with client.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "track", "track_id": "t", "kind": "mic", "speaker": "me"})
        for f in frames(pcm, "t", 200):
            ws.send_bytes(f)
        assert client.post(f"/api/v1/meetings/{mid}/stop").json()["status"] in ("processing", "ready")
        read_until(ws, lambda m: m.get("type") == "status" and m["status"] == "ready")
    done = wait_status(client, mid, "ready")
    assert done["speakers"] == ["Me"]  # QUILL_SPEAKER_NAME default labels the mic track
    assert client.post(f"/api/v1/meetings/{mid}/stop").status_code == 409


@requires_vad
def test_crash_recovery_finalizes_live_meeting_from_disk(make_client):
    c1 = make_client()
    mid = new_meeting(c1)
    pcm = speech_pcm()
    with c1.websocket_connect(f"/api/v1/meetings/{mid}/live{TOKEN}") as ws:
        ws.receive_json()
        ws.send_json({"type": "track", "track_id": "t1", "kind": "participant", "speaker": "Zed"})
        for f in frames(pcm, "t1", 100):
            ws.send_bytes(f)
        ws.send_json({"type": "ping"})
        read_until(ws, lambda m: m.get("type") == "pong")
    assert c1.get(f"/api/v1/meetings/{mid}").json()["status"] == "live"
    # simulate SIGKILL: no clean shutdown of the first process; a second one starts on the same data
    s1 = state_of(c1)
    d = s1.meetings.session_path(s1.meetings.require(mid))
    wav = d / "t1.wav"
    with open(wav, "r+b") as fh:  # stale header, as after a crash between timer ticks
        fh.seek(40)
        fh.write(struct.pack("<I", 0))
    s1.db.execute("UPDATE meetings SET status='live' WHERE id=?", (mid,))
    c2 = make_client(engine=FakeEngine())
    done = wait_status(c2, mid, "ready")
    assert done["status"] == "ready" and done["capture_status"] == "recovered"
    transcript = c2.get(f"/api/v1/meetings/{mid}/transcript").json()
    assert transcript["segments"] and transcript["segments"][0]["speaker"] == "Zed"
    assert len(sf.read(wav, dtype="int16")[0]) == len(pcm) // 2
    # a meeting that never saved audio is failed, not stuck
    empty = new_meeting(c2)
    s2 = state_of(c2)
    s2.db.execute("UPDATE meetings SET status='live' WHERE id=?", (empty,))
    s2.jobs.recover()
    assert c2.get(f"/api/v1/meetings/{empty}").json()["status"] == "failed"
