"""Frame encoding and the 16 kHz resampler, tested in Node (no browser)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _results() -> list[dict]:
    proc = subprocess.run([NODE, str(HERE / "js_units.js")], capture_output=True, text=True, timeout=120)
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "[]"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        pytest.fail(f"node crashed:\n{proc.stdout}\n{proc.stderr}")


_CACHE: list[dict] | None = None


def _all() -> list[dict]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _results()
    return _CACHE


def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        names = [r["name"] for r in _all()] if NODE else []
        metafunc.parametrize("case", names)


def test_js_unit(case):
    result = next(r for r in _all() if r["name"] == case)
    assert result["ok"], result.get("error")


def test_js_unit_count():
    assert len(_all()) >= 15
