"""End-to-end API tests using FastAPI's TestClient against fake infra.

Exercises the real app wiring (auth -> bind -> forward -> response, usage
emit) with an in-memory repo/cache/sink and a fake upstream, so no Postgres/
Redis/Kafka or network is required.
"""
import pytest
from fastapi.testclient import TestClient

from ghcproxy.credential.device_flow import DeviceCode
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
        self.prompt_events = []
        self.audit_events = []

    async def usage(self, e):
        self.usage_events.append(e)

    async def prompt(self, e):
        self.prompt_events.append(e)

    async def audit(self, e):
        self.audit_events.append(e)


class FakeStreamResp:
    def __init__(self, status_code=200, chunks=None, headers=None):
        self.status_code = status_code
        self._chunks = chunks or [b'data: {"model":"gpt-4o","usage":{"prompt_tokens":4,'
                                  b'"completion_tokens":1}}\n\n']
        self.headers = headers or {"content-type": "text/event-stream"}
        self.closed = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b"".join(self._chunks)

    async def aclose(self):
        self.closed = True


class FakeUpstream:
    def __init__(self):
        self.resp = (200, b'{"model":"gpt-4o","usage":{"prompt_tokens":5,"completion_tokens":2}}')
        self.stream_by_account = {}
        self.stream_calls = []

    async def send(self, *, account, path, method, headers, body, anthropic):
        return UpstreamResult(status=self.resp[0], headers={"content-type": "application/json"},
                              body=self.resp[1])

    def stream(self, *, account, path, method, headers, body, anthropic):
        upstream = self

        class CM:
            async def __aenter__(self):
                upstream.stream_calls.append(account.id)
                return upstream.stream_by_account.get(account.id, FakeStreamResp())

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return CM()


class FakeDeviceFlow:
    async def request_device_code(self):
        return DeviceCode("DEV", "ABCD-1234", "https://github.com/login/device", 5, 900)

    async def poll_once(self, device_code):
        assert device_code == "DEV"
        return "gho_AUTHORIZED"


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
        repo.usage.append(a)
    repo.bump_usage = bump_usage
    repo.usage = []

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
    assert ctx.sink.usage_events[0]["account_id"] == "acc1"
    assert ctx.sink.prompt_events[0]["request"]["messages"][0]["content"] == "hi"
    assert repo.usage[0][1] == "acc1"


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


def test_streaming_preserves_upstream_error_status():
    ctx, repo = _make()
    repo.add_account("acc1", status="idle")
    ctx.upstream.stream_by_account["acc1"] = FakeStreamResp(
        status_code=429, chunks=[b"slow down"],
        headers={"content-type": "application/json"})
    key = _seed_user_key(repo)
    client = TestClient(create_app(ctx))
    r = client.post("/v1/chat/completions",
                    headers={"authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o", "messages": [], "stream": True})
    assert r.status_code == 429
    assert r.content == b"slow down"


def test_streaming_login_expiry_rebinds_and_retries():
    ctx, repo = _make()
    repo.add_account("acc1", status="idle")
    repo.add_account("acc2", status="idle")
    ctx.upstream.stream_by_account["acc1"] = FakeStreamResp(
        status_code=401, chunks=[b'{"message":"Bad credentials"}'],
        headers={"content-type": "application/json"})
    ctx.upstream.stream_by_account["acc2"] = FakeStreamResp()
    key = _seed_user_key(repo)
    client = TestClient(create_app(ctx))
    r = client.post("/v1/chat/completions",
                    headers={"authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o", "messages": [], "stream": True})
    assert r.status_code == 200
    assert ctx.upstream.stream_calls == ["acc1", "acc2"]
    assert repo.accounts["acc1"].status == "quarantined"
    assert repo.accounts["acc2"].status == "bound"
    assert ctx.sink.usage_events[0]["account_id"] == "acc2"


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


def test_admin_device_flow_start_and_poll_persists_authorized_account():
    ctx, repo = _make()
    ctx.admin_token = "secret"
    ctx.device_flow = FakeDeviceFlow()
    client = TestClient(create_app(ctx))

    start = client.post("/admin/accounts/octo/login/start",
                        headers={"x-admin-token": "secret"})
    assert start.status_code == 200
    assert start.json()["user_code"] == "ABCD-1234"

    poll = client.post("/admin/accounts/octo/login/poll",
                       headers={"x-admin-token": "secret"})
    assert poll.status_code == 200
    account_id = poll.json()["account_id"]
    assert repo.accounts[account_id].oauth_token == "gho_AUTHORIZED"
    assert repo.accounts[account_id].status == "idle"
    assert ctx.sink.audit_events[0]["event"] == "login_authorized"


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
