"""Live ingest (spec section 6): tracks, VAD, recognizer queue, fan-out.

One :class:`LiveMeeting` per meeting that is receiving audio. Everything here
runs on the event loop; the only cross-thread hop is the recognizer result,
which is marshalled back with ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, Sequence

import numpy as np

from .asr import Utterance
from .asr.worker import LIVE, RecognizerWorker
from .audio import SAMPLE_RATE, Cut, SpeechCutter
from .config import Config
from .errors import ApiError, conflict
from .meetings import MeetingService
from .models import ModelManager
from .storage import WavWriter, next_segment_file
from .util import slug

log = logging.getLogger("quill.live")

KINDS = ("mic", "system", "participant", "upload")
MAINTENANCE_S = 5.0
# A meeting with no connected client and no activity is finalized after this
# long, so a crashed browser or bot cannot leave it `live` forever.
DISCONNECT_GRACE_S = 300.0
MAX_FRAME_BYTES = 256 * 1024

SendJson = Callable[[dict[str, Any]], Awaitable[None]]


def display_speaker(config: Config, kind: str, speaker: str, label: str | None) -> str:
    """The name that appears in the transcript for a track."""
    if label:
        return label
    if kind == "mic" and speaker.lower() in ("", "me"):
        return config.speaker_name
    return speaker or kind


class LiveTrack:
    def __init__(self, track_id: str, kind: str, speaker: str, label: str | None, participant_id: str | None,
                 wav: WavWriter, file: str, cutter: SpeechCutter, owner: "LiveConnection | None") -> None:  # fmt: skip
        self.id = track_id
        self.kind = kind
        self.speaker = speaker
        self.label = label
        self.participant_id = participant_id
        self.wav = wav
        self.file = file
        self.cutter = cutter
        self.owner = owner
        self.offset_ms: int | None = None  # set by the first frame


class LiveMeeting:
    def __init__(self, hub: "LiveHub", meeting_id: str) -> None:
        self.hub = hub
        self.id = meeting_id
        self.t0: float | None = None
        self.active: dict[str, LiveTrack] = {}
        self.connections: set["LiveConnection"] = set()
        self.pending_ms = 0
        self.inflight = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.stopping = False
        self.finalized = asyncio.Event()
        self._maintenance: asyncio.Task | None = None
        self._grace: asyncio.TimerHandle | None = None
        self._finalizer: asyncio.Task | None = None

    # --- clock and tracks ---

    def clock_ms(self) -> int:
        return 0 if self.t0 is None else int((time.monotonic() - self.t0) * 1000)

    def register_track(self, msg: dict[str, Any], owner: "LiveConnection | None") -> None:
        """Register (or re-describe) a track; raises :class:`LiveError` on bad input."""
        hub = self.hub
        track_id = str(msg.get("track_id") or "").strip()
        if not track_id or len(track_id) > 64:
            raise LiveError("bad_track", "track_id is required (max 64 characters)")
        kind = str(msg.get("kind") or "participant")
        if kind not in KINDS:
            raise LiveError("bad_track", f"kind must be one of {', '.join(KINDS)}")
        if int(msg.get("sample_rate") or SAMPLE_RATE) != SAMPLE_RATE:
            raise LiveError("bad_sample_rate", f"sample_rate must be {SAMPLE_RATE}")
        speaker = str(msg.get("speaker") or "")
        label = str(msg["label"]).strip() if msg.get("label") else None
        participant_id = str(msg["participant_id"]) if msg.get("participant_id") else None
        shown = display_speaker(hub.config, kind, speaker, label)

        existing = self.active.get(track_id)
        if existing is not None:
            existing.speaker, existing.label = shown, label
            hub.meetings.update_track_identity(self.id, track_id, shown, label)
            return
        if not hub.models.vad_ready:
            raise LiveError("model_not_ready", "the voice-activity model is still downloading")

        directory = hub.meetings.session_path(hub.meetings.require(self.id))
        file = next_segment_file(directory, slug(track_id))
        track = LiveTrack(
            track_id, kind, shown, label, participant_id,
            WavWriter(directory / file), file, SpeechCutter(hub.models.vad_path), owner,
        )  # fmt: skip
        self.active[track_id] = track
        hub.meetings.upsert_track(
            self.id, track_id, kind=kind, speaker=shown, label=label, participant_id=participant_id,
            offset_ms=0,
            segment={"file": file, "start_offset_ms": 0, "end_offset_ms": 0, "frames_written": 0,
                     "sample_rate_hz": SAMPLE_RATE, "channels": 1},
        )  # fmt: skip
        if hub.meetings.require(self.id)["status"] != "live":
            hub.meetings.set_status(self.id, "live")
            hub.meetings.emit("meeting.started", self.id)

    def on_frame(self, track_id: str, pcm: bytes) -> None:
        track = self.active.get(track_id)
        if track is None:
            raise LiveError("unknown_track", f"track {track_id!r} is not registered")
        if track.offset_ms is None:
            if self.t0 is None:
                self.t0 = time.monotonic()
                self._start_maintenance()
            track.offset_ms = self.clock_ms()
            self._persist(track)
        usable = pcm[: len(pcm) - len(pcm) % 2]
        if not usable:
            return
        track.wav.write(usable)
        self._submit(track, track.cutter.feed(np.frombuffer(usable, dtype=np.int16)))

    def end_track(self, track_id: str) -> None:
        track = self.active.pop(track_id, None)
        if track is None:
            return
        self._submit(track, track.cutter.flush())
        track.wav.close()
        self._persist(track, closed=True)

    def _segment_info(self, track: LiveTrack) -> dict[str, Any]:
        start = track.offset_ms or 0
        return {
            "file": track.file,
            "start_offset_ms": start,
            "end_offset_ms": start + track.wav.frames * 1000 // SAMPLE_RATE,
            "frames_written": track.wav.frames,
            "sample_rate_hz": SAMPLE_RATE,
            "channels": 1,
        }

    def _persist(self, track: LiveTrack, closed: bool = False) -> None:
        """Keep the track row current so a crash can be recovered from the DB."""
        row = next((t for t in self.hub.meetings.tracks(self.id) if t["id"] == track.id), None)
        if row is None:
            return
        info = self._segment_info(track)
        files = [s for s in row["segments"] if s["file"] != track.file] + [info]
        self.hub.meetings.save_track_segments(
            self.id, track.id, files, "complete" if closed else "live", info["end_offset_ms"]
        )
        if track.offset_ms is not None and len(files) == 1:
            self.hub.meetings.db.execute(
                "UPDATE tracks SET started_offset_ms=? WHERE meeting_id=? AND id=?", (track.offset_ms, self.id, track.id)
            )

    # --- recognition ---

    def _submit(self, track: LiveTrack, cuts: Sequence[Cut]) -> None:
        loop = asyncio.get_running_loop()
        for cut in cuts:
            self.pending_ms += cut.duration_ms
            self.inflight += 1
            self.idle.clear()
            fut = self.hub.worker.submit(cut.samples, priority=LIVE)
            fut.add_done_callback(
                lambda f, t=track, c=cut: loop.call_soon_threadsafe(self._on_result, t, c, f)
            )

    def _on_result(self, track: LiveTrack, cut: Cut, fut: "Future[list[Utterance]]") -> None:
        self.pending_ms = max(0, self.pending_ms - cut.duration_ms)
        self.inflight -= 1
        try:
            if fut.cancelled() or fut.exception() is not None:
                log.warning("live chunk dropped: %s", fut.exception())
            else:
                base = (track.offset_ms or 0) + cut.start_ms
                for utt in fut.result():
                    self._store(track, utt, base)
        except ApiError:  # meeting deleted while a chunk was in flight
            pass
        finally:
            if self.inflight == 0:
                self.idle.set()
        self.publish_status()

    def _store(self, track: LiveTrack, utt: Utterance, base_ms: int) -> None:
        words = [{"w": w.w, "s": w.s + base_ms, "e": w.e + base_ms} for w in utt.words] or None
        seg = self.hub.meetings.add_segment(
            self.id,
            track_id=track.id,
            speaker=track.speaker,
            start_ms=base_ms + utt.start_ms,
            end_ms=base_ms + utt.end_ms,
            text=utt.text,
            words=words,
        )
        public = {k: seg[k] for k in ("seq", "track_id", "speaker", "start_ms", "end_ms", "text")}
        self.hub.meetings.bus.publish(self.id, {"type": "segment", "segment": public})

    def publish_status(self, status: str | None = None) -> None:
        self.hub.meetings.bus.publish(
            self.id,
            {"type": "status", "status": status or ("processing" if self.stopping else "live"), "lag_ms": self.pending_ms},
        )

    # --- maintenance, connections ---

    def _start_maintenance(self) -> None:
        if self._maintenance is None:
            self._maintenance = asyncio.get_running_loop().create_task(self._maintain())

    async def _maintain(self) -> None:
        """Every few seconds: fix WAV headers (crash safety), persist, report lag."""
        while True:
            await asyncio.sleep(MAINTENANCE_S)
            for track in list(self.active.values()):
                track.wav.update_header()
                try:
                    self._persist(track)
                except ApiError:
                    return
            self.publish_status()

    def attach(self, conn: "LiveConnection") -> None:
        self.connections.add(conn)
        if self._grace is not None:
            self._grace.cancel()
            self._grace = None

    def detach(self, conn: "LiveConnection") -> None:
        self.connections.discard(conn)
        for track in [t for t in self.active.values() if t.owner is conn]:
            self.end_track(track.id)
        if not self.connections and not self.stopping and self._grace is None:
            self._grace = asyncio.get_running_loop().call_later(
                DISCONNECT_GRACE_S, lambda: asyncio.ensure_future(self.stop())
            )

    # --- stop ---

    async def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        if self._grace is not None:
            self._grace.cancel()
        meetings = self.hub.meetings
        if meetings.require(self.id)["status"] == "live":
            meetings.emit("meeting.ended", self.id)
        meetings.set_status(self.id, "processing", lag_ms=self.pending_ms)
        self._finalizer = asyncio.get_running_loop().create_task(self._finalize())

    async def _finalize(self) -> None:
        meetings = self.hub.meetings
        try:
            for track_id in list(self.active):
                self.end_track(track_id)
            if self._maintenance is not None:
                self._maintenance.cancel()
            await self.idle.wait()
            tracks = meetings.tracks(self.id)
            if not tracks:
                meetings.fail(self.id, "no audio received")
                return
            end = max((t["ended_offset_ms"] or 0 for t in tracks), default=0)
            engine = self.hub.worker.engine
            meetings.complete(self.id, engine=engine.name, model=engine.model, duration_ms=end)
        except Exception as exc:
            log.exception("finalizing %s failed", self.id)
            try:
                meetings.fail(self.id, f"finalization failed: {exc}")
            except ApiError:
                pass
        finally:
            self.hub.sessions.pop(self.id, None)
            self.finalized.set()

    def abandon(self) -> None:
        """Server shutdown: close files, keep status `live` so startup recovery finishes it."""
        if self._maintenance is not None:
            self._maintenance.cancel()
        if self._grace is not None:
            self._grace.cancel()
        for track_id in list(self.active):
            track = self.active.pop(track_id)
            track.wav.close()
            try:
                self._persist(track, closed=True)
            except ApiError:
                pass


class LiveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LiveConnection:
    """One WebSocket client's protocol state; transport-agnostic for testing."""

    def __init__(self, meeting: LiveMeeting, send: SendJson) -> None:
        self.meeting = meeting
        self._send = send
        self._reported: set[str] = set()
        meeting.attach(self)

    async def error(self, code: str, message: str) -> None:
        await self._send({"type": "error", "code": code, "message": message})

    async def on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            kind = msg["type"]
        except (ValueError, KeyError, TypeError):
            await self.error("bad_message", "expected a JSON object with a type")
            return
        m = self.meeting
        try:
            if kind == "track":
                m.register_track(msg, self)
            elif kind == "track_end":
                m.end_track(str(msg.get("track_id") or ""))
            elif kind == "note":
                m.hub.meetings.update(m.id, user_notes_md=str(msg.get("markdown") or ""))
            elif kind == "stop":
                await m.stop()
            elif kind == "ping":
                await self._send({"type": "pong"})
            else:
                await self.error("unknown_type", f"unknown message type {kind!r}")
        except LiveError as exc:
            await self.error(exc.code, exc.message)
        except ApiError as exc:
            await self.error(exc.code, exc.message)

    async def on_binary(self, data: bytes) -> None:
        if len(data) > MAX_FRAME_BYTES:
            await self.error("frame_too_large", f"frames are limited to {MAX_FRAME_BYTES} bytes")
            return
        n = data[0] if data else 0
        if n == 0 or len(data) < 1 + n:
            await self.error("bad_frame", "binary frame: byte 0 is the track id length")
            return
        track_id = data[1 : 1 + n].decode("utf-8", "replace")
        try:
            self.meeting.on_frame(track_id, data[1 + n :])
        except LiveError as exc:
            if track_id not in self._reported:  # one report per track, not one per 100 ms frame
                self._reported.add(track_id)
                await self.error(exc.code, exc.message)

    def close(self) -> None:
        self.meeting.detach(self)


class LiveHub:
    """The live ingest service. Also what the Jitsi bot manager receives.

    Public surface for the bot: ``internal_token``, ``create_meeting`` and the
    WebSocket endpoint documented in the README; the bot does not call the
    other methods.
    """

    def __init__(
        self, config: Config, meetings: MeetingService, models: ModelManager, worker: RecognizerWorker
    ) -> None:
        self.config = config
        self.meetings = meetings
        self.models = models
        self.worker = worker
        self.sessions: dict[str, LiveMeeting] = {}

    @property
    def internal_token(self) -> str:
        return self.config.internal_token

    @property
    def live_count(self) -> int:
        return len(self.sessions)

    async def create_meeting(self, title: str | None = None, source: str = "bot",
                             attendees: Sequence[Any] | None = None) -> str:  # fmt: skip
        """Create a meeting in `scheduled` and return its id (what the bot joins as)."""
        return self.meetings.create(title=title, source=source, attendees=attendees)["id"]

    def session(self, meeting_id: str) -> LiveMeeting:
        row = self.meetings.require(meeting_id)
        existing = self.sessions.get(row["id"])
        if existing is not None and not existing.stopping:
            return existing
        if row["status"] not in ("scheduled", "live"):
            raise conflict(f"meeting is {row['status']} and no longer accepts audio", "meeting_closed")
        session = LiveMeeting(self, row["id"])
        self.sessions[row["id"]] = session
        return session

    async def stop(self, meeting_id: str) -> None:
        row = self.meetings.require(meeting_id)
        session = self.sessions.get(row["id"])
        if session is None:
            if row["status"] == "scheduled":
                self.meetings.fail(row["id"], "stopped before any audio arrived")
                return
            raise conflict(f"meeting is {row['status']}, not live", "not_live")
        await session.stop()

    async def shutdown(self) -> None:
        for session in list(self.sessions.values()):
            session.abandon()
        self.sessions.clear()
