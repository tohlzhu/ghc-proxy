"""Dynamic per-request header derivation for the GHC model API.

Two values must be derived from the request body rather than hardcoded, to
match real editor clients and avoid being flagged / overbilled:

* ``X-Initiator`` — ``agent`` when the conversation already contains an
  ``assistant`` or ``tool`` message (i.e. an agent follow-up turn), else
  ``user``. The Copilot backend uses this to separate human-typed messages
  (billable premium requests) from agent loop follow-ups; hardcoding ``user``
  multiplies premium-request charges on every agent iteration.
* ``Copilot-Vision-Request: true`` — when any message carries image content
  (OpenAI ``image_url`` parts or Anthropic ``image`` blocks).

Both parse defensively: a non-JSON or unexpected body yields the safe default
(``user`` / no vision) rather than raising.
"""
from __future__ import annotations

import json
from typing import Any

_AGENT_ROLES = {"assistant", "tool"}


def _load_messages(body: bytes) -> list[dict]:
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    msgs = obj.get("messages")
    return msgs if isinstance(msgs, list) else []


def derive_initiator(body: bytes) -> str:
    """``"agent"`` if any message role is assistant/tool, else ``"user"``."""
    for msg in _load_messages(body):
        if isinstance(msg, dict) and msg.get("role") in _AGENT_ROLES:
            return "agent"
    return "user"


def _content_has_image(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        # OpenAI: {"type":"image_url", ...}; Anthropic: {"type":"image", ...}
        if ptype in ("image_url", "image", "input_image"):
            return True
    return False


def has_vision_content(body: bytes) -> bool:
    """True if any message carries image content (OpenAI or Anthropic shape)."""
    for msg in _load_messages(body):
        if isinstance(msg, dict) and _content_has_image(msg.get("content")):
            return True
    return False
