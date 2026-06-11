"""Tests for proxy API key generation, hashing and verification."""
from ghcproxy.common.keys import generate_api_key, hash_api_key, verify_api_key


def test_generated_key_has_recognisable_prefix():
    key = generate_api_key()
    assert key.startswith("ghcp_")
    # enough entropy to be unguessable
    assert len(key) > 40


def test_generate_returns_unique_keys():
    assert generate_api_key() != generate_api_key()


def test_hash_is_deterministic():
    key = "ghcp_example"
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_is_not_the_plaintext():
    key = "ghcp_example"
    h = hash_api_key(key)
    assert isinstance(h, bytes)
    assert key.encode() not in h
    assert len(h) == 32  # sha-256


def test_verify_accepts_matching_key():
    key = generate_api_key()
    stored = hash_api_key(key)
    assert verify_api_key(key, stored) is True


def test_verify_rejects_wrong_key():
    stored = hash_api_key(generate_api_key())
    assert verify_api_key(generate_api_key(), stored) is False
