"""AEAD envelope encryption for the durable ``gho_`` OAuth tokens.

The plaintext token is never written to the database or logs. We store the
output of :meth:`TokenCipher.encrypt` (a random 12-byte nonce followed by the
AES-256-GCM ciphertext+tag) in ``accounts.oauth_token_enc``.

In production the 32-byte data key should itself be wrapped by a KMS
(envelope encryption); here it is supplied directly so the same code works in
local tests and in cloud with a key injected via secret.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class TokenCipher:
    """AES-256-GCM cipher with a random nonce per message."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("data key must be exactly 32 bytes (AES-256)")
        self._aes = AESGCM(key)

    @classmethod
    def from_base64(cls, key_b64: str) -> "TokenCipher":
        return cls(base64.b64decode(key_b64))

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ct

    def decrypt(self, blob: bytes) -> str:
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return self._aes.decrypt(nonce, ct, None).decode("utf-8")
