"""Tests for credential encryption and decryption."""

import pytest

from src.utils.crypto import decrypt_credentials, encrypt_credentials


def test_encrypt_decrypt_roundtrip():
    creds = {
        "routers.master.api_password": "supersecret",
        "routers.backup.api_password": "backup123",
    }
    encrypted = encrypt_credentials(creds, "mypassword")
    decrypted = decrypt_credentials(encrypted, "mypassword")
    assert decrypted == creds


def test_wrong_password_fails():
    creds = {"key": "value"}
    encrypted = encrypt_credentials(creds, "correct")
    with pytest.raises(Exception):
        decrypt_credentials(encrypted, "wrong")


def test_empty_credentials():
    creds = {}
    encrypted = encrypt_credentials(creds, "pass")
    decrypted = decrypt_credentials(encrypted, "pass")
    assert decrypted == {}


def test_special_characters_in_password():
    creds = {"pw": "DA!h03f257m!23"}
    encrypted = encrypt_credentials(creds, "p@ss!wörd#123")
    decrypted = decrypt_credentials(encrypted, "p@ss!wörd#123")
    assert decrypted == creds
