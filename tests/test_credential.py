"""Tests for upstream header construction and auth-failure classification."""
from ghcproxy.common.config import UpstreamConfig
from ghcproxy.credential.client import (
    build_upstream_headers,
    is_login_expired,
)


def test_openai_headers_include_required_client_identity():
    h = build_upstream_headers(UpstreamConfig(), "gho_TOKEN", anthropic=False)
    assert h["Authorization"] == "Bearer gho_TOKEN"
    assert h["Copilot-Integration-Id"] == "copilot-developer-cli"
    assert h["Editor-Version"] == "copilot/1.0.61"
    assert h["X-GitHub-Api-Version"] == "2026-06-01"
    assert h["X-Initiator"] == "user"
    assert "anthropic-version" not in h


def test_anthropic_headers_add_anthropic_version():
    h = build_upstream_headers(UpstreamConfig(), "gho_TOKEN", anthropic=True)
    assert h["anthropic-version"] == "2023-06-01"
    assert h["Authorization"] == "Bearer gho_TOKEN"


def test_hop_by_hop_headers_are_not_set_by_builder():
    h = build_upstream_headers(UpstreamConfig(), "gho_TOKEN", anthropic=False)
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
