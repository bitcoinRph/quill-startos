# Quill

Quill records meetings and transcribes them on your own server. Audio and transcripts stay on this machine unless you point Quill at a remote engine or LLM yourself. This page covers first steps, the three ways to capture a meeting, how to connect AI agents, and what to check when something does not work.

## First steps

1. Run **Set Admin Password** and copy the password. StartOS also shows this as a task, and Quill does not start until you do it.
2. Start the service. On the first start Quill downloads the speech model (about 490 MB from GitHub). The **Speech Model** health check shows the download until it is ready; recording and uploads work once it reads ready.
3. Open the **Web UI** from the Interfaces tab and sign in with the admin password. Use an **HTTPS** address (a `.local` or domain address, not a plain `http://` IP address): the browser recorder only works in a secure context.
4. Optional: run **Configure Profile** to set your name and email. Your name labels your own voice in browser recordings.

## Capturing a meeting

### 1. The Jitsi bot

The bot joins a meeting on your own Jitsi server as a participant and transcribes each person on a separate track, named by their Jitsi display name.

1. Run **Configure Jitsi Bot** and add the host names of your Jitsi servers (for example `meet.example.com`). The bot only joins `https` links on these hosts. Leave the list empty and the bot stays off.
2. In the web app choose **New meeting**, paste the meeting link and send the bot. Agents can do the same with the `join_meeting` MCP tool.
3. The bot posts a recording notice in the meeting chat. Keep this on: recording people without telling them is unlawful in many places. It leaves after the meeting ends, after nobody else has been there for the configured time, or at the maximum length.

Only add hosts you control. The bot opens the meeting page in a browser on this server, which can reach your local network.

### 2. The browser recorder

In **New meeting** choose the browser recorder. It records your microphone, and with a Chrome or Edge window it can also record the audio of a shared tab, so the other side of a call is captured too. Other browsers can record the microphone only. Choose the tab in the browser's share dialog and tick **Share tab audio**. You need an HTTPS address for the web app.

### 3. Upload

Drop audio files into **New meeting**. Anything ffmpeg can read works. Each file becomes one track and is transcribed in the background.

## Connecting AI agents

Quill exposes an MCP endpoint and a REST API, both authenticated with the service API key.

1. Run **Get API Credentials**. It shows the key, the MCP URL and two ready-made snippets.
2. For Claude Code, copy the **Claude Code command** and run it in a terminal. Other clients (Codex, Hermes Agent, OpenClaw and others) take the **MCP JSON configuration** or the MCP URL plus the header `Authorization: Bearer <key>`.
3. Agents can then list and search meetings, read transcripts in windows, follow a live meeting and save notes back with `save_notes`.

The REST API lives at `/api/v1` on the same address, with an OpenAPI document at `/openapi.json`. It also serves an OpenAI-compatible `/v1/audio/transcriptions` endpoint and a Granola-compatible API (`/v1/notes`, `/v1/folders`, `/v1/webhook-endpoints`). Tools built for the Granola API work after two changes: set the base URL (`https://public-api.granola.ai` becomes your Quill address) and use your Quill API key. Run **Configure Profile** so notes carry your name and email as their owner.

**Rotate API Key** replaces the service key. Anything using the old key stops working until you update it. Keys you create in the web app under Settings are separate and are not affected.

## Optional services

- **OpenWhispr** transcribes in 99 languages instead of the built-in engine's 25 European ones. Install OpenWhispr, run its **Get API Credentials** action, then run Quill's **Configure Transcription**, choose **OpenWhispr service**, and paste the token. Quill cannot read the token from another service. StartOS then shows OpenWhispr as a running dependency of Quill.
- **Ollama** can write notes with a local model. Install Ollama and pull a model in it, then run **Configure Notes LLM**, choose **Ollama service** and enter the exact model tag. Turn on **Write notes automatically** to generate notes when a transcript is ready.
- **Configure Transcription** also offers Parakeet v2 (English only) and any other OpenAI-compatible transcription server. A server outside your network receives your audio.
- **Configure Notes LLM** also accepts any OpenAI-compatible chat server. A remote one receives your transcripts.

With no notes LLM, an MCP agent writes the notes itself and saves them to the meeting.

## Privacy

Everything runs on this server. Quill sends nothing to the cloud except the one-time model download from GitHub and anything you configure yourself (a custom transcription or LLM server). **Configure Retention** deletes audio after a number of days while keeping transcripts and notes, and compresses finished recordings to save space.

Tell people when you record them. Quill does not collect consent for you; the bot's chat notice is the only built-in notice.

## Backups

Backups include the `main` volume: the database, every meeting's audio, transcripts and notes, and the service settings. The downloaded speech models are not included. After a restore Quill downloads them again.

## Troubleshooting

- **Speech Model stays on loading.** The server cannot reach GitHub or the disk is full. Check the logs; a half-downloaded file is discarded and retried on the next start.
- **The service will not start and asks for the admin password.** Run **Set Admin Password**.
- **The browser recorder cannot capture a tab or the microphone.** Open the web app on an HTTPS address and use Chrome or Edge for tab audio.
- **The bot says the host is not allowed.** Add the host to **Configure Jitsi Bot**. The match is exact and only `https` links work.
- **Transcription is slow.** On a small CPU raise **CPU threads** in **Configure Transcription**, or send audio to the OpenWhispr service.
- **Quill fails to start after choosing OpenWhispr or Ollama.** That service must be installed and running. StartOS shows the dependency on the Dashboard.
- **Notes fail with a 409 error.** No notes LLM is set. Run **Configure Notes LLM**, or let an agent write the notes.
