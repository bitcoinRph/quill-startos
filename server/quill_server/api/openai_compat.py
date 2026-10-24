"""OpenAI-compatible ``POST /v1/audio/transcriptions`` (synchronous)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .. import transcripts
from ..asr import EngineError
from ..audio import AudioError
from ..errors import ApiError, bad_request
from ..offline import transcribe_file
from ..state import AppState
from .deps import get_state, require_auth, save_upload

router = APIRouter(tags=["openai"], dependencies=[Depends(require_auth)])

RESPONSE_FORMATS = ("json", "text", "verbose_json", "srt", "vtt")


@router.post("/v1/audio/transcriptions")
@router.post("/api/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
    prompt: str | None = Form(None),  # accepted for compatibility, not used by the engines
    response_format: str = Form("json"),
    timestamp_granularities: list[str] = Form(default_factory=list, alias="timestamp_granularities[]"),
    state: AppState = Depends(get_state),
) -> Response:
    if response_format not in RESPONSE_FORMATS:
        raise bad_request(f"response_format must be one of {', '.join(RESPONSE_FORMATS)}")
    if not state.models.ready:
        raise ApiError(503, "model_not_ready", "the speech model is still being prepared; retry shortly")

    tmp_dir = state.config.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_dir) as work:
        path = Path(work) / "upload"
        await save_upload(file, path)
        try:
            result = await asyncio.to_thread(
                transcribe_file, path, state.worker, state.cutter_factory, language=language
            )
        except (AudioError, EngineError) as exc:
            raise ApiError(422, "transcription_failed", str(exc)) from exc

    segments = [
        {"seq": i, "speaker": "", "start_ms": u.start_ms, "end_ms": u.end_ms, "text": u.text}
        for i, u in enumerate(result.utterances, start=1)
    ]
    text = " ".join(u.text for u in result.utterances)
    if response_format == "text":
        return PlainTextResponse(text + "\n")
    if response_format == "srt":
        return PlainTextResponse(transcripts.render_srt(segments, speakers=False), media_type="application/x-subrip")
    if response_format == "vtt":
        return PlainTextResponse(transcripts.render_vtt(segments, speakers=False), media_type="text/vtt")
    if response_format == "json":
        return JSONResponse({"text": text})

    doc: dict[str, Any] = {
        "task": "transcribe",
        "language": language or "unknown",
        "duration": round(result.duration_ms / 1000, 3),
        "text": text,
        "model": model,
        "segments": [
            {
                "id": i, "seek": 0, "start": u.start_ms / 1000, "end": u.end_ms / 1000, "text": u.text,
                "tokens": [], "temperature": 0.0, "avg_logprob": 0.0, "compression_ratio": 0.0, "no_speech_prob": 0.0,
            }
            for i, u in enumerate(result.utterances)
        ],
    }  # fmt: skip
    if "word" in timestamp_granularities:
        doc["words"] = [
            {"word": w.w, "start": w.s / 1000, "end": w.e / 1000} for u in result.utterances for w in u.words
        ]
    return JSONResponse(doc)
