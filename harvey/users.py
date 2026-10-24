"""Dashboard accounts: passwords, invite and session tokens.

Passwords are stored as scrypt hashes (stdlib). Invite and session tokens are
random, shown once, and stored only as a sha256 hash: a copy of the database
cannot be used to sign in or to accept an invite.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

ROLES = ("admin", "operator")
MIN_PASSWORD = 10
INVITE_DAYS = 7
SESSION_DAYS = 14

_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,}")
_SCRYPT = dict(n=2 ** 14, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(_EMAIL_RE.fullmatch(email))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    """Always spends the hashing time, so an unknown account (stored == "")
    can't be told from a wrong password by timing."""
    try:
        _, salt_hex, digest_hex = stored.split("$")
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except ValueError:
        salt, want = b"\0" * 16, b"\0" * 32
        hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
        return False
    got = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.compare_digest(got, want)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def expiry(days: int) -> str:
    """Naive UTC, like every timestamp in the database."""
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)).isoformat()
