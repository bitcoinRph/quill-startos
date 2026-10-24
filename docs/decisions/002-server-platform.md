# ADR-002 :: Server platform and StartOS package

Last updated: `2026.10.24`

> Add a fourth platform root, `server/`, that runs Quill as a headless,
> self-hosted meeting recorder and transcriber on a Linux server. Package it
> for StartOS under `deploy/startos/`. Keep the shared session and transcript
> contracts, and extend them only with additive fields.

## 1. Decision

- Add `server/`: a Python service (FastAPI) with its own build graph, tests and
  Docker image. It imports nothing from `macos/`, `windows/` or `linux/`.
- Add `deploy/startos/`: a StartOS package (`@start9labs/start-sdk` 2.x) that
  builds the `server/` image and runs it as the `quill` service.
- Add `skills/`: Agent Skills (`SKILL.md`) that teach AI agents to use the
  server through its MCP endpoint.
- Transcribe with NVIDIA Parakeet TDT 0.6B (int8 ONNX) through
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), with Silero VAD for
  live segmentation. Allow an external OpenAI-compatible engine (for example
  the `openwhispr` StartOS service) as an option, not as a requirement.

## 2. Rationale

A server has no microphone and no system audio. Audio reaches it in four ways:

| Source | Speakers | Notes |
|---|---|---|
| Jitsi notetaker bot | one track per participant, named | headless Chromium with `lib-jitsi-meet` from the user's own Jitsi server |
| Browser recorder | `me` (mic) and `them` (shared tab audio) | Chromium-based browsers only for tab audio |
| Upload / OpenAI-compatible API | one track per file | any format ffmpeg reads |
| Quill desktop import | `me` / `them` from `meta.json` v2 | the macOS `on_stop` hook can push a finished session |

Parakeet TDT 0.6B v3 int8 decoded 76.9 s of audio in 8.0 s (9.6× real time) on
a 4-vCPU x86_64 sandbox with `num_threads=4` (sherpa-onnx 2.0.0, measured
2026.10.24). It returns token timestamps, which give word timings. The macOS
implementation already uses Parakeet, so the transcripts stay comparable.
sherpa-onnx publishes the Parakeet, Silero VAD and speaker-diarization models
as GitHub release assets, so one dependency covers recognition, segmentation
and later diarization, without a Hugging Face token.

OpenWhispr (whisper.cpp) covers 99 languages but is not measured against
Parakeet on the same hardware here. It stays an optional engine for languages
outside Parakeet v3's 25 European languages.

## 3. Contract extensions

All additions are optional fields. Readers that do not know them ignore them.

- `meta.json` v2 track `kind` adds `participant` and `upload` next to `mic` and
  `system`. `speaker` is a free label (a Jitsi display name, `me`, `them`).
  A track can carry `label` (display text) and `participant_id`.
- Server segment files are `.wav` while capturing. After finalization they can
  be transcoded to `.ogg` (Opus); `meta.json` is rewritten atomically with the
  new file names.
- `transcript.json` segments can carry `track` and `words`
  (`[{ "w": "word", "s": start_ms, "e": end_ms }]`).

## 4. Consequences

- The server is a fourth independent build and CI job.
- The Jitsi bot adds Chromium to the image. It opens only domains on an
  allowlist, because a browser on the server can reach the LAN.
- Agents get transcripts through MCP and write notes back, so subscription
  agents (Claude Code, Codex, Hermes Agent, OpenClaw) do the summarization
  with their own models. The server calls an LLM only when one is configured.

## 5. When to revisit

Revisit the engine choice if a CPU model beats Parakeet v3 on accuracy and
speed for meeting audio, or if StartOS GPU support makes a larger model the
better default.
