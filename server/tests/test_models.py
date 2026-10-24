from __future__ import annotations

import contextlib
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from quill_server.config import Config
from quill_server.models import (
    VAD_ID,
    ChecksumError,
    ModelManager,
    ModelSpec,
    UnsafeArchiveError,
    extract_asr_files,
)

FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")


def make_archive(members: dict[str, bytes], dest: Path) -> bytes:
    with tarfile.open(dest, "w:bz2") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return dest.read_bytes()


def fetcher(blobs: dict[str, bytes]):
    @contextlib.contextmanager
    def fetch(url: str):
        data = blobs[url]
        yield len(data), iter([data[i : i + 7] for i in range(0, len(data), 7)])

    return fetch


def spec(model_id: str, blob: bytes, kind: str, sha: str | None = None) -> ModelSpec:
    return ModelSpec(model_id, f"https://x/{model_id}", sha or hashlib.sha256(blob).hexdigest(), len(blob), kind)


def manager(tmp_path: Path, specs, blobs, **cfg) -> ModelManager:
    config = Config(models_dir=tmp_path / "models", data_dir=tmp_path / "data", **cfg)
    config.models_dir.mkdir(parents=True)
    return ModelManager(config, {s.id: s for s in specs}, fetcher(blobs))


GOOD = {f"pkg/{n}": n.encode() * 3 for n in FILES} | {"pkg/test_wavs/en.wav": b"wav"}


def test_install_extracts_only_four_files_and_vad(tmp_path):
    archive = make_archive(GOOD, tmp_path / "a.tar.bz2")
    vad = b"onnx-bytes"
    specs = [spec("asr", archive, "archive"), spec(VAD_ID, vad, "file")]
    m = manager(tmp_path, specs, {"https://x/asr": archive, f"https://x/{VAD_ID}": vad}, asr_model="asr")
    assert not m.ready
    m.ensure()
    assert m.ready
    assert sorted(p.name for p in m.model_dir("asr").iterdir()) == sorted(FILES)
    assert m.vad_path.read_bytes() == vad
    assert not list((tmp_path / "models" / ".partial").iterdir())  # temp files cleaned


def test_checksum_mismatch_never_installs(tmp_path):
    archive = make_archive(GOOD, tmp_path / "a.tar.bz2")
    bad = spec("asr", archive, "archive", sha="0" * 64)
    m = manager(tmp_path, [bad], {"https://x/asr": archive}, asr_model="asr")
    with pytest.raises(ChecksumError):
        m.install(bad)
    assert not m.is_installed("asr") and not m.model_dir("asr").exists()
    assert not list((tmp_path / "models" / ".partial").iterdir())


@pytest.mark.parametrize("evil", ["../evil.txt", "/etc/evil", "pkg/../../evil"])
def test_traversal_rejected(tmp_path, evil):
    archive_path = tmp_path / "evil.tar.bz2"
    make_archive({**GOOD, evil: b"pwn"}, archive_path)
    with pytest.raises(UnsafeArchiveError):
        extract_asr_files(archive_path, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists() and not Path("/etc/evil").exists()


def test_symlink_member_rejected(tmp_path):
    p = tmp_path / "l.tar.bz2"
    with tarfile.open(p, "w:bz2") as tar:
        info = tarfile.TarInfo("pkg/tokens.txt")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(UnsafeArchiveError):
        extract_asr_files(p, tmp_path / "out")


def test_incomplete_archive_rejected(tmp_path):
    p = tmp_path / "short.tar.bz2"
    make_archive({"pkg/tokens.txt": b"t"}, p)
    with pytest.raises(Exception, match="missing"):
        extract_asr_files(p, tmp_path / "out")


def test_remote_engine_needs_only_vad_and_a_url(tmp_path):
    vad = b"v"
    s = spec(VAD_ID, vad, "file")
    m = manager(tmp_path, [s], {f"https://x/{VAD_ID}": vad}, asr_engine="openai")
    assert m.required == [VAD_ID]
    m.ensure()
    assert not m.ready  # VAD present but no remote URL configured
    m.config = Config(models_dir=m.config.models_dir, data_dir=m.config.data_dir, asr_engine="openai",
                      remote_asr_url="http://asr:8081")  # fmt: skip
    assert m.ready


def test_progress_reported_during_download(tmp_path):
    seen = []
    blob = b"x" * 100
    s = spec(VAD_ID, blob, "file")

    @contextlib.contextmanager
    def fetch(url):
        def chunks():
            for i in range(0, 100, 25):
                yield blob[i : i + 25]
                seen.append(m.status()["model_download"])

        yield 100, chunks()

    m = manager(tmp_path, [s], {})
    m._fetch = fetch
    m.install(s)
    assert seen[0] == {"bytes": 25, "total": 100} and seen[-1] == {"bytes": 100, "total": 100}
    assert m.status()["model_download"] is None


def test_health_reports_download_error(tmp_path, make_client):
    client = make_client()
    state = client.app.state.quill
    state.models._error = "boom"
    assert client.get("/health").json()["model_error"] == "boom"
