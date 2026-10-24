"""BotManager + bot page against a scripted fake Jitsi (no Docker needed).

Real: Chromium, the bot page and its scripts, the router, the WebSocket, the
WebRTC remote streams. Fake: the Jitsi server (see fakejitsi.py).
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from botkit import TOKEN, chromium_path
from fakejitsi import PASSWORD, start_fake_jitsi
from quill_server.bot.manager import BotManager
from quill_server.bot.settings import BotSettings
from quill_server.bot.urls import BotError, parse_allowlist

pytestmark = pytest.mark.browser


@pytest.fixture
async def fake(tmp_path):
    jitsi, stop = await start_fake_jitsi(tmp_path)
    try:
        yield jitsi
    finally:
        await stop()


@pytest.fixture
async def make(quill_double, fake):
    """Factory for managers wired to the double server and the fake Jitsi; all are shut down."""
    created: list[BotManager] = []

    def factory(**overrides) -> BotManager:
        base = dict(
            allowed_hosts=parse_allowlist(fake.host),
            port=quill_double.port,
            internal_token=TOKEN,
            chromium_path=chromium_path(),
            insecure_tls=True,
            alone_minutes=0,  # off unless a test turns it on
            max_minutes=0,
            announce=True,
        )
        base.update(overrides.pop("settings", {}))
        mgr = BotManager(settings=BotSettings(**base), **overrides)
        created.append(mgr)
        return mgr

    yield factory
    for mgr in created:
        await mgr.shutdown()


async def until(predicate, timeout: float = 20, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def state(mgr, mid):
    return (mgr.get(mid) or {}).get("state")


async def page_js(mgr, mid, expr, arg=None):
    return await mgr._sessions[mid]._page.evaluate(expr, arg)


async def joined(mgr, fake, room="room1", **kw):
    mid = await mgr.join(fake.url(room), **kw)
    await until(lambda: state(mgr, mid) in {"joined", "failed", "lobby"}, 30, "bot to reach a stable state")
    return mid


def peak_hz(samples, skip_s=1.0):
    x = np.asarray(samples[int(16000 * skip_s):], dtype=float)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / 16000)[spec.argmax()])


# ---- happy path --------------------------------------------------------------


async def test_join_stream_named_tracks_and_leave(make, fake, quill_double):
    mgr = make()
    mid = await joined(mgr, fake, "Standup", title="Daily", display_name="  Note\x07taker  ")
    rec = quill_double.rec
    assert state(mgr, mid) == "joined"
    assert rec.meetings == [{"id": mid, "title": "Daily", "source": "bot"}]
    info = mgr.get(mid)
    assert info["display_name"] == "Notetaker" and info["room"] == "Standup" and info["host"] == fake.host
    assert await page_js(mgr, mid, "__fake.displayName") == "Notetaker"
    # jitsi-meet's room handling: lowercased conference name, `room` param on the service URL
    assert await page_js(mgr, mid, "__fake.roomName") == "standup"
    opts = await page_js(mgr, mid, "__fake.connectionOptions")
    assert opts["serviceUrl"] == f"wss://{fake.host}/xmpp-websocket?room=standup"
    assert opts["bosh"] == f"https://{fake.host}/http-bind"
    assert opts["flags"]["ssrcRewritingEnabled"] is True
    assert (await page_js(mgr, mid, "__fake.confOptions.p2p")) == {"enabled": False}
    assert await page_js(mgr, mid, "__fake.receiverConstraints") == {"lastN": 0}
    assert await page_js(mgr, mid, "__fake.initOptions") == {"disableAudioLevels": True, "disableThirdPartyRequests": True}

    await page_js(mgr, mid, "[__fake.addParticipant('aaaa1111', 'Alice', 440), __fake.addParticipant('bbbb2222', 'Bob', 880)]")
    await until(lambda: len([t for t in rec.texts[mid] if t["type"] == "track"]) == 2, 20, "two tracks")
    regs = {t["speaker"]: t for t in rec.texts[mid] if t["type"] == "track"}
    assert regs["Alice"] == {"type": "track", "track_id": "aaaa1111", "kind": "participant", "speaker": "Alice",
                             "label": "Alice", "participant_id": "aaaa1111", "sample_rate": 16000}
    assert regs["Bob"]["track_id"] == "bbbb2222"
    await until(lambda: True, 0.1)
    messages = []
    for _ in range(40):
        messages = await page_js(mgr, mid, "__fake.messages")
        if messages:
            break
        await asyncio.sleep(0.1)
    assert messages == ["Notetaker is recording and transcribing this meeting."]
    assert mgr.get(mid)["participants"] == 2

    await asyncio.sleep(4)
    pcm = rec.decoded(mid)
    assert abs(peak_hz(pcm["aaaa1111"]) - 440) < 8
    assert abs(peak_hz(pcm["bbbb2222"]) - 880) < 8, "tracks must not be mixed"

    browser = mgr._sessions[mid]._browser
    final = await mgr.leave(mid)
    assert final["state"] == "ended" and final["reason"] == "leave"
    types = rec.types(mid)
    assert types[-1] == "stop" and types.count("track_end") == 2
    assert types.index("stop") > max(i for i, t in enumerate(types) if t == "track_end")
    assert not browser.is_connected(), "Chromium must be closed"
    assert await until(lambda: rec.sockets_open == 0, 5, "socket closed")
    assert not rec.stops, "the page sent stop itself, no REST fallback needed"


async def test_name_arrives_after_track_is_still_one_track(make, fake, quill_double):
    """A track waits briefly for the display name instead of starting as a placeholder."""
    mgr = make()
    mid = await joined(mgr, fake)
    rec = quill_double.rec
    await page_js(mgr, mid, "__fake.addParticipant('cccc3333', '', 440)")
    await asyncio.sleep(0.4)
    assert "track" not in rec.types(mid)
    await page_js(mgr, mid, "__fake.rename('cccc3333', 'Carol')")
    await until(lambda: "track" in rec.types(mid), 5, "registration")
    assert [t["speaker"] for t in rec.texts[mid] if t["type"] == "track"] == ["Carol"]


async def test_unnamed_participant_gets_a_stable_placeholder(make, fake, quill_double):
    mgr = make()
    mid = await joined(mgr, fake)
    await page_js(mgr, mid, "__fake.addParticipant('dddd4444', '', 440)")
    rec = quill_double.rec
    await until(lambda: "track" in rec.types(mid), 6, "registration after the name wait")
    assert [t["speaker"] for t in rec.texts[mid] if t["type"] == "track"] == ["Guest dddd"]


async def test_rename_reopens_track_and_rejoin_is_a_new_registration(make, fake, quill_double):
    mgr = make()
    mid = await joined(mgr, fake)
    rec = quill_double.rec
    await page_js(mgr, mid, "__fake.addParticipant('eeee5555', 'Eve', 440)")
    await until(lambda: "track" in rec.types(mid), 10, "first track")
    await asyncio.sleep(1)
    await page_js(mgr, mid, "__fake.rename('eeee5555', 'Evelyn')")
    await until(lambda: [t["type"] for t in rec.texts[mid]].count("track") == 2, 10, "reopened track")
    # participant leaves and comes back with the same id
    await page_js(mgr, mid, "__fake.removeParticipant('eeee5555')")
    await until(lambda: [t["type"] for t in rec.texts[mid]].count("track_end") == 2, 10, "track_end on leave")
    await page_js(mgr, mid, "__fake.addParticipant('eeee5555', 'Evelyn', 440)")
    await until(lambda: [t["type"] for t in rec.texts[mid]].count("track") == 3, 10, "re-registration")
    seq = [(t["type"], t.get("speaker")) for t in rec.texts[mid] if t["type"] in {"track", "track_end"}]
    assert seq == [("track", "Eve"), ("track_end", None), ("track", "Evelyn"), ("track_end", None), ("track", "Evelyn")]
    # audio kept flowing on the re-opened track (the frames after the last registration)
    before = len(rec.frames[mid])
    await asyncio.sleep(1.5)
    assert len(rec.frames[mid]) > before


async def test_track_remapped_to_another_owner(make, fake, quill_double):
    """ssrc rewriting reuses a track: TRACK_REMOVED then TRACK_ADDED with a new owner."""
    mgr = make()
    mid = await joined(mgr, fake)
    rec = quill_double.rec
    await page_js(mgr, mid, "__fake.addParticipant('ffff6666', 'Fay', 440)")
    await page_js(mgr, mid, "__fake.addParticipant('gggg7777', 'Gus', 440, false)")
    await until(lambda: "track" in rec.types(mid), 10, "Fay's track")
    await page_js(mgr, mid, """() => {
      const f = __fake, t = f.participants.get('ffff6666').track;
      f.conference.emit('conference.trackRemoved', t);
      t.getParticipantId = () => 'gggg7777';
      f.conference.emit('conference.trackAdded', t);
    }""")
    await until(lambda: [t.get("speaker") for t in rec.texts[mid] if t["type"] == "track"] == ["Fay", "Gus"], 10, "Gus track")
    await until(lambda: [t["track_id"] for t in rec.texts[mid] if t["type"] == "track_end"] == ["ffff6666"], 10, "Fay's track_end")


# ---- leave rules -----------------------------------------------------------


async def test_alone_timer(make, fake, quill_double):
    mgr = make(settings={"alone_minutes": 0.06})  # 3.6 s
    mid = await joined(mgr, fake)
    started = time.monotonic()
    await until(lambda: state(mgr, mid) == "ended", 20, "alone timeout")
    assert 3 < time.monotonic() - started < 12
    assert mgr.get(mid)["reason"] == "alone"
    assert quill_double.rec.types(mid)[-1] == "stop"


async def test_alone_timer_resets_when_someone_is_present(make, fake, quill_double):
    mgr = make(settings={"alone_minutes": 0.06})
    mid = await joined(mgr, fake)
    await page_js(mgr, mid, "__fake.addParticipant('hhhh8888', 'Hal', 440)")
    await asyncio.sleep(6)
    assert state(mgr, mid) == "joined", "someone is there, so the bot stays"
    await page_js(mgr, mid, "__fake.removeParticipant('hhhh8888')")
    await until(lambda: state(mgr, mid) == "ended", 20, "alone timeout after the last person left")
    assert mgr.get(mid)["reason"] == "alone"


async def test_max_duration(make, fake, quill_double):
    mgr = make(settings={"max_minutes": 0.05})
    mid = await joined(mgr, fake)
    await page_js(mgr, mid, "__fake.addParticipant('iiii9999', 'Ian', 440)")
    await until(lambda: state(mgr, mid) == "ended", 20, "hard stop")
    assert mgr.get(mid)["reason"] == "max_duration"
    assert quill_double.rec.types(mid)[-1] == "stop"


async def test_conference_destroyed_ends_the_bot(make, fake, quill_double):
    mgr = make()
    mid = await joined(mgr, fake)
    await page_js(mgr, mid, "__fake.addParticipant('jjjj0000', 'Jo', 440)")
    await until(lambda: "track" in quill_double.rec.types(mid), 10, "track")
    await page_js(mgr, mid, "__fake.destroy()")
    await until(lambda: state(mgr, mid) == "ended", 20, "ended")
    assert mgr.get(mid)["reason"] == "conference_ended"
    assert quill_double.rec.types(mid)[-1] == "stop"


async def test_kicked(make, fake, quill_double):
    mgr = make()
    mid = await joined(mgr, fake)
    await page_js(mgr, mid, "__fake.kick()")
    await until(lambda: state(mgr, mid) == "ended", 20, "ended")
    assert mgr.get(mid)["reason"] == "kicked"


async def test_server_stopping_the_meeting_ends_the_bot(make, fake, quill_double):
    mgr = make()
    mid = await joined(mgr, fake)
    ws = quill_double.rec.ws[mid]
    await ws.close(code=1000)
    await until(lambda: state(mgr, mid) == "ended", 20, "ended")
    assert mgr.get(mid)["reason"] == "server_closed"


async def test_leave_is_idempotent_and_unknown_id_errors(make, fake):
    mgr = make()
    mid = await joined(mgr, fake)
    first = await mgr.leave(mid)
    again = await mgr.leave(mid)
    assert first["state"] == again["state"] == "ended"
    with pytest.raises(BotError) as exc:
        await mgr.leave("nope")
    assert exc.value.code == "not_found"


# ---- lobby, password, failures -----------------------------------------------


async def test_lobby_then_admitted(make, fake, quill_double):
    states: list[str] = []
    mgr = make(on_state=lambda s: states.append(s["state"]))
    mid = await mgr.join(fake.url("lobby-room"), display_name="Bot")
    await until(lambda: state(mgr, mid) == "joined", 30, "admitted after the lobby")
    assert states[:3] == ["starting", "lobby", "joined"] or states[:2] == ["lobby", "joined"], states
    assert await page_js(mgr, mid, "__fake.calls") == ["connect", "join", "joinLobby"]
    assert await page_js(mgr, mid, "__fake.lobbyName") == "Bot"


async def test_lobby_denied(make, fake):
    mgr = make()
    mid = await mgr.join(fake.url("lobby-deny"))
    await until(lambda: state(mgr, mid) == "failed", 30, "denied")
    assert "denied" in mgr.get(mid)["error"]


async def test_password(make, fake):
    mgr = make()
    wrong = await mgr.join(fake.url("pw-room"), password="nope")
    await until(lambda: state(mgr, wrong) == "failed", 20, "wrong password")
    assert "password" in mgr.get(wrong)["error"] and "rejected" in mgr.get(wrong)["error"]
    none = await mgr.join(fake.url("pw-room2"))
    await until(lambda: state(mgr, none) == "failed", 20, "no password")
    assert "needs a password" in mgr.get(none)["error"]
    ok = await mgr.join(fake.url("pw-room3"), password=PASSWORD)
    await until(lambda: state(mgr, ok) == "joined", 20, "right password")
    assert await page_js(mgr, ok, "__fake.joinPassword") == PASSWORD
    dump = str(mgr.list())
    assert PASSWORD not in dump and "nope" not in dump


async def test_room_closed_to_guests(make, fake):
    mgr = make()
    mid = await mgr.join(fake.url("closed-room"))
    await until(lambda: state(mgr, mid) == "failed", 20, "closed room")
    assert "not open to guests" in mgr.get(mid)["error"]


async def test_jwt_goes_to_the_connection_not_the_url(make, fake, quill_double):
    mgr = make()
    mid = await mgr.join(fake.url("jwtroom", "?jwt=aaa.bbb.ccc"))
    await until(lambda: state(mgr, mid) == "joined", 20, "joined")
    assert await page_js(mgr, mid, "__fake.token") == "aaa.bbb.ccc"
    assert "aaa.bbb" not in mgr._sessions[mid]._page.url
    assert "aaa.bbb" not in str(mgr.list())


async def test_certificate_error_without_the_test_flag(make, fake):
    mgr = make(settings={"insecure_tls": False})
    mid = await mgr.join(fake.url("room"))
    await until(lambda: state(mgr, mid) == "failed", 30, "TLS failure")
    assert "config.js" in mgr.get(mid)["error"]


async def test_browser_crash_is_a_failure_and_cleans_up(make, fake):
    mgr = make()
    mid = await joined(mgr, fake)
    sess = mgr._sessions[mid]
    await sess._browser.close()  # simulates the process dying under the bot
    await until(lambda: state(mgr, mid) == "failed", 20, "failure")
    assert "browser" in mgr.get(mid)["error"]


async def test_start_timeout(make, fake, monkeypatch):
    import quill_server.bot.session as session_mod

    monkeypatch.setattr(session_mod, "START_TIMEOUT_S", 2.0)
    mgr = make()
    mid = await mgr.join(fake.url("stuck-room"))
    await until(lambda: state(mgr, mid) == "failed", 20, "start timeout")
    assert "timed out" in mgr.get(mid)["error"]


async def test_lobby_timeout(make, fake, monkeypatch):
    import quill_server.bot.session as session_mod

    monkeypatch.setattr(session_mod, "LOBBY_TIMEOUT_S", 1.5)
    mgr = make()
    mid = await mgr.join(fake.url("lobby-hold"))
    await until(lambda: state(mgr, mid) == "failed", 20, "lobby timeout")
    assert "lobby" in mgr.get(mid)["error"]


# ---- manager rules ---------------------------------------------------------------


async def test_rejections_happen_before_anything_launches(make, fake, quill_double):
    mgr = make()
    for url, code in [
        ("http://" + fake.host + "/room", "invalid_url"),
        (f"https://localhost:{fake.port + 1}/room", "host_not_allowed"),
        ("https://evil.example.org/room", "host_not_allowed"),
        ("https://127.0.0.1:%d/room" % fake.port, "host_not_allowed"),
        (f"https://user:pw@{fake.host}/room", "invalid_url"),
    ]:
        with pytest.raises(BotError) as exc:
            await mgr.join(url)
        assert exc.value.code == code, url
    assert mgr._playwright is None, "no browser may be started for a rejected URL"
    assert quill_double.rec.meetings == []

    disabled = make(settings={"allowed_hosts": ()})
    with pytest.raises(BotError) as exc:
        await disabled.join(fake.url("room"))
    assert exc.value.code == "bot_disabled"

    no_token = make(settings={"internal_token": ""})
    with pytest.raises(BotError) as exc:
        await no_token.join(fake.url("room"))
    assert exc.value.code == "misconfigured"


async def test_same_room_twice_and_concurrency_limit(make, fake):
    mgr = make(settings={"max_concurrent": 2})
    a = await joined(mgr, fake, "alpha")
    with pytest.raises(BotError) as exc:
        await mgr.join(fake.url("ALPHA"))
    assert exc.value.code == "already_joined"
    await joined(mgr, fake, "beta")
    with pytest.raises(BotError) as exc:
        await mgr.join(fake.url("gamma"))
    assert exc.value.code == "limit_reached"
    await mgr.leave(a)
    await joined(mgr, fake, "alpha")  # a finished bot frees the room and a slot
    states = {m["room"]: m["state"] for m in mgr.list()}
    assert states["beta"] == "joined"


async def test_create_meeting_callback_and_state_hook(make, fake, quill_double):
    calls, seen = [], []

    async def create(title, source):
        calls.append((title, source))
        return "custom-id-1"

    mgr = make(create_meeting=create, on_state=lambda s: seen.append((s["meeting_id"], s["state"])))
    mid = await joined(mgr, fake, "cb")
    assert mid == "custom-id-1" and calls == [("Jitsi: cb", "bot")]
    assert quill_double.rec.meetings == [], "the REST fallback must not be used"
    await mgr.leave(mid)
    assert seen[0] == ("custom-id-1", "starting") and seen[-1] == ("custom-id-1", "ended")


async def test_meeting_creation_failure_closes_the_browser(make, fake):
    async def create(title, source):
        raise RuntimeError("db is down")

    mgr = make(create_meeting=create)
    with pytest.raises(BotError) as exc:
        await mgr.join(fake.url("room"))
    assert exc.value.code == "meeting_create_failed" and "db is down" in exc.value.message
    assert mgr.list() == []
    assert mgr._starting == set()


async def test_launch_failure_is_reported(make, fake):
    mgr = make(settings={"chromium_path": "/nonexistent/chromium"})
    with pytest.raises(BotError) as exc:
        await mgr.join(fake.url("room"))
    assert exc.value.code == "launch_failed"
    assert mgr.list() == []


async def test_shutdown_leaves_every_bot(make, fake, quill_double):
    mgr = make()
    ids = [await joined(mgr, fake, f"room{i}") for i in range(2)]
    browsers = [mgr._sessions[i]._browser for i in ids]
    await mgr.shutdown()
    assert all(not b.is_connected() for b in browsers)
    assert {m["state"] for m in mgr.list()} == {"ended"}
    assert all(quill_double.rec.types(i)[-1] == "stop" for i in ids)
    await mgr.shutdown()  # twice is fine
    with pytest.raises(BotError):
        await mgr.join(fake.url("again"))


async def test_page_network_is_restricted(make, fake, quill_double):
    """HTTP(S) from the bot page reaches only the Jitsi host and Quill on loopback."""
    mgr = make()
    mid = await joined(mgr, fake)
    probe = """async (url) => { try { const r = await fetch(url, {mode: 'no-cors'}); return 'reached'; } catch (e) { return 'blocked'; } }"""
    assert await page_js(mgr, mid, probe, "http://example.org/") == "blocked"
    assert await page_js(mgr, mid, probe, "https://example.org/") == "blocked"
    assert await page_js(mgr, mid, probe, "http://169.254.169.254/latest/meta-data") == "blocked"
    assert await page_js(mgr, mid, probe, f"http://127.0.0.1:{quill_double.port + 1}/") == "blocked"
    assert await page_js(mgr, mid, probe, f"https://localhost:{fake.port}/config.js") == "reached"
    assert await page_js(mgr, mid, probe, f"http://127.0.0.1:{quill_double.port}/bot/page/bot.js?internal={TOKEN}") == "reached"


async def test_fallback_stop_when_the_page_cannot(make, fake, quill_double):
    """If the page dies after the socket opened, the manager ends the meeting over REST."""
    mgr = make()
    mid = await joined(mgr, fake)
    sess = mgr._sessions[mid]
    await sess._page.evaluate("() => { window.QuillBot.leave = () => new Promise(() => {}); }")  # page hangs
    import quill_server.bot.session as session_mod

    old = session_mod.PAGE_LEAVE_TIMEOUT_S
    session_mod.PAGE_LEAVE_TIMEOUT_S = 1
    try:
        final = await mgr.leave(mid)
    finally:
        session_mod.PAGE_LEAVE_TIMEOUT_S = old
    assert final["state"] == "ended"
    assert quill_double.rec.stops == [mid]
