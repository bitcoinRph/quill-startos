"""Offline transcription of a finished audio file: decode, VAD-cut, recognize."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from concurrent.futures import Future
from pathlib import Path
from typing import Callable

from .asr import EngineError, Utterance, Word
from .asr.worker import OFFLINE, RecognizerWorker
from .audio import SAMPLE_RATE, Cut, SpeechCutter, iter_pcm16

log = logging.getLogger("quill.offline")

# Chunks in flight at once: enough to keep the recognizer busy, few enough to
# bound memory on very long files.
_WINDOW = 6


def shift(utt: Utterance, offset_ms: int) -> Utterance:
    return Utterance(
        utt.start_ms + offset_ms,
        utt.end_ms + offset_ms,
        utt.text,
        [Word(w.w, w.s + offset_ms, w.e + offset_ms) for w in utt.words],
    )


@dataclass(frozen=True)
class OfflineResult:
    utterances: list[Utterance]
    duration_ms: int


def transcribe_file(
    path: Path,
    worker: RecognizerWorker,
    cutter_factory: Callable[[], SpeechCutter],
    *,
    language: str | None = None,
) -> OfflineResult:
    """Blocking; run it in a thread. Times are relative to the file's start.

    One failed chunk is skipped so it cannot cost the rest of the file; if
    every chunk fails the error is raised instead of returning an empty
    transcript that looks like silence.
    """
    cutter = cutter_factory()
    in_flight: deque[tuple[Cut, Future]] = deque()
    results: list[Utterance] = []
    stats = {"ok": 0, "failed": 0}
    last_error: Exception | None = None

    def collect_one() -> None:
        nonlocal last_error
        cut, fut = in_flight.popleft()
        try:
            for utt in fut.result():
                results.append(shift(utt, cut.start_ms))
            stats["ok"] += 1
        except EngineError as exc:
            stats["failed"] += 1
            last_error = exc
            log.warning("skipping chunk at %d ms: %s", cut.start_ms, exc)

    def submit(cuts: list[Cut]) -> None:
        for cut in cuts:
            while len(in_flight) >= _WINDOW:
                collect_one()
            in_flight.append((cut, worker.submit(cut.samples, priority=OFFLINE, language=language)))

    for pcm in iter_pcm16(path):
        submit(cutter.feed(pcm))
    submit(cutter.flush())
    while in_flight:
        collect_one()

    if stats["failed"] and not stats["ok"] and last_error is not None:
        raise last_error
    results.sort(key=lambda u: (u.start_ms, u.end_ms))
    return OfflineResult(results, cutter.fed * 1000 // SAMPLE_RATE)

