"""Tests for the GitHub device-flow login state machine.

The browser step is performed by a human; we only test the proxy-side
mechanics: requesting a device code, surfacing the user_code, and polling the
token endpoint until authorization completes (handling authorization_pending
and slow_down).
"""
import pytest

from ghcproxy.common.config import DeviceFlowConfig
from ghcproxy.credential.device_flow import (
    DeviceFlow,
    DeviceCode,
    DeviceFlowError,
    AuthorizationPending,
)


class FakeHTTP:
    """Queue of (status, json) responses keyed by URL substring."""

    def __init__(self):
        self.queues: dict[str, list] = {}
        self.calls: list[tuple[str, dict]] = []

    def enqueue(self, url_part: str, status: int, body: dict):
        self.queues.setdefault(url_part, []).append((status, body))

    async def post_form(self, url: str, data: dict, headers: dict) -> tuple[int, dict]:
        self.calls.append((url, data))
        for part, q in self.queues.items():
            if part in url and q:
                return q.pop(0)
        raise AssertionError(f"no fake response for {url}")


@pytest.fixture
def http():
    return FakeHTTP()


@pytest.fixture
def flow(http):
    return DeviceFlow(DeviceFlowConfig(), http)


async def test_request_device_code_returns_user_code(flow, http):
    http.enqueue("device/code", 200, {
        "device_code": "DEV", "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "interval": 5, "expires_in": 900,
    })
    dc = await flow.request_device_code()
    assert isinstance(dc, DeviceCode)
    assert dc.user_code == "ABCD-1234"
    assert dc.verification_uri == "https://github.com/login/device"
    assert dc.device_code == "DEV"


async def test_poll_pending_raises_authorization_pending(flow, http):
    http.enqueue("access_token", 200, {"error": "authorization_pending"})
    with pytest.raises(AuthorizationPending):
        await flow.poll_once("DEV")


async def test_poll_success_returns_gho_token(flow, http):
    http.enqueue("access_token", 200, {
        "access_token": "gho_REALTOKEN", "token_type": "bearer", "scope": "read:user",
    })
    token = await flow.poll_once("DEV")
    assert token == "gho_REALTOKEN"


async def test_poll_error_other_than_pending_raises(flow, http):
    http.enqueue("access_token", 200, {"error": "access_denied"})
    with pytest.raises(Exception) as ei:
        await flow.poll_once("DEV")
    assert "access_denied" in str(ei.value)


async def test_request_device_code_sends_client_id_and_scope(flow, http):
    http.enqueue("device/code", 200, {
        "device_code": "D", "user_code": "U", "verification_uri": "v",
        "interval": 5, "expires_in": 900,
    })
    await flow.request_device_code()
    url, data = http.calls[-1]
    assert data["client_id"] == DeviceFlowConfig().client_id
    assert data["scope"] == DeviceFlowConfig().scope


async def test_request_device_code_rejects_placeholder_client_id():
    """A placeholder client_id (e.g. the docs default 'Iv1.<CLIENT_ID>') would
    make GitHub return 404 with an opaque body. Catch it before the network and
    raise a clear, actionable error so the operator knows to configure
    GHCPROXY_DEVICE_FLOW__CLIENT_ID."""
    http = FakeHTTP()
    flow = DeviceFlow(DeviceFlowConfig(client_id="Iv1.<CLIENT_ID>"), http)
    with pytest.raises(DeviceFlowError) as ei:
        await flow.request_device_code()
    assert "client_id" in str(ei.value).lower()
    # must NOT have hit the network with a bogus client id
    assert http.calls == []


# ---------------------------------------------------------------------------
# _HttpxForm adapter: transport errors must surface as DeviceFlowError so the
# admin endpoint can map them to a clean 502 (not bubble up as an opaque 500).
# This is the failure mode seen when the host has no egress to github.com.
# ---------------------------------------------------------------------------

class _RaisingClient:
    """Fake httpx client whose post() raises a transport error."""

    def __init__(self, exc):
        self._exc = exc

    async def post(self, url, data=None, headers=None):
        raise self._exc


async def test_httpxform_translates_transport_error_to_device_flow_error():
    import httpx

    from ghcproxy.context import _HttpxForm

    form = _HttpxForm(_RaisingClient(httpx.ConnectTimeout("timed out")))
    with pytest.raises(DeviceFlowError) as ei:
        await form.post_form("https://github.com/login/device/code", {}, {})
    msg = str(ei.value).lower()
    assert "github" in msg or "reach" in msg or "network" in msg


async def test_request_device_code_surfaces_transport_error_as_device_flow_error():
    """End-to-end at the DeviceFlow level: a transport failure from the http
    adapter must come out as DeviceFlowError, which start_login maps to 502."""
    import httpx

    from ghcproxy.context import _HttpxForm

    flow = DeviceFlow(
        DeviceFlowConfig(),  # valid default client_id, so we reach the network
        _HttpxForm(_RaisingClient(httpx.ConnectError("no route"))),
    )
    with pytest.raises(DeviceFlowError):
        await flow.request_device_code()
