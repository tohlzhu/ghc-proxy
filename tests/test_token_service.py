"""Tests for the two-tier Copilot token service.

GHC has two auth shapes decided by which OAuth client minted the credential:

* **Editor client** (``Iv1.*`` GitHub App → ``ghu_``): the durable token MUST be
  exchanged at ``GET api.github.com/copilot_internal/v2/token`` for a short-lived
  (~30 min) Copilot bearer (token B), refreshed before expiry.
* **CLI client** (``Ov23*`` OAuth App → ``gho_``): the exchange endpoint returns
  **404**; the durable token is used DIRECTLY as the model-API bearer.

The service supports BOTH: it tries the exchange, falls back to direct-bearer on
404, caches the result, and refreshes token B before it expires. A 401/403 on the
exchange means the durable login is dead and must propagate as ``CopilotAuthExpired``
(so the forwarder quarantines + rebinds).
"""
import pytest

from ghcproxy.common.config import UpstreamConfig
from ghcproxy.credential.token_service import (
    CopilotAuthExpired,
    CopilotTokenService,
    CopilotTokenUnavailable,
)


class FakeResp:
    def __init__(self, status_code, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeHttp:
    """Records GET calls; returns queued responses in order (or a fixed one).

    A queued item may be an Exception instance, which is raised (simulating a
    network failure).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        item = self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Acct:
    def __init__(self, account_id="acc1", oauth_token="ghu_DURABLE"):
        self.id = account_id
        self.oauth_token = oauth_token


def _svc(responses, *, clock=None, **kw):
    clock = clock or Clock()
    return CopilotTokenService(UpstreamConfig(), FakeHttp(responses), clock=clock, **kw), clock


async def test_exchange_returns_short_lived_token_b():
    clock = Clock(1000.0)
    svc, _ = _svc([FakeResp(200, {"token": "tokB", "expires_at": 1000 + 1800})], clock=clock)
    bearer = await svc.bearer_for(Acct())
    assert bearer == "tokB"


async def test_exchange_uses_token_scheme_not_bearer_for_durable_token():
    # The EXCHANGE call authenticates with `Authorization: token <durable>`,
    # NOT Bearer — verified against litellm / copilot-api / stencila.
    svc, _ = _svc([FakeResp(200, {"token": "tokB", "expires_at": 9999999999})])
    await svc.bearer_for(Acct(oauth_token="ghu_DURABLE"))
    assert svc._http.calls[0]["headers"]["Authorization"] == "token ghu_DURABLE"
    assert "/copilot_internal/v2/token" in svc._http.calls[0]["url"]


async def test_token_b_is_cached_until_near_expiry():
    clock = Clock(1000.0)
    svc, _ = _svc([FakeResp(200, {"token": "tokB", "expires_at": 1000 + 1800})], clock=clock)
    await svc.bearer_for(Acct())
    clock.t = 1000 + 100        # well before expiry
    await svc.bearer_for(Acct())
    assert len(svc._http.calls) == 1   # served from cache, no second exchange


async def test_token_b_refreshes_within_skew_window():
    clock = Clock(1000.0)
    svc, _ = _svc(
        [FakeResp(200, {"token": "tokB1", "expires_at": 1000 + 1800}),
         FakeResp(200, {"token": "tokB2", "expires_at": 1000 + 3600})],
        clock=clock, skew_s=120)
    assert await svc.bearer_for(Acct()) == "tokB1"
    clock.t = 1000 + 1800 - 60   # inside the 120s pre-expiry skew window
    assert await svc.bearer_for(Acct()) == "tokB2"
    assert len(svc._http.calls) == 2


async def test_404_falls_back_to_direct_bearer():
    # CLI-minted gho_ token: exchange 404s, durable token is the bearer.
    svc, _ = _svc([FakeResp(404, content=b'{"message":"Not Found"}')])
    bearer = await svc.bearer_for(Acct(oauth_token="gho_CLI"))
    assert bearer == "gho_CLI"


async def test_direct_mode_is_cached_and_not_reprobed_each_call():
    clock = Clock(1000.0)
    svc, _ = _svc([FakeResp(404)], clock=clock, direct_ttl_s=1800)
    await svc.bearer_for(Acct(oauth_token="gho_CLI"))
    clock.t = 1000 + 100
    await svc.bearer_for(Acct(oauth_token="gho_CLI"))
    assert len(svc._http.calls) == 1   # direct decision cached, no re-probe


async def test_401_on_exchange_raises_auth_expired():
    svc, _ = _svc([FakeResp(401, content=b'{"message":"Bad credentials"}')])
    with pytest.raises(CopilotAuthExpired):
        await svc.bearer_for(Acct(oauth_token="ghu_DEAD"))


async def test_403_on_exchange_raises_auth_expired():
    svc, _ = _svc([FakeResp(403, content=b'{"error":"forbidden"}')])
    with pytest.raises(CopilotAuthExpired):
        await svc.bearer_for(Acct(oauth_token="ghu_DEAD"))


async def test_500_on_exchange_is_transient_not_auth_expired():
    # A flaky GitHub API must NOT be mistaken for a dead login (no quarantine).
    svc, _ = _svc([FakeResp(500, content=b"server error")])
    with pytest.raises(CopilotTokenUnavailable):
        await svc.bearer_for(Acct())


async def test_429_on_exchange_is_transient():
    svc, _ = _svc([FakeResp(429, content=b"rate limited")])
    with pytest.raises(CopilotTokenUnavailable):
        await svc.bearer_for(Acct())


async def test_network_error_on_exchange_is_transient():
    svc, _ = _svc([ConnectionError("boom")])
    with pytest.raises(CopilotTokenUnavailable):
        await svc.bearer_for(Acct())


async def test_transient_error_does_not_poison_cache():
    # After a transient failure, a subsequent success must populate the cache.
    svc, _ = _svc([FakeResp(500), FakeResp(200, {"token": "tokB", "expires_at": 9999999999})])
    with pytest.raises(CopilotTokenUnavailable):
        await svc.bearer_for(Acct())
    assert await svc.bearer_for(Acct()) == "tokB"


async def test_expires_at_missing_falls_back_to_refresh_in():
    clock = Clock(1000.0)
    svc, _ = _svc([FakeResp(200, {"token": "tokB", "refresh_in": 1500})], clock=clock)
    await svc.bearer_for(Acct())
    # next call within refresh_in - skew is cached
    clock.t = 1000 + 100
    await svc.bearer_for(Acct())
    assert len(svc._http.calls) == 1


async def test_locks_do_not_accumulate_after_auth_expiry():
    # After an exchange 401 (CopilotAuthExpired), the per-account lock must not
    # linger in _locks — otherwise it leaks one Lock per dead account.
    svc, _ = _svc([FakeResp(401, content=b"bad")])
    with pytest.raises(CopilotAuthExpired):
        await svc.bearer_for(Acct(oauth_token="ghu_DEAD"))
    assert "acc1" not in svc._locks


async def test_lock_retained_while_bearer_cached():
    # A live cached entry means the lock still guards it; keep it.
    svc, _ = _svc([FakeResp(200, {"token": "tokB", "expires_at": 9999999999})])
    await svc.bearer_for(Acct())
    assert "acc1" in svc._locks   # guards the cached token B


async def test_invalidate_forces_reexchange():
    clock = Clock(1000.0)
    svc, _ = _svc(
        [FakeResp(200, {"token": "tokB1", "expires_at": 1000 + 1800}),
         FakeResp(200, {"token": "tokB2", "expires_at": 1000 + 1800})],
        clock=clock)
    assert await svc.bearer_for(Acct()) == "tokB1"
    svc.invalidate("acc1")
    assert await svc.bearer_for(Acct()) == "tokB2"
    assert len(svc._http.calls) == 2
