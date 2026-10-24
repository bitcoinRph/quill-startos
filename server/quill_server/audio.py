"""Audio plumbing: ffmpeg decode/transcode and Silero VAD cutting."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

SAMPLE_RATE = 16000
VAD_MIN_SILENCE_S = 0.5
VAD_MAX_SPEECH_S = 20.0
_FEED_SAMPLES = SAMPLE_RATE // 10


class AudioError(Exception):
    pass


@dataclass(frozen=True)
class Cut:
    """A speech span: where it starts in its stream and the audio itself."""

    start_sample: int
    samples: np.ndarray  # float32, [-1, 1]

    @property
    def start_ms(self) -> int:
        return self.start_sample * 1000 // SAMPLE_RATE

    @property
    def duration_ms(self) -> int:
        return len(self.samples) * 1000 // SAMPLE_RATE


class SpeechCutter:
    """Silero VAD over a PCM stream; one instance per stream (it holds state)."""

    def __init__(self, vad_model: Path) -> None:
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(vad_model)
        cfg.silero_vad.min_silence_duration = VAD_MIN_SILENCE_S
        cfg.silero_vad.max_speech_duration = VAD_MAX_SPEECH_S
        cfg.sample_rate = SAMPLE_RATE
        self._vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
        self.fed = 0  # samples consumed so far

    def feed(self, pcm16: np.ndarray) -> list[Cut]:
        """Feed int16 samples. They go in 100 ms slices whatever the caller's chunking:
        sherpa-onnx places a speech start late (up to the chunk length) when handed
        large chunks, which would clip the first words of every utterance."""
        cuts: list[Cut] = []
        for i in range(0, len(pcm16), _FEED_SAMPLES):
            piece = pcm16[i : i + _FEED_SAMPLES]
            self._vad.accept_waveform(piece.astype(np.float32) / 32768.0)
            self.fed += len(piece)
            cuts += self._drain()
        return cuts

    def flush(self) -> list[Cut]:
        self._vad.flush()
        return self._drain()

    def _drain(self) -> list[Cut]:
        cuts: list[Cut] = []
        while not self._vad.empty():
            seg = self._vad.front
            cuts.append(Cut(int(seg.start), np.asarray(seg.samples, dtype=np.float32)))
            self._vad.pop()
        return cuts


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def iter_pcm16(path: Path, chunk_seconds: float = 1.0) -> Iterator[np.ndarray]:
    """Decode any ffmpeg-readable file to 16 kHz mono int16, streamed in chunks.

    Streaming keeps memory flat for multi-hour recordings. Raises
    :class:`AudioError` when ffmpeg cannot read the file.
    """
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
        "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
    ]  # fmt: skip
    chunk_bytes = int(SAMPLE_RATE * chunk_seconds) * 2
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise AudioError(f"ffmpeg not runnable: {exc}") from exc
    produced = 0
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(chunk_bytes)
            if not buf:
                break
            produced += len(buf)
            yield np.frombuffer(buf[: len(buf) - len(buf) % 2], dtype=np.int16)
    finally:
        proc.stdout.close()  # type: ignore[union-attr]
        err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        if proc.poll() is None:
            proc.kill()
        code = proc.wait()
    if code != 0 and produced == 0:
        raise AudioError(f"ffmpeg could not decode {path.name}: {err[-300:] or code}")


def decode_pcm16(path: Path) -> np.ndarray:
    chunks = list(iter_pcm16(path))
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)


def transcode_to_opus(src: Path, dst: Path) -> None:
    """Write ``dst`` (Ogg Opus, mono 16 kHz, ~24 kbit/s) atomically."""
    tmp = dst.with_name(f".{dst.name}.tmp")
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "libopus", "-b:a", "24k",
        "-f", "ogg", str(tmp),
    ]  # fmt: skip
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise AudioError(f"opus transcode failed: {res.stderr.decode('utf-8', 'replace')[-300:]}")
    os.replace(tmp, dst)
