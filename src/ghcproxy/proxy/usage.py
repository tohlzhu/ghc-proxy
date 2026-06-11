"""Derive model + token usage from upstream responses.

Supports both wire formats the GHC upstream speaks:

* OpenAI ``/chat/completions``: ``usage.{prompt_tokens,completion_tokens}``;
  for streams the usage object appears in the final chunk.
* Anthropic ``/v1/messages``: ``usage.{input_tokens,output_tokens}``; for
  streams ``message_start`` carries the input count and ``message_delta``
  carries the (cumulative) output count.

The accumulator is deliberately tolerant: malformed or partial SSE lines are
ignored so a parsing hiccup never breaks the proxied response.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class Usage:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _merge(u: Usage, obj: dict) -> None:
    """Fold a single decoded JSON object's model/usage fields into ``u``."""
    model = obj.get("model")
    if not model and isinstance(obj.get("message"), dict):
        model = obj["message"].get("model")
    if model:
        u.model = model

    usage = obj.get("usage")
    if usage is None and isinstance(obj.get("message"), dict):
        usage = obj["message"].get("usage")
    if not isinstance(usage, dict):
        return

    if "prompt_tokens" in usage:
        u.prompt_tokens = usage["prompt_tokens"]
    if "input_tokens" in usage:
        u.prompt_tokens = usage["input_tokens"]
    if "completion_tokens" in usage:
        u.completion_tokens = usage["completion_tokens"]
    if "output_tokens" in usage:
        # Anthropic streams a cumulative output count; the latest wins.
        u.completion_tokens = usage["output_tokens"]


def usage_from_json(body: dict) -> Usage:
    u = Usage()
    _merge(u, body)
    return u


class UsageAccumulator:
    """Feed it raw SSE lines; ``result()`` returns the folded :class:`Usage`."""

    def __init__(self) -> None:
        self._u = Usage()

    def observe_sse(self, line: bytes | str) -> None:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.strip()
        if not line.startswith("data:"):
            return
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError):
            return
        if isinstance(obj, dict):
            _merge(self._u, obj)

    def result(self) -> Usage:
        return self._u
