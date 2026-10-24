"""Authentication: admin session cookie, API keys, bot internal token."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Config
from .db import Database
from .errors import not_found
from .util import iso, new_id

COOKIE_NAME = "quill_session"
COOKIE_MAX_AGE = 30 * 24 * 3600
KEY_PREFIX = "qk_"
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_S = 300
_TOUCH_INTERVAL_S = 60


@dataclass(frozen=True)
class Principal:
    kind: str  # "session" (admin cookie), "key" (stored API key), "bootstrap" (QUILL_API_KEY)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class LoginLimiter:
    """Failed sign-ins per client address in a sliding window."""

    def __init__(self, attempts: int = LOGIN_ATTEMPTS, window_s: float = LOGIN_WINDOW_S) -> None:
        self.attempts = attempts
        self.window_s = window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, who: str, now: float) -> deque[float]:
        q = self._failures[who]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, who: str, now: float | None = None) -> bool:
        return len(self._trim(who, now or time.monotonic())) >= self.attempts

    def fail(self, who: str, now: float | None = None) -> None:
        now = now or time.monotonic()
        self._trim(who, now).append(now)

    def reset(self, who: str) -> None:
        self._failures.pop(who, None)


class Auth:
    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.limiter = LoginLimiter()
        self._signer = URLSafeTimedSerializer(config.session_secret, salt="quill-session")
        self._touched: dict[str, float] = {}

    @property
    def signin_enabled(self) -> bool:
        return bool(self.config.admin_password)

    # --- admin password and cookie ---

    def check_password(self, candidate: str) -> bool:
        if not self.signin_enabled:
            return False
        return hmac.compare_digest(candidate.encode(), self.config.admin_password.encode())

    def issue_cookie(self) -> str:
        return self._signer.dumps({"u": "admin"})

    def valid_cookie(self, value: str | None) -> bool:
        if not value or not self.signin_enabled:
            return False
        try:
            return self._signer.loads(value, max_age=COOKIE_MAX_AGE).get("u") == "admin"
        except (BadSignature, SignatureExpired):
            return False

    # --- bearer tokens ---

    def check_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        if self.config.api_key and hmac.compare_digest(token.encode(), self.config.api_key.encode()):
            return Principal("bootstrap")
        if not token.startswith(KEY_PREFIX):
            return None
        row = self.db.one("SELECT id FROM api_keys WHERE sha256=? AND revoked_at IS NULL", (hash_key(token),))
        if row is None:
            return None
        now = time.monotonic()
        if now - self._touched.get(row["id"], -1e9) > _TOUCH_INTERVAL_S:
            self._touched[row["id"]] = now
            self.db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (iso(), row["id"]))
        return Principal("key")

    def check_internal(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token.encode(), self.config.internal_token.encode())  # type: ignore[union-attr]

    # --- API key management ---

    def create_key(self, name: str) -> tuple[dict[str, Any], str]:
        secret = KEY_PREFIX + secrets.token_urlsafe(24)  # 32 url-safe characters
        key_id = new_id("key")
        self.db.execute(
            "INSERT INTO api_keys(id, name, prefix, sha256, created_at) VALUES (?,?,?,?,?)",
            (key_id, name.strip() or "unnamed", secret[:8], hash_key(secret), iso()),
        )
        return self.public_key(key_id), secret

    def public_key(self, key_id: str) -> dict[str, Any]:
        row = self.db.one(
            "SELECT id, name, prefix, created_at, last_used_at, revoked_at FROM api_keys WHERE id=?", (key_id,)
        )
        if row is None:
            raise not_found("key")
        return row

    def list_keys(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT id, name, prefix, created_at, last_used_at, revoked_at FROM api_keys ORDER BY created_at DESC, id"
        )

    def revoke_key(self, key_id: str) -> None:
        self.public_key(key_id)
        self.db.execute("UPDATE api_keys SET revoked_at=COALESCE(revoked_at, ?) WHERE id=?", (iso(), key_id))
