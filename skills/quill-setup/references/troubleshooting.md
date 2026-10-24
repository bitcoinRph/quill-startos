# Quill connection troubleshooting

Work from the top. Stop when the problem is fixed.

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 or "unauthorized" | Wrong, old or empty key | Check that `QUILL_API_KEY` is set in the shell or `.env` file the agent reads. Run **Get API Credentials** again. If the key was rotated, update the variable and restart the agent. |
| Header shows literal `${QUILL_API_KEY}` | The variable is unset | Claude Code and Hermes send or reject the literal text. Set the variable and restart. |
| Timeout or "connection refused" | The machine cannot reach the server | Open the MCP URL host in a browser on the same machine. Check LAN, VPN, Tor or Tailscale access. Check that Quill is running in StartOS. |
| TLS or certificate error | The URL uses a private CA (a StartOS local address) | Install the StartOS root certificate on the machine that runs the agent. Some agents have their own switch: Hermes has `ssl_verify` (a CA bundle path), OpenClaw has `sslVerify`. Do not turn off TLS checks unless the user accepts the risk. |
| 404 on the URL | The URL is missing `/mcp` | Use the exact MCP URL from **Get API Credentials**. |
| Tools missing after adding the server | The session started before the change | Start a new session. In Claude Code, run `/mcp` and check the status. |
| `get_status` works, other tools fail | Transcription model not ready, or a feature is off | Read the error text. Follow the table in `SKILL.md`. |
| Server shows "pending approval" (Claude Code project `.mcp.json`) | Project servers need approval | Run `claude` interactively once and approve it. |

## Facts and sources

These were read from each project's own docs or source (see `skills/README.md` for links). Where a doc site could not be read, the source code was used instead.

- Claude Code accepts `${VAR}` and `${VAR:-default}` in `url` and `headers` of `.mcp.json`.
- Codex `[mcp_servers.<name>]` accepts `url`, `bearer_token_env_var`, `http_headers` (literal values) and `env_http_headers` (header name to variable name).
- Hermes resolves `${VAR}` in `url` and `headers` from `~/.hermes/.env` and the environment. An unset variable makes the connection fail with a clear message.
- OpenClaw resolves `${VAR}` in any config string. `~/.openclaw/.env` is read at start.
