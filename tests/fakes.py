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
        self.device_sessions: dict[str, dict] = {}

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

    async def create_device_session(
        self, login, device_code, user_code, verification_uri, interval_s, expires_at,
        *, plan="enterprise", api_base="https://api.enterprise.githubcopilot.com",
    ):
        account_id = f"acc-{login}"
        self.accounts[account_id] = AccountRow(
            id=account_id, login=login, oauth_token="", api_base=api_base,
            plan=plan, status="logging_in")
        session_id = f"sess-{len(self.device_sessions) + 1}"
        self.device_sessions[session_id] = {
            "id": session_id, "account_id": account_id, "login": login,
            "device_code": device_code, "user_code": user_code,
            "verification_uri": verification_uri, "interval_s": interval_s,
            "expires_at": expires_at, "status": "pending"}
        return account_id, session_id

    async def get_pending_device_session(self, login):
        for session in reversed(list(self.device_sessions.values())):
            if session["login"] == login and session["status"] == "pending":
                return dict(session)
        return None

    async def mark_device_session(self, session_id, status):
        self.device_sessions[session_id]["status"] = status

    async def complete_device_session(self, session_id, oauth_token):
        session = self.device_sessions[session_id]
        session["status"] = "authorized"
        account = self.accounts[session["account_id"]]
        account.oauth_token = oauth_token
        account.status = "idle"
        return account.id
