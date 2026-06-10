"""Resolve the front-end proxy key from incoming request headers.

Accepts the three shapes real clients use:
* ``Authorization: Bearer <key>`` (Codex / OpenAI clients)
* ``Authorization: token <key>``
* ``x-api-key: <key>`` (Anthropic / Claude Code)
"""
from __future__ import annotations


class InvalidAuth(Exception):
    """No usable proxy key on the request."""


def extract_proxy_key(headers: dict) -> str:
    lower = {k.lower(): v for k, v in headers.items()}

    xkey = lower.get("x-api-key")
    if xkey and xkey.strip():
        return xkey.strip()

    auth = lower.get("authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            key = parts[1].strip()
            if key:
                return key

    raise InvalidAuth("missing or malformed proxy credentials")
