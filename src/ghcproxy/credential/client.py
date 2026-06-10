"""Upstream credential helpers.

Builds the request headers the GHC model API expects and classifies upstream
responses that mean "this account's login is no longer valid" (so the router
can quarantine the account and re-route the user).

Verified live against Copilot CLI 1.0.61: the durable ``gho_`` token is used
*directly* as the bearer; no short-lived exchange is required for CLI tokens.
"""
from __future__ import annotations

from ghcproxy.common.config import UpstreamConfig

# Login-expiry signals seen in GHC upstream error bodies.
_LOGIN_EXPIRED_MARKERS = (
    b"login_required",
    b"token_expired",
    b"bad credentials",
    b"missing or invalid",
)


def build_upstream_headers(
    cfg: UpstreamConfig, token: str, *, anthropic: bool
) -> dict[str, str]:
    """Return the headers to send upstream for a given account token.

    ``anthropic`` selects the Anthropic Messages dialect (adds
    ``anthropic-version``). Hop-by-hop headers (Host, Content-Length,
    Connection) are intentionally left for the HTTP client to set.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Copilot-Integration-Id": cfg.integration_id,
        "Editor-Version": cfg.editor_version,
        "X-GitHub-Api-Version": cfg.api_version,
        "X-Initiator": "user",
        "User-Agent": cfg.user_agent,
        "Content-Type": "application/json",
    }
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
