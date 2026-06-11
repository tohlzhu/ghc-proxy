"""Tests for the AEAD envelope encryption used to protect gho_ OAuth tokens."""
import os

import pytest

from ghcproxy.common.crypto import TokenCipher


def _key() -> bytes:
    return b"\x01" * 32


def test_roundtrip_recovers_plaintext():
    cipher = TokenCipher(_key())
    blob = cipher.encrypt("gho_EXAMPLEtoken0000000000000000000000000")
    assert cipher.decrypt(blob) == "gho_EXAMPLEtoken0000000000000000000000000"


def test_ciphertext_is_not_plaintext():
    cipher = TokenCipher(_key())
    secret = "gho_secretvalue"
    blob = cipher.encrypt(secret)
    assert secret.encode() not in blob


def test_nonce_is_random_so_same_plaintext_differs():
    cipher = TokenCipher(_key())
    a = cipher.encrypt("same")
    b = cipher.encrypt("same")
    assert a != b  # random 12-byte nonce prefix


def test_tamper_detection_raises():
    cipher = TokenCipher(_key())
    blob = bytearray(cipher.encrypt("payload"))
    blob[-1] ^= 0xFF  # flip a bit in the GCM tag / ciphertext
    with pytest.raises(Exception):
        cipher.decrypt(bytes(blob))


def test_wrong_key_cannot_decrypt():
    blob = TokenCipher(_key()).encrypt("payload")
    other = TokenCipher(b"\x02" * 32)
    with pytest.raises(Exception):
        other.decrypt(blob)


def test_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        TokenCipher(b"too-short")


def test_from_base64_constructor():
    import base64

    key = os.urandom(32)
    cipher = TokenCipher.from_base64(base64.b64encode(key).decode())
    blob = cipher.encrypt("hello")
    assert cipher.decrypt(blob) == "hello"
