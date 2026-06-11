"""Upstream credential helpers.

Builds the request headers the GHC model API expects and classifies upstream
responses that mean "this account's login is no longer valid" (so the router
can quarantine the account and re-route the user).

The bearer passed in is whatever the token service resolved: either a
short-lived Copilot token B (editor/``ghu_`` accounts, exchanged at
``copilot_internal/v2/token``) or the durable token used directly (CLI/``gho_``
accounts, whose exchange 404s). Header *identity* is independent of which.

The header set mirrors a real editor client (cross-checked against litellm's
``get_copilot_default_headers`` and the live Copilot CLI) to keep prompt-cache
hit-rate high and avoid anomaly flagging — see ghc-proxy-design.md §2.5.
"""
from __future__ import annotations

import uuid

from ghcproxy.common.config import UpstreamConfig

# Login-expiry signals seen in GHC upstream error bodies.
_LOGIN_EXPIRED_MARKERS = (
    b"login_required",
    b"token_expired",
    b"bad credentials",
    b"missing or invalid",
)


def build_upstream_headers(
    cfg: UpstreamConfig, token: str, *, anthropic: bool,
    initiator: str = "user", vision: bool = False,
    request_id: str | None = None,
) -> dict[str, str]:
    """Return the headers to send upstream for a given bearer token.

    ``anthropic`` selects the Anthropic Messages dialect (adds
    ``anthropic-version``). ``initiator`` (``user``/``agent``) and ``vision``
    are derived per-request from the body (see ``headers.py``). ``request_id``
    defaults to a fresh UUID. Hop-by-hop headers (Host, Content-Length,
    Connection) are intentionally left for the HTTP client to set.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Copilot-Integration-Id": cfg.integration_id,
        "Editor-Version": cfg.editor_version,
        "Editor-Plugin-Version": cfg.editor_plugin_version,
        "X-GitHub-Api-Version": cfg.api_version,
        "OpenAI-Intent": cfg.openai_intent,
        "X-Initiator": initiator,
        "X-Request-Id": request_id or str(uuid.uuid4()),
        "User-Agent": cfg.user_agent,
        "Content-Type": "application/json",
    }
    if vision:
        headers["Copilot-Vision-Request"] = "true"
    if anthropic:
        headers["anthropic-version"] = cfg.anthropic_version
    return headers


def is_login_expired(status: int, body: bytes) -> bool:
    """True iff the upstream response means the account login is dead.

    Only 401/403 count. Rate limiting (429) and server errors (5xx) are
    transient and must NOT quarantine the account.
    """
    if status not in (401, 403):
        return False
    if status == 401:
        return True
    low = body.lower()
    return any(marker in low for marker in _LOGIN_EXPIRED_MARKERS)
