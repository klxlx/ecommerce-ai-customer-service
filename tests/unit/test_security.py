"""Unit tests for password hashing and session token helpers."""

from __future__ import annotations

from app.core.security import (
    create_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)


def test_password_hash_round_trip():
    encoded = hash_password("s3cret!")
    assert encoded.startswith("scrypt$")
    assert verify_password("s3cret!", encoded) is True


def test_password_hash_rejects_wrong_password():
    encoded = hash_password("right")
    assert verify_password("wrong", encoded) is False


def test_password_hash_rejects_malformed():
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "scrypt$bad") is False


def test_password_hashes_are_salted():
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a)
    assert verify_password("same", b)


def test_session_token_is_urlsafe_and_unique():
    t1 = create_session_token()
    t2 = create_session_token()
    assert t1 != t2
    assert len(t1) >= 32


def test_session_token_hash_is_deterministic():
    token = create_session_token()
    assert hash_session_token(token) == hash_session_token(token)
    assert len(hash_session_token(token)) == 64
