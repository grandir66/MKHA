"""Tests for password hashing and verification."""

from src.utils.auth import hash_password, verify_password


def test_hash_and_verify():
    pw_hash, salt = hash_password("secret123")
    assert verify_password("secret123", pw_hash, salt)


def test_wrong_password():
    pw_hash, salt = hash_password("secret123")
    assert not verify_password("wrong", pw_hash, salt)


def test_different_salts_different_hashes():
    h1, s1 = hash_password("same")
    h2, s2 = hash_password("same")
    # Different salts → different hashes
    assert s1 != s2
    assert h1 != h2
    # But both verify correctly
    assert verify_password("same", h1, s1)
    assert verify_password("same", h2, s2)


def test_deterministic_with_same_salt():
    _, salt = hash_password("any")
    h1, _ = hash_password("test", salt=salt)
    h2, _ = hash_password("test", salt=salt)
    assert h1 == h2
