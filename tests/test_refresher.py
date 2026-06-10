"""Tests for the refresher's liveness-validation decisions.

The HTTP call and DB are faked; we assert the policy: a 401 quarantines the
account, a 200 marks it seen, and a network error does NOT quarantine.
"""
import pytest

from ghcproxy.credential.refresher import Refresher
from tests.fakes import FakeRepo, AccountRow


class FakeHttp:
    def __init__(self, status=None, body=b"{}", raise_exc=None):
        self.status = status
        self.body = body
        self.raise_exc = raise_exc

    async def get(self, url, headers=None, timeout=None):
        if self.raise_exc:
            raise self.raise_exc

        class R:
            status_code = self.status
            content = self.body
        return R()


class FakeSink:
    def __init__(self):
        self.audits = []

    async def audit(self, e):
        self.audits.append(e)


class FakeCtx:
    def __init__(self, repo, http, sink):
        from ghcproxy.common.config import Settings
        self.repo = repo
        self.http = http
        self.sink = sink
        self.cfg = Settings()
        self.cache = None


def _account():
    return AccountRow(id="acc1", login="acct", oauth_token="gho_x",
                      api_base="https://api.enterprise.githubcopilot.com")


async def test_healthy_account_marked_seen():
    repo = FakeRepo()
    repo.add_account("acc1", status="bound")
    ctx = FakeCtx(repo, FakeHttp(status=200, body=b'{"data":[]}'), FakeSink())
    ok = await Refresher(ctx).validate_account(_account())
    assert ok is True
    assert repo.accounts["acc1"].status == "bound"  # not quarantined


async def test_expired_login_quarantines():
    repo = FakeRepo()
    repo.add_account("acc1", status="bound")
    sink = FakeSink()
    ctx = FakeCtx(repo, FakeHttp(status=401, body=b"bad creds"), sink)
    ok = await Refresher(ctx).validate_account(_account())
    assert ok is False
    assert repo.accounts["acc1"].status == "quarantined"
    assert sink.audits and sink.audits[0]["event"] == "quarantine"


async def test_network_error_does_not_quarantine():
    repo = FakeRepo()
    repo.add_account("acc1", status="bound")
    ctx = FakeCtx(repo, FakeHttp(raise_exc=ConnectionError("boom")), FakeSink())
    ok = await Refresher(ctx).validate_account(_account())
    assert ok is True
    assert repo.accounts["acc1"].status == "bound"


def test_heartbeat_written(tmp_path, monkeypatch):
    import ghcproxy.credential.refresher as r
    hb = tmp_path / "hb"
    monkeypatch.setattr(r, "HEARTBEAT_PATH", str(hb))
    ctx = FakeCtx(FakeRepo(), FakeHttp(), FakeSink())
    r.Refresher(ctx)._heartbeat()
    assert hb.exists()
    assert float(hb.read_text()) > 0
