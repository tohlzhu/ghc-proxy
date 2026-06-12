"""Tests for upstream header construction and auth-failure classification.

The bearer here is whatever the token service resolved (short-lived token B for
editor/``ghu_`` accounts, or the durable token directly for CLI/``gho_``
accounts) — header construction is identical either way. Dynamic headers
(``X-Initiator``/``Copilot-Vision-Request``/``X-Request-Id``) get their own
coverage in ``test_headers.py``.
"""
from ghcproxy.common.config import UpstreamConfig
from ghcproxy.credential.client import (
    build_upstream_headers,
    is_login_expired,
)


def test_openai_headers_include_required_client_identity():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    assert h["Authorization"] == "Bearer tokB"
    assert h["Copilot-Integration-Id"] == "copilot-developer-cli"
    assert h["Editor-Version"] == "copilot/1.0.61"
    assert h["X-GitHub-Api-Version"] == "2026-06-01"
    assert h["X-Initiator"] == "user"     # default; overridden per-request
    assert "anthropic-version" not in h


def test_anthropic_headers_add_anthropic_version():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=True)
    assert h["anthropic-version"] == "2023-06-01"
    assert h["Authorization"] == "Bearer tokB"


def test_hop_by_hop_headers_are_not_set_by_builder():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    for banned in ("Host", "Content-Length", "Connection"):
        assert banned not in h


def test_401_is_login_expired():
    assert is_login_expired(401, b'{"message":"Bad credentials"}') is True


def test_403_with_login_required_is_login_expired():
    assert is_login_expired(403, b'{"error":"login_required"}') is True


def test_200_is_not_login_expired():
    assert is_login_expired(200, b"{}") is False


def test_429_rate_limit_is_not_login_expired():
    # rate limiting must not trigger account quarantine
    assert is_login_expired(429, b"slow down") is False


def test_500_upstream_error_is_not_login_expired():
    assert is_login_expired(500, b"oops") is False
