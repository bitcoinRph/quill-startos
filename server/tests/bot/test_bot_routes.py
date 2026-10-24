"""The bot page is served only with the internal token."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quill_server.bot.routes import ASSETS, PAGE_DIR, router, token_ok

TOKEN = "s3cr3t-internal-token"


def client(token: str | None = TOKEN, env: str | None = None, monkeypatch=None) -> TestClient:
    app = FastAPI()
    if token is not None:
        app.state.config = SimpleNamespace(internal_token=token)
    elif env is not None and monkeypatch is not None:
        monkeypatch.setenv("QUILL_INTERNAL_TOKEN", env)
    app.include_router(router)
    return TestClient(app)


def test_page_requires_token():
    c = client()
    assert c.get("/bot/page").status_code == 403
    assert c.get("/bot/page?internal=").status_code == 403
    assert c.get("/bot/page?internal=wrong").status_code == 403
    assert c.get("/bot/page?internal=" + TOKEN[:-1]).status_code == 403
    assert c.get("/bot/page?internal=" + TOKEN + "x").status_code == 403
    r = c.get("/bot/page?internal=" + TOKEN)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Quill notetaker bot" in r.text


def test_error_shape_matches_the_api():
    r = client().get("/bot/page")
    assert r.json() == {"error": {"code": "forbidden", "message": "internal token required"}}


@pytest.mark.parametrize("name", sorted(ASSETS))
def test_assets_need_the_token_and_are_served_verbatim(name):
    c = client()
    assert c.get(f"/bot/page/{name}").status_code == 403
    r = c.get(f"/bot/page/{name}?internal={TOKEN}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.content == (PAGE_DIR / name).read_bytes()


def test_unknown_assets_and_traversal():
    c = client()
    assert c.get(f"/bot/page/nothing.js?internal={TOKEN}").status_code == 404
    assert c.get("/bot/page/nothing.js").status_code == 403  # does not reveal which names exist
    for path in ("../routes.py", "..%2Froutes.py", "%2e%2e/routes.py", "bot.html"):
        r = c.get(f"/bot/page/{path}?internal={TOKEN}")
        assert r.status_code in (404, 405), path
        assert "APIRouter" not in r.text


def test_no_referrer_and_no_cache_headers():
    r = client().get("/bot/page?internal=" + TOKEN)
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert 'name="referrer" content="no-referrer"' in r.text


def test_empty_server_token_never_matches():
    c = client(token="")
    assert c.get("/bot/page?internal=").status_code == 403
    assert c.get("/bot/page?internal=x").status_code == 403


def test_env_fallback_without_a_config_object(monkeypatch):
    c = client(token=None, env="from-env", monkeypatch=monkeypatch)
    assert c.get("/bot/page?internal=from-env").status_code == 200
    assert c.get("/bot/page?internal=nope").status_code == 403


def test_no_token_anywhere_refuses(monkeypatch):
    monkeypatch.delenv("QUILL_INTERNAL_TOKEN", raising=False)
    assert client(token=None).get("/bot/page?internal=anything").status_code == 403


def test_token_compare_is_constant_time_function():
    import hmac
    import inspect

    from quill_server.bot import routes

    assert "compare_digest" in inspect.getsource(routes.token_ok)
    assert hmac is not None
    assert token_ok("a", "a") and not token_ok("a", "b") and not token_ok(None, "a") and not token_ok("", "")
    assert token_ok("é", "é") and not token_ok("é", "e")  # non-ASCII must not raise


def test_only_get():
    c = client()
    assert c.post("/bot/page?internal=" + TOKEN).status_code == 405


def test_every_page_script_exists_and_the_html_loads_them():
    html = (PAGE_DIR / "bot.html").read_text()
    names = re.findall(r"['\"]([a-z0-9-]+\.js)['\"]", html)
    assert names
    for name in names:
        assert name in ASSETS
        assert (PAGE_DIR / name).is_file()
        assert client().get(f"/bot/page/{name}?internal={TOKEN}").status_code == 200
    assert (PAGE_DIR / "pcm-worklet.js").is_file()
