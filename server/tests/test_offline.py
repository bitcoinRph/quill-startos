from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import TEST_WAV, requires_parakeet, requires_vad, state_of, wait_for, wait_status


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def wav_bytes() -> bytes:
    return TEST_WAV.read_bytes()


@pytest.fixture
def pair(tmp_path):
    """The English clip as a 16k WAV and as ALAC in a .caf container."""
    ffmpeg("-i", str(TEST_WAV), "-ar", "16000", str(tmp_path / "me.wav"))
    ffmpeg("-i", str(TEST_WAV), "-c:a", "alac", str(tmp_path / "them.caf"))
    return tmp_path / "me.wav", tmp_path / "them.caf"


@requires_vad
def test_upload_two_files_transcribed_in_background(client, pair):
    me, them = pair
    mid = client.post("/api/v1/meetings", json={"title": "Upload", "source": "upload"}).json()["id"]
    files = [("file", ("me.wav", me.read_bytes())), ("file", ("them.caf", them.read_bytes())),
             ("speaker", (None, "Alice")), ("speaker", (None, "Bob"))]  # fmt: skip
    r = client.post(f"/api/v1/meetings/{mid}/audio", files=files)
    assert r.status_code == 202 and r.json()["status"] == "processing"
    done = wait_status(client, mid, "ready")
    assert done["status"] == "ready" and done["speakers"] == ["Alice", "Bob"]
    assert done["duration_ms"] > 3000
    seg = client.get(f"/api/v1/meetings/{mid}/transcript").json()["segments"]
    assert [s["speaker"] for s in seg] == ["Alice", "Bob"] and [s["seq"] for s in seg] == [1, 2]
    tracks = client.get(f"/api/v1/meetings/{mid}?include=tracks").json()["tracks"]
    assert [t["kind"] for t in tracks] == ["upload", "upload"]
    audio = client.get(f"/api/v1/meetings/{mid}/audio/u1")
    assert audio.status_code == 200 and audio.headers["x-quill-offset-ms"] == "0" and audio.content[:4] == b"RIFF"
    assert client.get(f"/api/v1/meetings/{mid}/audio/zzz").status_code == 404

    engine_calls = state_of(client).worker.engine.calls
    assert client.post(f"/api/v1/meetings/{mid}/retranscribe").status_code == 202
    wait_for(lambda: state_of(client).worker.engine.calls > engine_calls)
    assert wait_status(client, mid, "ready")["status"] == "ready"
    assert [s["seq"] for s in client.get(f"/api/v1/meetings/{mid}/transcript").json()["segments"]] == [1, 2]


@requires_vad
def test_undecodable_upload_fails_meeting(client):
    mid = client.post("/api/v1/meetings", json={}).json()["id"]
    client.post(f"/api/v1/meetings/{mid}/audio", files=[("file", ("x.wav", b"this is not audio"))])
    done = wait_status(client, mid, "failed")
    assert done["status"] == "failed" and "ffmpeg" in done["error"]


def test_retranscribe_without_audio_is_409(client):
    mid = client.post("/api/v1/meetings", json={}).json()["id"]
    assert client.post(f"/api/v1/meetings/{mid}/retranscribe").status_code == 409


def v2_meta(**over):
    meta = {
        "schema_version": 2, "started": "2026-03-04T10:00:00Z", "ended": "2026-03-04T10:30:00Z",
        "duration_seconds": 1800, "status": "recovered",
        "tracks": [
            {"kind": "mic", "speaker": "me", "status": "complete", "interruptions": [], "warnings": [],
             "segments": [{"file": "mic.caf", "start_offset_ms": 0, "end_offset_ms": 4000, "frames_written": 1,
                           "sample_rate_hz": 48000, "channels": 1}]},
            {"kind": "system", "speaker": "them", "status": "recovered", "interruptions": [], "warnings": [],
             "segments": [{"file": "system.caf", "start_offset_ms": 10000, "end_offset_ms": 14000, "frames_written": 1,
                           "sample_rate_hz": 48000, "channels": 2},
                          {"file": "system-002.caf", "start_offset_ms": 60000, "end_offset_ms": 64000,
                           "frames_written": 1, "sample_rate_hz": 48000, "channels": 2}]},
        ],
    }  # fmt: skip
    meta.update(over)
    return meta


@requires_vad
def test_import_desktop_v2_session_with_caf(client, tmp_path):
    ffmpeg("-i", str(TEST_WAV), "-ar", "48000", "-c:a", "alac", str(tmp_path / "mic.caf"))
    ffmpeg("-i", str(TEST_WAV), "-ac", "2", "-c:a", "pcm_s16le", str(tmp_path / "system.caf"))
    (tmp_path / "system-002.caf").write_bytes((tmp_path / "system.caf").read_bytes())
    files = [("file", (n, (tmp_path / n).read_bytes())) for n in ("mic.caf", "system.caf", "system-002.caf")]
    files.append(("file", ("meta.json", json.dumps(v2_meta()).encode())))
    r = client.post("/api/v1/imports/quill", files=files, data={"title": "From my Mac"})
    assert r.status_code == 201 and r.json()["source"] == "import" and r.json()["status"] == "processing"
    mid = r.json()["id"]
    done = wait_status(client, mid, "ready")
    assert done["started_at"] == "2026-03-04T10:00:00Z" and done["capture_status"] == "recovered"
    segs = client.get(f"/api/v1/meetings/{mid}/transcript").json()["segments"]
    assert [(s["speaker"], s["start_ms"] // 1000) for s in segs] == [("me", 0), ("them", 10), ("them", 60)]
    assert "capture: recovered" in client.get(f"/api/v1/meetings/{mid}/transcript", params={"format": "md"}).text


@requires_vad
def test_import_desktop_v1_session(client, tmp_path):
    ffmpeg("-i", str(TEST_WAV), "-c:a", "pcm_s16le", str(tmp_path / "mic.caf"))
    meta = {"started": "2025-12-01T09:00:00Z", "files": {"mic": "mic.caf"}, "start_offset_ms": {"mic": 2500}}
    r = client.post("/api/v1/imports/quill", files=[
        ("file", ("mic.caf", (tmp_path / "mic.caf").read_bytes())), ("file", ("meta.json", json.dumps(meta).encode()))])  # fmt: skip
    mid = r.json()["id"]
    wait_status(client, mid, "ready")
    segs = client.get(f"/api/v1/meetings/{mid}/transcript").json()["segments"]
    assert segs[0]["speaker"] == "me" and 2500 <= segs[0]["start_ms"] < 3000


def test_import_with_transcript_json_is_not_retranscribed(client):
    engine = state_of(client).worker.engine
    transcript = {"engine": "parakeet", "model": "parakeet-tdt-0.6b-v2-coreml", "created_at": "2026-03-04T10:31:00Z",
                  "segments": [{"speaker": "me", "start_ms": 1000, "end_ms": 2000, "text": "Imported hello."},
                               {"speaker": "them", "start_ms": 3000, "end_ms": 4000, "text": "Imported reply."}]}  # fmt: skip
    r = client.post("/api/v1/imports/quill", files=[
        ("file", ("meta.json", json.dumps(v2_meta()).encode())),
        ("file", ("transcript.json", json.dumps(transcript).encode())),
        ("file", ("mic.caf", b"audio-not-decoded")), ("file", ("system.caf", b"x")), ("file", ("system-002.caf", b"x")),
    ])  # fmt: skip
    assert r.status_code == 201 and r.json()["status"] == "ready"
    body = r.json()
    assert body["engine"] == "parakeet" and body["duration_ms"] == 1_800_000 and engine.calls == 0
    segs = client.get(f"/api/v1/meetings/{body['id']}/transcript").json()["segments"]
    assert [s["text"] for s in segs] == ["Imported hello.", "Imported reply."] and segs[0]["track"] == "mic"
    assert client.get("/api/v1/search", params={"q": "reply"}).json()["hits"][0]["meeting_id"] == body["id"]


def test_import_validation(client):
    assert client.post("/api/v1/imports/quill", files=[("file", ("x.caf", b"x"))]).status_code == 400
    bad = client.post("/api/v1/imports/quill", files=[("file", ("meta.json", b"{nope"))])
    assert bad.status_code == 400
    no_files = client.post("/api/v1/imports/quill", files=[("file", ("meta.json", json.dumps(v2_meta()).encode()))])
    assert no_files.status_code == 400 and no_files.json()["error"]["code"] == "no_audio"
    assert client.get("/api/v1/meetings").json()["meetings"] == []  # failed imports leave nothing behind
    evil = client.post("/api/v1/imports/quill", files=[
        ("file", ("meta.json", json.dumps(v2_meta()).encode())), ("file", ("../../evil.caf", b"x"))])  # fmt: skip
    assert evil.status_code == 400


@requires_vad
def test_openai_compatible_endpoint_formats(client, pair):
    me, _ = pair
    post = lambda **data: client.post("/v1/audio/transcriptions", files={"file": ("a.wav", me.read_bytes())}, data=data)  # noqa: E731
    j = post(model="whisper-1").json()
    assert j == {"text": "fake chunk 1"}
    v = post(model="my-model", response_format="verbose_json", language="en", **{"timestamp_granularities[]": ["segment", "word"]}).json()
    assert v["task"] == "transcribe" and v["model"] == "my-model" and v["language"] == "en"
    assert v["segments"][0]["text"].startswith("fake chunk") and 3 < v["duration"] < 4.5
    assert [w["word"] for w in v["words"]] == ["fake", "chunk"]
    assert post(response_format="text").text.startswith("fake chunk")
    assert post(response_format="srt").text.startswith("1\n00:00:00,")
    assert post(response_format="vtt").text.startswith("WEBVTT")
    assert post(response_format="xml").status_code == 400
    assert client.post("/api/v1/audio/transcriptions", files={"file": ("a.wav", me.read_bytes())}).json()["text"]
    bad = client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"garbage")})
    assert bad.status_code == 422
    assert client.post("/v1/audio/transcriptions", headers={"Authorization": ""}, files={"file": ("a.wav", b"x")}).status_code == 401


@requires_parakeet
def test_openai_endpoint_with_real_parakeet(make_client, pair):
    from .conftest import MODELS_DIR, PARAKEET

    c = make_client(real_engine=True, asr_threads=4, models_dir=MODELS_DIR, asr_model=PARAKEET)
    assert c.get("/health").json()["model_ready"] is True and c.get("/health").json()["engine"] == "parakeet"
    me, _ = pair
    r = c.post("/v1/audio/transcriptions", files={"file": ("en.wav", me.read_bytes())},
               data={"response_format": "verbose_json", "timestamp_granularities[]": ["word"]})  # fmt: skip
    body = r.json()
    assert body["text"] == "Ask not what your country can do for you, ask what you can do for your country."
    assert body["words"][0]["word"] == "Ask" and body["words"][-1]["word"] == "country."
    assert 0 <= body["segments"][0]["start"] < 0.5 and body["segments"][0]["end"] > 3.0
