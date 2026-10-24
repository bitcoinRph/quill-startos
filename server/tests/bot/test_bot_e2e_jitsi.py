"""End to end against a real Jitsi (docker-jitsi-meet). Opt-in.

    QUILL_E2E_JITSI_HOST=localhost:8443 \
    QUILL_E2E_WAV=/path/to/speech.wav \
    pytest tests/bot/test_bot_e2e_jitsi.py -s

A second headless Chromium joins as "Alice" and plays the wav through a fake
microphone. The bot joins the same room and must deliver Alice's PCM, named
"Alice", to the (test double of the) live ingest socket. The Jitsi server uses
a self-signed certificate, so the bot runs with its test-only insecure TLS flag.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import wave
from pathlib import Path

import pytest

from botkit import TOKEN, chromium_path
from quill_server.bot.manager import BotManager
from quill_server.bot.settings import BotSettings
from quill_server.bot.urls import parse_allowlist

HOST = os.environ.get("QUILL_E2E_JITSI_HOST")
WAV = os.environ.get("QUILL_E2E_WAV")

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not HOST or not WAV, reason="set QUILL_E2E_JITSI_HOST and QUILL_E2E_WAV"),
]


def transcribe(wav_path: Path) -> None:
    """Optional: run Parakeet over what the bot captured (needs sherpa-onnx in another interpreter).

    QUILL_E2E_SHERPA_PYTHON=<python with sherpa_onnx>  QUILL_E2E_MODEL_DIR=<parakeet model dir>
    and QUILL_E2E_EXPECT=<word expected in the speech> (default: country, for the JFK clip).
    """
    py, model = os.environ.get("QUILL_E2E_SHERPA_PYTHON"), os.environ.get("QUILL_E2E_MODEL_DIR")
    if not py or not model:
        return
    code = (
        "import sys,wave,numpy as np,sherpa_onnx as s\n"
        "d=sys.argv[1]+'/'\n"
        "r=s.OfflineRecognizer.from_transducer(encoder=d+'encoder.int8.onnx',decoder=d+'decoder.int8.onnx',"
        "joiner=d+'joiner.int8.onnx',tokens=d+'tokens.txt',num_threads=2,model_type='nemo_transducer')\n"
        "w=wave.open(sys.argv[2]);a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768\n"
        "st=r.create_stream();st.accept_waveform(w.getframerate(),a);r.decode_stream(st);print(st.result.text)\n"
    )
    out = subprocess.run([py, "-c", code, model, str(wav_path)], capture_output=True, text=True, timeout=120)
    print("transcript of what the bot received:", out.stdout.strip())
    assert os.environ.get("QUILL_E2E_EXPECT", "country").lower() in out.stdout.lower(), out.stderr[-500:]


class Speaker:
    """A second headless Chromium in the Jitsi room, talking from a wav file."""

    def __init__(self, browser, page):
        self.browser, self.page = browser, page

    async def js(self, expr: str):
        return await self.page.evaluate(expr)

    async def my_id(self) -> str:
        return await self.js("APP.conference._room.myUserId()")

    async def close(self):
        try:
            await self.browser.close()
        except Exception:
            pass


async def join_as_speaker(pw, room: str, name: str, wav: str) -> Speaker:
    args = [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav}",
        "--ignore-certificate-errors",
        "--autoplay-policy=no-user-gesture-required",
    ]
    kwargs = {"headless": True, "args": args}
    if chromium_path():
        kwargs["executable_path"] = chromium_path()
    browser = await pw.chromium.launch(**kwargs)
    ctx = await browser.new_context(ignore_https_errors=True)
    page = await ctx.new_page()
    page.on("console", lambda m: print("[speaker]", m.text[:160]) if "error" in m.type else None)
    # Keep the prejoin page: bypassing it by URL makes Jitsi start without any media.
    frag = f'#config.startWithVideoMuted=true&userInfo.displayName="{name}"'
    await page.goto(f"https://{HOST}/{room}{frag}")
    await page.locator('[data-testid="prejoin.joinMeeting"]').click(timeout=30000)
    await page.wait_for_function("APP.conference._room && APP.conference._room.isJoined()", timeout=30000)
    return Speaker(browser, page)


async def wait_for(cond, timeout: float, what: str):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if cond():
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what}")


async def test_bot_hears_named_speaker(quill_double, playwright_instance, tmp_path):
    room = "quill-e2e-" + secrets.token_hex(4)
    settings = BotSettings(
        allowed_hosts=parse_allowlist(HOST),
        port=quill_double.port,
        internal_token=TOKEN,
        chromium_path=chromium_path(),
        insecure_tls=True,
        alone_minutes=0.25,
        announce=True,
    )
    mgr = BotManager(settings=settings)
    speaker = None
    try:
        speaker = await join_as_speaker(playwright_instance, room, "Alice", WAV)
        await asyncio.sleep(4)  # let Alice become the room's moderator first
        mid = await mgr.join(f"https://{HOST}/{room}", title="e2e", display_name="Quill Notetaker")
        rec = quill_double.rec
        assert rec.meetings[-1] == {"id": mid, "title": "e2e", "source": "bot"}
        await wait_for(lambda: (mgr.get(mid) or {}).get("state") in {"joined", "failed"}, 60, "bot to join")
        assert mgr.get(mid)["state"] == "joined", mgr.get(mid)
        assert mgr.get(mid)["participants"] >= 1

        await wait_for(lambda: any(t.get("type") == "track" for t in rec.texts.get(mid, [])), 40, "track registration")
        track = next(t for t in rec.texts[mid] if t.get("type") == "track")
        assert track["kind"] == "participant"
        assert track["speaker"] == "Alice" and track["label"] == "Alice"
        assert track["sample_rate"] == 16000
        assert track["participant_id"] == track["track_id"]

        await asyncio.sleep(12)
        pcm = rec.decoded(mid)
        samples = pcm[track["track_id"]]
        seconds = len(samples) / 16000
        rms = (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5
        out = Path(os.environ.get("QUILL_E2E_OUT", tmp_path))
        out.mkdir(parents=True, exist_ok=True)
        wav_path = out / "alice-16k.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"".join(int(s).to_bytes(2, "little", signed=True) for s in samples))
        print(f"\nreceived {seconds:.1f}s of audio for {track['speaker']!r}, rms={rms:.0f}, wrote {wav_path}")
        assert seconds > 8
        assert rms > 300, "audio is silent: the remote stream did not reach Web Audio"
        transcribe(wav_path)

        chat = await speaker.js("APP.store.getState()['features/chat'].messages.map(m => m.message)")
        assert any("is recording and transcribing" in m for m in chat), chat
        # Alice leaves: the alone timer ends the bot and a stop reaches the server.
        await speaker.close()
        speaker = None
        await wait_for(lambda: (mgr.get(mid) or {}).get("state") in {"ended", "failed"}, 60, "bot to leave when alone")
        final = mgr.get(mid)
        assert final["state"] == "ended" and final["reason"] == "alone", final
        types = rec.types(mid)
        assert "track_end" in types and types[-1] == "stop"
    finally:
        if speaker:
            await speaker.close()
        await mgr.shutdown()


async def wait_for_js(speaker: Speaker, expr: str, timeout: float):
    await speaker.page.wait_for_function(expr, timeout=timeout * 1000)


def make_manager(quill_double, **overrides) -> BotManager:
    base = dict(
        allowed_hosts=parse_allowlist(HOST),
        port=quill_double.port,
        internal_token=TOKEN,
        chromium_path=chromium_path(),
        insecure_tls=True,
        alone_minutes=5,
    )
    base.update(overrides)
    return BotManager(settings=BotSettings(**base))


def tracks_of(rec, mid):
    return [t for t in rec.texts.get(mid, []) if t.get("type") in {"track", "track_end"}]


async def test_two_speakers_rename_and_rejoin(quill_double, playwright_instance):
    """Two named tracks; a rename and a rejoin close and reopen tracks with the right labels."""
    room = "quill-e2e-" + secrets.token_hex(4)
    fr = str(Path(WAV).with_name("fr.wav"))
    mgr = make_manager(quill_double)
    alice = bob = bob2 = None
    try:
        alice = await join_as_speaker(playwright_instance, room, "Alice", WAV)
        bob = await join_as_speaker(playwright_instance, room, "Bob", fr)
        mid = await mgr.join(f"https://{HOST}/{room}")
        rec = quill_double.rec
        names = lambda: sorted(t["speaker"] for t in rec.texts.get(mid, []) if t.get("type") == "track")
        await wait_for(lambda: names() == ["Alice", "Bob"], 60, "both speakers registered")
        reg = {t["speaker"]: t["track_id"] for t in rec.texts[mid] if t["type"] == "track"}
        assert reg["Alice"] == await alice.my_id() and reg["Bob"] == await bob.my_id()

        await asyncio.sleep(6)
        pcm = rec.decoded(mid)
        for who in ("Alice", "Bob"):
            s = pcm[reg[who]]
            assert len(s) / 16000 > 4 and (sum(x * x for x in s) / len(s)) ** 0.5 > 300, who

        # Rename: the protocol has no rename message, so the track is closed and reopened.
        await alice.js("APP.conference._room.setDisplayName('Alicia')")
        await wait_for(lambda: "Alicia" in names(), 20, "renamed track")
        evs = [(t["type"], t.get("speaker")) for t in tracks_of(rec, mid) if t.get("track_id", "") == reg["Alice"]]
        assert evs == [("track", "Alice"), ("track_end", None), ("track", "Alicia")], evs

        # Bob leaves and comes back as a new participant: his old track ends, a new one opens.
        await bob.close()
        await wait_for(lambda: any(t["type"] == "track_end" and t["track_id"] == reg["Bob"] for t in rec.texts[mid]), 20, "Bob's track_end")
        bob2 = await join_as_speaker(playwright_instance, room, "Bob", fr)
        await wait_for(lambda: names().count("Bob") == 2, 40, "Bob's second track")
        assert await bob2.my_id() != reg["Bob"]
    finally:
        for p in (alice, bob, bob2):
            if p:
                await p.close()
        await mgr.shutdown()


async def test_lobby_admit_and_deny(quill_double, playwright_instance):
    room = "quill-e2e-" + secrets.token_hex(4)
    mgr = make_manager(quill_double)
    alice = None
    try:
        alice = await join_as_speaker(playwright_instance, room, "Alice", WAV)
        await alice.page.wait_for_function("APP.conference._room.getRole() === 'moderator'", timeout=20000)
        await alice.js("APP.conference._room.enableLobby()")
        # Listen on the library directly; this is what the jitsi-meet UI would show as "knocking".
        await alice.js("window.__knock = []; APP.conference._room.on('conference.lobby.userJoined', (id, name) => window.__knock.push({id, name}))")
        await asyncio.sleep(1)
        rec = quill_double.rec

        mid = await mgr.join(f"https://{HOST}/{room}")
        await wait_for(lambda: mgr.get(mid)["state"] == "lobby", 40, "lobby state")
        await wait_for_js(alice, "window.__knock.length === 1", 20)
        knocking = await alice.js("window.__knock")
        assert [k["name"] for k in knocking] == ["Quill Notetaker"], knocking
        await alice.js(f"APP.conference._room.lobbyApproveAccess('{knocking[0]['id']}')")
        await wait_for(lambda: mgr.get(mid)["state"] == "joined", 40, "admitted")
        await wait_for(lambda: any(t.get("type") == "track" for t in rec.texts.get(mid, [])), 40, "audio after admission")
        await mgr.leave(mid)
        assert mgr.get(mid)["state"] == "ended"

        mid2 = await mgr.join(f"https://{HOST}/{room}")
        await wait_for(lambda: mgr.get(mid2)["state"] == "lobby", 40, "lobby state (2)")
        await wait_for_js(alice, "window.__knock.length === 2", 20)
        knocking = await alice.js("window.__knock")
        await alice.js(f"APP.conference._room.lobbyDenyAccess('{knocking[1]['id']}')")
        await wait_for(lambda: mgr.get(mid2)["state"] == "failed", 40, "denied")
        assert "denied" in mgr.get(mid2)["error"]
    finally:
        if alice:
            await alice.close()
        await mgr.shutdown()


async def test_room_password(quill_double, playwright_instance):
    room = "quill-e2e-" + secrets.token_hex(4)
    mgr = make_manager(quill_double)
    alice = None
    try:
        alice = await join_as_speaker(playwright_instance, room, "Alice", WAV)
        await alice.page.wait_for_function("APP.conference._room.getRole() === 'moderator'", timeout=20000)
        await alice.js("APP.conference._room.lock('s3cret')")
        await asyncio.sleep(1)

        for password, needle in ((None, "needs a password"), ("wrong", "rejected")):
            mid = await mgr.join(f"https://{HOST}/{room}", password=password)
            await wait_for(lambda: mgr.get(mid)["state"] == "failed", 40, f"failure for password={password!r}")
            assert needle in mgr.get(mid)["error"], mgr.get(mid)
            assert "wrong" not in str(mgr.get(mid)) or password == "wrong"  # state never carries the password back
        mid = await mgr.join(f"https://{HOST}/{room}", password="s3cret")
        await wait_for(lambda: mgr.get(mid)["state"] == "joined", 40, "joined with the password")
        assert "s3cret" not in str(mgr.list())
    finally:
        if alice:
            await alice.close()
        await mgr.shutdown()


async def test_self_signed_certificate_is_refused_by_default(quill_double):
    """Without the test-only flag a bad certificate must stop the bot."""
    mgr = make_manager(quill_double, insecure_tls=False)
    try:
        mid = await mgr.join(f"https://{HOST}/quill-e2e-tls")
        await wait_for(lambda: mgr.get(mid)["state"] == "failed", 40, "TLS failure")
        assert "config.js" in mgr.get(mid)["error"]
    finally:
        await mgr.shutdown()


async def test_hard_time_limit_sends_stop(quill_double, playwright_instance):
    room = "quill-e2e-" + secrets.token_hex(4)
    mgr = make_manager(quill_double, max_minutes=0.4)
    alice = None
    try:
        alice = await join_as_speaker(playwright_instance, room, "Alice", WAV)
        mid = await mgr.join(f"https://{HOST}/{room}")
        rec = quill_double.rec
        await wait_for(lambda: (mgr.get(mid) or {}).get("state") in {"ended", "failed"}, 60, "time limit")
        assert mgr.get(mid)["state"] == "ended" and mgr.get(mid)["reason"] == "max_duration"
        assert rec.types(mid)[-1] == "stop" and not rec.stops  # the page sent stop itself; no REST fallback
    finally:
        if alice:
            await alice.close()
        await mgr.shutdown()
