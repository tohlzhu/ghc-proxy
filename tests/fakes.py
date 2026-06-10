"""In-memory fakes emulating the repository contracts for unit tests.

These mimic the *behaviour* the real Postgres/Redis repos guarantee (atomic
idle-account claim, sticky binding rows, token cache) without a database, so
policy logic can be tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccountRow:
    id: str
    login: str = "login"
    oauth_token: str = "gho_faketoken"
    api_base: str = "https://api.enterprise.githubcopilot.com"
    plan: str = "enterprise"
    status: str = "idle"
    last_error: str | None = None


class FakeRepo:
    """Emulates AccountRepo + BindingRepo for binding tests."""

    def __init__(self) -> None:
        self.accounts: dict[str, AccountRow] = {}
        self.bindings: dict[str, str] = {}  # user_id -> account_id

    # -- test helpers -----------------------------------------------------
    def add_account(self, account_id: str, status: str = "idle", **kw) -> None:
        self.accounts[account_id] = AccountRow(id=account_id, status=status, **kw)

    # -- BindingRepo contract --------------------------------------------
    async def get_binding(self, user_id: str) -> str | None:
        return self.bindings.get(user_id)

    async def claim_idle_account(self, user_id: str) -> str | None:
        """Atomically pick an idle account, mark it bound, write the binding."""
        for acc in self.accounts.values():
            if acc.status == "idle":
                acc.status = "bound"
                self.bindings[user_id] = acc.id
                return acc.id
        return None

    async def quarantine_account(self, account_id: str, error: str | None = None) -> None:
        acc = self.accounts.get(account_id)
        if acc:
            acc.status = "quarantined"
            acc.last_error = error

    async def release_binding(self, user_id: str) -> None:
        account_id = self.bindings.pop(user_id, None)
        # mirror the real repo: a released 'bound' account returns to idle
        if account_id and account_id in self.accounts:
            if self.accounts[account_id].status == "bound":
                self.accounts[account_id].status = "idle"

    async def get_account(self, account_id: str) -> AccountRow | None:
        return self.accounts.get(account_id)

    async def mark_seen(self, account_id: str, refresh_at) -> None:
        acc = self.accounts.get(account_id)
        if acc:
            acc.last_error = None
