"""Tests for extracting model + token usage from upstream responses.

Usage must be derived for both wire protocols (OpenAI chat/completions and
Anthropic messages) and for both non-streaming JSON bodies and streamed SSE,
because the proxy emits one ``ghcproxy.usage`` event per request.
"""
from ghcproxy.proxy.usage import UsageAccumulator, usage_from_json


# ---- non-streaming JSON --------------------------------------------------

def test_openai_json_usage():
    body = {
        "model": "gpt-4o-2024-11-20",
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }
    u = usage_from_json(body)
    assert u.model == "gpt-4o-2024-11-20"
    assert u.prompt_tokens == 12
    assert u.completion_tokens == 7


def test_anthropic_json_usage():
    body = {
        "model": "claude-sonnet-4-5",
        "usage": {"input_tokens": 30, "output_tokens": 5},
    }
    u = usage_from_json(body)
    assert u.model == "claude-sonnet-4-5"
    assert u.prompt_tokens == 30
    assert u.completion_tokens == 5


def test_missing_usage_is_zero_but_keeps_model():
    u = usage_from_json({"model": "gpt-4o"})
    assert u.model == "gpt-4o"
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0


# ---- streaming SSE -------------------------------------------------------

def test_openai_stream_accumulates_final_usage():
    acc = UsageAccumulator()
    # OpenAI streams usage only in the final chunk when stream_options ask for it
    acc.observe_sse(b'data: {"model":"gpt-4o","choices":[{"delta":{"content":"hi"}}]}')
    acc.observe_sse(
        b'data: {"model":"gpt-4o","usage":{"prompt_tokens":40,"completion_tokens":3}}'
    )
    acc.observe_sse(b"data: [DONE]")
    u = acc.result()
    assert u.model == "gpt-4o"
    assert u.prompt_tokens == 40
    assert u.completion_tokens == 3


def test_anthropic_stream_accumulates_usage_across_events():
    acc = UsageAccumulator()
    acc.observe_sse(
        b'data: {"type":"message_start","message":{"model":"claude-opus-4-8",'
        b'"usage":{"input_tokens":100,"output_tokens":1}}}'
    )
    acc.observe_sse(
        b'data: {"type":"message_delta","usage":{"output_tokens":25}}'
    )
    u = acc.result()
    assert u.model == "claude-opus-4-8"
    assert u.prompt_tokens == 100
    assert u.completion_tokens == 25  # last/cumulative output count wins


def test_stream_ignores_non_data_lines_and_blank():
    acc = UsageAccumulator()
    acc.observe_sse(b"event: message")
    acc.observe_sse(b"")
    acc.observe_sse(b": comment")
    u = acc.result()
    assert u.prompt_tokens == 0
