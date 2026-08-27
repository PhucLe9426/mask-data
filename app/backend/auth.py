"""Password hashing and opaque session-token helpers."""

import hashlib
import re
import secrets

from pwdlib import PasswordHash


password_hash = PasswordHash.recommended()
_DUMMY_HASH = password_hash.hash("not-a-real-user-password")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Email không hợp lệ")
    return email


def hash_password(value: str) -> str:
    return password_hash.hash(value)


def verify_password(value: str, encoded_hash: str | None) -> bool:
    """Verify against a dummy hash when the user is absent to reduce timing leaks."""
    try:
        return password_hash.verify(value, encoded_hash or _DUMMY_HASH)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
