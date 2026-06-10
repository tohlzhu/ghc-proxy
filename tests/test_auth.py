"""Tests for extracting the proxy key from request headers."""
import pytest

from ghcproxy.proxy.auth import extract_proxy_key, InvalidAuth


def test_bearer_scheme():
    assert extract_proxy_key({"authorization": "Bearer ghcp_abc"}) == "ghcp_abc"


def test_token_scheme():
    assert extract_proxy_key({"authorization": "token ghcp_abc"}) == "ghcp_abc"


def test_x_api_key_header_for_anthropic_clients():
    # Claude Code sends the key in x-api-key
    assert extract_proxy_key({"x-api-key": "ghcp_abc"}) == "ghcp_abc"


def test_case_insensitive_header_names():
    assert extract_proxy_key({"Authorization": "Bearer ghcp_abc"}) == "ghcp_abc"


def test_missing_auth_raises():
    with pytest.raises(InvalidAuth):
        extract_proxy_key({})


def test_empty_bearer_raises():
    with pytest.raises(InvalidAuth):
        extract_proxy_key({"authorization": "Bearer "})
