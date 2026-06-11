"""1:1 sticky user<->account binding.

Each front-end user is pinned to exactly one backend GHC account so the
upstream never sees one account driven by many users (the account-sharing
pattern that triggers bans). The storage layer enforces strict 1:1 via two
UNIQUE columns on the ``bindings`` table and claims idle accounts atomically
(``SELECT ... FOR UPDATE SKIP LOCKED``). This service holds the policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class NoAccountAvailable(Exception):
    """Raised when no healthy idle account can back a user (-> 503 upstream)."""


@dataclass
class Binding:
    user_id: str
    account_id: str


class BindingRepo(Protocol):
    async def get_binding(self, user_id: str) -> str | None: ...
    async def claim_idle_account(self, user_id: str) -> str | None: ...
    async def quarantine_account(self, account_id: str, error: str | None = None) -> None: ...
    async def release_binding(self, user_id: str) -> None: ...


class BindingService:
    def __init__(self, repo: BindingRepo) -> None:
        self._repo = repo

    async def get_or_bind(self, user_id: str) -> Binding:
        existing = await self._repo.get_binding(user_id)
        if existing is not None:
            return Binding(user_id, existing)
        account_id = await self._repo.claim_idle_account(user_id)
        if account_id is None:
            raise NoAccountAvailable(user_id)
        return Binding(user_id, account_id)

    async def rebind_away_from(self, dead_account_id: str, user_id: str) -> Binding:
        """Quarantine the dead account, release the user, bind to a fresh one.

        The account is quarantined *first* so it cannot be re-handed-out, even
        if no replacement is available (then NoAccountAvailable propagates).

        Note on concurrency: these three repo calls are not one transaction, but
        the storage layer's invariants prevent corruption — ``claim_idle_account``
        uses ``FOR UPDATE SKIP LOCKED`` and the ``bindings`` double-UNIQUE makes
        double-binding impossible. The only observable race effect is that a
        second concurrent request for the *same* user may get a transient
        NoAccountAvailable; the client's retry then hits the settled binding.
        """
        await self._repo.quarantine_account(dead_account_id)
        await self._repo.release_binding(user_id)
        account_id = await self._repo.claim_idle_account(user_id)
        if account_id is None:
            raise NoAccountAvailable(user_id)
        return Binding(user_id, account_id)
