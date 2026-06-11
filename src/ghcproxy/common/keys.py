"""Proxy API keys.

Front-end users authenticate to the proxy with a key it issues. We only ever
persist the SHA-256 hash of the key (``api_keys.key_hash``); the plaintext is
returned exactly once at creation time. Lookups hash the presented key and
compare in constant time.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_PREFIX = "ghcp_"


def generate_api_key() -> str:
    """Return a new opaque key: ``ghcp_`` + 43 url-safe base64 chars (~256 bits)."""
    return _PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> bytes:
    """SHA-256 of the key. Deterministic so it can be used as a unique index."""
    return hashlib.sha256(key.encode("utf-8")).digest()


def verify_api_key(presented: str, stored_hash: bytes) -> bool:
    return hmac.compare_digest(hash_api_key(presented), stored_hash)
