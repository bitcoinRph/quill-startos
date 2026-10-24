"""Filesystem-backed transcription queue.

The ``jobs`` table plus the session directories are the queue: after a crash,
:meth:`JobRunner.recover` re-queues whatever was running and finalizes meetings
that were still ``live`` from the audio already on disk. Jobs run one at a time;
the recognizer thread is the real bottleneck anyway.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .asr.worker import RecognizerWorker
from .audio import SpeechCutter
from .config import Config
from .db import Database
from .errors import ApiError
from .meetings import MeetingService
from .models import ModelManager
from .offline import shift, transcribe_file
from .storage import SAMPLE_RATE, repair_wav
from .util import iso, new_id

log = logging.getLogger("quill.jobs")


class JobRunner:
    def __init__(
        self, config: Config, db: Database, meetings: MeetingService, worker: RecognizerWorker, models: ModelManager
    ) -> None:
        self.config = config
        self.db = db
        self.meetings = meetings
        self.worker = worker
        self.models = models
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False

    # --- queue ---

    def start(self) -> None:
        self.recover()
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def depth(self) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')") + self.worker.pending()

    def enqueue(self, meeting_id: str, kind: str = "transcribe") -> str:
        job_id = new_id("job")
        now = iso()
        self.db.execute(
            "INSERT INTO jobs(id, meeting_id, kind, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (job_id, meeting_id, kind, "queued", now, now),
        )
        self._wake.set()
        return job_id

    def queue_transcription(self, meeting_id: str) -> None:
        """Mark the meeting `processing` and queue a pass over all its audio."""
        self.meetings.set_status(meeting_id, "processing")
        self.enqueue(meeting_id)

    def _set_job(self, job_id: str, status: str, error: str | None = None, attempts: int | None = None) -> None:
        self.db.execute(
            "UPDATE jobs SET status=?, error=?, updated_at=?, attempts=COALESCE(?, attempts) WHERE id=?",
            (status, error, iso(), attempts, job_id),
        )

    async def _loop(self) -> None:
        while not self._stopping:
            job = self.db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1")
            if job is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            self._set_job(job["id"], "running", attempts=job["attempts"] + 1)
            try:
                await self._transcribe(job["meeting_id"])
                self._set_job(job["id"], "done")
            except asyncio.CancelledError:
                raise
            except ApiError as exc:  # meeting vanished mid-job
                self._set_job(job["id"], "failed", exc.message)
            except Exception as exc:
                log.exception("job %s failed", job["id"])
                self._set_job(job["id"], "failed", str(exc))
                try:
                    self.meetings.fail(job["meeting_id"], f"transcription failed: {exc}")
                except ApiError:
                    pass

    # --- the work ---

    async def _transcribe(self, meeting_id: str) -> None:
        meetings = self.meetings
        row = meetings.row(meeting_id)
        if row is None:
            return
        directory = meetings.session_path(row)
        tracks = meetings.tracks(row["id"])
        items: list[dict[str, Any]] = []
        duration = 0
        transcribed = 0
        cutter: Callable[[], SpeechCutter] = lambda: SpeechCutter(self.models.vad_path)  # noqa: E731
        if not self.models.vad_ready:
            raise RuntimeError("voice-activity model is not installed yet")

        for track in tracks:
            files = []
            for seg in track["segments"]:
                path = directory / seg["file"]
                if not path.is_file():
                    log.warning("missing audio %s for %s", seg["file"], row["id"])
                    files.append(seg)
                    continue
                result = await asyncio.to_thread(transcribe_file, path, self.worker, cutter)
                transcribed += 1
                offset = seg["start_offset_ms"]
                for utt in (shift(u, offset) for u in result.utterances):
                    items.append(
                        {
                            "track_id": track["id"], "speaker": track["label"] or track["speaker"],
                            "start_ms": utt.start_ms, "end_ms": utt.end_ms, "text": utt.text,
                            "words": [{"w": w.w, "s": w.s, "e": w.e} for w in utt.words] or None,
                        }
                    )  # fmt: skip
                end = offset + result.duration_ms
                duration = max(duration, end)
                files.append({**seg, "end_offset_ms": end, "frames_written": result.duration_ms * SAMPLE_RATE // 1000})
            meetings.save_track_segments(
                row["id"], track["id"], files, "complete", max((f["end_offset_ms"] for f in files), default=0)
            )
        if not tracks or not transcribed:
            meetings.fail(row["id"], "no audio files to transcribe")
            return
        meetings.replace_segments(row["id"], items)
        engine = self.worker.engine
        meetings.complete(row["id"], engine=engine.name, model=engine.model, duration_ms=duration)

    # --- crash recovery ---

    def recover(self) -> None:
        """Re-queue interrupted work and finalize meetings left `live` by a crash."""
        self.db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        for row in self.db.query("SELECT id FROM meetings WHERE status='live'"):
            meeting = self.meetings.require(row["id"])
            directory = self.meetings.session_path(meeting)
            usable = False
            for track in self.meetings.tracks(meeting["id"]):
                for seg in track["segments"]:
                    path = directory / seg["file"]
                    if path.suffix == ".wav" and path.is_file():
                        seg["frames_written"] = repair_wav(path)
                        seg["end_offset_ms"] = seg["start_offset_ms"] + seg["frames_written"] * 1000 // SAMPLE_RATE
                    usable = usable or path.is_file()
                self.meetings.save_track_segments(
                    meeting["id"], track["id"], track["segments"], "complete",
                    max((s["end_offset_ms"] for s in track["segments"]), default=0),
                )  # fmt: skip
            if usable:
                self.db.execute("UPDATE meetings SET capture_status='recovered' WHERE id=?", (meeting["id"],))
                log.info("recovering interrupted meeting %s", meeting["id"])
                self.queue_transcription(meeting["id"])
            else:
                self.meetings.fail(meeting["id"], "interrupted by a restart before any audio was saved")
        queued = {r["meeting_id"] for r in self.db.query("SELECT meeting_id FROM jobs WHERE status='queued'")}
        for row in self.db.query("SELECT id FROM meetings WHERE status='processing'"):
            if row["id"] not in queued:
                self.enqueue(row["id"])
