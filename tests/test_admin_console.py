"""Admin Console JSON API tests.

Covers the endpoints the operator console needs beyond the original minimal
admin API: usage aggregation over usage_rollup (by time / user / account /
model), full user+key lifecycle (list, issue, rotate, revoke, enable/disable),
account status changes, binding visualization and manual release.

All endpoints sit behind require_admin (static X-Admin-Token). These tests use
the FastAPI TestClient against an in-memory FakeRepo that mirrors the real
PgRepo contract, so no Postgres is required.

Invariant asserted throughout: key plaintext/hash is NEVER returned except the
one-time plaintext field on create/rotate.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from ghcproxy.common.keys import hash_api_key
from ghcproxy.proxy.app import create_app
from ghcproxy.router.binding import BindingService
from tests.fakes import FakeRepo

ADMIN = "secret"
H = {"x-admin-token": ADMIN}


class FakeCache:
    def __init__(self):
        self.k = {}

    async def get_user_for_key(self, h):
        return self.k.get(h)

    async def set_user_for_key(self, h, u, ttl=300):
        self.k[h] = u


class FakeSink:
    def __init__(self):
        self.audit_events = []

    async def usage(self, e):
        pass

    async def prompt(self, e):
        pass

    async def audit(self, e):
        self.audit_events.append(e)


class Ctx:
    pass


def _make(repo=None):
    repo = repo or FakeRepo()
    ctx = Ctx()
    ctx.repo = repo
    ctx.cache = FakeCache()
    ctx.sink = FakeSink()
    ctx.upstream = None
    ctx.binding = BindingService(repo)
    ctx.forwarder = None
    ctx.device_flow = None
    ctx.admin_token = ADMIN
    ctx.pending_logins = {}
    return ctx, repo


def _client(repo=None):
    ctx, repo = _make(repo)
    return TestClient(create_app(ctx)), repo, ctx


# --------------------------------------------------------------------------
# auth: every new endpoint must require the admin token
# --------------------------------------------------------------------------

NEW_GET_ENDPOINTS = [
    "/admin/usage/timeseries",
    "/admin/usage/by-user",
    "/admin/usage/by-account",
    "/admin/usage/by-model",
    "/admin/users",
    "/admin/bindings",
]


@pytest.mark.parametrize("path", NEW_GET_ENDPOINTS)
def test_new_get_endpoints_require_admin(path):
    client, _, _ = _client()
    assert client.get(path).status_code == 403
    assert client.get(path, headers={"x-admin-token": "wrong"}).status_code == 403


def test_mutation_endpoints_require_admin():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    repo.add_account("acc1", status="quarantined")
    repo.add_key_sync("u1", "k1", name="default")
    # all should be 403 without the token
    assert client.patch("/admin/users/u1", json={"status": "disabled"}).status_code == 403
    assert client.post("/admin/users/u1/keys", json={"name": "ci"}).status_code == 403
    assert client.post("/admin/keys/k1/rotate").status_code == 403
    assert client.post("/admin/keys/k1/revoke").status_code == 403
    assert client.patch("/admin/accounts/acc1/status",
                        json={"status": "idle"}).status_code == 403
    assert client.post("/admin/bindings/u1/release").status_code == 403


# --------------------------------------------------------------------------
# usage aggregation
# --------------------------------------------------------------------------

def _seed_usage(repo):
    # two users, two accounts, two models, three days
    repo.create_user_sync("u1", external_id="alice", display_name="Alice")
    repo.create_user_sync("u2", external_id="bob", display_name="Bob")
    repo.add_account("acc1", login="octo-1", status="bound")
    repo.add_account("acc2", login="octo-2", status="bound")
    rows = [
        # user, account, day, model, prompt, completion, requests
        ("u1", "acc1", dt.date(2026, 6, 1), "gpt-4o", 100, 10, 3),
        ("u1", "acc1", dt.date(2026, 6, 2), "gpt-4o", 50, 5, 2),
        ("u1", "acc1", dt.date(2026, 6, 2), "claude-sonnet-4.5", 30, 3, 1),
        ("u2", "acc2", dt.date(2026, 6, 2), "gpt-4o", 200, 20, 4),
        ("u2", "acc2", dt.date(2026, 6, 3), "claude-sonnet-4.5", 40, 4, 1),
    ]
    for u, a, day, model, p, c, r in rows:
        repo.seed_usage(u, a, day, model, p, c, r)


def test_usage_timeseries_aggregates_per_day():
    client, repo, _ = _client()
    _seed_usage(repo)
    r = client.get("/admin/usage/timeseries",
                   params={"from": "2026-06-01", "to": "2026-06-03"}, headers=H)
    assert r.status_code == 200
    data = r.json()
    by_day = {row["day"]: row for row in data}
    # ascending by day
    assert [row["day"] for row in data] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert by_day["2026-06-01"]["prompt_tokens"] == 100
    assert by_day["2026-06-01"]["requests"] == 3
    # 2026-06-02: 50+30+200 prompt, 5+3+20 completion, 2+1+4 requests
    assert by_day["2026-06-02"]["prompt_tokens"] == 280
    assert by_day["2026-06-02"]["completion_tokens"] == 28
    assert by_day["2026-06-02"]["requests"] == 7


def test_usage_timeseries_respects_date_window():
    client, repo, _ = _client()
    _seed_usage(repo)
    r = client.get("/admin/usage/timeseries",
                   params={"from": "2026-06-02", "to": "2026-06-02"}, headers=H)
    assert r.status_code == 200
    days = [row["day"] for row in r.json()]
    assert days == ["2026-06-02"]


def test_usage_by_user_ranks_and_labels():
    client, repo, _ = _client()
    _seed_usage(repo)
    r = client.get("/admin/usage/by-user",
                   params={"from": "2026-06-01", "to": "2026-06-03"}, headers=H)
    assert r.status_code == 200
    data = r.json()
    # u1 total prompt = 180, u2 = 240 -> u2 ranks first (desc by tokens)
    assert data[0]["user_id"] == "u2"
    assert data[0]["external_id"] == "bob"
    assert data[0]["prompt_tokens"] == 240
    assert data[1]["user_id"] == "u1"
    assert data[1]["prompt_tokens"] == 180
    assert data[1]["display_name"] == "Alice"


def test_usage_by_account_distribution():
    client, repo, _ = _client()
    _seed_usage(repo)
    r = client.get("/admin/usage/by-account",
                   params={"from": "2026-06-01", "to": "2026-06-03"}, headers=H)
    assert r.status_code == 200
    data = r.json()
    by_acc = {row["account_id"]: row for row in data}
    assert by_acc["acc1"]["login"] == "octo-1"
    assert by_acc["acc1"]["prompt_tokens"] == 180
    assert by_acc["acc2"]["prompt_tokens"] == 240


def test_usage_by_model_share():
    client, repo, _ = _client()
    _seed_usage(repo)
    r = client.get("/admin/usage/by-model",
                   params={"from": "2026-06-01", "to": "2026-06-03"}, headers=H)
    assert r.status_code == 200
    by_model = {row["model"]: row for row in r.json()}
    # gpt-4o: 100+50+200 = 350 ; claude: 30+40 = 70
    assert by_model["gpt-4o"]["prompt_tokens"] == 350
    assert by_model["claude-sonnet-4.5"]["prompt_tokens"] == 70


# --------------------------------------------------------------------------
# users & keys
# --------------------------------------------------------------------------

def test_list_users_returns_key_metadata_only():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice", display_name="Alice")
    # store a known key; the hash must never appear in the response
    plaintext = "ghcp_secretplaintext"
    repo.add_key_sync("u1", "k1", name="default", key_hash=hash_api_key(plaintext),
                      scopes=["chat"], rate_limit=60)
    r = client.get("/admin/users", headers=H)
    assert r.status_code == 200
    body = r.text
    users = r.json()
    assert users[0]["external_id"] == "alice"
    key = users[0]["keys"][0]
    assert key["name"] == "default"
    assert key["scopes"] == ["chat"]
    assert key["rate_limit"] == 60
    assert key["status"] == "active"
    # never leak plaintext or hash
    assert plaintext not in body
    assert hash_api_key(plaintext).hex() not in body
    assert "key_hash" not in key
    assert "hash" not in key


def test_create_user_returns_plaintext_once():
    client, repo, _ = _client()
    r = client.post("/admin/users", headers=H,
                    json={"external_id": "carol", "display_name": "Carol"})
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("ghcp_")
    # the same key must not be retrievable later
    listing = client.get("/admin/users", headers=H).text
    assert body["api_key"] not in listing


def test_issue_additional_key_for_user():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    r = client.post("/admin/users/u1/keys", headers=H,
                    json={"name": "ci-runner", "scopes": ["chat"], "rate_limit": 120})
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("ghcp_")
    assert body["name"] == "ci-runner"
    # the new key now shows up as metadata
    users = client.get("/admin/users", headers=H).json()
    names = [k["name"] for k in users[0]["keys"]]
    assert "ci-runner" in names


def test_rotate_key_revokes_old_and_issues_new():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    repo.add_key_sync("u1", "k1", name="default", scopes=["chat"], rate_limit=60)
    r = client.post("/admin/keys/k1/rotate", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("ghcp_")
    # old key revoked, new key active and carries the same metadata
    users = client.get("/admin/users", headers=H).json()
    keys = {k["id"]: k for k in users[0]["keys"]}
    assert keys["k1"]["status"] == "revoked"
    new_id = body["api_key_id"]
    assert new_id != "k1"
    assert keys[new_id]["status"] == "active"
    assert keys[new_id]["name"] == "default"
    assert keys[new_id]["scopes"] == ["chat"]
    assert keys[new_id]["rate_limit"] == 60


def test_revoke_key():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    repo.add_key_sync("u1", "k1", name="default")
    r = client.post("/admin/keys/k1/revoke", headers=H)
    assert r.status_code == 200
    users = client.get("/admin/users", headers=H).json()
    assert users[0]["keys"][0]["status"] == "revoked"


def test_disable_and_enable_user():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    r = client.patch("/admin/users/u1", headers=H, json={"status": "disabled"})
    assert r.status_code == 200
    assert client.get("/admin/users", headers=H).json()[0]["status"] == "disabled"
    r = client.patch("/admin/users/u1", headers=H, json={"status": "active"})
    assert r.status_code == 200
    assert client.get("/admin/users", headers=H).json()[0]["status"] == "active"


def test_disable_user_rejects_invalid_status():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    r = client.patch("/admin/users/u1", headers=H, json={"status": "bogus"})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------

def test_list_accounts_extended_columns():
    client, repo, _ = _client()
    repo.add_account("acc1", login="octo-1", status="idle", plan="enterprise",
                     api_base="https://api.enterprise.githubcopilot.com")
    r = client.get("/admin/accounts", headers=H)
    assert r.status_code == 200
    acc = r.json()[0]
    for col in ("id", "login", "plan", "api_base", "status",
                "last_error", "last_seen_at", "refresh_at", "updated_at"):
        assert col in acc
    assert acc["login"] == "octo-1"
    assert acc["api_base"] == "https://api.enterprise.githubcopilot.com"


def test_change_account_status_clears_quarantine():
    client, repo, _ = _client()
    repo.add_account("acc1", login="octo-1", status="quarantined")
    r = client.patch("/admin/accounts/acc1/status", headers=H, json={"status": "idle"})
    assert r.status_code == 200
    assert repo.accounts["acc1"].status == "idle"


def test_change_account_status_to_disabled():
    client, repo, _ = _client()
    repo.add_account("acc1", login="octo-1", status="idle")
    r = client.patch("/admin/accounts/acc1/status", headers=H, json={"status": "disabled"})
    assert r.status_code == 200
    assert repo.accounts["acc1"].status == "disabled"


def test_change_account_status_rejects_invalid():
    client, repo, _ = _client()
    repo.add_account("acc1", login="octo-1", status="idle")
    r = client.patch("/admin/accounts/acc1/status", headers=H, json={"status": "bound"})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# bindings
# --------------------------------------------------------------------------

def test_list_bindings_joins_user_and_account():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    repo.add_account("acc1", login="octo-1", status="idle")
    # bind u1 -> acc1 directly (claim path is covered elsewhere)
    repo.bindings["u1"] = "acc1"
    repo.accounts["acc1"].status = "bound"
    r = client.get("/admin/bindings", headers=H)
    assert r.status_code == 200
    b = r.json()[0]
    assert b["user_id"] == "u1"
    assert b["external_id"] == "alice"
    assert b["account_id"] == "acc1"
    assert b["login"] == "octo-1"
    assert b["status"] == "active"


def test_manual_release_returns_account_to_idle():
    client, repo, _ = _client()
    repo.create_user_sync("u1", external_id="alice")
    repo.add_account("acc1", login="octo-1", status="bound")
    repo.bindings["u1"] = "acc1"
    r = client.post("/admin/bindings/u1/release", headers=H)
    assert r.status_code == 200
    assert "u1" not in repo.bindings
    assert repo.accounts["acc1"].status == "idle"
