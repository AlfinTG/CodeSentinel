"""Session token and OAuth CSRF state cryptographic management.

Architecture & Session Revocation:
- Session tokens use symmetric authenticated encryption (Fernet) embedding a unique session_id (UUID),
  user_id, and creation timestamp.
- On user logout, revoke_session() registers the session_id with an expiration matching the token TTL.
- In multi-worker or distributed deployments, REDIS_URL must be configured so session revocations
  propagate immediately across all worker processes. An in-memory cache is maintained locally
  for fast evaluation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

# Server-side revocation registry for revoked session IDs (e.g. on logout)
# Maps session_id -> expiration_timestamp
_REVOKED_SESSIONS: dict[str, float] = {}


def _get_fernet(secret: str | None = None) -> Fernet:
    """Derive a deterministic 32-byte URL-safe base64 Fernet key from secret."""
    effective_secret = secret or settings.SESSION_SECRET_KEY
    digest = hashlib.sha256(effective_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def revoke_session(session_id: str, ttl: int | None = None) -> None:
    """Revoke a session on the server side so even an unexpired replayed cookie is rejected."""
    if not session_id:
        return

    max_ttl = ttl or settings.SESSION_MAX_AGE_SECONDS
    expiry_time = time.time() + max_ttl
    _REVOKED_SESSIONS[session_id] = expiry_time

    # If Redis is configured, also record in Redis
    if settings.REDIS_URL:
        try:
            import redis

            r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)
            r.setex(f"codesentinel:revoked_session:{session_id}", max_ttl, "1")
        except Exception as exc:
            logger.warning("Failed to record session revocation in Redis (session_id=%s): %s", session_id, exc)


def is_session_revoked(session_id: str) -> bool:
    """Check whether a session ID has been revoked."""
    if not session_id:
        return True

    now = time.time()
    # Check in-memory store
    if session_id in _REVOKED_SESSIONS:
        if _REVOKED_SESSIONS[session_id] > now:
            return True
        del _REVOKED_SESSIONS[session_id]

    # Check Redis store if available
    if settings.REDIS_URL:
        try:
            import redis

            r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)
            if r.exists(f"codesentinel:revoked_session:{session_id}"):
                return True
        except Exception as exc:
            logger.warning("Failed to check session revocation in Redis (session_id=%s): %s", session_id, exc)

    return False


def create_session_token(
    user_id: str,
    extra_data: dict[str, Any] | None = None,
    secret: str | None = None,
) -> str:
    """Create a signed and encrypted session token containing session_id, user_id, and timestamp."""
    fernet = _get_fernet(secret)
    session_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_id": str(user_id),
        "created_at": int(time.time()),
    }
    if extra_data:
        payload.update(extra_data)

    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return fernet.encrypt(serialized).decode("utf-8")


def verify_session_token(
    token: str,
    max_age: int | None = None,
    secret: str | None = None,
) -> dict[str, Any] | None:
    """Decrypt and verify session token signature, expiration, and server revocation status.

    Returns the session payload dict if valid, or None if invalid/expired/revoked/tampered.
    """
    if not token or not isinstance(token, str):
        return None

    ttl = max_age if max_age is not None else settings.SESSION_MAX_AGE_SECONDS
    fernet = _get_fernet(secret)
    try:
        decrypted = fernet.decrypt(token.encode("utf-8"), ttl=ttl)
        payload = json.loads(decrypted.decode("utf-8"))
        if not isinstance(payload, dict) or "user_id" not in payload:
            return None

        # Check server-side revocation
        session_id = payload.get("session_id")
        if session_id and is_session_revoked(session_id):
            logger.debug("Session token rejected: session_id=%s is revoked", session_id)
            return None

        return payload
    except (InvalidToken, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("Session token verification failed: %s", exc)
        return None


def is_safe_relative_url(url: str | None) -> bool:
    """Ensure URL is a strictly relative path on the same host (prevents open redirects)."""
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("/") or url.startswith("//") or url.startswith("/\\"):
        return False
    path_only = url.split("?")[0].split("#")[0]
    return not ("\\" in path_only or ":" in path_only)


def create_oauth_state_token(
    return_to: str | None = None,
    secret: str | None = None,
) -> tuple[str, str]:
    """Generate a random CSRF state token and an encrypted cookie value."""
    raw_state = secrets.token_urlsafe(32)
    fernet = _get_fernet(secret)
    safe_return = return_to if is_safe_relative_url(return_to) else None
    payload = {
        "state": raw_state,
        "created_at": int(time.time()),
        "return_to": safe_return,
    }
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signed_cookie_value = fernet.encrypt(serialized).decode("utf-8")
    return raw_state, signed_cookie_value


def verify_oauth_state(
    raw_state: str,
    cookie_value: str | None,
    secret: str | None = None,
) -> tuple[bool, str | None]:
    """Validate that raw_state matches the encrypted cookie value and is unexpired.

    Returns (is_valid, safe_return_to).
    """
    if not raw_state or not cookie_value:
        return False, None

    ttl = settings.GITHUB_OAUTH_STATE_MAX_AGE_SECONDS
    fernet = _get_fernet(secret)
    try:
        decrypted = fernet.decrypt(cookie_value.encode("utf-8"), ttl=ttl)
        payload = json.loads(decrypted.decode("utf-8"))
        stored_state = payload.get("state")
        if not stored_state:
            return False, None
        is_valid = hmac.compare_digest(raw_state, stored_state)
        return_to = payload.get("return_to")
        safe_return = return_to if is_safe_relative_url(return_to) else None
        return is_valid, safe_return
    except (InvalidToken, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
