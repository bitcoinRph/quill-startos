"""Background upkeep: Opus compression of finished WAVs and audio retention."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from .audio import AudioError, transcode_to_opus
from .config import Config
from .errors import ApiError
from .meetings import MeetingService
from .util import iso, utcnow

log = logging.getLogger("quill.housekeeping")


async def compress_session(config: Config, meetings: MeetingService, meeting_id: str) -> None:
    """Transcode a meeting's finished ``.wav`` segments to ``.ogg`` (Opus).

    The new file is written first, then the track rows and ``meta.json`` are
    updated, then the WAV is removed, so a failure at any point leaves a
    playable session.
    """
    if not config.compress_audio:
        return
    try:
        row = meetings.require(meeting_id)
    except ApiError:
        return
    directory = meetings.session_path(row)
    changed = False
    for track in meetings.tracks(row["id"]):
        segments = track["segments"]
        for seg in segments:
            src = directory / seg["file"]
            if src.suffix != ".wav" or not src.is_file():
                continue
            dst = src.with_suffix(".ogg")
            try:
                await asyncio.to_thread(transcode_to_opus, src, dst)
            except AudioError as exc:
                log.warning("keeping %s as WAV: %s", src.name, exc)
                continue
            seg["file"] = dst.name
            meetings.save_track_segments(row["id"], track["id"], segments, track["status"], None)
            src.unlink(missing_ok=True)
            changed = True
    if changed:
        meetings.write_files(row["id"])


def delete_old_audio(config: Config, meetings: MeetingService) -> int:
    """Delete audio older than the retention window; transcripts and notes stay."""
    days = config.audio_retention_days
    if days <= 0:
        return 0
    cutoff = iso(utcnow() - timedelta(days=days))
    removed = 0
    for row in meetings.db.query("SELECT id FROM meetings WHERE status IN ('ready','failed') AND ended_at < ?", (cutoff,)):
        meeting = meetings.require(row["id"])
        directory = meetings.session_path(meeting)
        for track in meetings.tracks(meeting["id"]):
            for seg in track["segments"]:
                path = directory / seg["file"]
                if path.is_file():
                    path.unlink()
                    removed += 1
    return removed


async def retention_loop(config: Config, meetings: MeetingService) -> None:
    while True:
        try:
            removed = await asyncio.to_thread(delete_old_audio, config, meetings)
            if removed:
                log.info("retention removed %d audio files", removed)
        except Exception:
            log.exception("retention sweep failed")
        await asyncio.sleep(6 * 3600)
