"""Fixtures for the bot tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import uvicorn

from botkit import TOKEN, Double, Recorder, build_double_app, chromium_path


def _playwright_chromium() -> bool:
    """True when Playwright's own Chromium build is installed (CI installs it)."""
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    if chromium_path() is None and not _playwright_chromium():
        skip = pytest.mark.skip(reason="no Chromium available")
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "browser: needs headless Chromium")


@pytest.fixture
async def quill_double():
    rec = Recorder()
    app = build_double_app(rec, TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", ws="auto"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield Double(port=port, token=TOKEN, rec=rec, app=app)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


@pytest.fixture
async def playwright_instance():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        yield pw


@pytest.fixture
async def browser(playwright_instance):
    kwargs: dict[str, Any] = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream"]}
    if chromium_path():
        kwargs["executable_path"] = chromium_path()
    b = await playwright_instance.chromium.launch(**kwargs)
    try:
        yield b
    finally:
        await b.close()
