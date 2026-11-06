"""Single-owner authentication: Argon2 password, opaque session cookie, CSRF.

The session cookie is HttpOnly + SameSite=Strict; every state-changing request
must also send the per-session CSRF token in X-CSRF-Token.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request

from .db import Database, now

COOKIE = "filmlab_session"
_hasher = PasswordHasher()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def set_owner(db: Database, username: str, password: str):
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    await db.execute(
        "INSERT INTO owner (id, username, password_hash, updated_at) VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username = excluded.username, password_hash = excluded.password_hash, "
        "updated_at = excluded.updated_at",
        (username, _hasher.hash(password), now()))
    await db.execute("DELETE FROM sessions")  # rotating the password ends all sessions


async def verify_owner(db: Database, username: str, password: str) -> bool:
    row = await db.fetchone("SELECT username, password_hash FROM owner WHERE id = 1")
    if not row:
        _hasher.hash(password)  # keep timing similar when no owner exists
        return False
    try:
        ok = _hasher.verify(row["password_hash"], password)
    except VerifyMismatchError:
        return False
    return ok and secrets.compare_digest(row["username"], username)


async def create_session(db: Database, hours: int) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    await db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (_hash_token(token), csrf, now(), expires))
    return token, csrf


async def end_session(db: Database, token: str | None):
    if token:
        await db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


async def session_for(db: Database, token: str | None) -> dict | None:
    if not token:
        return None
    row = await db.fetchone("SELECT csrf_token, expires_at FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    if not row or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return row


async def require_owner(request: Request) -> dict:
    """FastAPI dependency: valid session, plus CSRF for unsafe methods."""
    sess = await session_for(request.app.state.db, request.cookies.get(COOKIE))
    if not sess:
        raise HTTPException(401, "login required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(sent, sess["csrf_token"]):
            raise HTTPException(403, "csrf token missing or invalid")
    return sess
