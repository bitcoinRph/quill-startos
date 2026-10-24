from __future__ import annotations

import httpx
import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError

from quill_server.mcp_server import QuillTools

from .conftest import API_KEY, install_fake_bot, seed_meeting, state_of

LINES = [("Ann", i * 1000, i * 1000 + 800, f"line number {i} " + "word " * 20) for i in range(60)]


@pytest.fixture
def tools(client):
    return client, QuillTools(state_of(client))


def test_list_meetings_lines_and_cursor(tools):
    client, t = tools
    ids = [seed_meeting(client, [("Ann", 0, 1000, "hello")], title=f"M{i}") for i in range(3)]
    out = t.list_meetings(limit=2)
    rows = out.splitlines()
    assert len(rows) == 3 and rows[2].startswith("cursor=")
    first = rows[0].split(" | ")
    assert first[0] in ids and len(first) == 5 and first[4] == "ready"
    rest = t.list_meetings(limit=2, cursor=rows[2].removeprefix("cursor=")).splitlines()
    assert len(rest) == 1 and not rest[0].startswith("cursor=")
    assert t.list_meetings(query="nomatchxyz") == "no meetings"
    with pytest.raises(ToolError, match="bad_cursor"):
        t.list_meetings(cursor="zzz")


def test_get_meeting_includes_notes_and_bot_state(tools):
    client, t = tools
    bot = install_fake_bot(client)
    mid = seed_meeting(client, [("Ann", 0, 1000, "hello")], "Standup")
    state_of(client).db.execute("UPDATE meetings SET source='bot' WHERE id=?", (mid,))
    bot.bots[mid] = {"meeting_id": mid, "state": "failed", "error": "lobby timeout"}
    t.save_notes(mid, "## Summary\nAll good")
    text = t.get_meeting(mid)
    assert "title: Standup" in text and "speakers: Ann" in text
    assert "bot: failed (lobby timeout)" in text and "All good" in text and "## AI notes (agent)" in text
    with pytest.raises(ToolError, match="not_found"):
        t.get_meeting("not_aaaaaaaaaaaaaa")


def test_get_transcript_pages_by_offset(tools):
    client, t = tools
    mid = seed_meeting(client, LINES)
    pages, offset, guard = [], 0, 0
    while True:
        page = t.get_transcript(mid, offset=offset, max_chars=1500)
        guard += 1
        assert guard < 50
        if "-- next_offset=" not in page:
            pages.append(page)
            break
        body, footer = page.rsplit("-- next_offset=", 1)
        pages.append(body)
        offset = int(footer.split(" ")[0])
        assert body.endswith("\n") and len(body) <= 1500
    joined = "".join(pages)
    assert joined.count("\n") == 60 and joined.startswith("[0:00:00] Ann: line number 0")
    assert len(pages) > 3
    window = t.get_transcript(mid, start_ms=10_000, end_ms=12_000)
    assert window.count("\n") == 2 and "line number 10 " in window and "line number 11 " in window
    assert t.get_transcript(mid, speaker="nobody") == "(no transcript lines)"


def test_search_meetings(tools):
    client, t = tools
    mid = seed_meeting(client, [("Ann", 3_725_000, 3_726_000, "we should revisit the pricing model")])
    out = t.search_meetings("pricing")
    assert out.startswith(f"{mid} | Seeded | [1:02:05] Ann: ") and "**pricing**" in out
    assert t.search_meetings("zzzz") == "no matches"
    assert t.search_meetings('"""') == "no matches"


def test_live_transcript_deltas(tools):
    client, t = tools
    mid = seed_meeting(client, [("Ann", 0, 500, "one"), ("Bob", 600, 900, "two"), ("Ann", 1000, 1500, "three")])
    first = t.get_live_transcript(mid, since_seq=0, max_chars=30)
    assert first.splitlines()[-1] == "-- next_seq=1 status=ready"
    nxt = t.get_live_transcript(mid, since_seq=1)
    assert "two" in nxt and "three" in nxt and nxt.splitlines()[-1] == "-- next_seq=3 status=ready"
    assert t.get_live_transcript(mid, since_seq=3) == "-- next_seq=3 status=ready"


async def test_generate_notes_without_llm_tells_agent_to_write_them(tools):
    client, t = tools
    mid = seed_meeting(client, [("Ann", 0, 500, "one")])
    with pytest.raises(ToolError, match="no_llm.*save_notes"):
        await t.generate_notes(mid)
    assert "General" in t.list_templates() and "Brainstorm" in t.list_templates()
    assert "engine=fake model=fake-1" in t.get_status()


async def test_join_and_leave_via_bot(make_client):
    client = make_client(jitsi_domains=("meet.example.com",))
    bot = install_fake_bot(client)
    t = QuillTools(state_of(client))
    out = await t.join_meeting("https://meet.example.com/room", title="Sync")
    mid = out.split("meeting_id=")[1].split(" ")[0]
    assert out.endswith("bot=starting") and bot.joins == [("https://meet.example.com/room", "Sync", None, None)]
    assert (await t.leave_meeting(mid)).startswith("status=")
    with pytest.raises(ToolError, match="invalid_bot_request"):
        await t.join_meeting("https://evil.example.org/x")


async def test_bot_disabled_without_manager(client):
    t = QuillTools(state_of(client))
    with pytest.raises(ToolError, match="bot_unavailable"):
        await t.join_meeting("https://meet.example.com/room")


def test_mcp_requires_bearer(client):
    anon = {"Authorization": ""}
    r = client.post("/mcp", headers=anon, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    r = client.post("/mcp", headers={"Authorization": "Bearer wrong"}, json={})
    assert r.status_code == 401


async def test_mcp_end_to_end_over_http(live_server):
    base, state = live_server
    # seed through the REST API so the data lives in the running server
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {API_KEY}"}) as rest:
        mid = rest.post("/api/v1/meetings", json={"title": "E2E"}).json()["id"]
        state.meetings.replace_segments(mid, [{"track_id": "", "speaker": "Ann", "start_ms": 5000, "end_ms": 6000, "text": "budget approved"}])
        state.db.execute("UPDATE meetings SET status='ready', engine='fake', model='m' WHERE id=?", (mid,))

    bad = httpx2.AsyncClient(headers={"Authorization": "Bearer nope"})
    async with bad:
        with pytest.raises(Exception):
            async with Client(streamable_http_client(f"{base}/mcp", http_client=bad)) as c:
                await c.list_tools()

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {API_KEY}"})
    async with http:
        async with Client(streamable_http_client(f"{base}/mcp", http_client=http)) as client:
            assert client.server_info.name == "quill"
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == {
                "list_meetings", "get_meeting", "get_transcript", "search_meetings", "join_meeting", "leave_meeting",
                "get_live_transcript", "save_notes", "generate_notes", "list_templates", "get_status",
            }  # fmt: skip
            assert "next_offset" in tools["get_transcript"].description
            assert "next_seq" in tools["get_live_transcript"].description
            listing = await client.call_tool("list_meetings", {"query": "budget"})
            assert mid in listing.content[0].text and not listing.is_error
            tr = await client.call_tool("get_transcript", {"meeting_id": mid})
            assert tr.content[0].text == "[0:00:05] Ann: budget approved\n"
            saved = await client.call_tool("save_notes", {"meeting_id": mid, "markdown": "## Summary\nok"})
            assert saved.content[0].text == "ok"
            err = await client.call_tool("get_meeting", {"meeting_id": "not_aaaaaaaaaaaaaa"})
            assert err.is_error and "not_found" in err.content[0].text
            prompt = await client.get_prompt("meeting_notes", {"meeting_id": mid, "template": "standup"})
            text = prompt.messages[0].content.text
            assert "budget approved" in text and "save_notes" in text and "Blockers" in text
    assert state.meetings.require(mid)["ai_notes_source"] == "agent"
