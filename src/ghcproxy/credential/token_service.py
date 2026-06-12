"""Two-tier Copilot token service.

GitHub Copilot has two auth shapes, decided by which OAuth client minted the
account's durable token:

* **Editor client** (``Iv1.*`` GitHub App → ``ghu_``): the durable token must be
  exchanged at ``GET {github_api_base}/copilot_internal/v2/token`` (authenticated
  with ``Authorization: token <durable>``) for a short-lived **token B** that
  carries ``token`` + ``expires_at`` (and sometimes ``refresh_in``). Token B is
  the model-API bearer; it must be refreshed before expiry (~25-30 min).
* **CLI client** (``Ov23...`` OAuth App → ``gho_``): the exchange endpoint returns
  **404**; the durable token is used DIRECTLY as the model-API bearer.

``bearer_for(account)`` returns the right bearer for either case. It:
  1. checks a per-account cache (token B, or a "this account is direct-mode" flag);
  2. on miss, calls the exchange endpoint;
  3. 200 → cache token B, refreshing ``skew_s`` seconds before ``expires_at``;
  4. 404 → remember direct-mode for ``direct_ttl_s`` and return the durable token;
  5. 401/403 → raise ``CopilotAuthExpired`` (durable login is dead → quarantine);
  6. network error / 5xx / 429 → raise ``CopilotTokenUnavailable`` (transient;
     the caller must NOT quarantine — a flaky GitHub API is not a dead login).

The cache is in-process. Each proxy replica resolves independently; token B is
account-scoped state that is cheap to re-derive, so no shared store is needed.
The clock is injectable for testing (``Date``-free).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ghcproxy.common.config import UpstreamConfig


class CopilotAuthExpired(Exception):
    """The durable OAuth token is no longer valid (exchange returned 401/403)."""


class CopilotTokenUnavailable(Exception):
    """The bearer could not be resolved for a TRANSIENT reason (5xx/429/network).

    Distinct from ``CopilotAuthExpired``: callers must treat this as retryable
    and must NOT quarantine the account — the login may be perfectly healthy.
    """


@dataclass
class _Entry:
    bearer: str
    refresh_after: float   # wall-clock seconds; re-resolve once clock passes this
    direct: bool           # True => bearer IS the durable token (no exchange)


class CopilotTokenService:
    def __init__(
        self, cfg: UpstreamConfig, http, *,
        clock=time.time, skew_s: int = 120, direct_ttl_s: int = 1800,
    ) -> None:
        self._cfg = cfg
        self._http = http
        self._clock = clock
        self._skew_s = skew_s
        self._direct_ttl_s = direct_ttl_s
        self._cache: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def invalidate(self, account_id: str) -> None:
        """Drop any cached bearer for an account (e.g. after an upstream 401).

        The per-account lock is left for ``bearer_for`` to reclaim (see its
        ``finally``) — evicting it here would be unsafe if a refresh is in
        flight or another coroutine is waiting on it.
        """
        self._cache.pop(account_id, None)

    async def bearer_for(self, account) -> str:
        entry = self._cache.get(account.id)
        if entry is not None and self._clock() < entry.refresh_after:
            return entry.bearer
        # Serialize concurrent refreshes for the same account.
        lock = self._locks.setdefault(account.id, asyncio.Lock())
        try:
            async with lock:
                entry = self._cache.get(account.id)
                if entry is not None and self._clock() < entry.refresh_after:
                    return entry.bearer
                return await self._refresh(account)
        finally:
            # Bound ``_locks``: once the lock is free, nobody is waiting on it,
            # and there's no cached entry it guards (e.g. after auth-expiry or a
            # transient failure), drop it. No ``await`` occurs between the
            # ``async with`` exit and here, so the checks can't race.
            if (not lock.locked()
                    and not getattr(lock, "_waiters", None)
                    and account.id not in self._cache):
                self._locks.pop(account.id, None)

    async def _refresh(self, account) -> str:
        url = self._cfg.github_api_base.rstrip("/") + self._cfg.token_exchange_path
        try:
            resp = await self._http.get(
                url,
                headers={
                    # The EXCHANGE authenticates with the durable token using the
                    # `token` scheme (NOT Bearer) — matches litellm / copilot-api.
                    "Authorization": f"token {account.oauth_token}",
                    "Editor-Version": self._cfg.editor_version,
                    "Editor-Plugin-Version": self._cfg.editor_plugin_version,
                    "User-Agent": self._cfg.user_agent,
                    "Accept": "application/json",
                },
                timeout=20.0,
            )
        except Exception as exc:  # network error: transient, do not quarantine
            raise CopilotTokenUnavailable(
                f"exchange request failed for account {account.id}: {exc}") from exc
        status = resp.status_code
        if status == 200:
            body = resp.json()
            token_b = body["token"]
            self._cache[account.id] = _Entry(
                token_b, self._token_b_refresh_after(body), direct=False)
            return token_b
        if status == 404:
            # CLI-minted durable token: no exchange, use it directly. Cache the
            # decision so we don't re-probe the 404 on every request.
            self._cache[account.id] = _Entry(
                account.oauth_token, self._clock() + self._direct_ttl_s, direct=True)
            return account.oauth_token
        if status in (401, 403):
            self.invalidate(account.id)
            raise CopilotAuthExpired(
                f"exchange returned {status} for account {account.id}")
        # 5xx / 429 / anything else: transient. Don't poison the cache and don't
        # quarantine — a flaky exchange endpoint is not a dead login.
        raise CopilotTokenUnavailable(
            f"exchange returned {status} for account {account.id}")

    def _token_b_refresh_after(self, body: dict) -> float:
        """When token B should be refreshed: ``skew_s`` before it expires.

        Prefer ``expires_at`` (absolute Unix seconds); fall back to
        ``refresh_in`` (relative seconds from now); finally a conservative
        default so a malformed response still refreshes promptly.
        """
        if isinstance(body.get("expires_at"), (int, float)):
            expires_at = float(body["expires_at"])
        elif isinstance(body.get("refresh_in"), (int, float)):
            expires_at = self._clock() + float(body["refresh_in"])
        else:
            expires_at = self._clock() + 1500.0
        return expires_at - self._skew_s
