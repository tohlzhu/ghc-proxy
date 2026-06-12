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
        # admin-console state (mirrors users / api_keys / usage_rollup tables)
        self.users: dict[str, dict] = {}
        self.api_keys: dict[str, dict] = {}
        self.usage_rows: list[dict] = []
        self._key_seq = 0

    # -- test helpers -----------------------------------------------------
    def add_account(self, account_id: str, status: str = "idle", **kw) -> None:
        self.accounts[account_id] = AccountRow(id=account_id, status=status, **kw)

    def create_user_sync(self, user_id: str, *, external_id: str | None = None,
                         display_name: str | None = None, status: str = "active") -> None:
        self.users[user_id] = {
            "id": user_id, "external_id": external_id, "display_name": display_name,
            "status": status, "created_at": None}

    def add_key_sync(self, user_id: str, key_id: str, *, name: str | None = None,
                     key_hash: bytes | None = None, scopes: list[str] | None = None,
                     rate_limit: int | None = None, status: str = "active") -> None:
        self.api_keys[key_id] = {
            "id": key_id, "user_id": user_id, "name": name,
            "key_hash": key_hash or b"", "scopes": list(scopes or []),
            "rate_limit": rate_limit, "status": status,
            "created_at": None, "last_used_at": None}

    def seed_usage(self, user_id: str, account_id: str | None, day, model: str,
                   prompt_tokens: int, completion_tokens: int, requests: int) -> None:
        self.usage_rows.append({
            "user_id": user_id, "account_id": account_id, "day": day, "model": model,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "requests": requests})

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

    # -- admin-console contract (mirrors PgRepo) -------------------------
    def _usage_window(self, date_from, date_to):
        for row in self.usage_rows:
            if date_from is not None and row["day"] < date_from:
                continue
            if date_to is not None and row["day"] > date_to:
                continue
            yield row

    async def usage_timeseries(self, date_from, date_to):
        acc: dict = {}
        for row in self._usage_window(date_from, date_to):
            day = row["day"].isoformat()
            cur = acc.setdefault(day, {"day": day, "prompt_tokens": 0,
                                       "completion_tokens": 0, "requests": 0})
            cur["prompt_tokens"] += row["prompt_tokens"]
            cur["completion_tokens"] += row["completion_tokens"]
            cur["requests"] += row["requests"]
        return [acc[k] for k in sorted(acc)]

    async def usage_by_user(self, date_from, date_to):
        acc: dict = {}
        for row in self._usage_window(date_from, date_to):
            uid = row["user_id"]
            cur = acc.setdefault(uid, {"prompt_tokens": 0, "completion_tokens": 0,
                                       "requests": 0})
            cur["prompt_tokens"] += row["prompt_tokens"]
            cur["completion_tokens"] += row["completion_tokens"]
            cur["requests"] += row["requests"]
        out = []
        for uid, v in acc.items():
            user = self.users.get(uid, {})
            out.append({"user_id": uid, "external_id": user.get("external_id"),
                        "display_name": user.get("display_name"), **v})
        out.sort(key=lambda r: r["prompt_tokens"] + r["completion_tokens"], reverse=True)
        return out

    async def usage_by_account(self, date_from, date_to):
        acc: dict = {}
        for row in self._usage_window(date_from, date_to):
            aid = row["account_id"]
            cur = acc.setdefault(aid, {"prompt_tokens": 0, "completion_tokens": 0,
                                       "requests": 0})
            cur["prompt_tokens"] += row["prompt_tokens"]
            cur["completion_tokens"] += row["completion_tokens"]
            cur["requests"] += row["requests"]
        out = []
        for aid, v in acc.items():
            account = self.accounts.get(aid)
            out.append({"account_id": aid,
                        "login": account.login if account else None, **v})
        out.sort(key=lambda r: r["prompt_tokens"] + r["completion_tokens"], reverse=True)
        return out

    async def usage_by_model(self, date_from, date_to):
        acc: dict = {}
        for row in self._usage_window(date_from, date_to):
            model = row["model"]
            cur = acc.setdefault(model, {"model": model, "prompt_tokens": 0,
                                         "completion_tokens": 0, "requests": 0})
            cur["prompt_tokens"] += row["prompt_tokens"]
            cur["completion_tokens"] += row["completion_tokens"]
            cur["requests"] += row["requests"]
        out = list(acc.values())
        out.sort(key=lambda r: r["prompt_tokens"] + r["completion_tokens"], reverse=True)
        return out

    async def list_users_with_keys(self):
        out = []
        for uid, user in self.users.items():
            keys = [
                {"id": k["id"], "name": k["name"], "scopes": list(k["scopes"]),
                 "status": k["status"], "rate_limit": k["rate_limit"],
                 "created_at": k["created_at"], "last_used_at": k["last_used_at"]}
                for k in self.api_keys.values() if k["user_id"] == uid
            ]
            out.append({"id": uid, "external_id": user["external_id"],
                        "display_name": user["display_name"],
                        "status": user["status"], "created_at": user["created_at"],
                        "keys": keys})
        return out

    async def create_user(self, external_id, display_name=None):
        for uid, user in self.users.items():
            if user["external_id"] == external_id:
                user["display_name"] = display_name
                return uid
        uid = f"user-{len(self.users) + 1}"
        self.create_user_sync(uid, external_id=external_id, display_name=display_name)
        return uid

    async def add_api_key(self, user_id, key_hash, name=None, scopes=None,
                          rate_limit=None):
        self._key_seq += 1
        key_id = f"key-{self._key_seq}"
        self.add_key_sync(user_id, key_id, name=name, key_hash=key_hash,
                          scopes=scopes, rate_limit=rate_limit)
        return key_id

    async def set_user_status(self, user_id, status):
        if user_id in self.users:
            self.users[user_id]["status"] = status
            return True
        return False

    async def get_api_key_meta(self, key_id):
        k = self.api_keys.get(key_id)
        if not k:
            return None
        return {"id": k["id"], "user_id": k["user_id"], "name": k["name"],
                "scopes": list(k["scopes"]), "rate_limit": k["rate_limit"],
                "status": k["status"]}

    async def revoke_api_key(self, key_id):
        if key_id in self.api_keys:
            self.api_keys[key_id]["status"] = "revoked"
            return True
        return False

    async def set_account_status(self, account_id, status):
        acc = self.accounts.get(account_id)
        if acc:
            acc.status = status
            return True
        return False

    async def list_accounts(self):
        return [
            {"id": a.id, "login": a.login, "plan": a.plan, "api_base": a.api_base,
             "status": a.status, "last_error": a.last_error,
             "last_seen_at": None, "refresh_at": None, "updated_at": None}
            for a in self.accounts.values()
        ]

    async def list_bindings(self):
        out = []
        for uid, aid in self.bindings.items():
            user = self.users.get(uid, {})
            account = self.accounts.get(aid)
            out.append({
                "user_id": uid, "external_id": user.get("external_id"),
                "account_id": aid, "login": account.login if account else None,
                "status": "active", "bound_at": None, "last_active_at": None})
        return out
