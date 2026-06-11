"""End-to-end API tests using FastAPI's TestClient against fake infra.

Exercises the real app wiring (auth -> bind -> forward -> response, usage
emit) with an in-memory repo/cache/sink and a fake upstream, so no Postgres/
Redis/Kafka or network is required.
"""
import pytest
from fastapi.testclient import TestClient

from ghcproxy.common.keys import hash_api_key
from ghcproxy.proxy.app import create_app
from ghcproxy.proxy.forwarder import Forwarder, UpstreamResult
from ghcproxy.router.binding import BindingService
from tests.fakes import FakeRepo


class FakeCache:
    def __init__(self):
        self.k = {}

    async def get_user_for_key(self, h):
        return self.k.get(h)

    async def set_user_for_key(self, h, u, ttl=300):
        self.k[h] = u


class FakeSink:
    def __init__(self):
        self.usage_events = []

    async def usage(self, e):
        self.usage_events.append(e)

    async def prompt(self, e):
        pass

    async def audit(self, e):
        pass


class FakeUpstream:
    def __init__(self):
        self.resp = (200, b'{"model":"gpt-4o","usage":{"prompt_tokens":5,"completion_tokens":2}}')

    async def send(self, *, account, path, method, headers, body, anthropic):
        return UpstreamResult(status=self.resp[0], headers={"content-type": "application/json"},
                              body=self.resp[1])


class Ctx:
    pass


def _make(repo=None):
    repo = repo or FakeRepo()
    # extend fake repo with key/user lookups used by the app
    repo.users = {}
    repo.keys = {}

    async def user_for_key_hash(h):
        return repo.keys.get(bytes(h))
    repo.user_for_key_hash = user_for_key_hash

    async def bump_usage(*a, **k):
        pass
    repo.bump_usage = bump_usage

    ctx = Ctx()
    ctx.repo = repo
    ctx.cache = FakeCache()
    ctx.sink = FakeSink()
    ctx.upstream = FakeUpstream()
    ctx.binding = BindingService(repo)
    ctx.forwarder = Forwarder(ctx.binding, ctx.upstream, repo)
    ctx.device_flow = None
    ctx.admin_token = None
    ctx.pending_logins = {}
    return ctx, repo


def _seed_user_key(repo, user_id="u1", key="ghcp_testkey"):
    repo.keys[hash_api_key(key)] = user_id
    return key


def test_healthz():
    ctx, _ = _make()
    client = TestClient(create_app(ctx))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_auth_returns_401():
    ctx, _ = _make()
    client = TestClient(create_app(ctx))
    r = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []})
    assert r.status_code == 401


def test_unknown_key_returns_401():
    ctx, _ = _make()
    client = TestClient(create_app(ctx))
    r = client.post("/v1/chat/completions",
                    headers={"authorization": "Bearer ghcp_nope"},
                    json={"model": "gpt-4o", "messages": []})
    assert r.status_code == 401


def test_chat_completion_happy_path_and_usage_emitted():
    ctx, repo = _make()
    repo.add_account("acc1", status="idle")
    key = _seed_user_key(repo)
    client = TestClient(create_app(ctx))
    r = client.post("/v1/chat/completions",
                    headers={"authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.json()["model"] == "gpt-4o"
    assert ctx.sink.usage_events[0]["prompt_tokens"] == 5


def test_no_account_returns_503():
    ctx, repo = _make()
    key = _seed_user_key(repo)  # no accounts in pool
    client = TestClient(create_app(ctx))
    r = client.post("/v1/messages",
                    headers={"x-api-key": key},
                    json={"model": "claude-sonnet-4.5", "messages": []})
    assert r.status_code == 503
    assert r.headers.get("Retry-After") == "30"


def test_streaming_no_account_returns_503_not_500():
    # regression: NoAccountAvailable in the streaming path must yield 503,
    # not escape the lazy generator and surface as a 500.
    ctx, repo = _make()
    key = _seed_user_key(repo)  # no accounts
    client = TestClient(create_app(ctx))
    r = client.post("/v1/chat/completions",
                    headers={"authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o", "messages": [], "stream": True})
    assert r.status_code == 503
    assert r.headers.get("Retry-After") == "30"


def test_admin_closed_by_default_when_no_token():
    # regression: with no admin token configured the admin API must be CLOSED.
    ctx, repo = _make()
    ctx.admin_token = None
    client = TestClient(create_app(ctx))
    r = client.post("/admin/users", json={"external_id": "x"})
    assert r.status_code == 403


def test_admin_requires_matching_token_when_set():
    ctx, repo = _make()
    ctx.admin_token = "secret"
    client = TestClient(create_app(ctx))
    assert client.post("/admin/users", json={"external_id": "x"}).status_code == 403
    assert client.post("/admin/users", headers={"x-admin-token": "wrong"},
                       json={"external_id": "x"}).status_code == 403


def test_anthropic_via_x_api_key_header():
    ctx, repo = _make()
    repo.add_account("acc1", status="idle")
    ctx.upstream.resp = (200, b'{"model":"claude-sonnet-4-5","usage":{"input_tokens":9,"output_tokens":3}}')
    key = _seed_user_key(repo)
    client = TestClient(create_app(ctx))
    r = client.post("/v1/messages",
                    headers={"x-api-key": key},
                    json={"model": "claude-sonnet-4.5", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert ctx.sink.usage_events[0]["completion_tokens"] == 3
