"""A scripted stand-in for a Jitsi server: config.js plus a fake lib-jitsi-meet.

Serves over HTTPS (self-signed) the two files the bot page loads from the
Jitsi host. The fake library implements only the API surface the bot uses, with
the event and error names checked against lib-jitsi-meet. Remote audio is real:
an oscillator travels through a local RTCPeerConnection pair, so the bot's
`getOriginalStream()` is a genuine remote WebRTC stream (the Chromium case that
needs the muted media element).

Rooms behave by name: `lobby*` forces MEMBERS_ONLY then admits after joinLobby
(`lobby-deny*` denies, `lobby-hold*` waits forever), `pw*` needs the password `letmein`, `closed*` refuses guests,
`stuck*` never answers, anything else joins at once. Tests drive the
conference through `window.__fake`.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response

PASSWORD = "letmein"

FAKE_LIB = r"""
(function () {
  const E = {
    conference: {
      TRACK_ADDED: 'conference.trackAdded', TRACK_REMOVED: 'conference.trackRemoved',
      USER_JOINED: 'conference.userJoined', USER_LEFT: 'conference.userLeft',
      DISPLAY_NAME_CHANGED: 'conference.displayNameChanged', CONFERENCE_JOINED: 'conference.joined',
      CONFERENCE_FAILED: 'conference.failed', KICKED: 'conference.kicked',
    },
    connection: {
      CONNECTION_ESTABLISHED: 'connection.connectionEstablished', CONNECTION_FAILED: 'connection.connectionFailed',
      CONNECTION_DISCONNECTED: 'connection.connectionDisconnected',
    },
  };
  const ERR = {
    conference: {
      MEMBERS_ONLY_ERROR: 'conference.connectionError.membersOnly', PASSWORD_REQUIRED: 'conference.passwordRequired',
      CONFERENCE_ACCESS_DENIED: 'conference.connectionError.accessDenied', CONFERENCE_DESTROYED: 'conference.destroyed',
      AUTHENTICATION_REQUIRED: 'conference.authenticationRequired', NOT_ALLOWED_ERROR: 'conference.connectionError.notAllowed',
      CONFERENCE_MAX_USERS: 'conference.max_users', CONFERENCE_TIME_LIMIT: 'conference.time_limit',
      VIDEOBRIDGE_NOT_AVAILABLE: 'conference.videobridgeNotAvailable', ICE_FAILED: 'conference.iceFailed',
      FOCUS_DISCONNECTED: 'conference.focusDisconnected', FOCUS_LEFT: 'conference.focusLeft',
      GRACEFUL_SHUTDOWN: 'conference.gracefulShutdown',
    },
    connection: {},
  };
  class Emitter {
    constructor() { this.h = {}; }
    addEventListener(ev, fn) { (this.h[ev] = this.h[ev] || []).push(fn); }
    emit(ev, ...a) { (this.h[ev] || []).slice().forEach((fn) => fn(...a)); }
  }
  const fake = (window.__fake = { calls: [], participants: new Map(), messages: [], remote: {} });
  const later = (fn, ms = 20) => setTimeout(fn, ms);

  async function remoteStream(freq) {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    osc.frequency.value = freq;
    const gain = ctx.createGain();
    gain.gain.value = 0.5;
    const dest = ctx.createMediaStreamDestination();
    osc.connect(gain); gain.connect(dest); osc.start();
    const a = new RTCPeerConnection(), b = new RTCPeerConnection();
    a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate);
    b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
    const got = new Promise((r) => (b.ontrack = (e) => r(e.streams[0])));
    dest.stream.getTracks().forEach((t) => a.addTrack(t, dest.stream));
    await a.setLocalDescription(await a.createOffer());
    await b.setRemoteDescription(a.localDescription);
    await b.setLocalDescription(await b.createAnswer());
    await a.setRemoteDescription(b.localDescription);
    return got;
  }

  class Conference extends Emitter {
    constructor(name, options) {
      super();
      this.name = name; this.options = options; this.joined = false; this.displayName = '';
      fake.conference = this; fake.confOptions = options; fake.roomName = name;
    }
    setDisplayName(n) { this.displayName = n; fake.displayName = n; }
    join(password) {
      fake.joinPassword = password;
      fake.calls.push('join');
      const room = decodeURIComponent(this.name);
      if (room.startsWith('lobby')) return later(() => this.emit(E.conference.CONFERENCE_FAILED, ERR.conference.MEMBERS_ONLY_ERROR));
      if (room.startsWith('pw') && password !== '__PASSWORD__') return later(() => this.emit(E.conference.CONFERENCE_FAILED, ERR.conference.PASSWORD_REQUIRED));
      if (room.startsWith('stuck')) return; // never answers
      if (room.startsWith('closed')) return later(() => this.emit(E.conference.CONFERENCE_FAILED, ERR.conference.NOT_ALLOWED_ERROR));
      later(() => { this.joined = true; this.emit(E.conference.CONFERENCE_JOINED); });
    }
    joinLobby(name, email) {
      fake.calls.push('joinLobby');
      fake.lobbyName = name;
      if (decodeURIComponent(this.name).startsWith('lobby-hold')) return new Promise(() => {}); // waits forever
      return new Promise((resolve) => later(() => {
        resolve();
        if (!(fake.lobbyDecision === 'deny' || decodeURIComponent(this.name).startsWith('lobby-deny'))) later(() => { this.joined = true; this.emit(E.conference.CONFERENCE_JOINED); }, 300);
        else this.emit(E.conference.CONFERENCE_FAILED, ERR.conference.CONFERENCE_ACCESS_DENIED);
      }));
    }
    getParticipants() { return [...fake.participants.values()]; }
    getParticipantById(id) { return fake.participants.get(id); }
    sendTextMessage(m) { fake.messages.push(m); }
    setReceiverConstraints(c) { fake.receiverConstraints = c; }
    async leave() { fake.calls.push('leave'); this.joined = false; }
  }

  class Connection extends Emitter {
    constructor(appId, token, options) {
      super(); this.options = options; fake.token = token; fake.connectionOptions = options;
    }
    connect(o) { fake.calls.push('connect'); fake.connectOptions = o; later(() => this.emit(E.connection.CONNECTION_ESTABLISHED)); }
    initJitsiConference(name, options) { return new Conference(name, options); }
    async disconnect() { fake.calls.push('disconnect'); }
  }

  window.JitsiMeetJS = {
    init(o) { fake.initOptions = o; },
    setLogLevel() {}, logLevels: { WARN: 'warn' },
    events: E, errors: ERR, JitsiConnection: Connection,
  };

  // ---- test controls -------------------------------------------------------
  fake.addParticipant = async (id, name, freq, withAudio = true) => {
    const p = { _id: id, _name: name, tracks: [] };
    p.getId = () => id; p.getDisplayName = () => p._name; p.isHidden = () => false;
    p.getTracks = () => p.tracks;
    fake.participants.set(id, p);
    fake.conference.emit(E.conference.USER_JOINED, id, p);
    if (withAudio) {
      const stream = await remoteStream(freq || 440);
      const track = { isLocal: () => false, getType: () => 'audio', getParticipantId: () => id, getOriginalStream: () => stream, isMuted: () => false };
      p.tracks.push(track); p.track = track;
      fake.conference.emit(E.conference.TRACK_ADDED, track);
    }
  };
  fake.removeTrack = (id) => { const p = fake.participants.get(id); if (p && p.track) fake.conference.emit(E.conference.TRACK_REMOVED, p.track); };
  fake.removeParticipant = (id) => {
    const p = fake.participants.get(id);
    if (!p) return;
    if (p.track) fake.conference.emit(E.conference.TRACK_REMOVED, p.track);
    fake.participants.delete(id);
    fake.conference.emit(E.conference.USER_LEFT, id, p);
  };
  fake.rename = (id, name) => { const p = fake.participants.get(id); p._name = name; fake.conference.emit(E.conference.DISPLAY_NAME_CHANGED, id, name); };
  fake.destroy = () => fake.conference.emit(E.conference.CONFERENCE_FAILED, ERR.conference.CONFERENCE_DESTROYED);
  fake.kick = () => fake.conference.emit(E.conference.KICKED);
})();
""".replace("__PASSWORD__", PASSWORD)


def make_cert(directory: Path) -> tuple[Path, Path]:
    key, crt = directory / "key.pem", directory / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
         "-days", "2", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return key, crt


@dataclass
class FakeJitsi:
    port: int
    requests: list[str]

    @property
    def host(self) -> str:
        return f"localhost:{self.port}"

    def url(self, room: str, query: str = "") -> str:
        return f"https://{self.host}/{room}{query}"


async def start_fake_jitsi(tmp: Path):
    """Returns (FakeJitsi, stop coroutine)."""
    key, crt = make_cert(tmp)
    app = FastAPI()
    requests: list[str] = []

    @app.get("/config.js")
    async def config_js():
        requests.append("/config.js")
        body = (
            "var config = {hosts: {domain: 'fake.jitsi', muc: 'muc.fake.jitsi'},"
            " bosh: '/http-bind', websocket: '//localhost:__PORT__/xmpp-websocket', p2p: {enabled: true}, disableAudioLevels: false};"
        )
        return Response(body.replace("__PORT__", str(app.state.port)), media_type="text/javascript")

    @app.get("/libs/lib-jitsi-meet.min.js")
    async def lib():
        requests.append("/libs/lib-jitsi-meet.min.js")
        return Response(FAKE_LIB, media_type="text/javascript")

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", ssl_keyfile=str(key), ssl_certfile=str(crt))
    )
    task = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    app.state.port = port

    async def stop():
        server.should_exit = True
        await asyncio.wait_for(task, 10)

    return FakeJitsi(port=port, requests=requests), stop
