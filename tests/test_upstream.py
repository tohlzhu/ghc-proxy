"""Tests for HttpxUpstream wiring: bearer resolution + dynamic headers.

The upstream client must (a) resolve the bearer through the token service
(two-tier exchange or direct), not blindly send the durable token, and
(b) derive ``X-Initiator`` / ``Copilot-Vision-Request`` from the request body.
"""
import httpx
import pytest

from ghcproxy.common.config import UpstreamConfig
from ghcproxy.proxy.upstream import HttpxUpstream


class FakeTokenService:
    def __init__(self, bearer="tokB", raise_exc=None):
        self.bearer = bearer
        self.raise_exc = raise_exc
        self.calls = []
        self.invalidated = []

    async def bearer_for(self, account):
        self.calls.append(account.id)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.bearer

    def invalidate(self, account_id):
        self.invalidated.append(account_id)


class Acct:
    def __init__(self, account_id="acc1", oauth_token="ghu_DURABLE",
                 api_base="https://api.enterprise.githubcopilot.com"):
        self.id = account_id
        self.oauth_token = oauth_token
        self.api_base = api_base


def _client_capturing(captured, status=200, body=b'{"ok":true}'):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(status, content=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def captured():
    return {}


async def test_send_uses_token_service_bearer_not_durable_token(captured):
    ts = FakeTokenService(bearer="tokB-short-lived")
    client = _client_capturing(captured)
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    await up.send(account=Acct(oauth_token="ghu_DURABLE"),
                  path="/chat/completions", method="POST", headers={},
                  body=b'{"messages":[{"role":"user","content":"hi"}]}',
                  anthropic=False)
    assert captured["headers"]["authorization"] == "Bearer tokB-short-lived"
    assert ts.calls == ["acc1"]
    await client.aclose()


async def test_send_derives_agent_initiator_from_body(captured):
    ts = FakeTokenService()
    client = _client_capturing(captured)
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    body = b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"x"}]}'
    await up.send(account=Acct(), path="/chat/completions", method="POST",
                  headers={}, body=body, anthropic=False)
    assert captured["headers"]["x-initiator"] == "agent"
    await client.aclose()


async def test_send_sets_vision_header_when_image_present(captured):
    ts = FakeTokenService()
    client = _client_capturing(captured)
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    body = (b'{"messages":[{"role":"user","content":['
            b'{"type":"image_url","image_url":{"url":"data:image/png;base64,A"}}]}]}')
    await up.send(account=Acct(), path="/chat/completions", method="POST",
                  headers={}, body=body, anthropic=False)
    assert captured["headers"]["copilot-vision-request"] == "true"
    await client.aclose()


async def test_send_invalidates_token_cache_on_401(captured):
    # A 401 means the resolved bearer is dead — drop it so the rebind/retry
    # re-resolves rather than reusing a stale token B.
    ts = FakeTokenService()
    client = _client_capturing(captured, status=401, body=b'{"message":"Bad credentials"}')
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    res = await up.send(account=Acct(), path="/chat/completions", method="POST",
                        headers={}, body=b'{"messages":[]}', anthropic=False)
    assert res.status == 401
    assert ts.invalidated == ["acc1"]
    await client.aclose()


async def test_auth_expired_becomes_401_for_rebind_retry(captured):
    # CopilotAuthExpired (dead durable login) must surface as a synthetic 401
    # so the Forwarder's existing quarantine + rebind-retry logic kicks in —
    # not propagate as an unhandled 500.
    from ghcproxy.credential.token_service import CopilotAuthExpired
    ts = FakeTokenService(raise_exc=CopilotAuthExpired("dead"))
    client = _client_capturing(captured)
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    res = await up.send(account=Acct(), path="/chat/completions", method="POST",
                        headers={}, body=b'{"messages":[]}', anthropic=False)
    assert res.status == 401
    await client.aclose()


async def test_transient_token_error_becomes_503_no_quarantine(captured):
    # CopilotTokenUnavailable (flaky exchange) must surface as 503 (transient),
    # NOT 401 — a 401 would quarantine a healthy account.
    from ghcproxy.credential.token_service import CopilotTokenUnavailable
    ts = FakeTokenService(raise_exc=CopilotTokenUnavailable("5xx"))
    client = _client_capturing(captured)
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    res = await up.send(account=Acct(), path="/chat/completions", method="POST",
                        headers={}, body=b'{"messages":[]}', anthropic=False)
    assert res.status == 503
    await client.aclose()


async def test_stream_auth_expired_yields_synthetic_401():
    # The streaming path must also surface a dead login as a 401-shaped response
    # (so the app's stream handler rebinds) rather than raising.
    from ghcproxy.credential.token_service import CopilotAuthExpired
    ts = FakeTokenService(raise_exc=CopilotAuthExpired("dead"))
    async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
        async with up.stream(account=Acct(), path="/chat/completions", method="POST",
                             headers={}, body=b'{"messages":[]}', anthropic=False) as resp:
            assert resp.status_code == 401
            assert await resp.aread()


async def test_stream_transient_yields_synthetic_503():
    from ghcproxy.credential.token_service import CopilotTokenUnavailable
    ts = FakeTokenService(raise_exc=CopilotTokenUnavailable("5xx"))
    async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
        async with up.stream(account=Acct(), path="/chat/completions", method="POST",
                             headers={}, body=b'{"messages":[]}', anthropic=False) as resp:
            assert resp.status_code == 503


async def test_send_targets_account_api_base(captured):
    ts = FakeTokenService()
    client = _client_capturing(captured)
    up = HttpxUpstream(UpstreamConfig(), client, token_service=ts)
    await up.send(account=Acct(api_base="https://api.individual.githubcopilot.com"),
                  path="/chat/completions", method="POST", headers={},
                  body=b'{"messages":[]}', anthropic=False)
    assert captured["url"] == "https://api.individual.githubcopilot.com/chat/completions"
    await client.aclose()
