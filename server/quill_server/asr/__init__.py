"""Speech recognition engines behind one small protocol.

An engine turns one VAD-cut chunk of 16 kHz mono audio into utterances with
times relative to the chunk. Segmentation (VAD) and session offsets are not
the engine's business, which is what lets a remote OpenAI-compatible server
replace the local model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from ..config import Config
    from ..models import ModelManager


@dataclass(frozen=True)
class Word:
    w: str
    s: int  # ms from the start of the chunk
    e: int


@dataclass(frozen=True)
class Utterance:
    start_ms: int
    end_ms: int
    text: str
    words: list[Word] = field(default_factory=list)


class EngineError(Exception):
    """Transcription failed for this chunk; the caller may continue with others."""


class AsrEngine(Protocol):
    name: str
    model: str

    def ready(self) -> bool:
        """True when ``transcribe`` can run now (model files present, URL set)."""

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Utterance]:
        """Float32 mono 16 kHz samples in [-1, 1] -> utterances (chunk-relative)."""


def build_engine(config: "Config", models: "ModelManager") -> AsrEngine:
    if config.asr_engine == "openai":
        from .openai_remote import OpenAIEngine

        return OpenAIEngine(config.remote_asr_url, config.remote_asr_key, config.remote_asr_model)
    from .parakeet import ParakeetEngine

    return ParakeetEngine(models, config.asr_model, config.threads)
