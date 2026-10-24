"""Environment configuration (spec section 2).

One immutable object, built once at startup and passed around, so tests and
the bot manager can construct their own without touching ``os.environ``.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("/data")
    models_dir: Path = Path("/models")
    port: int = 8080
    admin_password: str = ""
    api_key: str = ""
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    internal_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    public_url: str = ""
    asr_engine: str = "parakeet"
    asr_model: str = "parakeet-tdt-0.6b-v3-int8"
    asr_threads: int = 0
    remote_asr_url: str = ""
    remote_asr_key: str = ""
    remote_asr_model: str = "whisper-1"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    auto_notes: bool = False
    jitsi_domains: tuple[str, ...] = ()
    bot_name: str = "Quill Notetaker"
    bot_announce: bool = True
    bot_max_minutes: int = 240
    bot_alone_minutes: int = 3
    audio_retention_days: int = 0
    compress_audio: bool = True
    speaker_name: str = "Me"
    owner_email: str = ""
    web_dir: Path | None = None  # QUILL_WEB_DIR; default is ../web beside the package

    @property
    def db_path(self) -> Path:
        return self.data_dir / "quill.db"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def threads(self) -> int:
        return self.asr_threads if self.asr_threads > 0 else min(4, os.cpu_count() or 1)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        d = cls()
        domains = tuple(
            h.strip().lower() for h in e.get("QUILL_JITSI_DOMAINS", "").split(",") if h.strip()
        )
        return cls(
            data_dir=Path(e.get("QUILL_DATA_DIR") or d.data_dir),
            models_dir=Path(e.get("QUILL_MODELS_DIR") or d.models_dir),
            port=_int(e.get("QUILL_PORT"), d.port),
            admin_password=e.get("QUILL_ADMIN_PASSWORD", ""),
            api_key=e.get("QUILL_API_KEY", ""),
            session_secret=e.get("QUILL_SESSION_SECRET") or d.session_secret,
            internal_token=e.get("QUILL_INTERNAL_TOKEN") or d.internal_token,
            public_url=e.get("QUILL_PUBLIC_URL", "").rstrip("/"),
            asr_engine=(e.get("QUILL_ASR_ENGINE") or d.asr_engine).lower(),
            asr_model=e.get("QUILL_ASR_MODEL") or d.asr_model,
            asr_threads=_int(e.get("QUILL_ASR_THREADS"), 0),
            remote_asr_url=e.get("QUILL_REMOTE_ASR_URL", "").rstrip("/"),
            remote_asr_key=e.get("QUILL_REMOTE_ASR_KEY", ""),
            remote_asr_model=e.get("QUILL_REMOTE_ASR_MODEL") or d.remote_asr_model,
            llm_base_url=e.get("QUILL_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=e.get("QUILL_LLM_API_KEY", ""),
            llm_model=e.get("QUILL_LLM_MODEL", ""),
            auto_notes=_flag(e.get("QUILL_AUTO_NOTES"), d.auto_notes),
            jitsi_domains=domains,
            bot_name=e.get("QUILL_BOT_NAME") or d.bot_name,
            bot_announce=_flag(e.get("QUILL_BOT_ANNOUNCE"), d.bot_announce),
            bot_max_minutes=_int(e.get("QUILL_BOT_MAX_MINUTES"), d.bot_max_minutes),
            bot_alone_minutes=_int(e.get("QUILL_BOT_ALONE_MINUTES"), d.bot_alone_minutes),
            audio_retention_days=_int(e.get("QUILL_AUDIO_RETENTION_DAYS"), 0),
            compress_audio=_flag(e.get("QUILL_COMPRESS_AUDIO"), d.compress_audio),
            speaker_name=e.get("QUILL_SPEAKER_NAME") or d.speaker_name,
            owner_email=e.get("QUILL_OWNER_EMAIL", ""),
            web_dir=Path(e["QUILL_WEB_DIR"]) if e.get("QUILL_WEB_DIR") else None,
        )
