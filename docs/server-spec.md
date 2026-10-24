# Quill Server :: implementation spec

Last updated: `2026.10.24`

This is the contract between the parts of the Quill server: the Python
service, the web UI, the Jitsi bot, the StartOS package and the agent skills.
Change it first when a contract changes. See
[ADR-002](decisions/002-server-platform.md) for the reasons.

## 1. Layout

```text
server/
├── pyproject.toml            # package quill_server, Python 3.12
├── Dockerfile                # image for StartOS (x86_64, aarch64)
├── quill_server/
│   ├── __main__.py           # `python -m quill_server` -> uvicorn on QUILL_PORT
│   ├── app.py                # FastAPI app factory, mounts API, MCP, web UI
│   ├── config.py             # env vars (section 2)
│   ├── auth.py               # admin session cookie, API keys, internal token
│   ├── db.py                 # SQLite (WAL) schema + migrations + FTS5
│   ├── storage.py            # session directories (section 4)
│   ├── models.py             # model download + checksum verification
│   ├── asr/                  # engine protocol, parakeet (sherpa-onnx), openai remote
│   ├── live.py               # live ingest: tracks, VAD, segment queue, fan-out
│   ├── jobs.py               # filesystem-backed transcription queue
│   ├── notes.py              # LLM note generation (OpenAI-compatible) + templates
│   ├── webhooks.py           # signed outbound events
│   ├── api/                  # REST routers (section 5)
│   ├── mcp_server.py         # MCP tools (section 7)
│   └── bot/                  # Jitsi bot manager + page (section 8)
├── web/                      # static UI, no build step (section 9)
└── tests/                    # pytest
deploy/startos/               # StartOS package (section 10)
skills/                       # Agent Skills (section 11)
```

Runtime dependencies (pin exact versions in `pyproject.toml`):
`fastapi`, `uvicorn[standard]`, `python-multipart`, `sse-starlette`, `httpx`,
`pydantic`, `argon2-cffi`, `itsdangerous`, `sherpa-onnx==2.0.0`, `numpy`,
`soundfile`, `mcp`, `playwright`. System packages in the image: `ffmpeg`,
`libsndfile1`, Chromium through `playwright install --with-deps chromium`.

## 2. Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `QUILL_DATA_DIR` | `/data` | sessions, `quill.db`, logs (backed up) |
| `QUILL_MODELS_DIR` | `/models` | downloaded models (not backed up) |
| `QUILL_PORT` | `8080` | HTTP port for UI, API, MCP |
| `QUILL_ADMIN_PASSWORD` | (empty) | web UI sign-in; empty = sign-in disabled |
| `QUILL_API_KEY` | (empty) | bootstrap API key (StartOS generates it); also accepted as Bearer |
| `QUILL_SESSION_SECRET` | random per start | cookie signing key |
| `QUILL_INTERNAL_TOKEN` | random per start | bot -> server live ingest token |
| `QUILL_PUBLIC_URL` | (empty) | base URL used in links and webhooks |
| `QUILL_ASR_ENGINE` | `parakeet` | `parakeet` or `openai` |
| `QUILL_ASR_MODEL` | `parakeet-tdt-0.6b-v3-int8` | or `parakeet-tdt-0.6b-v2-int8` (English only) |
| `QUILL_ASR_THREADS` | `0` | 0 = `min(4, cpu_count)` |
| `QUILL_REMOTE_ASR_URL` | (empty) | base URL of an OpenAI-compatible engine, e.g. `http://openwhispr.startos:8081` |
| `QUILL_REMOTE_ASR_KEY` | (empty) | Bearer for the remote engine |
| `QUILL_REMOTE_ASR_MODEL` | `whisper-1` | `model` form field sent to it |
| `QUILL_LLM_BASE_URL` | (empty) | OpenAI-compatible `/v1` base for notes; empty = no server notes |
| `QUILL_LLM_API_KEY` | (empty) | |
| `QUILL_LLM_MODEL` | (empty) | |
| `QUILL_AUTO_NOTES` | `false` | generate notes when a transcript is ready |
| `QUILL_JITSI_DOMAINS` | (empty) | comma list of Jitsi hosts the bot may open; empty = bot disabled |
| `QUILL_BOT_NAME` | `Quill Notetaker` | bot display name |
| `QUILL_BOT_ANNOUNCE` | `true` | bot posts a recording notice in the meeting chat |
| `QUILL_BOT_MAX_MINUTES` | `240` | hard stop |
| `QUILL_BOT_ALONE_MINUTES` | `3` | leave when no other participant for this long |
| `QUILL_AUDIO_RETENTION_DAYS` | `0` | delete audio (keep transcripts) after N days; 0 = keep |
| `QUILL_COMPRESS_AUDIO` | `true` | transcode finished WAV segments to Opus |
| `QUILL_SPEAKER_NAME` | `Me` | label for the browser mic track |
| `QUILL_OWNER_EMAIL` | (empty) | note owner email in the Granola-compatible API |
| `QUILL_BOT_MAX_CONCURRENT` | `3` | most bots in meetings at once |
| `QUILL_CHROMIUM_PATH` | (empty) | Chromium binary for the bot; empty = Playwright's own |
| `QUILL_BOT_TEST_INSECURE_TLS` | `false` | tests only: accept a self-signed Jitsi certificate; never set in production |

## 3. Models

Downloaded on first start into `QUILL_MODELS_DIR`, verified by SHA-256 before
extraction. A partially downloaded file never counts as installed. `GET
/health` reports `model_ready`.

| Id | URL | SHA-256 | Bytes |
|---|---|---|---|
| `parakeet-tdt-0.6b-v3-int8` | `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2` | `5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf` | 487170055 |
| `parakeet-tdt-0.6b-v2-int8` | `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2` | `157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad` | 482468385 |
| `silero-vad` | `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx` | `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` | 643854 |

The archive holds `encoder.int8.onnx`, `decoder.int8.onnx`,
`joiner.int8.onnx`, `tokens.txt` (and `test_wavs/`, which can be discarded).
Installed layout: `QUILL_MODELS_DIR/<model id>/` holds those four files, and
`QUILL_MODELS_DIR/silero_vad.onnx` is the VAD. Load with
`sherpa_onnx.OfflineRecognizer.from_transducer(..., model_type="nemo_transducer")`.
Result tokens that start with a space start a new word; group tokens into
words with the token timestamps.

## 4. Storage

- `QUILL_DATA_DIR/quill.db`: SQLite, WAL, `foreign_keys=ON`. The index and the
  source of truth for the app.
- `QUILL_DATA_DIR/sessions/<yyyy.MM.dd-HHmm>-<id8>/`: one directory per
  meeting, in the shared contract shape: `meta.json` (schema v2 plus the
  ADR-002 extensions), track segment files, `transcript.json`,
  `transcript.md`, `notes.md` (AI notes), `user-notes.md`.
- `transcript.json` keeps the macOS shape (`engine`, `model`, `created_at`,
  `segments[]` of `speaker`, `start_ms`, `end_ms`, `text`) and adds `track`
  and `words` per segment.
- Every file write is atomic (temp file + `os.replace`).

Tables: `meetings` (id TEXT ulid, title, status, source, started_at,
ended_at, duration_ms, session_dir, created_at, updated_at, calendar_url,
user_notes_md, ai_notes_md, ai_notes_source, attendees_json, error),
`tracks` (id, meeting_id, kind, speaker, label, participant_id, status,
started_offset_ms, ended_offset_ms), `segments` (id, meeting_id, track_id,
seq, speaker, start_ms, end_ms, text, words_json), `segments_fts` (FTS5 over
text, content table `segments`), `folders`, `meeting_folders`, `api_keys`
(id, name, prefix, sha256, created_at, last_used_at, revoked_at),
`webhooks` (id, url, secret, events_json, active), `templates` (id, name,
prompt, builtin), `jobs` (id, meeting_id, kind, status, attempts, error,
created_at, updated_at).

Meeting `status`: `scheduled`, `live`, `processing`, `ready`, `failed`.
`source`: `bot`, `browser`, `upload`, `import`, `api`.

## 5. HTTP API

Base `/api/v1`. JSON. Errors are `{"error": {"code": "...", "message": "..."}}`
with a matching HTTP status. Lists use `limit` (1 to 100, default 20) and an
opaque `cursor`; responses carry `has_more` and `cursor`. OpenAPI at
`/openapi.json`.

Auth: `Authorization: Bearer <key>` (API keys `qk_...` or `QUILL_API_KEY`),
or the signed `quill_session` cookie from `POST /api/v1/auth/login`. Cookies
are `HttpOnly`, `SameSite=Strict`. Unauthenticated: `GET /health`,
`POST /api/v1/auth/login`, the static UI shell.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{status, model_ready, engine, model, queue_depth, live_meetings, model_download: {bytes, total} \| null, model_error: str \| null}` |
| POST | `/api/v1/auth/login` / `logout` | admin password -> cookie |
| GET | `/api/v1/meetings` | list; filters `q`, `status`, `source`, `folder_id`, `created_after`, `created_before` |
| POST | `/api/v1/meetings` | create (`title`, `source`, `attendees`) -> meeting in `scheduled` |
| GET | `/api/v1/meetings/{id}` | `include=transcript,notes,tracks` |
| PATCH | `/api/v1/meetings/{id}` | `title`, `user_notes_md`, `attendees`, `speakers` (rename map) |
| DELETE | `/api/v1/meetings/{id}` | delete row and session directory |
| POST | `/api/v1/meetings/{id}/stop` | end a live meeting -> `processing` |
| POST | `/api/v1/meetings/{id}/audio` | multipart `file` (+ `speaker`), repeatable; queues a job |
| GET | `/api/v1/meetings/{id}/audio/{track_id}` | stream the track audio |
| GET | `/api/v1/meetings/{id}/transcript` | `format=json|md|txt|srt|vtt|compact`, `since_seq`, `start_ms`, `end_ms`, `speaker` |
| GET | `/api/v1/meetings/{id}/events` | SSE: `segment`, `status`, `notes` events |
| WS | `/api/v1/meetings/{id}/live` | live ingest (section 6) |
| POST | `/api/v1/meetings/{id}/notes/generate` | `template_id` -> server LLM notes (409 when no LLM) |
| PUT | `/api/v1/meetings/{id}/notes` | `markdown`, `source` (`agent`, `user`, `llm`) -> save AI notes |
| POST | `/api/v1/meetings/{id}/retranscribe` | rerun the offline pass on stored audio |
| GET | `/api/v1/search` | `q`, `limit` -> hits `{meeting_id, title, seq, start_ms, speaker, snippet}` |
| POST | `/api/v1/imports/quill` | multipart: a Quill session (`meta.json`, segment files, optional `transcript.json`) |
| POST | `/api/v1/bot/join` | `url`, `title`, `display_name`, `password` -> meeting |
| POST | `/api/v1/bot/{meeting_id}/leave` | |
| GET | `/api/v1/bot` | active bots and their state |
| GET/POST/DELETE | `/api/v1/folders`, `/api/v1/folders/{id}/meetings/{meeting_id}` | folders |
| GET/POST/DELETE | `/api/v1/keys` | API keys (cookie session only); the secret shows once |
| GET/POST/DELETE | `/api/v1/webhooks` | events `meeting.started`, `meeting.ended`, `transcript.ready`, `notes.ready` |
| GET/POST/PUT/DELETE | `/api/v1/templates` | note templates; built-ins are read-only |
| POST | `/v1/audio/transcriptions` | OpenAI-compatible (also `/api/v1/audio/transcriptions`) |

OpenAI-compatible transcription: multipart `file`, `model` (ignored, echoed),
`language`, `prompt` (ignored), `response_format` `json` | `text` |
`verbose_json` | `srt` | `vtt`, `timestamp_granularities[]` `segment` |
`word`. Synchronous. Limit 500 MB.

Webhook delivery follows [Standard Webhooks](https://www.standardwebhooks.com/),
the same scheme Granola uses: POST JSON `{id, type, created_at, data}` with
headers `webhook-id`, `webhook-timestamp` (unix seconds) and
`webhook-signature: v1,<base64 hmac-sha256(key, "<id>.<timestamp>.<body>")>`,
where the secret is `whsec_<base64 key>` and is shown once on creation.
Retry on 408, 429, 5xx and network errors with backoff 5 s, 30 s, 2 min,
10 min, 1 h; never follow redirects; 15 s timeout.

IDs: meetings are `not_` + 14 base62 characters, folders `fol_` + 14,
webhook endpoints `whe_` + 14, so the native API and the Granola-compatible
API share IDs. Path parameters are case-insensitive on the prefix.

### 5.1 Granola-compatible API

Source of truth: Granola's OpenAPI document
(`https://docs.granola.ai/api-reference/openapi.json`, API v1.6.0 changelog,
read 2026.10.24). A client built for Granola changes only its base URL
(`https://public-api.granola.ai` → the Quill origin) and its API key. Routes
are at the root, next to `/v1/audio/transcriptions`:

| Granola route | Quill behavior |
|---|---|
| `GET /v1/notes` | `created_before`, `created_after`, `updated_after` (date or date-time), `folder_id` (includes child folders), `space_id`, `cursor`, `page_size` 1–30 (default 10). Returns `{notes: NoteSummary[], hasMore, cursor}` (`cursor` null on the last page). Lists meetings in status `ready`. |
| `GET /v1/notes/{note_id}` | `include=transcript`. Returns `Note`. When the transcript has more than 2000 items, `413` with code `TRANSCRIPT_TOO_LARGE`. Unknown id `404`. |
| `GET /v1/notes/{note_id}/transcript` | `cursor`, `page_size` 1–100 (default 50). Returns `{transcript: Transcript[], hasMore, cursor}`. Bad cursor `400`. |
| `GET /v1/folders` | `{folders: Folder[], hasMore, cursor}`, alphabetical, with `parent_folder_id` and `space_id` (null). |
| `GET /v1/spaces` | `{spaces: [], hasMore: false, cursor: null}` (Quill has no spaces). |
| `POST/GET /v1/webhook-endpoints`, `PATCH/DELETE /v1/webhook-endpoints/{id}` | Granola body and output shapes (`url`, `scopes`, `events`, `folder_ids`, `enabled`, `signing_secret` on create). Granola-style deliveries: payload `{event_id, event_type, note_id, occurred_at}` (`note.edited` adds `data.changed_fields: ["summary"]`), Standard Webhooks headers. HTTPS is not required for LAN URLs (self-hosted), unlike Granola. |

Field mapping for `Note`:

- `object: "note"`, `title`, `created_at`, `updated_at` (ISO 8601 UTC),
  `deleted_at: null`.
- `owner`: `{name: QUILL_SPEAKER_NAME, email: QUILL_OWNER_EMAIL}` (new
  variable, default empty string).
- `web_url`: `QUILL_PUBLIC_URL` + `/#/meetings/<id>`.
- `calendar_event`: null unless the meeting has calendar fields.
- `attachments: []`, `space_membership: []`.
- `attendees`: `[{name, email}]` from the meeting attendees and the bot's
  participant names (email `""` when unknown).
- `folder_membership`: the folders that hold the meeting plus their ancestors.
- `summary_markdown`: the AI notes (null when none); `summary_text`: the same
  as plain text (`""` when none).
- `private_notes_markdown` / `private_notes_text`: the user notes (null when
  none).
- `transcript`: null unless `include=transcript`. Items are
  `{speaker: {source, attribution?, name?, diarization_label?}, text,
  start_time, end_time}`; times are ISO 8601 date-times (meeting start +
  offset). Track `mic` → `source: "microphone"`, `attribution: "me"`.
  Track `system` → `source: "speaker"`, `attribution: "them"`. Track
  `participant` → `source: "speaker"`, `name: <display name>`,
  `attribution: "me"` when the name equals `QUILL_SPEAKER_NAME`, else
  `"them"`. Track `upload` → `source: "speaker"`, `diarization_label:
  <speaker label>`.

Documented deviations: Quill lists ready meetings even before AI notes exist
(Granola lists only summarized notes), because agents often write the notes
later. Granola's error body shape is not documented; Quill returns its own
error shape with the same status codes. Granola's MCP server uses OAuth and
its tool arguments are not documented, so Quill does not imitate its MCP
tools; the Quill MCP tools in section 7 cover the same jobs.

## 6. Live ingest protocol (WebSocket)

`WS /api/v1/meetings/{id}/live`. Auth: cookie, `?token=<api key>`, or
`?internal=<QUILL_INTERNAL_TOKEN>` (bot only). The meeting moves to `live` on
the first track.

Client to server:

- Text `{"type": "track", "track_id": "t1", "kind": "mic|system|participant|upload", "speaker": "me", "label": "Alice", "participant_id": "abc", "sample_rate": 16000}`.
  Registers a track. `sample_rate` must be 16000. A track id can be
  re-registered after `track_end` (a participant rejoins); the server opens a
  new segment file for it.
- Binary: byte 0 = length N of the track id, bytes 1..N = track id (UTF-8),
  the rest = PCM signed 16-bit little-endian, mono, 16 kHz. Frames of 20 ms to
  500 ms.
- Text `{"type": "track_end", "track_id": "t1"}`.
- Text `{"type": "note", "markdown": "..."}`: replaces the user notes.
- Text `{"type": "stop"}`: ends the meeting.
- Text `{"type": "ping"}`.

Server to client:

- `{"type": "ready", "meeting_id": "...", "session_offset_ms": 0}`
- `{"type": "segment", "segment": {"seq", "track_id", "speaker", "start_ms", "end_ms", "text"}}`
- `{"type": "status", "status": "live|processing|ready|failed", "lag_ms": 0}`
- `{"type": "error", "code": "...", "message": "..."}`
- `{"type": "pong"}`

Server behavior: one monotonic session clock per meeting. Each track's
offset is the session clock when its first frame arrives; gaps between frames
are not filled. Each track writes a WAV segment (header fixed up every 5 s,
crash-safe). Silero VAD (sherpa-onnx `VoiceActivityDetector`, min silence
0.5 s, max speech 20 s) cuts speech; each cut goes to one shared recognizer
worker queue (one thread, `QUILL_ASR_THREADS`). Results are stored, numbered
with a per-meeting `seq`, and fanned out to WebSocket and SSE listeners.
When the meeting stops, the server flushes the VAD, waits for the queue,
writes `meta.json`, `transcript.json`, `transcript.md`, marks it `ready`,
fires `transcript.ready`, then runs auto notes if enabled.

## 7. MCP

Streamable HTTP at `/mcp` (same port, same Bearer auth as the API). Server
name `quill`. Tool output is compact text by default to save tokens. Every
tool that returns a list takes `limit` and returns a cursor or `next_seq`.

| Tool | Arguments | Returns |
|---|---|---|
| `list_meetings` | `query?`, `status?`, `created_after?`, `created_before?`, `limit=20`, `cursor?` | one line per meeting: id, date, duration, title, status |
| `get_meeting` | `meeting_id` | title, times, attendees, speakers, status, user notes, AI notes |
| `get_transcript` | `meeting_id`, `start_ms?`, `end_ms?`, `speaker?`, `offset=0`, `max_chars=20000` | `[h:mm:ss] Speaker: text` lines, `next_offset` |
| `search_meetings` | `query`, `limit=10` | hits with meeting id, timestamp, speaker, snippet |
| `join_meeting` | `url`, `title?`, `display_name?`, `password?` | meeting id and bot state |
| `leave_meeting` | `meeting_id` | final status |
| `get_live_transcript` | `meeting_id`, `since_seq=0`, `max_chars=8000` | new lines, `next_seq`, status |
| `save_notes` | `meeting_id`, `markdown` | ok (stored as source `agent`, fires `notes.ready`) |
| `generate_notes` | `meeting_id`, `template?` | notes from the server LLM, or an error that tells the agent to write them itself |
| `list_templates` | | template names and prompts |
| `get_status` | | engine, model, queue, live meetings, bot domains |

MCP prompts: `meeting_notes` (args `meeting_id`, `template`) returns the
template plus the transcript, for agents that write notes themselves.

## 8. Jitsi bot

- `BotManager` in `quill_server/bot/manager.py`: `join(url, title,
  display_name, password) -> meeting_id`, `leave(meeting_id)`, `list()`.
  Rejects hosts not in `QUILL_JITSI_DOMAINS` (exact host match, `https` only).
- One headless Chromium per active bot (Playwright, launch flags
  `--use-fake-ui-for-media-stream`, `--autoplay-policy=no-user-gesture-required`).
- The page `quill_server/bot/page/bot.html` (served by Quill at
  `/bot/page`, internal token required) loads
  `https://<host>/config.js` and `https://<host>/libs/lib-jitsi-meet.min.js`
  from the user's own Jitsi server, so the library version matches it.
- It joins receive-only (no local tracks), as `QUILL_BOT_NAME`. It handles a
  room password and the lobby (`joinLobby`), and posts the recording notice
  when `QUILL_BOT_ANNOUNCE` is on.
- On each remote audio track (`TRACK_ADDED`), an AudioWorklet resamples to
  16 kHz PCM16 and sends frames on one live WebSocket with
  `track_id = participant id`, `kind = participant`, `speaker = label =
  display name`. `DISPLAY_NAME_CHANGED` updates the label. `TRACK_REMOVED` /
  `USER_LEFT` sends `track_end`.
- The bot leaves after `QUILL_BOT_ALONE_MINUTES` alone, at
  `QUILL_BOT_MAX_MINUTES`, on `leave`, or when the conference ends; it then
  sends `stop`.
- State per bot: `starting`, `lobby`, `joined`, `leaving`, `ended`, `failed`
  (+ `error`), exposed in `GET /api/v1/bot` and on the meeting.

## 9. Web UI

Static files in `server/web/`, served at `/`. Vanilla ES modules and CSS, no
build step, no CDN (everything vendored or hand-written). Views:

- Sign in.
- Library: meetings list with search (FTS), status, source, duration.
- Meeting: transcript (speaker colors, click a timestamp to play audio),
  user notes editor, AI notes (markdown rendered), generate/regenerate notes
  with a template, copy/export (md, txt, srt, vtt), rename speakers, delete.
- New meeting: (a) send the Jitsi bot to a URL, (b) browser recorder:
  mic-only, or mic + shared tab audio (`getDisplayMedia({audio: true})`),
  live transcript and a notes editor side by side, (c) upload audio files.
- Settings: API keys (create shows the secret once), webhooks, templates,
  status (engine, model download state, queue), MCP and agent setup snippets.

The recorder uses an AudioWorklet to produce 16 kHz PCM16 and the protocol in
section 6. Capture needs a secure context (HTTPS). It works in Chromium-based
browsers for tab audio, and in all current browsers for mic-only.

## 10. StartOS package

`deploy/startos/`, SDK `2.0.9`, package id `quill`, version `0.1.3:0`.

- Image `quill`: `dockerBuild` with `workdir: '../../server'`, arch `x86_64`
  and `aarch64`.
- Volumes: `main` (mounted at `/data`, backed up) and `models` (mounted at
  `/models`, not backed up).
- Interfaces on one host, port 8080: `ui` (Web UI, path ``), `api` (REST,
  path `/openapi.json`), `mcp` (path `/mcp`).
- `store.json`: `adminPassword`, `apiKey`, `sessionSecret`, `internalToken`,
  plus settings for the engine, LLM, Jitsi and retention.
- Init: `seedSecrets` (API key, secrets on install), `watchCredentials`
  (critical task to run `set-admin-password` while it is unset).
- Actions: `set-admin-password`, `get-api-credentials` (API key, MCP URL,
  ready-made snippets), `rotate-api-key`, `configure-transcription` (model,
  engine, threads, remote engine URL and key, optional `openwhispr`
  dependency), `configure-notes-llm` (base URL, key, model, auto notes,
  optional `ollama` dependency), `configure-jitsi` (allowed domains, bot name,
  announce, limits), `configure-retention`.
- Health checks: `ui` (port 8080) and `model` (`/health` `model_ready`,
  `loading` while downloading).
- Optional dependencies: `openwhispr` (remote engine), `ollama` (notes LLM),
  both resolved with `sdk.host.getBridgeAddress` when selected.

## 11. Agent skills

`skills/<name>/SKILL.md` in the Agent Skills format (YAML front matter `name`,
`description`). They call the MCP tools in section 7 and follow token rules:
fetch transcripts in windows, prefer `get_live_transcript` deltas, never pull
a full long transcript twice.

- `quill-meeting-copilot`: join a Jitsi meeting, follow it live, answer
  questions, flag action items while it runs.
- `quill-meeting-notes`: Granola-style notes after a meeting from the user
  notes plus the transcript, with templates, saved back with `save_notes`.
- `quill-meeting-recall`: answer questions across past meetings with search.
- `quill-follow-up`: turn notes into follow-up emails, tasks and CRM updates.
- `quill-setup`: connect Claude Code, Codex, Hermes Agent and OpenClaw to the
  MCP endpoint.

## 12. Roadmap

Phase 1 is sections 1 to 11. The items below come after it, in priority
order. Items marked *(Speakr)* are ideas taken from
[Speakr](https://github.com/murtaza-nasir/speakr) (reviewed 2026.10.24).
Speakr is AGPL-3.0, so Quill reuses its ideas only, never its code.

| # | Item | Notes |
|---|---|---|
| 1 | Ask across meetings in the web UI *(Speakr "Inquire")* | Chat over search hits with cited timestamps, through the configured LLM. Agents already do this over MCP. |
| 2 | Prompts per folder and per tag *(Speakr)* | A folder or tag selects the note template. Several tags stack their instructions in a fixed order. |
| 3 | Speaker diarization for single-track uploads | sherpa-onnx speaker diarization (pyannote segmentation 3.0 ONNX + speaker embedding), CPU, no Hugging Face token. Fills `diarization_label`. |
| 4 | Speaker library with voice profiles *(Speakr)* | Store speaker embeddings, suggest names for new meetings. Needs item 3. |
| 5 | Sharing and public links *(Speakr)* | Read-only links per meeting with expiry, view or edit rights, revocable. |
| 6 | Multiple users and single sign-on *(Speakr)* | Local users, then OIDC. Granola `owner` and `attribution` follow the user. |
| 7 | More exports and auto-export *(Speakr)* | `.docx` export, and auto-export of notes and transcripts as Markdown to a folder (for example an Obsidian vault). |
| 8 | Calendar auto-join | Read an ICS or CalDAV feed, send the bot to Jitsi links at the start time, fill `calendar_event`. |
| 9 | Jigasi transcription backend | Accept per-participant audio from a Jigasi in Jitsi's Skynet streaming protocol, as an alternative to the bot. |
| 10 | Tags and retention policies per folder *(Speakr)* | Retention by folder or tag, not only globally. |
