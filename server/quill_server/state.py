"""Application state: every service, wired once and shared by routers and MCP."""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from . import housekeeping
from .asr import AsrEngine, build_engine
from .asr.worker import RecognizerWorker
from .audio import SpeechCutter
from .auth import Auth
from .config import Config
from .db import Database
from .events import EventBus
from .folders import Folders
from .jobs import JobRunner
from .live import LiveHub
from .meetings import MeetingService
from .models import Fetch, ModelManager, http_fetch
from .notes import Notes
from .webhooks import Webhooks

log = logging.getLogger("quill.state")


@dataclass
class AppState:
    config: Config
    db: Database
    auth: Auth
    bus: EventBus
    folders: Folders
    webhooks: Webhooks
    meetings: MeetingService
    models: ModelManager
    worker: RecognizerWorker
    hub: LiveHub
    jobs: JobRunner
    notes: Notes
    started_at: float = field(default_factory=time.time)
    bot: Any = None
    _bot_checked: bool = False
    _tasks: list[asyncio.Task] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        config: Config,
        *,
        engine: AsrEngine | None = None,
        fetch: Fetch = http_fetch,
        webhook_client: Callable[[], httpx.AsyncClient] | None = None,
        notes_client: Callable[[], httpx.AsyncClient] | None = None,
    ) -> "AppState":
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.sessions_dir.mkdir(parents=True, exist_ok=True)
        config.models_dir.mkdir(parents=True, exist_ok=True)
        db = Database(config.db_path)
        bus = EventBus()
        folders = Folders(db)
        webhooks = Webhooks(
            db, folders_of=folders.ids_of_meeting_with_ancestors, run_on_loop=bus.run_on_loop, client_factory=webhook_client
        )
        meetings = MeetingService(config, db, bus, webhooks, folders)
        models = ModelManager(config, fetch=fetch)
        worker = RecognizerWorker(engine or build_engine(config, models))
        notes = Notes(config, db, meetings, notes_client)
        state = cls(
            config=config,
            db=db,
            auth=Auth(config, db),
            bus=bus,
            folders=folders,
            webhooks=webhooks,
            meetings=meetings,
            models=models,
            worker=worker,
            hub=LiveHub(config, meetings, models, worker),
            jobs=JobRunner(config, db, meetings, worker, models),
            notes=notes,
        )

        async def after_ready(meeting_id: str) -> None:
            await asyncio.gather(
                notes.auto_generate(meeting_id),
                housekeeping.compress_session(config, meetings, meeting_id),
            )

        meetings.after_ready = after_ready
        return state

    def cutter_factory(self) -> SpeechCutter:
        return SpeechCutter(self.models.vad_path)

    # --- lifecycle ---

    async def start(self) -> None:
        self.bus.bind(asyncio.get_running_loop())
        self.notes.seed_templates()
        self.models.start_background()
        self.worker.start()
        self.jobs.start()
        loop = asyncio.get_running_loop()
        self._tasks.append(loop.create_task(housekeeping.retention_loop(self.config, self.meetings)))
        if self.config.asr_engine == "openai":
            self._tasks.append(loop.create_task(self._probe_remote_asr()))

    async def _probe_remote_asr(self) -> None:
        """Log whether the remote engine answers. A configured URL counts as ready either way."""
        url = self.config.remote_asr_url
        if not url:
            log.warning("QUILL_ASR_ENGINE=openai but QUILL_REMOTE_ASR_URL is empty")
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
            log.info("remote ASR probe %s -> HTTP %s", url, resp.status_code)
        except httpx.HTTPError as exc:
            log.warning("remote ASR probe %s failed: %s", url, exc)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.bot is not None:
            await self.bot.shutdown()
        await self.hub.shutdown()
        await self.jobs.stop()
        await self.webhooks.aclose()
        self.models.stop()
        self.worker.stop()
        self.db.close()

    # --- bot ---

    def get_bot(self) -> Any:
        """The Jitsi bot manager, or None when disabled or its package is unavailable."""
        if not self.config.jitsi_domains:
            return None
        if not self._bot_checked:
            self._bot_checked = True
            try:
                manager = importlib.import_module("quill_server.bot.manager")
                self.bot = manager.BotManager(
                    self.config, self.hub, create_meeting=self.hub.create_meeting, on_state=self._on_bot_state
                )
            except Exception as exc:
                log.warning("Jitsi bot unavailable: %s", exc)
        return self.bot

    def _on_bot_state(self, info: dict[str, Any]) -> None:
        """A bot that ends before any audio arrived leaves its meeting scheduled; mark it failed."""
        if info.get("state") not in ("failed", "ended"):
            return
        row = self.meetings.row(info["meeting_id"])
        if row is not None and row["status"] == "scheduled":
            self.meetings.fail(row["id"], info.get("error") or "the bot left before any audio was captured")

    def bot_state(self, meeting_id: str) -> dict[str, Any] | None:
        """State and error of the bot attached to a meeting, if any."""
        if self.bot is None:
            return None
        for entry in self.bot.list():
            if entry.get("meeting_id") == meeting_id:
                return {"state": entry.get("state"), "error": entry.get("error")}
        return None

    def health(self) -> dict[str, Any]:
        status = self.models.status()
        engine = self.worker.engine
        return {
            "status": "ok",
            "model_ready": status["model_ready"],
            "engine": engine.name,
            "model": engine.model,
            "queue_depth": self.jobs.depth(),
            "live_meetings": self.hub.live_count,
            "model_download": status["model_download"],
            "model_error": status["model_error"],
        }
