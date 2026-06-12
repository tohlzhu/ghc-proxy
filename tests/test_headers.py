"""Tests for cache-friendly, anomaly-safe upstream header construction.

Mirrors what real editor clients (and the live Copilot CLI) send, cross-checked
against litellm's ``get_copilot_default_headers`` + dynamic ``X-Initiator`` /
``Copilot-Vision-Request`` logic. Getting these wrong hurts prompt-cache hit
rate and inflates premium-request billing (see todo.md).
"""
from ghcproxy.common.config import UpstreamConfig
from ghcproxy.credential.client import build_upstream_headers
from ghcproxy.credential.headers import derive_initiator, has_vision_content


# -- static identity headers -------------------------------------------------
def test_includes_full_editor_identity():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    assert h["Authorization"] == "Bearer tokB"
    assert h["Copilot-Integration-Id"]
    assert h["Editor-Version"]
    assert h["Editor-Plugin-Version"]          # was missing before
    assert h["X-GitHub-Api-Version"]
    assert h["OpenAI-Intent"]                   # was missing before
    assert h["User-Agent"]


def test_x_request_id_is_present_and_unique_per_call():
    h1 = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    h2 = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    assert h1["X-Request-Id"] and h2["X-Request-Id"]
    assert h1["X-Request-Id"] != h2["X-Request-Id"]


def test_caller_can_supply_request_id():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False,
                               request_id="fixed-id")
    assert h["X-Request-Id"] == "fixed-id"


def test_anthropic_adds_version():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=True)
    assert h["anthropic-version"] == "2023-06-01"


def test_hop_by_hop_headers_not_set():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    for banned in ("Host", "Content-Length", "Connection"):
        assert banned not in h


# -- dynamic X-Initiator -----------------------------------------------------
def test_initiator_defaults_to_user():
    body = b'{"messages":[{"role":"user","content":"hi"}]}'
    assert derive_initiator(body) == "user"


def test_initiator_is_agent_when_assistant_message_present():
    body = b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}]}'
    assert derive_initiator(body) == "agent"


def test_initiator_is_agent_when_tool_message_present():
    body = b'{"messages":[{"role":"user","content":"hi"},{"role":"tool","content":"result"}]}'
    assert derive_initiator(body) == "agent"


def test_initiator_handles_non_json_body():
    assert derive_initiator(b"not json") == "user"
    assert derive_initiator(b"") == "user"


def test_initiator_explicit_override_in_headers():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False,
                               initiator="agent")
    assert h["X-Initiator"] == "agent"


def test_initiator_default_in_headers_is_user():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False)
    assert h["X-Initiator"] == "user"


# -- Copilot-Vision-Request --------------------------------------------------
def test_vision_detected_openai_image_url():
    body = (b'{"messages":[{"role":"user","content":['
            b'{"type":"image_url","image_url":{"url":"data:image/png;base64,AAA"}}]}]}')
    assert has_vision_content(body) is True


def test_vision_detected_anthropic_image_block():
    body = (b'{"messages":[{"role":"user","content":['
            b'{"type":"image","source":{"type":"base64","data":"AAA"}}]}]}')
    assert has_vision_content(body) is True


def test_no_vision_for_text_only():
    body = b'{"messages":[{"role":"user","content":"just text"}]}'
    assert has_vision_content(body) is False


def test_vision_header_set_when_images_present():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False, vision=True)
    assert h["Copilot-Vision-Request"] == "true"


def test_vision_header_absent_when_no_images():
    h = build_upstream_headers(UpstreamConfig(), "tokB", anthropic=False, vision=False)
    assert "Copilot-Vision-Request" not in h
