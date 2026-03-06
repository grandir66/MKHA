"""Encryption utilities for credential storage using Fernet + PBKDF2."""

from __future__ import annotations

import base64
import json
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt_credentials(credentials: dict[str, str], password: str) -> bytes:
    """Encrypt a credentials dict with a password.

    Returns bytes in JSON format: {"salt": "<b64>", "data": "<fernet-token>"}.
    """
    salt = os.urandom(16)
    key = _derive_key(password, salt)
    f = Fernet(key)

    plaintext = json.dumps(credentials).encode()
    encrypted = f.encrypt(plaintext)

    envelope = {
        "salt": base64.b64encode(salt).decode(),
        "data": encrypted.decode(),
    }
    return json.dumps(envelope, indent=2).encode()


def decrypt_credentials(file_bytes: bytes, password: str) -> dict[str, str]:
    """Decrypt a credentials file with a password.

    Raises cryptography.fernet.InvalidToken on wrong password.
    """
    envelope = json.loads(file_bytes)
    salt = base64.b64decode(envelope["salt"])
    key = _derive_key(password, salt)
    f = Fernet(key)

    decrypted = f.decrypt(envelope["data"].encode())
    return json.loads(decrypted)


def collect_sensitive_fields(config: object) -> dict[str, str]:
    """Extract all sensitive fields from an HAConfig into a flat dict."""
    sensitive: dict[str, str] = {}
    for role in ("master", "backup"):
        router = getattr(config.routers, role)
        if router.api_password:
            sensitive[f"routers.{role}.api_password"] = router.api_password
    if config.notifications.telegram_bot_token:  # type: ignore[union-attr]
        sensitive["notifications.telegram_bot_token"] = config.notifications.telegram_bot_token  # type: ignore[union-attr]
    if config.notifications.webhook_url:  # type: ignore[union-attr]
        sensitive["notifications.webhook_url"] = config.notifications.webhook_url  # type: ignore[union-attr]
    return sensitive


def apply_decrypted_credentials(config: object, credentials: dict[str, str]) -> None:
    """Apply decrypted credentials back onto an HAConfig object."""
    for key, value in credentials.items():
        parts = key.split(".")
        if parts[0] == "routers" and len(parts) == 3:
            role, field = parts[1], parts[2]
            router = getattr(config.routers, role)
            setattr(router, field, value)
        elif parts[0] == "notifications" and len(parts) == 2:
            setattr(config.notifications, parts[1], value)
