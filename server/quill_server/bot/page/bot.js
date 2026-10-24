/*
 * Quill Jitsi notetaker: runs inside headless Chromium (see manager.py).
 *
 * Joins a Jitsi conference receive-only with lib-jitsi-meet loaded from the
 * Jitsi server itself, pulls each remote participant's audio through the
 * AudioWorklet and streams 16 kHz PCM16 to Quill's live ingest WebSocket.
 * API names below were checked against lib-jitsi-meet and jitsi-meet sources;
 * see ../README.md for the files.
 *
 * Python talks to this page through `window.quillBotEvent(event)` (page ->
 * Python, exposed by Playwright) and `window.QuillBot` (Python -> page).
 */
(function () {
  'use strict';

  const ASSET_BASE = '/bot/page/';
  const NAME_WAIT_MS = 1500; // wait for a display name before naming a track
  const REANNOUNCE_GAP_MS = 120000;
  const STEP_TIMEOUT_MS = 6000;

  const query = new URLSearchParams(location.search);
  const params = {
    internal: query.get('internal') || '',
    host: query.get('host') || '',
    room: query.get('room') || '',
    meeting: query.get('meeting') || '',
    name: query.get('name') || 'Quill Notetaker',
    announce: query.get('announce') === '1',
  };
  // Secrets are injected by Python as an init script so they never sit in a URL.
  const secrets = window.__quillSecrets || {};

  const state = {
    value: 'starting',
    error: null,
    reason: null,
    others: 0,
    wsOpen: false,
    joined: false,
  };
  const peers = new Map(); // participant id -> {track, regName, timer, chain}
  const trackOwner = new Map(); // JitsiTrack -> participant id at add time
const earlyTracks = new Set(); // tracks seen before the ingest socket was open
  let connection = null;
  let conference = null;
  let ingest = null;
  let pipeline = null;
  let finishing = null;
  let lastAnnounce = 0;
  let announceTimer = null;

  const JM = () => window.JitsiMeetJS;

  function report(event) {
    try {
      if (typeof window.quillBotEvent === 'function') window.quillBotEvent(event);
    } catch (e) {
      /* Python side is gone; nothing to tell */
    }
  }

  function log(msg) {
    console.log('[quill-bot] ' + msg); // Python keeps these lines (see session.py) for failure reports
  }

  function setState(value, extra) {
    state.value = value;
    if (extra && 'error' in extra) state.error = extra.error;
    if (extra && 'reason' in extra) state.reason = extra.reason;
    const el = document.getElementById('status');
    if (el) el.textContent = value + (state.error ? ': ' + state.error : '');
    report(Object.assign({ type: 'state', state: value, error: state.error, reason: state.reason }, extra || {}));
  }

  const withTimeout = (promise, ms, what) =>
    Promise.race([
      Promise.resolve(promise),
      new Promise((_, reject) => setTimeout(() => reject(new Error(what + ' timed out')), ms)),
    ]);

  function loadScript(url) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url;
      s.onload = resolve;
      s.onerror = () => reject(new Error('could not load ' + url));
      document.head.appendChild(s);
    });
  }

  // ---- naming (matches jitsi-meet's room normalisation) --------------------

  function normalizedRoom(room) {
    let r = room;
    try {
      r = decodeURIComponent(r);
    } catch (e) {
      /* not percent-encoded */
    }
    return r.normalize('NFKC').toLowerCase();
  }

  // jitsi-meet getBackendSafeRoomName: encodeURIComponent(normalized).toLowerCase()
  const backendRoomName = (room) => encodeURIComponent(normalizedRoom(room)).toLowerCase();

  /** Resolve a config.js URL (absolute, //host, or /path) against the Jitsi origin. */
  function resolveServiceUrl(raw, websocket) {
    const u = new URL(raw, 'https://' + params.host + '/');
    if (websocket) u.protocol = 'wss:';
    return u;
  }

  function buildConnectionOptions(config) {
    const options = Object.assign({}, config);
    const useWs = !!config.websocket && !config.preferBosh;
    const raw = useWs ? config.websocket : config.bosh;
    if (!raw) throw new Error('config.js has neither websocket nor bosh');
    const url = resolveServiceUrl(raw, useWs);
    url.searchParams.append('room', normalizedRoom(params.room)); // as jitsi-meet's constructOptions
    options.serviceUrl = url.toString();
    for (const key of ['websocketKeepAliveUrl', 'conferenceRequestUrl']) {
      if (config[key]) {
        const u = resolveServiceUrl(config[key], false);
        u.searchParams.append('room', normalizedRoom(params.room));
        options[key] = u.toString();
      }
    }
    if (options.bosh) options.bosh = resolveServiceUrl(options.bosh, false).toString();
    if (!options.flags || typeof options.flags.ssrcRewritingEnabled === 'undefined') {
      options.flags = Object.assign({}, options.flags, { ssrcRewritingEnabled: true });
    }
    return options;
  }

  // ---- participants and audio ---------------------------------------------

  const fallbackName = (pid) => 'Guest ' + pid.slice(0, 4);

  function displayNameOf(pid) {
    const p = conference && conference.getParticipantById(pid);
    return String((p && p.getDisplayName && p.getDisplayName()) || '').trim(); // undefined until the nick arrives
  }

  function peerOf(pid) {
    let peer = peers.get(pid);
    if (!peer) peers.set(pid, (peer = { track: null, regName: null, timer: null, chain: Promise.resolve() }));
    return peer;
  }

  /** Serialise add/remove/rename per participant (TRACK_REMOVED + TRACK_ADDED arrive back to back). */
  function enqueue(pid, fn) {
    const peer = peerOf(pid);
    peer.chain = peer.chain.then(fn).catch((e) => log('track op failed for ' + pid + ': ' + e));
    return peer.chain;
  }

  function registerPeer(pid) {
    const peer = peers.get(pid);
    if (!peer || !peer.track || peer.regName) return;
    clearTimeout(peer.timer);
    const name = displayNameOf(pid) || fallbackName(pid);
    peer.regName = name;
    ingest.registerTrack({ trackId: pid, kind: 'participant', speaker: name, label: name, participantId: pid });
    log('track registered: ' + pid + ' as "' + name + '"');
  }

  function startPeerAudio(pid, track) {
    return enqueue(pid, async () => {
      const stream = track.getOriginalStream();
      if (!stream || !pipeline) return;
      const peer = peerOf(pid);
      if (peer.track) await stopPeerAudio(pid, true);
      peer.track = track;
      peer.regName = null;
      pipeline.addStream(pid, stream);
      if (displayNameOf(pid)) registerPeer(pid);
      else peer.timer = setTimeout(() => registerPeer(pid), NAME_WAIT_MS);
    });
  }

  /** Flush the audio worklet first, then close the track on the server. */
  async function stopPeerAudio(pid, keepEntry) {
    const peer = peers.get(pid);
    if (!peer || !peer.track) return;
    clearTimeout(peer.timer);
    peer.track = null;
    if (pipeline) await pipeline.removeTrack(pid);
    if (ingest) {
      // Gone before its name wait elapsed: still deliver the audio that was held back.
      if (!peer.regName && ingest.hasPending(pid)) registerPeerLate(pid);
      ingest.endTrack(pid);
    }
    peer.regName = null;
    if (!keepEntry) peers.delete(pid);
  }

  function registerPeerLate(pid) {
    const name = displayNameOf(pid) || fallbackName(pid);
    ingest.registerTrack({ trackId: pid, kind: 'participant', speaker: name, label: name, participantId: pid });
  }

  function onTrackAdded(track) {
    if (track.isLocal() || track.getType() !== 'audio') return;
    const pid = track.getParticipantId();
    if (!pid) return; // ssrc rewriting: owner is set later and TRACK_ADDED fires again
    if (!state.wsOpen) {
      earlyTracks.add(track); // the ingest socket opens right after CONFERENCE_JOINED
      return;
    }
    const known = peers.get(pid);
    if (known && known.track === track) return;
    trackOwner.set(track, pid);
    startPeerAudio(pid, track);
  }

  function onTrackRemoved(track) {
    const pid = trackOwner.get(track);
    trackOwner.delete(track);
    if (!pid) return;
    const peer = peers.get(pid);
    if (peer && peer.track === track) enqueue(pid, () => stopPeerAudio(pid, true));
  }

  function onUserLeft(id) {
    enqueue(id, () => stopPeerAudio(id, false));
    reportOthers();
  }

  function onDisplayNameChanged(id) {
    const peer = peers.get(id);
    if (!peer || !peer.track) return;
    const name = displayNameOf(id);
    if (!name) return;
    if (!peer.regName) return registerPeer(id);
    if (name === peer.regName) return;
    // The protocol has no rename message: close the track and open it under the new name.
    const track = peer.track;
    enqueue(id, async () => {
      await stopPeerAudio(id, true);
      if (!ingest || finishing) return;
      peerOf(id).track = track;
      pipeline.addStream(id, track.getOriginalStream());
      registerPeer(id);
    });
  }

  function reportOthers() {
    if (!conference) return;
    const others = conference.getParticipants().filter((p) => !p.isHidden()).length;
    state.others = others;
    report({ type: 'participants', others });
  }

  // ---- announcement --------------------------------------------------------

  function announce() {
    if (!params.announce || !conference || !state.joined) return;
    lastAnnounce = Date.now();
    conference.sendTextMessage(params.name + ' is recording and transcribing this meeting.');
  }

  function onUserJoined() {
    reportOthers();
    // Jitsi does not replay chat to late joiners, so repeat the notice (rate limited).
    if (params.announce && Date.now() - lastAnnounce > REANNOUNCE_GAP_MS) {
      clearTimeout(announceTimer);
      announceTimer = setTimeout(announce, 3000);
    }
  }

  // ---- lifecycle -----------------------------------------------------------

  async function onJoined() {
    state.joined = true;
    try {
      ingest = ingest || createIngest();
      await withTimeout(ingest.open(), STEP_TIMEOUT_MS, 'live ingest connect');
      state.wsOpen = true;
      report({ type: 'ingest', event: 'open' });
    } catch (e) {
      return fail('cannot reach the Quill live ingest: ' + e.message);
    }
    // Video is of no use to a notetaker; keep the bridge from sending it.
    try {
      conference.setReceiverConstraints({ lastN: 0 });
    } catch (e) {
      log('setReceiverConstraints failed: ' + e);
    }
    // Tracks that arrived before the socket was open, and any already on participants.
    const early = [...earlyTracks];
    earlyTracks.clear();
    for (const t of early) onTrackAdded(t);
    for (const p of conference.getParticipants()) {
      for (const t of p.getTracks()) onTrackAdded(t);
    }
    setState('joined');
    reportOthers();
    setTimeout(announce, 1000);
  }

  function createIngest() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url =
      proto + '//' + location.host + '/api/v1/meetings/' + encodeURIComponent(params.meeting) +
      '/live?internal=' + encodeURIComponent(params.internal);
    return new QuillProtocol.LiveIngest(url, {
      onClose: (code, reason) => {
        state.wsOpen = false;
        report({ type: 'ingest', event: 'closed', code, reason });
      },
      onMessage: (msg) => {
        if (msg.type === 'error') log('ingest error ' + msg.code + ': ' + msg.message);
        if (msg.type === 'status' && msg.status !== 'live') report({ type: 'ingest', event: 'status', status: msg.status });
      },
    });
  }

  function onConferenceFailed(error, ...args) {
    const E = JM().errors.conference;
    log('conference failed: ' + error + ' ' + JSON.stringify(args.filter((a) => typeof a !== 'object')));
    switch (error) {
      case E.MEMBERS_ONLY_ERROR:
        setState('lobby');
        conference
          .joinLobby(params.name, '')
          .catch((e) => fail('could not enter the lobby: ' + (e && e.message ? e.message : e)));
        return;
      case E.CONFERENCE_ACCESS_DENIED:
        return fail('a moderator denied access from the lobby');
      case E.PASSWORD_REQUIRED:
        return fail(secrets.password ? 'the room password was rejected' : 'the room needs a password');
      case E.AUTHENTICATION_REQUIRED:
      case E.NOT_ALLOWED_ERROR:
        return fail(
          secrets.jwt
            ? 'the Jitsi server rejected the token'
            : 'the room is not open to guests (secure domain: a moderator must start it, or pass ?jwt=)',
        );
      case E.CONFERENCE_DESTROYED:
        return finish('ended', 'conference_ended');
      case E.CONFERENCE_MAX_USERS:
        return fail('the room is full');
      case E.CONFERENCE_TIME_LIMIT:
        return finish('ended', 'time_limit');
      case E.VIDEOBRIDGE_NOT_AVAILABLE:
      case E.ICE_FAILED:
      case E.FOCUS_DISCONNECTED:
      case E.FOCUS_LEFT:
      case E.GRACEFUL_SHUTDOWN:
      default:
        return state.joined ? finish('ended', 'conference_lost: ' + error) : fail('conference failed: ' + error);
    }
  }

  function fail(message) {
    log('FAILED: ' + message);
    return finish('failed', 'error', message);
  }

  /**
   * Single exit path: flush audio, tell the server the meeting is over, leave
   * Jitsi, release the audio context. Idempotent; Python closes the browser.
   */
  function finish(finalState, reason, error) {
    if (finishing) return finishing;
    finishing = (async () => {
      if (finalState !== 'failed') setState('leaving', { reason });
      clearTimeout(announceTimer);
      for (const pid of [...peers.keys()]) {
        await withTimeout(stopPeerAudio(pid, false), STEP_TIMEOUT_MS, 'flush ' + pid).catch(() => {});
      }
      if (ingest && state.wsOpen) {
        ingest.stop();
        report({ type: 'ingest', event: 'stop_sent' });
        await new Promise((r) => setTimeout(r, 300));
      }
      if (ingest) ingest.close();
      try {
        if (conference) await withTimeout(conference.leave(), STEP_TIMEOUT_MS, 'conference.leave');
      } catch (e) {
        log('leave: ' + e);
      }
      try {
        if (connection) await withTimeout(connection.disconnect(), STEP_TIMEOUT_MS, 'disconnect');
      } catch (e) {
        log('disconnect: ' + e);
      }
      if (pipeline) await withTimeout(pipeline.close(), STEP_TIMEOUT_MS, 'audio close').catch(() => {});
      setState(finalState, { reason, error: error || null });
    })();
    return finishing;
  }

  function startConference() {
    const E = JM().events.conference;
    const config = window.config;
    const conferenceOptions = Object.assign({}, config, {
      // Receive-only, so stay on the bridge: P2P would flap tracks when a peer joins or leaves.
      p2p: Object.assign({}, config.p2p, { enabled: false }),
    });
    conference = connection.initJitsiConference(backendRoomName(params.room), conferenceOptions);

    conference.addEventListener(E.TRACK_ADDED, onTrackAdded);
    conference.addEventListener(E.TRACK_REMOVED, onTrackRemoved);
    conference.addEventListener(E.USER_JOINED, onUserJoined);
    conference.addEventListener(E.USER_LEFT, onUserLeft);
    conference.addEventListener(E.DISPLAY_NAME_CHANGED, onDisplayNameChanged);
    conference.addEventListener(E.CONFERENCE_JOINED, onJoined);
    conference.addEventListener(E.CONFERENCE_FAILED, onConferenceFailed);
    conference.addEventListener(E.KICKED, () => finish('ended', 'kicked'));

    // jitsi-meet publishes the name before join() so it is in the first presence.
    conference.setDisplayName(params.name);
    conference.join(secrets.password || '');
  }

  function connect() {
    const C = JM().events.connection;
    const options = buildConnectionOptions(window.config);
    connection = new (JM().JitsiConnection)(null, secrets.jwt || null, options);
    connection.addEventListener(C.CONNECTION_ESTABLISHED, startConference);
    connection.addEventListener(C.CONNECTION_FAILED, (err, msg) => fail('connection failed: ' + err + (msg ? ' ' + msg : '')));
    connection.addEventListener(C.CONNECTION_DISCONNECTED, () => {
      if (!finishing) finish('ended', 'connection_lost');
    });
    connection.connect({ name: backendRoomName(params.room) });
  }

  async function main() {
    if (!params.internal || !params.meeting || !params.room) throw new Error('missing page parameters');
    if (!/^(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:\d{1,5})?$/i.test(params.host)) throw new Error('bad host parameter');
    const base = 'https://' + params.host;
    // Same-version rule: the library and config come from the user's own server.
    await loadScript(base + '/config.js');
    await loadScript(base + '/libs/lib-jitsi-meet.min.js');
    if (!window.config || !window.JitsiMeetJS) throw new Error('config.js or lib-jitsi-meet did not load');

    pipeline = new QuillPipeline.AudioPipeline(
      (trackId, pcm) => ingest && ingest.pcm(trackId, pcm),
      { log },
    );
    await pipeline.init(ASSET_BASE + 'pcm-worklet.js?internal=' + encodeURIComponent(params.internal));
    ingest = createIngest();

    JM().init({ disableAudioLevels: true, disableThirdPartyRequests: true });
    JM().setLogLevel(JM().logLevels.WARN);
    connect();
  }

  window.QuillBot = {
    /** Called by Python on leave(): flush, stop, disconnect. */
    leave: (reason) => finish('ended', reason || 'leave'),
    snapshot: () => ({
      state: state.value,
      error: state.error,
      others: state.others,
      wsOpen: state.wsOpen,
      tracks: [...peers.entries()].filter(([, p]) => p.track).map(([id, p]) => ({ id, name: p.regName })),
      framesSent: ingest ? ingest.framesSent : 0,
      droppedFrames: ingest ? ingest.droppedFrames : 0,
      audioRate: pipeline ? pipeline.sampleRate : 0,
      remote: conference
        ? conference.getParticipants().map((p) => ({
            id: p.getId(),
            name: p.getDisplayName(),
            hidden: p.isHidden(),
            tracks: p.getTracks().map((t) => t.getType() + (t.isMuted() ? ':muted' : '')),
          }))
        : [],
    }),
  };

  main().catch((e) => fail(e && e.message ? e.message : String(e)));
})();
