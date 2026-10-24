from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pytest
from fastapi.testclient import TestClient

from quill_server.app import create_app
from quill_server.asr import Utterance, Word
from quill_server.audio import decode_pcm16
from quill_server.config import Config
from quill_server.state import AppState

MODELS_DIR = Path(os.environ.get("QUILL_MODELS_DIR", "/nonexistent"))
PARAKEET = "parakeet-tdt-0.6b-v3-int8"
HAVE_VAD = (MODELS_DIR / "silero_vad.onnx").is_file()
HAVE_PARAKEET = (MODELS_DIR / PARAKEET / "tokens.txt").is_file()
TEST_WAV = MODELS_DIR / PARAKEET / "test_wavs" / "en.wav"

requires_vad = pytest.mark.skipif(not HAVE_VAD, reason="QUILL_MODELS_DIR lacks silero_vad.onnx")
requires_parakeet = pytest.mark.skipif(
    not (HAVE_VAD and HAVE_PARAKEET and TEST_WAV.is_file()), reason="QUILL_MODELS_DIR lacks the Parakeet model"
)

ADMIN = "correct horse"
API_KEY = "bootstrap-key-for-tests"


class FakeEngine:
    """Deterministic engine: one utterance per chunk, text numbered by call."""

    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.calls = 0

    def ready(self) -> bool:
        return True

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Utterance]:
        self.calls += 1
        ms = len(samples) * 1000 // 16000
        return [Utterance(0, ms, f"fake chunk {self.calls}", [Word("fake", 0, ms // 2), Word("chunk", ms // 2, ms)])]


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(
        data_dir=tmp_path / "data",
        models_dir=MODELS_DIR if HAVE_VAD else tmp_path / "models",
        admin_password=ADMIN,
        api_key=API_KEY,
        session_secret="s" * 32,
        internal_token="internal-token",
        compress_audio=False,
        public_url="https://quill.test",
        owner_email="owner@example.com",
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(engine=None, real_engine=False, webhook_client=None, notes_client=None, **overrides) -> TestClient:
        state = AppState.create(
            make_config(tmp_path, **overrides), engine=None if real_engine else (engine or FakeEngine()),
            webhook_client=webhook_client, notes_client=notes_client,
        )  # fmt: skip
        client = TestClient(create_app(state=state), headers={"Authorization": f"Bearer {API_KEY}"})
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in reversed(clients):
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def state_of(client: TestClient) -> AppState:
    return client.app.state.quill


def wait_for(predicate: Callable[[], bool], timeout: float = 30.0, interval: float = 0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def wait_status(client: TestClient, meeting_id: str, status: str, timeout: float = 60.0) -> dict:
    last: dict = {}

    def check() -> bool:
        nonlocal last
        last = client.get(f"/api/v1/meetings/{meeting_id}").json()
        return last["status"] == status or last["status"] == "failed"

    wait_for(check, timeout)
    return last


def speech_pcm() -> bytes:
    """The model package's English test clip as 16 kHz mono PCM16."""
    return decode_pcm16(TEST_WAV).tobytes()


def frames(pcm: bytes, track_id: str, ms: int = 100) -> Iterator[bytes]:
    step = 16 * 2 * ms
    prefix = bytes([len(track_id)]) + track_id.encode()
    for i in range(0, len(pcm), step):
        yield prefix + pcm[i : i + step]


def seed_meeting(client: TestClient, segments: list[tuple[str, int, int, str]], title: str = "Seeded") -> str:
    """A ready meeting with given (speaker, start_ms, end_ms, text) segments, no audio."""
    s = state_of(client)

    async def create() -> str:  # on the app's event loop, like production code
        row = s.meetings.create(title, "api")
        s.meetings.replace_segments(
            row["id"],
            [{"track_id": "", "speaker": sp, "start_ms": a, "end_ms": b, "text": t} for sp, a, b, t in segments],
        )
        s.meetings.complete(row["id"], engine="fake", model="fake-1")
        return row["id"]

    return client.portal.call(create)


class FakeBot:
    """Stands in for quill_server.bot.manager.BotManager (owned by another module)."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.bots: dict[str, dict] = {}
        self.joins: list[tuple] = []

    async def join(self, url, title, display_name, password) -> str:
        if "evil" in url:
            raise ValueError("host not allowed")
        self.joins.append((url, title, display_name, password))
        meeting_id = await self.state.hub.create_meeting(title or "Jitsi call", "bot")
        self.bots[meeting_id] = {"meeting_id": meeting_id, "state": "starting", "error": None, "url": url}
        return meeting_id

    async def leave(self, meeting_id: str) -> None:
        if meeting_id not in self.bots:
            raise KeyError(meeting_id)
        self.bots[meeting_id]["state"] = "ended"

    def list(self) -> list[dict]:
        return list(self.bots.values())

    async def shutdown(self) -> None:
        pass


def install_fake_bot(client: TestClient) -> FakeBot:
    state = state_of(client)
    bot = FakeBot(state)
    state.bot, state._bot_checked = bot, True
    return bot


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[tuple[str, AppState]]:
    """The real app on a real port, for SSE and the MCP HTTP client."""
    import socket
    import threading

    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    state = AppState.create(make_config(tmp_path, jitsi_domains=("meet.example.com",)), engine=FakeEngine())
    server = uvicorn.Server(uvicorn.Config(create_app(state=state), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert wait_for(lambda: server.started, 20)
    yield f"http://127.0.0.1:{port}", state
    server.should_exit = True
    thread.join(15)
