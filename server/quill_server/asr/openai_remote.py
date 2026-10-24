"""Remote OpenAI-compatible engine (``POST {base}/v1/audio/transcriptions``)."""

from __future__ import annotations

import io

import httpx
import numpy as np
import soundfile as sf

from . import EngineError, Utterance, Word

SAMPLE_RATE = 16000


def _wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def utterances_from_verbose(doc: dict, duration_ms: int) -> list[Utterance]:
    """Map a ``verbose_json`` response to utterances; one segment if none are given."""
    words = [
        Word(str(w.get("word", "")).strip(), round(float(w["start"]) * 1000), round(float(w["end"]) * 1000))
        for w in doc.get("words") or []
        if "start" in w and "end" in w
    ]
    segments = doc.get("segments") or []
    out: list[Utterance] = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        s, e = round(float(seg.get("start", 0)) * 1000), round(float(seg.get("end", 0)) * 1000)
        out.append(Utterance(s, max(e, s), text, [w for w in words if s <= w.s < max(e, s + 1)]))
    if out:
        return out
    text = str(doc.get("text", "")).strip()
    return [Utterance(0, duration_ms, text, words)] if text else []


class OpenAIEngine:
    name = "openai"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "whisper-1",
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._client = client or httpx.Client(timeout=httpx.Timeout(300, connect=15))

    def ready(self) -> bool:
        return bool(self._base)

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> list[Utterance]:
        if len(samples) < SAMPLE_RATE // 10:
            return []
        data = {"model": self.model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            resp = self._client.post(
                f"{self._base}/v1/audio/transcriptions",
                data=data,
                files={"file": ("chunk.wav", _wav_bytes(samples), "audio/wav")},
                headers=headers,
            )
            resp.raise_for_status()
            doc = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EngineError(f"remote ASR failed: {exc}") from exc
        return utterances_from_verbose(doc, round(len(samples) * 1000 / SAMPLE_RATE))
