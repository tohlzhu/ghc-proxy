"""Tests for the core forward-with-rebind-retry logic.

The Forwarder ties together binding + credential headers + upstream call. On
an upstream login-expiry it must quarantine the dead account, rebind the user
to a fresh account, and retry exactly once. Rate-limit / server errors must
pass through untouched.
"""
import pytest

from ghcproxy.proxy.forwarder import Forwarder, UpstreamResult
from ghcproxy.router.binding import BindingService, NoAccountAvailable
from tests.fakes import FakeRepo


class FakeUpstream:
    """Records calls; returns queued (status, body) by account_id."""

    def __init__(self):
        self.by_account: dict[str, tuple[int, bytes]] = {}
        self.calls: list[str] = []

    def set(self, account_id: str, status: int, body: bytes = b"{}"):
        self.by_account[account_id] = (status, body)

    async def send(self, *, account, path, method, headers, body, anthropic):
        self.calls.append(account.id)
        status, rbody = self.by_account[account.id]
        return UpstreamResult(status=status, headers={}, body=rbody)


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def forwarder(repo):
    return Forwarder(BindingService(repo), FakeUpstream(), repo)


async def test_happy_path_forwards_to_bound_account(repo):
    up = FakeUpstream()
    fwd = Forwarder(BindingService(repo), up, repo)
    repo.add_account("acc1", status="idle")
    up.set("acc1", 200, b'{"ok":true}')

    res = await fwd.handle("userA", path="/v1/chat/completions", method="POST",
                           headers={}, body=b"{}", anthropic=False)
    assert res.status == 200
    assert up.calls == ["acc1"]


async def test_login_expiry_quarantines_and_retries_on_new_account(repo):
    up = FakeUpstream()
    fwd = Forwarder(BindingService(repo), up, repo)
    repo.add_account("acc1", status="idle")
    repo.add_account("acc2", status="idle")
    up.set("acc1", 401, b"bad creds")   # first bound account is dead
    up.set("acc2", 200, b'{"ok":true}') # replacement works

    res = await fwd.handle("userA", path="/v1/messages", method="POST",
                           headers={}, body=b"{}", anthropic=True)
    assert res.status == 200
    assert up.calls == ["acc1", "acc2"]            # retried once
    assert repo.accounts["acc1"].status == "quarantined"
    assert repo.accounts["acc2"].status == "bound"


async def test_login_expiry_with_no_spare_raises(repo):
    up = FakeUpstream()
    fwd = Forwarder(BindingService(repo), up, repo)
    repo.add_account("acc1", status="idle")
    up.set("acc1", 401, b"bad creds")

    with pytest.raises(NoAccountAvailable):
        await fwd.handle("userA", path="/v1/messages", method="POST",
                         headers={}, body=b"{}", anthropic=True)
    assert repo.accounts["acc1"].status == "quarantined"


async def test_rate_limit_passes_through_without_quarantine(repo):
    up = FakeUpstream()
    fwd = Forwarder(BindingService(repo), up, repo)
    repo.add_account("acc1", status="idle")
    up.set("acc1", 429, b"slow down")

    res = await fwd.handle("userA", path="/v1/chat/completions", method="POST",
                           headers={}, body=b"{}", anthropic=False)
    assert res.status == 429
    assert repo.accounts["acc1"].status == "bound"   # NOT quarantined
    assert up.calls == ["acc1"]                       # no retry


async def test_no_account_available_for_new_user(repo):
    up = FakeUpstream()
    fwd = Forwarder(BindingService(repo), up, repo)
    with pytest.raises(NoAccountAvailable):
        await fwd.handle("userA", path="/v1/models", method="GET",
                         headers={}, body=b"", anthropic=False)
