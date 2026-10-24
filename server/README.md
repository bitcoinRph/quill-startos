# Quill server

Headless Quill for a Linux server: records meetings (Jitsi bot, browser recorder, uploads, desktop
import), transcribes them with Parakeet TDT 0.6B through sherpa-onnx, and serves a web UI, a REST API,
an OpenAI-compatible transcription endpoint, a Granola-compatible API and an MCP endpoint.
The contract is [docs/server-spec.md](../docs/server-spec.md); this file only records what the code adds
or decides.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
QUILL_DATA_DIR=./data QUILL_MODELS_DIR=./models QUILL_ADMIN_PASSWORD=change-me python -m quill_server
pytest -q
```

Models download in the background on first start (`/health` shows progress). Tests that need the real
models skip unless `QUILL_MODELS_DIR` holds `silero_vad.onnx` and `parakeet-tdt-0.6b-v3-int8/`. The
Granola conformance tests skip unless `GRANOLA_OPENAPI` points to a copy of
`https://docs.granola.ai/api-reference/openapi.json`.

## Layout

| Module | Job |
|---|---|
| `config.py` | environment (spec section 2) plus `QUILL_OWNER_EMAIL`, `QUILL_WEB_DIR` |
| `db.py`, `storage.py` | SQLite/FTS5 schema; session directories, `meta.json` v1/v2, crash-safe WAV |
| `models.py` | download, SHA-256 check, safe extraction, progress |
| `asr/` | `AsrEngine` protocol; `parakeet`, `openai` engines; `worker.py` is the single recognizer thread (live chunks outrank offline ones) |
| `audio.py`, `offline.py` | ffmpeg decode, Silero VAD cutter, offline transcription of a file |
| `live.py` | live ingest: `LiveHub`, `LiveMeeting`, `LiveConnection` (protocol) |
| `meetings.py`, `folders.py` | meeting records, segments, tracks, notes, session files |
| `jobs.py` | filesystem-backed queue and crash recovery |
| `notes.py`, `webhooks.py`, `housekeeping.py` | LLM notes, signed webhooks, Opus compression and retention |
| `api/` | routers; `granola.py` is the Granola-compatible API |
| `mcp_server.py` | MCP tools (`QuillTools`) and the Bearer gate for `/mcp` |

## Bot integration

`app.py` includes `quill_server.bot.routes.router` when importable and creates the manager lazily, only
when `QUILL_JITSI_DOMAINS` is not empty:

```python
from quill_server.bot.manager import BotManager
bot = BotManager(config, live_hub)       # config: quill_server.config.Config, live_hub: LiveHub
meeting_id = await bot.join(url, title, display_name, password)   # title/display_name/password may be None
await bot.leave(meeting_id)              # KeyError when no bot is attached to it
bot.list()                               # list[dict]; each has "meeting_id", "state", "error"
await bot.shutdown()                     # called on server shutdown
```

The manager is built as `BotManager(config, live_hub, create_meeting=live_hub.create_meeting)`. If the import
fails or no domain is configured the bot routes answer 503 `bot_unavailable`. Exceptions with a `code` (the
manager's `BotError`) keep that code: `BotUrlError` is 400, `not_found` 404, `already_joined`/`limit_reached` 409,
anything else 502; a plain `ValueError` is 400 `invalid_bot_request`. The bot may call `POST
/api/v1/meetings/{id}/stop` with `Authorization: Bearer <internal token>` (its fallback stop); that token is
accepted on no other REST route. `GET /api/v1/meetings/{id}`
and the MCP `get_meeting` add `bot: {state, error}` for `source: "bot"` meetings, read from `list()`.

`live_hub` (`quill_server.live.LiveHub`) is the live ingest service. The bot needs only:

- `live_hub.internal_token`: the `?internal=` value for the WebSocket.
- `await live_hub.create_meeting(title=None, source="bot", attendees=None) -> str`: creates the meeting
  (status `scheduled`, moves to `live` with the first track) and returns its id, so `join` can return it.
- the WebSocket `ws://127.0.0.1:{config.port}/api/v1/meetings/{id}/live?internal={token}` of spec section 6.

`config` attributes are the lower-case env names without `QUILL_`: `port`, `internal_token`,
`jitsi_domains` (tuple of lower-case hosts), `bot_name`, `bot_announce`, `bot_max_minutes`,
`bot_alone_minutes`, `speaker_name`, `public_url`.

Live protocol details beyond the spec: sending `track` again for an active `track_id` updates its
`speaker`/`label` (a Jitsi display-name change) without opening a new file; a client disconnect ends the
tracks that connection registered; a meeting with no connected client for 5 minutes is finalized; frames
over 256 KiB are refused. Error codes: `bad_message`, `unknown_type`, `bad_track`, `bad_sample_rate`,
`model_not_ready`, `unknown_track`, `bad_frame`, `frame_too_large`, `meeting_closed`.

## Decisions and deviations

- **IDs** are `not_`/`fol_`/`whe_` + 14 base62 characters (spec 5), jobs/keys/templates use `job_`/`key_`/`tpl_`.
  The prefix is matched case-insensitively. The `tracks` primary key is `(meeting_id, id)` where `id` is the
  client's track id; `tracks` and `meetings` carry a few additive columns (`segments_json`, `engine`, `model`,
  `capture_status`) and `webhooks` carries `flavor`, `scopes_json`, `folder_ids_json`, `created_at`.
- **Meeting date**: `created_after`/`created_before` and ordering use `started_at`, falling back to `created_at`.
- **Dependencies**: `argon2-cffi` from the spec's list is not installed; the admin password is compared in constant time.
- **Auth**: `GET /api/v1/auth/me` (unauthenticated) tells the UI whether sign-in is enabled and whether the
  cookie is valid. `/api/v1/keys` accepts the admin cookie only. The login limiter (10 failures per 5 minutes) keys
  on the TCP peer address, so behind a reverse proxy all clients share one bucket.
- **Audio**: `GET .../audio/{track_id}?segment=N` serves one segment file; `X-Quill-Offset-Ms` is its start on the
  session clock. Tracks with several segments (a rejoin) are not mixed.
- **Search** marks matches with `**bold**` in `snippet`.
- **Webhooks**: Standard Webhooks signing (`webhook-id`, `webhook-timestamp`, `webhook-signature`), secret
  `whsec_<base64>` shown once, retries on 408/429/5xx/network errors after 5 s, 30 s, 2 min, 10 min, 1 h. Retries
  run in memory and are lost on restart. Native events: `meeting.started`, `meeting.ended`, `transcript.ready`,
  `notes.ready`. Granola endpoints receive `note.generated` (first notes) and `note.edited` (later changes).
- **Notes** `PUT .../notes` and MCP `save_notes` replace the single AI-notes document.
- **Recognizer lag**: `lag_ms` is the audio queued for decoding, reported in `status` messages every 5 s and
  after each result.
- **VAD feeding**: audio goes into sherpa-onnx's VAD in 100 ms slices. Larger chunks make it report speech
  starts late (a 1 s chunk clipped 0.6 s off the test clip).
- **Remote engine**: `QUILL_ASR_ENGINE=openai` downloads only `silero_vad.onnx`; `model_ready` is true once the VAD
  is present and `QUILL_REMOTE_ASR_URL` is set. A `GET` on the URL at startup is only logged.
- **MCP** is stateless JSON over streamable HTTP, so clients survive server restarts. The `meeting_notes` prompt
  returns the template, your notes and the transcript.
- **`.caf` import**: decoded by ffmpeg's CAF demuxer. Tests cover PCM and ALAC CAF; AAC-in-CAF (what macOS
  writes) could not be generated here because ffmpeg cannot mux it, so that path is untested.

## `/health`

```json
{"status": "ok", "model_ready": true, "engine": "parakeet", "model": "parakeet-tdt-0.6b-v3-int8",
 "queue_depth": 0, "live_meetings": 0,
 "model_download": {"bytes": 12345, "total": 487170055}, "model_error": null}
```

`model_download` is `null` unless a download is running. `model_error` holds the last download failure
(downloads retry with backoff).

## Docker and StartOS

The image runs as **root**: StartOS mounts `/data` (backed up) and `/models` owned by root and the package has
no chown step. Do not add a `USER`. SQLite is checkpointed (`wal_checkpoint(TRUNCATE)`) on shutdown, so an rsync of
`/data` after SIGTERM holds a complete `quill.db`. `web/dev/` (developer tooling) is excluded from the image and
never served.

```bash
docker build -t quill-server server/
docker run --rm -p 8080:8080 -v quill-data:/data -v quill-models:/models -e QUILL_ADMIN_PASSWORD=change-me quill-server
```
