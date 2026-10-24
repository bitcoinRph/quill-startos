from __future__ import annotations

from quill_server import transcripts as t
from quill_server.db import fts_query

from .conftest import seed_meeting

SEGS = [
    {"seq": 1, "track_id": "a", "speaker": "me", "start_ms": 5_000, "end_ms": 7_000, "text": "Hello there."},
    {"seq": 2, "track_id": "b", "speaker": "them", "start_ms": 3_725_000, "end_ms": 3_727_500, "text": "Late remark."},
]


def test_md_matches_macos_rendering():
    md = t.render_md("Weekly", "parakeet", "parakeet-tdt-0.6b-v3-int8", SEGS)
    assert md == (
        "# Weekly\n\nengine: parakeet (parakeet-tdt-0.6b-v3-int8)\n\n"
        "**[0:05] me:** Hello there.\n\n**[1:02:05] them:** Late remark.\n"
    )
    assert "capture: recovered" in t.render_md("x", "e", "m", SEGS, "recovered")
    assert "capture:" not in t.render_md("x", "e", "m", SEGS, "complete")


def test_compact_txt_srt_vtt():
    assert t.render_compact(SEGS) == "[0:00:05] me: Hello there.\n[1:02:05] them: Late remark.\n"
    assert t.render_txt(SEGS) == "me: Hello there.\n\nthem: Late remark.\n"
    srt = t.render_srt(SEGS)
    assert srt.startswith("1\n00:00:05,000 --> 00:00:07,000\nme: Hello there.\n\n2\n01:02:05,000 --> 01:02:07,500")
    vtt = t.render_vtt(SEGS)
    assert vtt.startswith("WEBVTT\n\n00:00:05.000 --> 00:00:07.000\nme: Hello there.")
    assert "Hello there." in t.render_srt(SEGS, speakers=False) and "me:" not in t.render_srt(SEGS, speakers=False)


def test_canonical_json_shape():
    doc = t.canonical_json(
        [{**SEGS[0], "words": [{"w": "Hello", "s": 5000, "e": 5400}]}, SEGS[1]], "parakeet", "m", "2026-01-01T00:00:00Z"
    )
    assert list(doc) == ["engine", "model", "created_at", "segments"]
    assert doc["segments"][0] == {
        "speaker": "me", "start_ms": 5000, "end_ms": 7000, "text": "Hello there.", "track": "a",
        "words": [{"w": "Hello", "s": 5000, "e": 5400}],
    }  # fmt: skip
    assert "words" not in doc["segments"][1]


def test_filters():
    assert [s["seq"] for s in t.filter_segments(SEGS, since_seq=1)] == [2]
    assert [s["seq"] for s in t.filter_segments(SEGS, start_ms=6_000, end_ms=100_000)] == [1]
    assert [s["seq"] for s in t.filter_segments(SEGS, speaker="THEM")] == [2]


def test_fts_query_quotes_everything():
    assert fts_query('budget AND "q3" -foo*') == '"budget" "AND" "q3" "-foo"*'  # AND is data, not an operator
    assert fts_query("") is None and fts_query("!!! ???") is None
    assert fts_query("hello wor") == '"hello" "wor"*'


def test_search_never_500s_on_fts_syntax(client):
    mid = seed_meeting(client, [("me", 0, 1000, "we agreed the budget for Q3"), ("them", 2000, 3000, "NEAR(x) is fine")])
    for q in ['budget"', "AND", "NEAR(", "col:budget", "*", "budget OR", "'", '"unterminated', "a" * 500, "-"]:
        assert client.get("/api/v1/search", params={"q": q}).status_code == 200, q
    hits = client.get("/api/v1/search", params={"q": "budg"}).json()["hits"]
    assert len(hits) == 1
    assert set(hits[0]) == {"meeting_id", "title", "seq", "start_ms", "speaker", "snippet"}
    assert hits[0]["meeting_id"] == mid and "**budget**" in hits[0]["snippet"]


def test_transcript_endpoint_formats(client):
    mid = seed_meeting(client, [("me", 0, 1500, "First line."), ("them", 61_000, 62_000, "Second line.")], "Standup")
    base = f"/api/v1/meetings/{mid}/transcript"
    j = client.get(base).json()
    assert j["engine"] == "fake" and [s["seq"] for s in j["segments"]] == [1, 2] and j["next_seq"] == 2
    assert client.get(base, params={"since_seq": 1}).json()["segments"][0]["text"] == "Second line."
    md = client.get(base, params={"format": "md"})
    assert md.text.startswith("# Standup\n\nengine: fake (fake-1)\n\n**[0:00] me:** First line.")
    assert client.get(base, params={"format": "compact"}).text == "[0:00:00] me: First line.\n[0:01:01] them: Second line.\n"
    assert client.get(base, params={"format": "srt"}).headers["content-type"].startswith("application/x-subrip")
    assert client.get(base, params={"format": "vtt"}).text.startswith("WEBVTT")
    assert client.get(base, params={"format": "txt", "speaker": "them"}).text == "them: Second line.\n"
    assert client.get(base, params={"format": "bogus"}).status_code == 422


def test_rename_speakers_updates_transcripts(client):
    mid = seed_meeting(client, [("Speaker 1", 0, 1000, "hi")])
    r = client.patch(f"/api/v1/meetings/{mid}", json={"speakers": {"Speaker 1": "Alice"}}).json()
    assert r["speakers"] == ["Alice"]
    s = client.app.state.quill
    row = s.meetings.require(mid)
    assert "**[0:00] Alice:** hi" in (s.meetings.session_path(row) / "transcript.md").read_text()
