# Quill agent skills

A small library of [Agent Skills](https://github.com/anthropics/skills) for Quill, the self-hosted meeting recorder and transcriber. The skills teach an AI agent how to use Quill's MCP tools well: join a meeting, take notes, find old decisions, write follow-ups.

Contract: [`docs/server-spec.md`](../docs/server-spec.md), sections 7 (MCP tools) and 11 (skills). Spec date: 2026.10.24.

## Skills

| Skill | Use it to |
|---|---|
| [`quill-setup`](quill-setup/SKILL.md) | Connect your agent to Quill's MCP endpoint and test it. |
| [`quill-meeting-copilot`](quill-meeting-copilot/SKILL.md) | Send the bot into a live Jitsi meeting, follow it, answer questions, track actions. |
| [`quill-meeting-notes`](quill-meeting-notes/SKILL.md) | Write Granola-style notes after a meeting and save them (6 templates). |
| [`quill-meeting-recall`](quill-meeting-recall/SKILL.md) | Answer questions across past meetings, with citations. |
| [`quill-follow-up`](quill-follow-up/SKILL.md) | Turn notes into email drafts, task lists and CRM updates. Asks before sending. |

## Why skills and MCP

MCP gives the agent tools: `join_meeting`, `get_transcript`, `save_notes` and so on. A tool list does not say how to use them well. A skill gives the procedure.

- A skill loads only when it is relevant. The agent sees just the name and description at start (about 100 tokens each). It reads the body when the task matches. It reads `references/` files only when needed. This is called progressive disclosure. ([spec](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx))
- The procedures here save tokens on purpose. They fetch transcripts in windows, read live deltas with `since_seq`, and never load a long transcript twice.
- They also add safety: a consent reminder, no sending without approval, no invented facts, and transcript text treated as data, not instructions.

## Format

Each skill is a folder named like its `name` field. `SKILL.md` has YAML front matter with `name` and `description`. The body is the procedure. Long material lives in `references/`.

Limits from the open spec: `name` is 1 to 64 characters (lowercase letters, digits, hyphens; must match the folder), `description` is 1 to 1024 characters, and `SKILL.md` should stay under 500 lines. These skills stay under about 150 lines. Source: <https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx>.

## Install

First connect the agent to Quill with the `quill-setup` steps below (or install that skill and ask the agent to follow it). You need the MCP URL and API key from the StartOS action **Get API Credentials**.

Run the install commands from the folder that holds this README's parent repo. `skills/quill-*` matches only skill folders.

| Agent | Install skills | Connect to MCP | Sources |
|---|---|---|---|
| **Claude Code** | `cp -r skills/quill-* ~/.claude/skills/` for all projects, or into `.claude/skills/` in a repo. | `claude mcp add --transport http --scope user quill "<quill-mcp-url>" --header "Authorization: Bearer $QUILL_API_KEY"`. Check with `claude mcp list` or `/mcp`. | [Skills](https://code.claude.com/docs/en/skills), [MCP](https://code.claude.com/docs/en/mcp) |
| **OpenAI Codex CLI** | `cp -r skills/quill-* ~/.codex/skills/` (`$CODEX_HOME/skills`), or `~/.agents/skills/`, or `.agents/skills/` in a repo. | In `~/.codex/config.toml`: `[mcp_servers.quill]`, `url = "<quill-mcp-url>"`, `bearer_token_env_var = "QUILL_API_KEY"`. Or `codex mcp add quill --url "<quill-mcp-url>" --bearer-token-env-var QUILL_API_KEY`. | [Skill roots](https://github.com/openai/codex/blob/main/codex-rs/exec-server/src/discoverV2/capability_locations.rs), [MCP fields](https://github.com/openai/codex/blob/main/codex-rs/config/src/mcp_types.rs), [`mcp add`](https://github.com/openai/codex/blob/main/codex-rs/cli/src/mcp_cmd.rs) |
| **Hermes Agent** | `cp -r skills/quill-* ~/.hermes/skills/`. Or add `~/.agents/skills` to `skills.external_dirs` in `~/.hermes/config.yaml`. Repo skills go in `.hermes/skills/` or `.agents/skills/` (run `hermes skills trust` once). | In `~/.hermes/config.yaml`: `mcp_servers.quill` with `url` and `headers: {Authorization: "Bearer ${QUILL_API_KEY}"}`; put the key in `~/.hermes/.env`. Or `hermes mcp add quill --url "<quill-mcp-url>" --auth header`. Test: `hermes mcp test quill`. | [Skills](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md), [Working with skills](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/work-with-skills.md), [MCP reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md), [CLI](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md) |
| **OpenClaw** | For each skill: `openclaw skills install ./skills/quill-setup` (add `--global` for all agents; `--as <slug>` to rename). Or copy into `<workspace>/skills/` or `~/.agents/skills/`. | `openclaw mcp add quill --url "<quill-mcp-url>" --transport streamable-http --header 'Authorization=Bearer ${QUILL_API_KEY}'`, key in `~/.openclaw/.env`. Test: `openclaw mcp doctor quill --probe`. | [Skills](https://github.com/openclaw/openclaw/blob/main/docs/tools/skills.md), [Creating skills](https://github.com/openclaw/openclaw/blob/main/docs/tools/creating-skills.md), [MCP](https://github.com/openclaw/openclaw/blob/main/docs/tools/mcp.md), [`mcp` CLI](https://github.com/openclaw/openclaw/blob/main/docs/cli/mcp/registry.md) |

`<quill-mcp-url>` is the URL from **Get API Credentials**. It ends in `/mcp`. Every agent sends `Authorization: Bearer <api key>`. Keep the key in an environment variable, not in a repo.

One copy for several agents: `~/.agents/skills/` is read by Codex (source), OpenClaw (docs) and Hermes (with `external_dirs`). Claude Code reads `~/.claude/skills/`.

OpenClaw lists skills by ClawHub too (`openclaw skills install @owner/<slug>`). These skills are not published there.

Restart or start a new session after you add skills or the MCP server. Then ask the agent: "Use the Quill setup skill and test the connection." It calls `get_status`.

## Update

1. Pull the latest repo.
2. Replace the installed copies. Remove the old folder first so deleted files do not stay: `rm -rf ~/.claude/skills/quill-* && cp -r skills/quill-* ~/.claude/skills/` (use your agent's path).
3. OpenClaw: reinstall with `openclaw skills install ./skills/<name>`.
4. Start a new session.

If the MCP tool names or arguments change, the contract changes first in `docs/server-spec.md`, then the skills.

## What is not verified

- **Codex official docs.** `developers.openai.com/codex/skills` and `/codex/mcp` could not be read from the build environment. Skill folders and MCP field names come from the Codex source code (links above). Check the official page if a path does not work.
- **Quill server.** The skills follow the spec. They were not run against a live Quill server. Details the spec leaves open are written defensively: the date format for `list_meetings` (ISO dates), how the end of `get_transcript` paging shows (no `next_offset`), that `save_notes` replaces earlier AI notes, and that bot state is visible through `get_meeting`.
- **Tool name prefixes.** Clients may show `mcp__quill__get_status` or similar. The skills use the plain names from the spec.
- **Private certificates.** A StartOS local address may use a private CA. Which agents accept it, and how, was not tested. Hermes (`ssl_verify`) and OpenClaw (`sslVerify`) document a CA option. See `quill-setup/references/troubleshooting.md`.
- **OpenClaw description length.** Its guide says to keep a description to one line under 160 characters. These descriptions are longer (they stay under the 1024 limit) so the trigger is precise. Shorten them if OpenClaw skips a skill.
- **Symlinks.** Installing by symlink instead of copy was not tested for any agent.
