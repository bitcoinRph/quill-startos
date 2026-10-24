"""Model download and verification (spec section 3).

Everything is fetched into a temporary file, hashed while streaming, checked
against the pinned SHA-256 and only then moved into place, so a partial or
tampered download never counts as installed.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, ContextManager, Iterator

import httpx

from .config import Config

log = logging.getLogger("quill.models")

_ASR_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
VAD_ID = "silero-vad"
VAD_FILE = "silero_vad.onnx"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    url: str
    sha256: str
    size: int
    kind: str  # "archive" (tar.bz2 holding the ASR files) or "file"


MODELS: dict[str, ModelSpec] = {
    s.id: s
    for s in (
        ModelSpec(
            "parakeet-tdt-0.6b-v3-int8",
            _BASE + "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
            "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
            487170055,
            "archive",
        ),
        ModelSpec(
            "parakeet-tdt-0.6b-v2-int8",
            _BASE + "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
            "157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad",
            482468385,
            "archive",
        ),
        ModelSpec(
            VAD_ID,
            _BASE + VAD_FILE,
            "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
            643854,
            "file",
        ),
    )
}


class ModelError(Exception):
    pass


class ChecksumError(ModelError):
    pass


class UnsafeArchiveError(ModelError):
    pass


# (url) -> context manager yielding (content_length or None, iterator of byte chunks)
Fetch = Callable[[str], ContextManager[tuple[int | None, Iterator[bytes]]]]


@contextlib.contextmanager
def http_fetch(url: str):
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=20)) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            length = resp.headers.get("content-length")
            yield (int(length) if length else None), resp.iter_bytes(1 << 20)


def extract_asr_files(archive: Path, dest: Path) -> None:
    """Copy the four ASR files out of a tar.bz2 into ``dest``.

    Member paths are only inspected, never used to build output paths: output
    names come from our fixed allowlist. Any absolute or ``..`` member path,
    anywhere in the archive, rejects the whole archive, as do links.
    """
    wanted = set(_ASR_FILES)
    found: set[str] = set()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r|bz2") as tar:
        for member in tar:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise UnsafeArchiveError(f"unsafe path in archive: {member.name!r}")
            if member.issym() or member.islnk():
                raise UnsafeArchiveError(f"link in archive: {member.name!r}")
            name = parts[-1] if parts else ""
            if not member.isfile() or name not in wanted:
                continue
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(dest / name, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            found.add(name)
    missing = wanted - found
    if missing:
        raise ModelError(f"archive is missing {', '.join(sorted(missing))}")


class ModelManager:
    def __init__(
        self,
        config: Config,
        specs: dict[str, ModelSpec] | None = None,
        fetch: Fetch = http_fetch,
    ) -> None:
        self.config = config
        self.specs = specs or MODELS
        self._fetch = fetch
        self._lock = threading.Lock()
        self._done = 0
        self._total = 0
        self._current: str | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- paths and status ---

    @property
    def required(self) -> list[str]:
        """Models to download. The remote engine needs only the VAD (live cutting)."""
        ids = [VAD_ID]
        if self.config.asr_engine != "openai":
            ids.append(self.config.asr_model)
        return ids

    def model_dir(self, model_id: str) -> Path:
        return self.config.models_dir / model_id

    @property
    def vad_path(self) -> Path:
        return self.config.models_dir / VAD_FILE

    def is_installed(self, model_id: str) -> bool:
        if model_id == VAD_ID:
            return self.vad_path.is_file()
        d = self.model_dir(model_id)
        return all((d / f).is_file() and (d / f).stat().st_size > 0 for f in _ASR_FILES)

    @property
    def vad_ready(self) -> bool:
        return self.is_installed(VAD_ID)

    @property
    def ready(self) -> bool:
        """VAD present, plus the ASR model (local) or a configured URL (remote)."""
        if self.config.asr_engine == "openai" and not self.config.remote_asr_url:
            return False
        return all(self.is_installed(m) for m in self.required)

    def status(self) -> dict:
        with self._lock:
            downloading = self._current is not None
            return {
                "model_ready": self.ready,
                "model_download": {"bytes": self._done, "total": self._total}
                if downloading
                else None,
                "model_error": self._error,
            }

    # --- download ---

    def start_background(self) -> None:
        if self._thread is None and not all(self.is_installed(m) for m in self.required):
            self._thread = threading.Thread(target=self._run, name="quill-models", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        delay = 10.0
        while not self._stop.is_set():
            try:
                self.ensure()
                return
            except Exception as exc:  # network, checksum, disk: retry with backoff
                log.warning("model download failed: %s", exc)
                with self._lock:
                    self._error = str(exc)
                    self._current = None
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, 300.0)

    def ensure(self) -> None:
        """Install every missing required model (blocking)."""
        for model_id in self.required:
            if not self.is_installed(model_id):
                self.install(self.specs[model_id])
        with self._lock:
            self._error = None
            self._current = None

    def install(self, spec: ModelSpec) -> None:
        root = self.config.models_dir
        work = root / ".partial"
        work.mkdir(parents=True, exist_ok=True)
        part = work / f"{spec.id}.part"
        digest = hashlib.sha256()
        with self._lock:
            self._current, self._done, self._total = spec.id, 0, spec.size
        try:
            with self._fetch(spec.url) as (length, chunks), open(part, "wb") as out:
                if length:
                    with self._lock:
                        self._total = length
                for chunk in chunks:
                    if self._stop.is_set():
                        raise ModelError("stopped")
                    out.write(chunk)
                    digest.update(chunk)
                    with self._lock:
                        self._done += len(chunk)
            if digest.hexdigest() != spec.sha256:
                raise ChecksumError(
                    f"{spec.id}: SHA-256 mismatch (got {digest.hexdigest()[:12]}..., "
                    f"expected {spec.sha256[:12]}...)"
                )
            if spec.kind == "file":
                os.replace(part, self.vad_path)
            else:
                staging = work / f"{spec.id}.staging"
                shutil.rmtree(staging, ignore_errors=True)
                extract_asr_files(part, staging)
                final = self.model_dir(spec.id)
                shutil.rmtree(final, ignore_errors=True)
                os.replace(staging, final)
        finally:
            part.unlink(missing_ok=True)
            shutil.rmtree(work / f"{spec.id}.staging", ignore_errors=True)
            with self._lock:
                self._current = None
        log.info("installed model %s", spec.id)

