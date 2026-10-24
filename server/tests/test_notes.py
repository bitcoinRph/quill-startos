from __future__ import annotations

import json

import httpx

from quill_server.notes import BUILTIN_TEMPLATES, CHUNK_CHARS, chunk_lines

from .conftest import seed_meeting, state_of, wait_for

LLM = dict(llm_base_url="http://llm.test/v1", llm_api_key="sk-test", llm_model="tiny")


def llm_client(calls, reply="## Summary\nMocked."):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((str(request.url), request.headers["authorization"], body))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": reply}}]})

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_builtin_templates_present(client):
    names = [t["name"] for t in client.get("/api/v1/templates").json()["templates"]]
    assert names == ["General", "1:1", "Sales / Customer call", "Standup", "Interview", "Brainstorm"]
    assert len(BUILTIN_TEMPLATES) == 6


def test_template_crud_and_builtin_readonly(client):
    made = client.post("/api/v1/templates", json={"name": "Retro", "prompt": "## Went well"}).json()
    assert not made["builtin"]
    assert client.put(f"/api/v1/templates/{made['id']}", json={"name": "Retro 2", "prompt": "p"}).json()["name"] == "Retro 2"
    assert client.put("/api/v1/templates/general", json={"name": "x", "prompt": "y"}).status_code == 403
    assert client.delete("/api/v1/templates/general").status_code == 403
    assert client.delete(f"/api/v1/templates/{made['id']}").status_code == 204


def test_generate_without_llm_is_409(client):
    mid = seed_meeting(client, [("me", 0, 1000, "hi")])
    r = client.post(f"/api/v1/meetings/{mid}/notes/generate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_llm"
    assert "save_notes" in r.json()["error"]["message"]


def test_generate_prompt_uses_user_notes_as_skeleton_and_never_invents(make_client):
    calls = []
    client = make_client(notes_client=llm_client(calls), **LLM)
    mid = seed_meeting(client, [("Ann", 0, 1000, "We ship Friday."), ("Bob", 2000, 3000, "I will write the docs.")], "Launch sync")
    client.patch(f"/api/v1/meetings/{mid}", json={"user_notes_md": "- ship date\n- docs owner", "attendees": ["Ann", "Bob"]})
    r = client.post(f"/api/v1/meetings/{mid}/notes/generate", json={"template_id": "standup"})
    assert r.status_code == 200 and r.json()["markdown"] == "## Summary\nMocked."
    (url, auth, body), = calls
    assert url == "http://llm.test/v1/chat/completions" and auth == "Bearer sk-test" and body["model"] == "tiny"
    system, user = (m["content"] for m in body["messages"])
    assert "skeleton" in system and "Never invent facts" in system
    assert "- ship date\n- docs owner" in user and "[0:00:00] Ann: We ship Friday." in user
    assert "Blockers" in user and "Ann, Bob" in user  # standup template + attendees
    got = client.get(f"/api/v1/meetings/{mid}?include=notes").json()
    assert got["ai_notes_md"] == "## Summary\nMocked." and got["ai_notes_source"] == "llm"
    assert (state_of(client).meetings.session_path(state_of(client).meetings.require(mid)) / "notes.md").read_text() == "## Summary\nMocked."


def test_long_transcripts_are_map_reduced(make_client):
    calls = []
    client = make_client(notes_client=llm_client(calls, "digest"), **LLM)
    line = "x" * 190
    mid = seed_meeting(client, [("A", i * 1000, i * 1000 + 500, line) for i in range(160)])  # ~34k chars
    assert client.post(f"/api/v1/meetings/{mid}/notes/generate", json={}).status_code == 200
    prompts = [c[2]["messages"][1]["content"] for c in calls]
    assert len(calls) >= 4  # 3 chunk digests + final
    assert all(len(p) < CHUNK_CHARS + 1500 for p in prompts[:-1])
    assert "Digests of consecutive parts" in prompts[-1] and "### Part 3" in prompts[-1]


def test_short_transcripts_are_one_call(make_client):
    calls = []
    client = make_client(notes_client=llm_client(calls), **LLM)
    mid = seed_meeting(client, [("A", 0, 500, "short")])
    client.post(f"/api/v1/meetings/{mid}/notes/generate", json={})
    assert len(calls) == 1


def test_chunk_lines_keeps_lines_whole():
    lines = ["a" * 5000] * 5
    chunks = chunk_lines(lines)
    assert [c.count("a") for c in chunks] == [10000, 10000, 5000] and all(len(c) <= CHUNK_CHARS for c in chunks)


def test_llm_failure_is_502(make_client):
    client = make_client(notes_client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))), **LLM)
    mid = seed_meeting(client, [("A", 0, 500, "x")])
    r = client.post(f"/api/v1/meetings/{mid}/notes/generate", json={})
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_failed"


def test_auto_notes_after_ready(make_client):
    calls = []
    client = make_client(notes_client=llm_client(calls), auto_notes=True, **LLM)
    mid = seed_meeting(client, [("A", 0, 500, "auto please")])
    assert wait_for(lambda: client.get(f"/api/v1/meetings/{mid}").json()["has_ai_notes"])
    assert len(calls) == 1


def test_put_notes_sources(client):
    mid = seed_meeting(client, [("A", 0, 500, "x")])
    r = client.put(f"/api/v1/meetings/{mid}/notes", json={"markdown": "mine", "source": "agent"}).json()
    assert r["ai_notes_md"] == "mine" and r["ai_notes_source"] == "agent"
    assert client.put(f"/api/v1/meetings/{mid}/notes", json={"markdown": "x", "source": "bogus"}).status_code == 422
