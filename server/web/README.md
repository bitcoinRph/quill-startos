# Quill web UI

Static single-page app served by the Quill server at `/` (with `index.html` fallback).
Vanilla ES modules and hand-written CSS: no build step, no CDN, no external fonts, no
dependencies. Contract: `docs/server-spec.md`, section 9 (UI), sections 5 and 6 (APIs).

## Layout

| Path | Purpose |
|---|---|
| `index.html`, `favicon.svg` | shell; loads `css/app.css` and `js/main.js` |
| `css/app.css` | all styles; colors are CSS custom properties, light/dark via `prefers-color-scheme` plus a toggle (`data-theme`) |
| `js/main.js` | app shell, hash router (`#/`, `#/meeting/<id>`, `#/new/<bot|recorder|upload>`, `#/settings/<tab>`, `#/signin`) |
| `js/api.js` | the only module that calls the server; errors become `ApiError` with the server's `error.message`; any 401 goes to sign-in |
| `js/recorder.js`, `js/worklet.js` | browser recorder: mic (+ tab audio) -> AudioWorklet (mono, 16 kHz, PCM16, 100 ms frames) -> live WebSocket, reconnect with backoff, wake lock |
| `js/transcript.js`, `js/md.js`, `js/util.js` | transcript list, safe markdown renderer, DOM helpers |
| `js/views/*.js` | signin, library, meeting, new (bot / recorder / upload), settings |

Recording needs a secure context (HTTPS or localhost); the UI detects this and explains how
to use the HTTPS address from StartOS. Tab audio needs a Chromium browser.

## Dev tools (not served in production)

`dev/` is for development only. The Python server should not expose it (the mock refuses to serve it too).

```bash
node dev/mock_server.mjs --port 8099     # mock of spec sections 5 and 6; password "quill"
node dev/smoke.mjs                       # Playwright smoke test against an in-process mock
SMOKE_BASE_URL=http://127.0.0.1:8080 SMOKE_PASSWORD=... node dev/smoke.mjs   # against a real server
node dev/shots.mjs [outDir]              # screenshots: desktop + phone, light + dark
```

Mock options: `MOCK_PASSWORD`, `MOCK_NO_LLM=1` (notes generation answers 409),
`MOCK_MODEL_LOADING=1` (health reports a model download in progress). Test hooks:
`GET /__mock/state`, `POST /__mock/drop` (drops live sockets), `POST /__mock/reset`.

The smoke test needs `require('playwright')` to resolve and Chromium installed.
It uses `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream` and asserts on the
WebSocket frames the page sends, so it works against both the mock and the real server.

## Response shapes the UI accepts

Lists may be a bare array or wrapped (`items`, `meetings`, `templates`, `keys`, `webhooks`,
`bots`, `hits`, `data`, `results`). Meeting detail (`include=transcript,notes,tracks`) provides
`transcript` (array or `{segments}`), `tracks[]` (`id`, `label`, `started_offset_ms`),
`user_notes_md`, `ai_notes_md`, `ai_notes_source`. `GET /health` may carry `model_download
{bytes,total}` or `model_progress` (0..1) while the model downloads.
