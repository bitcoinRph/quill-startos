"""Transcript formats. Pure functions over segment dicts.

A segment is ``{seq, track_id, speaker, start_ms, end_ms, text, words}``.
``md`` reproduces the macOS ``transcript.md`` byte for byte (same header, same
``**[m:ss] Speaker:** text`` lines); ``compact`` is the token-cheap form used
by MCP.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .util import clock

FORMATS = ("json", "md", "txt", "srt", "vtt", "compact")
CONTENT_TYPES = {
    "json": "application/json",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "compact": "text/plain; charset=utf-8",
}

Segment = dict[str, Any]


def filter_segments(
    segments: Iterable[Segment],
    *,
    since_seq: int | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    speaker: str | None = None,
) -> list[Segment]:
    """``start_ms``/``end_ms`` select segments overlapping the window."""
    out = []
    want = speaker.casefold() if speaker else None
    for s in segments:
        if since_seq is not None and s["seq"] <= since_seq:
            continue
        if start_ms is not None and s["end_ms"] <= start_ms:
            continue
        if end_ms is not None and s["start_ms"] >= end_ms:
            continue
        if want is not None and s["speaker"].casefold() != want:
            continue
        out.append(s)
    return out


def canonical_json(
    segments: Sequence[Segment], engine: str, model: str, created_at: str, *, with_seq: bool = False
) -> dict[str, Any]:
    """The ``transcript.json`` document (macOS shape plus ``track`` and ``words``)."""
    items = []
    for s in segments:
        item: dict[str, Any] = {
            "speaker": s["speaker"],
            "start_ms": s["start_ms"],
            "end_ms": s["end_ms"],
            "text": s["text"],
            "track": s.get("track_id") or "",
        }
        if s.get("words"):
            item["words"] = s["words"]
        if with_seq:
            item["seq"] = s["seq"]
        items.append(item)
    return {"engine": engine, "model": model, "created_at": created_at, "segments": items}


def render_md(
    title: str, engine: str, model: str, segments: Sequence[Segment], capture_status: str | None = None
) -> str:
    lines = [f"# {title}", "", f"engine: {engine} ({model})"]
    if capture_status and capture_status != "complete":
        lines.append(f"capture: {capture_status}")
    lines.append("")
    for s in segments:
        lines.append(f"**[{clock(s['start_ms'])}] {s['speaker']}:** {s['text']}")
        lines.append("")
    return "\n".join(lines)


def render_txt(segments: Sequence[Segment]) -> str:
    return "".join(f"{s['speaker']}: {s['text']}\n\n" for s in segments).rstrip("\n") + "\n"


def compact_line(s: Segment) -> str:
    return f"[{clock(s['start_ms'], always_hours=True)}] {s['speaker']}: {s['text']}"


def render_compact(segments: Sequence[Segment]) -> str:
    return "".join(compact_line(s) + "\n" for s in segments)


def _stamp(ms: int, sep: str) -> str:
    ms = max(0, ms)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


def _cue_text(s: Segment, speakers: bool) -> str:
    return f"{s['speaker']}: {s['text']}" if speakers else s["text"]


def render_srt(segments: Sequence[Segment], speakers: bool = True) -> str:
    blocks = []
    for i, s in enumerate(segments, start=1):
        blocks.append(
            f"{i}\n{_stamp(s['start_ms'], ',')} --> {_stamp(max(s['end_ms'], s['start_ms'] + 1), ',')}\n"
            f"{_cue_text(s, speakers)}\n"
        )
    return "\n".join(blocks)


def render_vtt(segments: Sequence[Segment], speakers: bool = True) -> str:
    blocks = ["WEBVTT\n"]
    for s in segments:
        blocks.append(
            f"{_stamp(s['start_ms'], '.')} --> {_stamp(max(s['end_ms'], s['start_ms'] + 1), '.')}\n"
            f"{_cue_text(s, speakers)}\n"
        )
    return "\n".join(blocks)


def render_text(
    fmt: str,
    segments: Sequence[Segment],
    *,
    title: str = "",
    engine: str = "",
    model: str = "",
    capture_status: str | None = None,
) -> str:
    if fmt == "md":
        return render_md(title, engine, model, segments, capture_status)
    if fmt == "txt":
        return render_txt(segments)
    if fmt == "srt":
        return render_srt(segments)
    if fmt == "vtt":
        return render_vtt(segments)
    if fmt == "compact":
        return render_compact(segments)
    raise ValueError(f"unknown transcript format: {fmt}")
