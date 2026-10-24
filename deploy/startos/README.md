<p align="center">
  <img src="icon.png" alt="Quill logo" width="21%">
</p>

# Quill on StartOS

> **Upstream docs:** <https://github.com/humanitas-labs/quill#readme>, and the server contract in [`docs/server-spec.md`](../../docs/server-spec.md)
>
> Everything not listed in this document should behave the same as the upstream Quill server. If a feature, setting, or behavior is not mentioned here, the upstream documentation is accurate and fully applicable.

Quill is a local meeting recorder and transcriber. The server in `server/` (FastAPI, sherpa-onnx Parakeet, a Jitsi notetaker bot, a web UI, a REST API and an MCP endpoint) is what this package runs. This directory is `deploy/startos/` of the Quill repository, a fork of [humanitas-labs/quill](https://github.com/humanitas-labs/quill).

---

## Table of Contents

- [Image and Container Runtime](#image-and-container-runtime)
- [Volume and Data Layout](#volume-and-data-layout)
- [Installation and First-Run Flow](#installation-and-first-run-flow)
- [Configuration Management](#configuration-management)
- [Network Access and Interfaces](#network-access-and-interfaces)
- [Actions (StartOS UI)](#actions-startos-ui)
- [Backups and Restore](#backups-and-restore)
- [Health Checks](#health-checks)
- [Dependencies](#dependencies)
- [Limitations and Differences](#limitations-and-differences)
- [What Is Unchanged from Upstream](#what-is-unchanged-from-upstream)
- [Contributing](#contributing)
- [Quick Reference for AI Consumers](#quick-reference-for-ai-consumers)

---

## Image and Container Runtime

| Image | Source | Architectures |
| --- | --- | --- |
| `quill` | Built from `../../server` with `../../server/Dockerfile` (`dockerBuild`) | x86_64, aarch64 |

One daemon, `ui`, runs the image's own entrypoint (`sdk.useEntrypoint()`, `python -m quill_server`) on port 8080. The same process serves the web UI, the REST API, the MCP endpoint and the Jitsi bot manager (headless Chromium started on demand). No command is overridden; everything is configured through environment variables.

## Volume and Data Layout

| Volume | Mounted at | Backed up | Contents |
| --- | --- | --- | --- |
| `main` | `/data` | yes | `quill.db` (SQLite), `sessions/<id>/` (audio, `transcript.json`, `notes.md`), and `store.json` (read by the package, not by Quill) |
| `models` | `/models` | no | Parakeet models and `silero_vad.onnx`, verified by SHA-256 on download |

`store.json` holds `adminPassword`, `apiKey`, `sessionSecret`, `internalToken`, and the settings objects `transcription`, `notes`, `jitsi`, `retention` and `profile`. Every key has a zod `.catch()` default, so a missing or invalid value self-heals on the next write.

## Installation and First-Run Flow

1. On install, `seedSecrets` writes the service API key (`qk_` plus 40 characters), the session secret and the internal bot token. On every init it merges the defaults so `store.json` is complete.
2. `watchCredentials` raises a critical task for **Set Admin Password** while no admin password is stored. Quill cannot start until it is set.
3. On start, `main.ts` composes the environment from the store, the service's own addresses and the optional dependencies, then starts Quill.
4. Quill downloads the speech model into `/models` (about 490 MB). The `model` health check reports `loading` until `/health` returns `model_ready: true`.

## Configuration Management

| StartOS-managed (actions and store) | Env var |
| --- | --- |
| Always set | `QUILL_DATA_DIR=/data`, `QUILL_MODELS_DIR=/models`, `QUILL_PORT=8080` |
| Secrets, generated on install | `QUILL_API_KEY`, `QUILL_SESSION_SECRET`, `QUILL_INTERNAL_TOKEN` |
| Set Admin Password | `QUILL_ADMIN_PASSWORD` |
| Public address (first public HTTPS address, else first non-local HTTPS, else first address of the `ui` interface; its origin) | `QUILL_PUBLIC_URL` |
| Configure Transcription | `QUILL_ASR_ENGINE`, `QUILL_ASR_MODEL`, `QUILL_ASR_THREADS`, `QUILL_REMOTE_ASR_URL`, `QUILL_REMOTE_ASR_KEY`, `QUILL_REMOTE_ASR_MODEL` |
| Configure Notes LLM | `QUILL_LLM_BASE_URL`, `QUILL_LLM_API_KEY`, `QUILL_LLM_MODEL`, `QUILL_AUTO_NOTES` |
| Configure Jitsi Bot | `QUILL_JITSI_DOMAINS`, `QUILL_BOT_NAME`, `QUILL_BOT_ANNOUNCE`, `QUILL_BOT_MAX_MINUTES`, `QUILL_BOT_ALONE_MINUTES` |
| Configure Retention | `QUILL_AUDIO_RETENTION_DAYS`, `QUILL_COMPRESS_AUDIO` |
| Configure Profile | `QUILL_SPEAKER_NAME`, `QUILL_OWNER_EMAIL` |

Engine mapping: built-in Parakeet v3 sets `QUILL_ASR_ENGINE=parakeet` and the v3 model id; Parakeet v2 sets the v2 id. The OpenWhispr service, the Ollama service and a custom server set `QUILL_ASR_ENGINE=openai`. For OpenWhispr, `QUILL_REMOTE_ASR_URL` is `http://<bridge address>` of OpenWhispr's `ui-multi` host, port 8081 (resolved with `sdk.host.getBridgeAddress`), and the key is the token the user pasted. For Ollama transcription, `QUILL_REMOTE_ASR_URL` is `http://<bridge address of ollama api-multi:11434>`, the key is empty and `QUILL_REMOTE_ASR_MODEL` is the model tag; Ollama's `/v1/audio/transcriptions` (added in Ollama v0.20.0) prompts an audio-capable chat model and returns `{"text"}` only, so Quill gets one segment per VAD cut and no word timings. For the Ollama notes source, `QUILL_LLM_BASE_URL` is `http://<bridge address of ollama api-multi:11434>/v1`.

Upstream's own settings in the web app (API keys, webhooks, templates, folders) are untouched and live in `quill.db`.

## Network Access and Interfaces

One host, `ui`, one binding: port 8080, protocol `http`, terminated by StartOS.

| Interface | Type | Path | Credential |
| --- | --- | --- | --- |
| Web UI (`ui`) | ui | `/` | Admin password from **Set Admin Password** |
| REST API (`api`) | api | `/openapi.json` | `Authorization: Bearer <API key>` from **Get API Credentials**; also serves `/v1/audio/transcriptions` and the Granola-compatible `/v1/notes`, `/v1/folders`, `/v1/webhook-endpoints` |
| MCP (`mcp`) | api | `/mcp` | `Authorization: Bearer <API key>` |

Reachability (LAN, domain, Tor) is the user's choice on the Interfaces tab. The browser recorder needs an HTTPS address.

## Actions (StartOS UI)

| Action | Purpose | Availability | Inputs | Outputs |
| --- | --- | --- | --- | --- |
| Set Admin Password (`set-admin-password`) | Set or reset the web sign-in password; also the install task | Any status | Optional password (min 12; generated when empty) | Password, masked |
| Get API Credentials (`get-api-credentials`) | Show the service API key, MCP URL, REST URL, a Claude Code command and a generic MCP JSON snippet | Any status | none | Masked and copyable values |
| Rotate API Key (`rotate-api-key`) | Replace the service API key | Any status | none | New key, masked |
| Configure Transcription (`configure-transcription`) | Engine: Parakeet v3, Parakeet v2, OpenWhispr (token, model), Ollama (model id, experimental), custom OpenAI-compatible (URL, key, model); CPU threads | Any status | Union form | none |
| Configure Notes LLM (`configure-notes-llm`) | None, Ollama (model id) or custom (base URL, key, model); automatic notes | Any status | Union form, toggle | none |
| Configure Jitsi Bot (`configure-jitsi`) | Allowed hosts (empty disables the bot), bot name, announce toggle, maximum minutes, alone minutes | Any status | List, text, toggle, numbers | none |
| Configure Retention (`configure-retention`) | Audio retention days (0 keeps), compress audio | Any status | Number, toggle | none |
| Configure Profile (`configure-profile`) | Your name (`QUILL_SPEAKER_NAME`) and email (`QUILL_OWNER_EMAIL`) | Any status | Two text fields | none |

Every action writes `store.json`; `main.ts` reads it with `.const()`, so a change restarts the service. Turning on automatic notes without an LLM is rejected.

## Backups and Restore

`sdk.Backups.ofVolumes('main')`: the database, sessions and `store.json`. The `models` volume is excluded; models are downloaded and checked again on the first start after a restore. Restoring brings back the admin password, API key and every setting with `store.json`.

## Health Checks

| Check | Method | Messages |
| --- | --- | --- |
| `ui` (Web Interface) | `checkPortListening` on 8080, 60 s grace period | ready / not ready |
| `model` (Speech Model) | `GET http://127.0.0.1:8080/health`; `success` when `model_ready` is true, otherwise `loading` with download progress | loading with a percentage when `/health` reports `download: {bytes, total}` or `progress`, else a generic first-start message |

The `/health` fields beyond `model_ready` and `model` are optional. The spec does not define a progress field yet; the check reads `download.bytes` and `download.total`, or `progress` (0 to 1 or 0 to 100), when present.

## Dependencies

Both are optional and declared as running requirements only while selected:

| Package | Selected by | Used for |
| --- | --- | --- |
| `openwhispr` (health check `whisper`, `>=0.1.9:0`) | Configure Transcription: OpenWhispr | Remote `/v1/audio/transcriptions` engine |
| `ollama` (health check `primary`, `>=0.34.0:0`) | Configure Notes LLM: Ollama, or Configure Transcription: Ollama | OpenAI-compatible `/v1` notes LLM, experimental `/v1/audio/transcriptions` engine |

Quill cannot read another package's store, so the OpenWhispr token is pasted by the user.

## Limitations and Differences

1. The service has no sign-in until an admin password is set, and cannot start without one.
2. The Jitsi bot is off until **Configure Jitsi Bot** lists at least one host.
3. `QUILL_PUBLIC_URL` follows the service's addresses and has no override.
4. The model download needs outbound access to GitHub on first start and after a restore.
5. A remote engine or LLM that is not on this server receives audio or transcripts.
6. GPU acceleration is not used.

## What Is Unchanged from Upstream

The web UI, the REST API, MCP tools, live ingest, notes templates, webhooks, folders, the OpenAI-compatible transcription endpoint and the Granola-compatible API behave as `docs/server-spec.md` describes.

## Contributing

See [UPDATING.md](UPDATING.md) for version rules and `AGENTS.md` for repository conventions. Build with `npm ci && make x86` inside a StartOS packaging workspace, or let `.github/workflows/startos.yml` build both architectures.

---

## Quick Reference for AI Consumers

```yaml
package_id: quill
upstream_version: server version in server/pyproject.toml (package 0.1.3:0)
image: quill (built from ../../server)
architectures: [x86_64, aarch64]
volumes:
  main: /data (backed up)
  models: /models (not backed up)
ports:
  ui: 8080
  api: 8080 (/openapi.json)
  mcp: 8080 (/mcp)
dependencies:
  - openwhispr (optional)
  - ollama (optional)
startos_managed_env_vars:
  - QUILL_DATA_DIR
  - QUILL_MODELS_DIR
  - QUILL_PORT
  - QUILL_ADMIN_PASSWORD
  - QUILL_API_KEY
  - QUILL_SESSION_SECRET
  - QUILL_INTERNAL_TOKEN
  - QUILL_PUBLIC_URL
  - QUILL_ASR_ENGINE
  - QUILL_ASR_MODEL
  - QUILL_ASR_THREADS
  - QUILL_REMOTE_ASR_URL
  - QUILL_REMOTE_ASR_KEY
  - QUILL_REMOTE_ASR_MODEL
  - QUILL_LLM_BASE_URL
  - QUILL_LLM_API_KEY
  - QUILL_LLM_MODEL
  - QUILL_AUTO_NOTES
  - QUILL_JITSI_DOMAINS
  - QUILL_BOT_NAME
  - QUILL_BOT_ANNOUNCE
  - QUILL_BOT_MAX_MINUTES
  - QUILL_BOT_ALONE_MINUTES
  - QUILL_AUDIO_RETENTION_DAYS
  - QUILL_COMPRESS_AUDIO
  - QUILL_SPEAKER_NAME
  - QUILL_OWNER_EMAIL
actions:
  - set-admin-password
  - get-api-credentials
  - rotate-api-key
  - configure-transcription
  - configure-notes-llm
  - configure-jitsi
  - configure-retention
  - configure-profile
```
