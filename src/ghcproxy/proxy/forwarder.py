"""Core request forwarding with automatic re-route on login expiry.

Sequence per request:

1. Resolve the user's 1:1 bound account (binding it on first use).
2. Send the request upstream with that account's credentials.
3. If the upstream says the account login is dead (401 / 403 login_required),
   quarantine it, rebind the user to a fresh idle account, and retry **once**.
4. Rate-limit (429) and 5xx are returned to the caller unchanged — they are
   transient and must not burn the account.

Token caching/refresh and Kafka logging live in the surrounding layers; this
unit is the routing + fault-tolerance policy and is fully unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ghcproxy.credential.client import is_login_expired
from ghcproxy.router.binding import BindingService


@dataclass
class UpstreamResult:
    status: int
    headers: dict
    body: bytes
    stream = None  # populated by the streaming path; None for buffered


@dataclass
class Upstream:  # pragma: no cover - protocol marker
    """Anything with ``async send(account, path, method, headers, body, anthropic)``."""


class Forwarder:
    def __init__(self, binding: BindingService, upstream, account_repo) -> None:
        self._binding = binding
        self._upstream = upstream
        self._accounts = account_repo

    async def handle(
        self, user_id: str, *, path: str, method: str, headers: dict,
        body: bytes, anthropic: bool,
    ) -> UpstreamResult:
        binding = await self._binding.get_or_bind(user_id)
        account = await self._accounts.get_account(binding.account_id)
        result = await self._upstream.send(
            account=account, path=path, method=method,
            headers=headers, body=body, anthropic=anthropic,
        )
        if is_login_expired(result.status, result.body):
            # account login is dead: quarantine + rebind + retry once
            new_binding = await self._binding.rebind_away_from(
                binding.account_id, user_id
            )
            account = await self._accounts.get_account(new_binding.account_id)
            result = await self._upstream.send(
                account=account, path=path, method=method,
                headers=headers, body=body, anthropic=anthropic,
            )
        return result
