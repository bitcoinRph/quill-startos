---
name: quill-setup
description: Connects an AI agent (Claude Code, OpenAI Codex CLI, Hermes Agent or OpenClaw) to a Quill meeting recorder through its MCP endpoint and checks the connection. Use when the user wants to set up, connect, fix or test Quill, when Quill tools such as get_status or join_meeting are missing, or when a Quill call fails with an auth or connection error.
---

# Quill setup

Quill is a self-hosted meeting recorder on the user's StartOS server. It speaks MCP over streamable HTTP at `/mcp`. Auth is one header: `Authorization: Bearer <api key>`.

## 1. Get the URL and the key

Ask the user to do this on their StartOS server. You cannot do it for them.

1. Open StartOS, then the Quill service, then Actions.
2. Run **Get API Credentials**.
3. Copy the MCP URL (it ends in `/mcp`) and the API key (it may start with `qk_`).

Rules for the key:
- Never ask the user to paste the key into the chat.
- Never print the key, write it to a file in a repo, or put it in a command that is saved to shell history if you can avoid it.
- Have the user store it in an environment variable named `QUILL_API_KEY`.
- If the key leaked, tell the user to run **Rotate API Key** in StartOS, then update the variable.

Below, `<quill-mcp-url>` is the MCP URL from the action.

## 2. Connect the agent

Pick the agent you are running in. The server name is `quill` in every example.

### Claude Code

```bash
claude mcp add --transport http --scope user quill "<quill-mcp-url>" \
  --header "Authorization: Bearer $QUILL_API_KEY"
```

The shell expands the variable, so the key is stored in `~/.claude.json`. To keep the key out of the file, use a project `.mcp.json` with `${QUILL_API_KEY}` instead:

```json
{ "mcpServers": { "quill": { "type": "http", "url": "<quill-mcp-url>",
  "headers": { "Authorization": "Bearer ${QUILL_API_KEY}" } } } }
```

Check with `claude mcp list`, or `/mcp` inside a session.

### OpenAI Codex CLI

Add this to `~/.codex/config.toml`, then export `QUILL_API_KEY` in the shell that starts Codex:

```toml
[mcp_servers.quill]
url = "<quill-mcp-url>"
bearer_token_env_var = "QUILL_API_KEY"
```

The same result from the CLI: `codex mcp add quill --url "<quill-mcp-url>" --bearer-token-env-var QUILL_API_KEY`.

### Hermes Agent

Put `QUILL_API_KEY=<key>` in `~/.hermes/.env`. Add this to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  quill:
    url: "<quill-mcp-url>"
    headers:
      Authorization: "Bearer ${QUILL_API_KEY}"
```

Or run `hermes mcp add quill --url "<quill-mcp-url>" --auth header`. It asks for the key and saves it to `~/.hermes/.env`. Test with `hermes mcp test quill`.

### OpenClaw

Put `QUILL_API_KEY=<key>` in `~/.openclaw/.env`. Then run:

```bash
openclaw mcp add quill --url "<quill-mcp-url>" --transport streamable-http \
  --header 'Authorization=Bearer ${QUILL_API_KEY}'
openclaw mcp doctor quill --probe
```

Keep the single quotes. OpenClaw expands `${QUILL_API_KEY}` itself when it loads the config.

## 3. Test

1. Restart the agent session if it does not show Quill tools.
2. Call `get_status`. Your client may show the name with a prefix, such as `mcp__quill__get_status`.
3. Read the answer to the user: engine, model, queue, live meetings, bot domains.

| `get_status` shows | Tell the user |
|---|---|
| model loading or not ready | The speech model is still downloading (about 490 MB on first start). Transcription works when it finishes. Try again in a few minutes. |
| `bot domains` empty | The Jitsi bot is off. Run **Configure Jitsi** in StartOS and add the Jitsi host. |
| a normal answer | Setup works. Offer `quill-meeting-copilot` for a live meeting. |

## If it fails

Read `references/troubleshooting.md` for 401 errors, timeouts, TLS and missing tools. If an agent is not listed above, say that you cannot verify its steps. Give the generic facts instead: streamable HTTP, URL ending in `/mcp`, header `Authorization: Bearer <key>`.
