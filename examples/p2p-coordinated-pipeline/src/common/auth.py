"""
Authentication: HMAC-SHA256 session tokens for execute-step authorization.

The ProtocolGenerator mints one token per step using generate_session_token().
The coordinator attaches the token to NegotiateAck and ExecuteStep messages.
Workers call verify_session_token() in both handlers to reject unauthorized
peers before any computation runs.

Production deployment: set AGENTMESH_TOKEN_SECRET to a long random value.
  python -c "import secrets; print(secrets.token_hex(32))"
"""
import hashlib
import hmac
import logging
import os

log = logging.getLogger(__name__)

_ENV_KEY = "AGENTMESH_TOKEN_SECRET"
_DEV_FALLBACK = b"agentmesh-dev-insecure-do-not-use-in-prod"
_warned = False


def _get_secret() -> bytes:
    """Return the signing secret from env or a dev fallback (with one-time warning)."""
    global _warned
    raw = os.environ.get(_ENV_KEY, "").strip()
    if raw:
        return raw.encode()
    if not _warned:
        log.warning(
            f"[auth] {_ENV_KEY} not set — using insecure dev fallback. "
            "Set this env var before running in production."
        )
        _warned = True
    return _DEV_FALLBACK


def generate_session_token(task_id: str, step_id: str) -> str:
    """
    Derive a 32-hex-character session token for one pipeline step.

    HMAC-SHA256(secret, "{task_id}:{step_id}") → first 32 hex chars.
    Deterministic: the coordinator can regenerate the same token to include
    in ExecuteStep without storing it separately.
    """
    key = _get_secret()
    msg = f"{task_id}:{step_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def verify_session_token(token: str, task_id: str, step_id: str) -> bool:
    """
    Constant-time token verification to resist timing attacks.
    Returns False immediately if token is empty (unauthenticated request).
    """
    if not token:
        return False
    expected = generate_session_token(task_id, step_id)
    return hmac.compare_digest(expected, token)
