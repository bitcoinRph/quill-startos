"""Parakeet TDT 0.6B (int8 ONNX) through sherpa-onnx."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Sequence

import numpy as np

from . import EngineError, Utterance, Word

if TYPE_CHECKING:
    from ..models import ModelManager

SAMPLE_RATE = 16000
_MIN_SAMPLES = SAMPLE_RATE // 10
_LAST_TOKEN_MS = 80  # one encoder frame (8x subsampling at 10 ms) when no duration is known


def words_from_tokens(
    tokens: Sequence[str],
    timestamps: Sequence[float],
    durations: Sequence[float] | None = None,
) -> list[Word]:
    """Group sub-word tokens into words.

    A token that starts with a space begins a new word (the first token always
    does); other tokens, including punctuation, extend the current one. A word
    ends where its last token ends, or where the next word begins when the
    model reports no durations.
    """
    groups: list[list[int]] = []
    for i, tok in enumerate(tokens):
        if not groups or tok.startswith(" "):
            groups.append([i])
        else:
            groups[-1].append(i)

    words: list[Word] = []
    for gi, idxs in enumerate(groups):
        text = "".join(tokens[i] for i in idxs).strip()
        if not text:
            continue
        start = round(timestamps[idxs[0]] * 1000)
        last = idxs[-1]
        if durations is not None and last < len(durations):
            end = round((timestamps[last] + durations[last]) * 1000)
        elif gi + 1 < len(groups):
            end = round(timestamps[groups[gi + 1][0]] * 1000)
        else:
            end = round(timestamps[last] * 1000) + _LAST_TOKEN_MS
        words.append(Word(text, start, max(end, start)))
    return words


class ParakeetEngine:
    name = "parakeet"

    def __init__(self, models: "ModelManager", model: str, threads: int) -> None:
        self.model = model
        self._models = models
        self._threads = threads
        self._recognizer = None
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self._models.is_installed(self.model)

    def _load(self):
        with self._lock:
            if self._recognizer is None:
                import sherpa_onnx

                d = self._models.model_dir(self.model)
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=str(d / "encoder.int8.onnx"),
                    decoder=str(d / "decoder.int8.onnx"),
                    joiner=str(d / "joiner.int8.onnx"),
                    tokens=str(d / "tokens.txt"),
                    num_threads=self._threads,
                    sample_rate=SAMPLE_RATE,
                    model_type="nemo_transducer",
                )
            return self._recognizer

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Utterance]:
        if len(samples) < _MIN_SAMPLES:
            return []
        try:
            rec = self._load()
            stream = rec.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples.astype(np.float32, copy=False))
            rec.decode_stream(stream)
            result = stream.result
        except Exception as exc:
            raise EngineError(f"parakeet failed: {exc}") from exc

        tokens = list(result.tokens)
        text = "".join(tokens).strip()
        if not text:
            return []
        durations = list(getattr(result, "durations", []) or []) or None
        words = words_from_tokens(tokens, list(result.timestamps), durations)
        start = words[0].s if words else 0
        end = words[-1].e if words else round(len(samples) * 1000 / SAMPLE_RATE)
        return [Utterance(start, end, text, words)]
