"""
server/auth.py
──────────────
Token-based authentication with per-IP sliding-window rate limiting.

Rate limit: max 5 failures per 60-second window per remote IP.
Tokens are hashed (SHA-256, first 16 hex chars) before being written to the
auth_events table so raw secrets never touch the database.
"""

import hashlib
import time
from collections import defaultdict, deque
from typing import Dict, Optional

from .database import db
from .logger import log

# ── Credentials store (in production: load from env / secrets manager) ────────
VALID_TOKENS: Dict[str, str] = {
    "TOKEN-ASHWIN-001": "ashwin",
    "TOKEN-ADMIN-002":  "admin",
    "TOKEN-DEMO-003":   "demo",
    "TOKEN-TEST-004":   "test",
}

# Per-IP sliding window of failure timestamps (maxlen caps memory use)
BRUTE_FORCE: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10))


def verify_token(token: str, client_ip: str) -> Optional[str]:
    """
    Authenticate a token for the given client IP.

    Returns the username string on success, or None if authentication fails
    (invalid token or rate-limited).
    """
    now = time.time()
    recent = [t for t in BRUTE_FORCE[client_ip] if now - t < 60]

    if len(recent) >= 5:
        db.log_auth("BLOCKED", client_ip, reason="Rate limit exceeded")
        log.warning(f"[AUTH] {client_ip} BLOCKED — brute-force protection")
        return None

    tok_hash = hashlib.sha256(token.encode()).hexdigest()[:16]

    if token in VALID_TOKENS:
        db.log_auth("SUCCESS", client_ip, tok_hash)
        log.info(f"[AUTH] {client_ip} OK as '{VALID_TOKENS[token]}'")
        return VALID_TOKENS[token]

    BRUTE_FORCE[client_ip].append(now)
    db.log_auth("FAILURE", client_ip, tok_hash, "Invalid token")
    log.warning(f"[AUTH] {client_ip} FAILED ({len(recent) + 1}/5 attempts)")
    return None
