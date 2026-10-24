from __future__ import annotations

from .conftest import ADMIN, API_KEY, seed_meeting, state_of


def test_health_shape_and_no_auth(client):
    r = client.get("/health", headers={"Authorization": ""})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "status", "model_ready", "engine", "model", "queue_depth", "live_meetings", "model_download", "model_error",
    }  # fmt: skip
    assert body["status"] == "ok" and body["engine"] == "fake"
    assert body["model_download"] is None and body["model_error"] is None


def test_auth_required_and_error_shape(client):
    r = client.get("/api/v1/meetings", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "missing or invalid credentials"}}


def test_login_cookie_flow(client):
    c = client
    anon = {"Authorization": ""}
    assert c.post("/api/v1/auth/login", json={"password": "bad"}, headers=anon).status_code == 401
    ok = c.post("/api/v1/auth/login", json={"password": ADMIN}, headers=anon)
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "quill_session=" in cookie
    assert "Max-Age=2592000" in cookie
    assert c.get("/api/v1/meetings", headers=anon).status_code == 200  # cookie jar
    me = c.get("/api/v1/auth/me", headers=anon).json()
    assert me == {"signin_enabled": True, "authenticated": True}
    c.post("/api/v1/auth/logout", headers=anon)
    assert c.get("/api/v1/meetings", headers=anon).status_code == 401


def test_login_rate_limit(client):
    anon = {"Authorization": ""}
    codes = [client.post("/api/v1/auth/login", json={"password": "x"}, headers=anon).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    # even the right password is refused while blocked
    assert client.post("/api/v1/auth/login", json={"password": ADMIN}, headers=anon).status_code == 429


def test_login_disabled_without_password(make_client):
    c = make_client(admin_password="")
    r = c.post("/api/v1/auth/login", json={"password": ""}, headers={"Authorization": ""})
    assert r.status_code == 503 and r.json()["error"]["code"] == "signin_disabled"


def test_api_keys_cookie_only_and_secret_shown_once(client):
    anon = {"Authorization": ""}
    assert client.get("/api/v1/keys").status_code == 401  # bearer is not enough
    client.post("/api/v1/auth/login", json={"password": ADMIN}, headers=anon)
    created = client.post("/api/v1/keys", json={"name": "laptop"}, headers=anon).json()
    secret = created["key"]
    assert secret.startswith("qk_") and len(secret) == 35
    listed = client.get("/api/v1/keys", headers=anon).json()["keys"]
    assert listed[0]["id"] == created["id"] and "key" not in listed[0] and "sha256" not in listed[0]
    row = state_of(client).db.one("SELECT sha256 FROM api_keys")
    assert row["sha256"] != secret and len(row["sha256"]) == 64
    with_key = {"Authorization": f"Bearer {secret}"}
    client.cookies.clear()
    assert client.get("/api/v1/meetings", headers=with_key).status_code == 200
    client.post("/api/v1/auth/login", json={"password": ADMIN}, headers=anon)
    assert client.delete(f"/api/v1/keys/{created['id']}", headers=anon).status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/meetings", headers=with_key).status_code == 401
    assert client.get("/api/v1/meetings", headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200


def test_meeting_crud_and_ids(client):
    m = client.post("/api/v1/meetings", json={"title": "Weekly", "attendees": ["ann@x.io", "Bob"]}).json()
    assert m["id"].startswith("not_") and len(m["id"]) == 18 and m["status"] == "scheduled"
    assert m["attendees"] == [{"name": "", "email": "ann@x.io"}, {"name": "Bob", "email": ""}]
    upper = m["id"].replace("not_", "NOT_")
    assert client.get(f"/api/v1/meetings/{upper}").json()["id"] == m["id"]
    patched = client.patch(f"/api/v1/meetings/{m['id']}", json={"title": "Renamed", "user_notes_md": "- a"}).json()
    assert patched["title"] == "Renamed" and patched["has_user_notes"]
    got = client.get(f"/api/v1/meetings/{m['id']}?include=notes,tracks,transcript").json()
    assert got["user_notes_md"] == "- a" and got["tracks"] == [] and got["transcript"] == []
    assert client.get("/api/v1/meetings/not_doesnotexist01").status_code == 404
    assert client.delete(f"/api/v1/meetings/{m['id']}").status_code == 204
    assert client.get(f"/api/v1/meetings/{m['id']}").status_code == 404


def test_list_filters_and_pagination(client):
    ids = [seed_meeting(client, [("me", 0, 1000, f"topic {i} budget")], title=f"Meeting {i}") for i in range(5)]
    first = client.get("/api/v1/meetings?limit=2").json()
    assert len(first["meetings"]) == 2 and first["has_more"] and first["cursor"]
    seen = [m["id"] for m in first["meetings"]]
    cursor = first["cursor"]
    while cursor:
        page = client.get(f"/api/v1/meetings?limit=2&cursor={cursor}").json()
        seen += [m["id"] for m in page["meetings"]]
        cursor = page["cursor"]
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 5
    assert len(client.get("/api/v1/meetings?q=budget").json()["meetings"]) == 5
    assert len(client.get("/api/v1/meetings?q=Meeting 3").json()["meetings"]) == 1
    assert client.get("/api/v1/meetings?status=live").json()["meetings"] == []
    assert client.get("/api/v1/meetings?limit=0").status_code == 422
    assert client.get("/api/v1/meetings?cursor=garbage").status_code == 400
    assert client.get("/api/v1/meetings?created_after=2999-01-01").json()["meetings"] == []
    assert client.get("/api/v1/meetings?created_after=not-a-date").status_code == 400


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_folders_tree_and_filter(client):
    a = seed_meeting(client, [("me", 0, 1000, "x")], "A")
    b = seed_meeting(client, [("me", 0, 1000, "y")], "B")
    parent = client.post("/api/v1/folders", json={"name": "Work"}).json()
    child = client.post("/api/v1/folders", json={"name": "Sales", "parent_folder_id": parent["id"]}).json()
    assert parent["id"].startswith("fol_") and child["parent_folder_id"] == parent["id"]
    assert client.post(f"/api/v1/folders/{child['id']}/meetings/{a}").status_code == 204
    in_parent = client.get(f"/api/v1/meetings?folder_id={parent['id']}").json()["meetings"]
    assert [m["id"] for m in in_parent] == [a]  # parent includes child folders
    assert client.get(f"/api/v1/meetings?folder_id={child['id'].replace('fol_', 'FOL_')}").json()["meetings"][0]["id"] == a
    assert b not in [m["id"] for m in in_parent]
    assert client.delete(f"/api/v1/folders/{parent['id']}").status_code == 204
    kept = client.get("/api/v1/folders").json()["folders"]
    assert kept[0]["id"] == child["id"] and kept[0]["parent_folder_id"] is None
