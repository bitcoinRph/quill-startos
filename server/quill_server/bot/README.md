# Jitsi notetaker bot

Sends a headless Chromium into a Jitsi meeting, listens to each remote
participant separately and streams their audio to Quill's live ingest. Spec:
[`docs/server-spec.md`](../../../docs/server-spec.md) sections 2, 6 and 8.

```text
BotManager.join(url)                        server core (HTTP routes, MCP)
  parse + allowlist check  ---- BotError -->  400 / 403 / 409
  launch Chromium (Playwright)
  create meeting row  (callback or POST /api/v1/meetings)
  open  http://127.0.0.1:PORT/bot/page?internal=...&host=...&room=...&meeting=...
        |
        |  page/bot.html -> protocol.js, pipeline.js, bot.js, pcm-worklet.js
        |  bot.js loads  https://<host>/config.js  and  https://<host>/libs/lib-jitsi-meet.min.js
        v
  lib-jitsi-meet joins receive-only; per remote audio track:
  MediaStream -> muted <audio> + MediaStreamSource -> AudioWorklet (16 kHz PCM16)
        -> ws://127.0.0.1:PORT/api/v1/meetings/<id>/live?internal=...   (spec section 6)
```

## Files

| File | Purpose |
|---|---|
| `manager.py` | `BotManager`: validation, capacity, meeting row, lifecycle. Contract in its docstring. |
| `session.py` | One bot: Chromium, network allowlist, time limits, alone timer, cleanup. |
| `urls.py` | URL parsing and the host allowlist (`BotError`, `parse_jitsi_url`). |
| `settings.py` | Config object or `QUILL_*` environment variables. |
| `routes.py` | `router`: `GET /bot/page` and `/bot/page/<asset>`, token protected. |
| `page/` | The in-browser bot (`bot.html`, `bot.js`, `protocol.js`, `pipeline.js`, `pcm-worklet.js`). |

## Interface for the server core

```python
from quill_server.bot import BotManager, BotError
from quill_server.bot.routes import router          # app.include_router(router)

bots = BotManager(
    config,                                          # Config or None (then os.environ)
    live_hub=None,                                   # reserved
    create_meeting=async_fn,                         # optional: async (title: str, source: str) -> meeting_id
    on_state=fn,                                     # optional: (state_dict) -> Any, may be async
)
meeting_id: str = await bots.join(url, title=None, display_name=None, password=None)
state: dict     = await bots.leave(meeting_id)       # waits until Chromium is closed
bots.list() -> list[dict]; bots.get(meeting_id) -> dict | None
await bots.shutdown()                                # call on app shutdown
```

* `BotError` has `.code` and `.message`: `bot_disabled`, `invalid_url`,
  `host_not_allowed`, `already_joined`, `limit_reached`, `misconfigured`,
  `launch_failed`, `meeting_create_failed`, `not_found`.
  Suggested HTTP mapping: `invalid_url` 400, `host_not_allowed` 403,
  `bot_disabled` 403, `already_joined` and `limit_reached` 409, `not_found` 404,
  the rest 500/502.
* State dict: `meeting_id, state, error, reason, host, room, title,
  display_name, participants, created_at, joined_at, ended_at`. `state` is
  `starting | lobby | joined | leaving | ended | failed`. `reason` says why a
  bot ended (`leave`, `alone`, `max_duration`, `conference_ended`, `kicked`,
  `server_closed`, `connection_lost`, `shutdown`). Password and JWT never appear.
* Without `create_meeting` the manager calls `POST /api/v1/meetings` on
  `127.0.0.1:QUILL_PORT` with `Authorization: Bearer <QUILL_INTERNAL_TOKEN>` and
  body `{"title", "source": "bot"}`; it reads `id` (or `meeting.id`) from the
  JSON answer. The same Bearer is used for the fallback
  `POST /api/v1/meetings/{id}/stop`, which the manager only calls when the page
  opened the live socket but died before sending `stop`.
* A bot that fails before it joined leaves its meeting in `scheduled`. Use
  `on_state` to delete it or mark it failed.
* The router reads the token from `app.state.config.internal_token`, else from
  `QUILL_INTERNAL_TOKEN`. Mount it without a prefix.
* Call `await bots.shutdown()` when the app stops: it makes every bot send
  `stop`, leave Jitsi and close Chromium.

## Configuration

Read from the `config` object (`jitsi_domains`, `bot_name`, `bot_announce`,
`bot_max_minutes`, `bot_alone_minutes`, `port`, `internal_token`) and, when an
attribute is missing, from the environment variable of spec section 2. Extras:

| Variable | Default | Meaning |
|---|---|---|
| `QUILL_BOT_MAX_CONCURRENT` | `3` | Chromium instances at once (about 300 to 500 MB each). |
| `QUILL_CHROMIUM_PATH` | unset | Use this Chromium instead of Playwright's. For sandboxes only. |
| `QUILL_BOT_TEST_INSECURE_TLS` | `false` | **Tests only.** Accept a self-signed Jitsi certificate. |

`QUILL_BOT_MAX_MINUTES=0` and `QUILL_BOT_ALONE_MINUTES=0` switch that limit off.
Fractions work (`0.5`). Fixed timeouts: 90 s to get into the conference, 10 min
waiting in a lobby.

## Safety rules

* `QUILL_JITSI_DOMAINS` is a comma list of `host` or `host:port` (IPv6 as
  `[::1]:8443`). Matching is exact and case-insensitive, https only. A port that
  is not listed is rejected (so is `:443` unless it is the default), as are
  userinfo, backslashes, control characters, any IP literal that is not listed
  and any path that is not exactly one room segment (Jitsi tenant paths are
  not supported). Bad list entries are dropped with a warning, never widened.
* The browser never receives the caller's URL; Quill builds the page URL from
  the parsed host and room. The JWT (`?jwt=` in the meeting URL) and the room
  password are injected as a page init script, not into any URL.
* `context.route` allows HTTP(S) only to the Jitsi host and to
  `127.0.0.1:QUILL_PORT`, so the page cannot be made to read the LAN. It does
  not cover WebSocket or WebRTC: those go to wherever the allowlisted server's
  own `config.js` points.
* `/bot/page` answers 403 without the internal token (constant-time compare,
  empty token never matches) and sends `Referrer-Policy: no-referrer`, because
  the page loads scripts from another origin.

## How the page joins (API checked against sources)

Checked on 2026.10.24 against `master` of
[jitsi/lib-jitsi-meet](https://github.com/jitsi/lib-jitsi-meet) and
[jitsi/jitsi-meet](https://github.com/jitsi/jitsi-meet), and against the
`lib-jitsi-meet.min.js` shipped in the `jitsi/web:stable` image:

| What | Where |
|---|---|
| `JitsiMeetJS.init`, `.events`, `.errors`, `.setLogLevel`, `.logLevels`, `IJitsiMeetJSOptions.disableAudioLevels/disableThirdPartyRequests` | `JitsiMeetJS.ts` |
| `new JitsiConnection(appId, token, options)`, `connect`, `disconnect`, `initJitsiConference`, `addEventListener` | `JitsiConnection.ts` |
| `CONNECTION_ESTABLISHED`, `CONNECTION_FAILED`, `CONNECTION_DISCONNECTED` | `JitsiConnectionEvents.ts` |
| `TRACK_ADDED`, `TRACK_REMOVED`, `USER_JOINED`, `USER_LEFT`, `DISPLAY_NAME_CHANGED`, `CONFERENCE_JOINED`, `CONFERENCE_FAILED`, `KICKED` | `JitsiConferenceEvents.ts` |
| `MEMBERS_ONLY_ERROR`, `PASSWORD_REQUIRED`, `CONFERENCE_ACCESS_DENIED`, `AUTHENTICATION_REQUIRED`, `NOT_ALLOWED_ERROR`, `CONFERENCE_DESTROYED`, `CONFERENCE_MAX_USERS`, `VIDEOBRIDGE_NOT_AVAILABLE`, `ICE_FAILED`, `FOCUS_LEFT` | `JitsiConferenceErrors.ts` |
| Which room event becomes which `CONFERENCE_FAILED` error (incl. lobby denial = `CONFERENCE_ACCESS_DENIED`, destroyed room = `CONFERENCE_DESTROYED`) | `JitsiConferenceEventManager.ts` |
| `join(password)`, `joinLobby(displayName, email)`, `setDisplayName`, `sendTextMessage`, `leave()`, `getParticipants()`, `getParticipantById`, `setReceiverConstraints`, `isP2PEnabled` (`p2p.enabled`) | `JitsiConference.ts` |
| After `joinLobby`, approval re-runs `mainRoom.join(invitePassword)` and fires `CONFERENCE_JOINED`; denial fires `MUC_DENIED_ACCESS` | `modules/xmpp/Lobby.js` |
| `getOriginalStream()` | `modules/RTC/JitsiTrack.ts` |
| `getParticipantId()`, owner changes with ssrc rewriting (`TRACK_REMOVED` then `TRACK_ADDED` on the same track) | `modules/RTC/JitsiRemoteTrack.ts`, `JitsiConferenceEventManager.ts` (`TRACK_OWNER_SET`) |
| `getDisplayName()`, `getId()`, `isHidden()`, `getTracks()` | `JitsiParticipant.ts` |
| `Listenable.addEventListener` | `modules/util/Listenable.ts` |
| Connection options: `serviceUrl = websocket \|\| bosh` (`preferBosh`), `?room=` appended, normalised room name, `flags.ssrcRewritingEnabled` default on | jitsi-meet `react/features/base/connection/actions.any.ts` (`constructOptions`) |
| Relative `bosh` made absolute | jitsi-meet `react/features/base/config/actions.ts` (`setConfig`) |
| Room name: `NFKC(decodeURIComponent(room)).toLowerCase()`; conference name `encodeURIComponent(...).toLowerCase()` | jitsi-meet `react/features/base/util/uri.ts` |
| Display name published before `join()`; password sent with `join(password)`; lobby via `joinLobby(name, email)` | jitsi-meet `base/conference/functions.ts`, `base/conference/actions.any.ts`, `lobby/actions.any.ts` |
| `config.js` defines the global `config`; the library is a UMD bundle exposing `JitsiMeetJS` | `jitsi/web:stable` image contents |

Behaviors the bot chooses: `p2p.enabled = false` (a receive-only client stays
on the bridge, so tracks do not flap when a peer joins), `setReceiverConstraints
({lastN: 0})` (no video), display name set before `join`.

## Chromium needs a media element for remote audio

A remote WebRTC `MediaStream` gives silence to Web Audio
(`createMediaStreamSource`) until the same stream is attached to a media element
(Chromium issues 121673 and 933677). `pipeline.js` attaches it to a muted
`Audio` element, and sends the worklet output through a zero-gain node so the
graph is rendered. `test_bot_audio_path.py::test_chromium_needs_the_element_for_remote_audio`
pins this down on the installed Chromium: bare stream peak 0, attached stream
audible.

## Limitations

* **Secure domains** (Jitsi with `ENABLE_AUTH`, guests not allowed): the room is
  closed to guests until a moderator logs in, and the bot fails with "not open
  to guests". If the Jitsi server accepts tokens, put `?jwt=...` in the meeting
  URL; the bot passes it to `JitsiConnection`. Username/password login is not
  implemented, and the bot does not wait for the host to open the room.
* **JWT** is passed through as given. The bot cannot refresh an expiring token.
* **Lobby**: the bot enters the lobby with its display name and waits up to 10
  minutes for a moderator. A denial fails the bot. A lobby "shared password" is
  tried when `password` is set (it is sent with `join`).
* **Names**: the track is named with the display name known after up to 1.5 s.
  The protocol has no rename message, so a later name change closes the track
  (`track_end`) and opens a new one with the new name (a new segment file).
  An unnamed participant is `Guest <first 4 chars of the id>`.
* **Recording notice**: sent as a chat message (`sendTextMessage`) after the
  join and again, at most every 2 minutes, when someone new joins, because Jitsi
  does not replay chat. It is not a legal notice; some jurisdictions need
  consent before recording.
* **Tenants**: `https://host/tenant/room` is rejected.
* **Audio**: JVB sends audio for all endpoints by default. With very large
  rooms or a server that limits audio forwarding, only forwarded speakers have
  tracks. Participants that mute keep a track open; the silence costs only VAD.
* **Network filter** covers HTTP(S) only (see Safety rules).
* A `config.js` that builds URLs from `window.location` sees `127.0.0.1`.
  Standard Jitsi configs use absolute or root-relative URLs, which are resolved
  against the Jitsi origin.

## Tests

Run from `server/` (the shared `tests/conftest.py` imports the whole app, so
cut it off for these):

```bash
python -m pip install pytest pytest-asyncio pytest-timeout httpx fastapi uvicorn websockets playwright==1.64.1 numpy
PYTHONPATH=. python -m pytest --confcutdir=tests/bot tests/bot --timeout=120
```

* `test_bot_urls.py`, `test_bot_routes.py`, `test_bot_js_units.py` (Node): fast, no browser.
* `test_bot_audio_path.py`: Chromium, real WebRTC loopback, real worklet and frames.
* `test_bot_manager.py`: Chromium plus a scripted fake Jitsi (`fakejitsi.py`).
* `test_bot_e2e_jitsi.py`: opt-in, a real docker-jitsi-meet. Set
  `QUILL_E2E_JITSI_HOST=localhost:8443` and `QUILL_E2E_WAV=<speech.wav>`. A
  second Chromium joins as "Alice" with `--use-file-for-fake-audio-capture`.
  Needs `jitsi/web`, `prosody`, `jicofo`, `jvb` (the `stable` images), with
  `JVB_ADVERTISE_IPS=127.0.0.1`, `ENABLE_IPV6=0`, and `PUBLIC_URL=https://localhost:8443`.

In a sandbox with only a pre-installed Chromium, set `QUILL_CHROMIUM_PATH`
(the tests default to `/opt/pw-browsers/chromium` when it exists).
