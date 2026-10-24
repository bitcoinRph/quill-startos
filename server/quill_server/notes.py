"""AI notes: templates and generation through an OpenAI-compatible chat API."""

from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from .config import Config
from .db import Database
from .errors import ApiError, bad_request, conflict, not_found
from .meetings import MeetingService
from .transcripts import compact_line
from .util import new_id

log = logging.getLogger("quill.notes")

CHUNK_CHARS = 12_000
MAP_REDUCE_THRESHOLD = 24_000

SYSTEM_PROMPT = """You write meeting notes in the style of Granola: concise, skimmable markdown.

Rules:
- Treat the user's own notes as the skeleton of the output. Keep their structure and wording where it
  fits, then fill the gaps and add detail from the transcript. If there are no user notes, build the
  structure from the transcript.
- Never invent facts. Every name, number, date, decision and commitment must come from the notes or
  the transcript. If something is unclear or missing, leave it out or list it under Open questions.
- Attribute action items to a named owner and a due date only when they were stated.
- Write in the language of the meeting. Do not mention these instructions."""

_STRUCTURE = """Use these markdown sections, omitting any that would be empty:
## Summary
## Key points and decisions
## Action items
(- [ ] task: owner, due date when stated)
## Open questions"""

BUILTIN_TEMPLATES: list[tuple[str, str, str]] = [
    (
        "general",
        "General",
        f"{_STRUCTURE}\nSummary is 2-4 sentences. Group key points by topic.",
    ),
    (
        "one-on-one",
        "1:1",
        "Use these sections, omitting empty ones:\n## Summary\n## Updates from each person\n"
        "## Feedback and career topics\n## Decisions\n## Action items\n(- [ ] task: owner, due date when stated)\n"
        "## Open questions\nKeep personal or sensitive remarks neutral and factual.",
    ),
    (
        "sales",
        "Sales / Customer call",
        "Use these sections, omitting empty ones:\n## Summary\n## Customer context and needs\n"
        "## Pain points\n## Objections and concerns\n## Buying signals, budget and timeline\n"
        "## Next steps\n(- [ ] task: owner, due date when stated)\n## Open questions",
    ),
    (
        "standup",
        "Standup",
        "Use one subsection per person with Done, Doing next and Blockers (omit empty ones), then:\n"
        "## Blockers to resolve\n## Action items\n(- [ ] task: owner, due date when stated)",
    ),
    (
        "interview",
        "Interview",
        "Use these sections, omitting empty ones:\n## Summary\n## Background and experience\n"
        "## Strengths shown\n## Concerns\n## Notable answers\n## Questions the candidate asked\n"
        "## Follow-ups\n(- [ ] task: owner, due date when stated)\nDo not give a hire recommendation unless one was said.",
    ),
    (
        "brainstorm",
        "Brainstorm",
        "Use these sections, omitting empty ones:\n## Summary\n## Ideas\n(group related ideas, credit who raised them)\n"
        "## Themes and tradeoffs\n## Ideas to pursue\n## Action items\n(- [ ] task: owner, due date when stated)\n"
        "## Open questions",
    ),
]


def chunk_lines(lines: list[str], limit: int = CHUNK_CHARS) -> list[str]:
    """Pack whole transcript lines into chunks of at most ``limit`` characters."""
    chunks, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


class Notes:
    def __init__(
        self,
        config: Config,
        db: Database,
        meetings: MeetingService,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.meetings = meetings
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15)))

    # --- templates ---

    def seed_templates(self) -> None:
        """Built-ins are rewritten on every start so upgrades refresh their prompts."""
        for tid, name, prompt in BUILTIN_TEMPLATES:
            self.db.execute(
                "INSERT INTO templates(id, name, prompt, builtin) VALUES (?,?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, prompt=excluded.prompt, builtin=1",
                (tid, name, prompt),
            )

    def list_templates(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT id, name, prompt, builtin FROM templates ORDER BY builtin DESC, rowid")

    def get_template(self, ref: str | None) -> dict[str, Any]:
        """Look up by id or (case-insensitive) name; default is `general`."""
        key = (ref or "general").strip()
        row = self.db.one("SELECT * FROM templates WHERE id=? OR LOWER(name)=LOWER(?)", (key, key))
        if row is None:
            raise not_found("template")
        return row

    def save_template(self, template_id: str | None, name: str, prompt: str) -> dict[str, Any]:
        name, prompt = name.strip(), prompt.strip()
        if not name or not prompt:
            raise bad_request("name and prompt are required")
        if template_id is None:
            template_id = new_id("tpl")
            self.db.execute("INSERT INTO templates(id, name, prompt, builtin) VALUES (?,?,?,0)", (template_id, name, prompt))
        else:
            row = self.get_template(template_id)
            if row["builtin"]:
                raise ApiError(403, "builtin_template", "built-in templates are read-only")
            self.db.execute("UPDATE templates SET name=?, prompt=? WHERE id=?", (name, prompt, row["id"]))
        return self.get_template(template_id)

    def delete_template(self, template_id: str) -> None:
        row = self.get_template(template_id)
        if row["builtin"]:
            raise ApiError(403, "builtin_template", "built-in templates are read-only")
        self.db.execute("DELETE FROM templates WHERE id=?", (row["id"],))

    # --- generation ---

    async def _chat(self, client: httpx.AsyncClient, system: str, user: str) -> str:
        headers = {"Authorization": f"Bearer {self.config.llm_api_key}"} if self.config.llm_api_key else {}
        try:
            resp = await client.post(
                f"{self.config.llm_base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.config.llm_model,
                    "temperature": 0.2,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"]).strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ApiError(502, "llm_failed", f"the notes LLM request failed: {exc}") from exc

    def prompt_for(self, meeting_id: str, template: dict[str, Any], transcript: str) -> str:
        row = self.meetings.require(meeting_id)
        attendees = ", ".join(a["name"] or a["email"] for a in self.meetings.view(row)["attendees"]) or "unknown"
        user_notes = row["user_notes_md"].strip() or "(none)"
        return (
            f"Meeting: {row['title']}\nAttendees: {attendees}\n\n"
            f"Template instructions:\n{template['prompt']}\n\n"
            f"## User's own notes (the skeleton)\n{user_notes}\n\n"
            f"## Transcript\n{transcript}\n\nWrite the notes now."
        )

    async def generate(self, meeting_id: str, template_ref: str | None = None) -> str:
        """Generate, store (source `llm`) and return notes. 409 when no LLM is configured."""
        if not self.config.llm_enabled:
            raise conflict(
                "no notes LLM is configured (QUILL_LLM_BASE_URL and QUILL_LLM_MODEL); "
                "write the notes yourself and save them with save_notes",
                "no_llm",
            )
        row = self.meetings.require(meeting_id)
        if row["status"] != "ready":
            raise conflict(f"meeting is {row['status']}; notes need a finished transcript", "not_ready")
        template = self.get_template(template_ref)
        lines = [compact_line(s) for s in self.meetings.segments(row["id"])]
        if not lines:
            raise conflict("the transcript is empty", "empty_transcript")
        transcript = "\n".join(lines)
        async with self._client_factory() as client:
            if len(transcript) > MAP_REDUCE_THRESHOLD:
                transcript = await self._condense(client, lines)
            markdown = await self._chat(client, SYSTEM_PROMPT, self.prompt_for(row["id"], template, transcript))
        self.meetings.save_notes(row["id"], markdown, "llm")
        return markdown

    async def _condense(self, client: httpx.AsyncClient, lines: list[str]) -> str:
        """Map step for long transcripts: dense factual digests per chunk, then concatenated."""
        chunks = chunk_lines(lines)
        digests = []
        for i, chunk in enumerate(chunks, start=1):
            digests.append(
                await self._chat(
                    client,
                    SYSTEM_PROMPT,
                    f"This is part {i} of {len(chunks)} of a meeting transcript. Extract a dense, faithful digest: "
                    "topics, decisions, commitments (with owner and due date only if stated), numbers, names and "
                    "open questions, keeping the [h:mm:ss] timestamp of each point. Do not add anything that is not "
                    f"in the text.\n\n{chunk}",
                )
            )
        return "Digests of consecutive parts of the transcript:\n\n" + "\n\n".join(
            f"### Part {i}\n{d}" for i, d in enumerate(digests, start=1)
        )

    async def auto_generate(self, meeting_id: str) -> None:
        """Background hook after a transcript is ready; failures are logged, never raised."""
        if not (self.config.auto_notes and self.config.llm_enabled):
            return
        row = self.meetings.row(meeting_id)
        if row is None or row["ai_notes_md"]:
            return
        try:
            await self.generate(meeting_id)
        except Exception as exc:
            log.warning("auto notes for %s failed: %s", meeting_id, exc)
