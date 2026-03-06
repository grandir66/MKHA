"""Simple password hashing and verification using PBKDF2-SHA256 (stdlib)."""

from __future__ import annotations

import hashlib
import secrets


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with PBKDF2-SHA256.

    Returns (hash_hex, salt_hex).
    """
    if salt is None:
        salt_bytes = secrets.token_bytes(16)
    else:
        salt_bytes = bytes.fromhex(salt)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, iterations=260_000)
    return dk.hex(), salt_bytes.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Verify a password against stored hash and salt."""
    computed, _ = hash_password(password, salt=salt)
    return secrets.compare_digest(computed, password_hash)
