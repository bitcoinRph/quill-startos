"""The single recognizer thread.

Decoding is CPU-bound and the model is not safely shared, so every chunk, live
or offline, goes through one thread. Live chunks outrank offline ones so a long
upload cannot stall a meeting in progress.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from concurrent.futures import Future

import numpy as np

from . import AsrEngine, EngineError, Utterance

log = logging.getLogger("quill.asr")

LIVE, OFFLINE = 0, 1
_STOP = object()


class RecognizerWorker:
    def __init__(self, engine: AsrEngine) -> None:
        self.engine = engine
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._thread = threading.Thread(target=self._run, name="quill-asr", daemon=True)
        self._stopping = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put((-1, next(self._counter), _STOP))
        self._thread.join(timeout=10)

    def pending(self) -> int:
        return self._queue.qsize()

    def submit(
        self, samples: np.ndarray, *, priority: int = OFFLINE, language: str | None = None
    ) -> "Future[list[Utterance]]":
        fut: Future[list[Utterance]] = Future()
        self._queue.put((priority, next(self._counter), (samples, language, fut)))
        return fut

    def _run(self) -> None:
        while True:
            _, _, item = self._queue.get()
            if item is _STOP:
                return
            samples, language, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            # Models can still be downloading when the first chunk arrives; the
            # chunk waits here instead of failing.
            while not self.engine.ready() and not self._stopping.wait(0.5):
                pass
            if self._stopping.is_set():
                fut.set_exception(EngineError("shutting down"))
                return
            try:
                fut.set_result(self.engine.transcribe(samples, language))
            except BaseException as exc:  # surfaced to the awaiting caller
                log.warning("transcription failed: %s", exc)
                fut.set_exception(exc)
