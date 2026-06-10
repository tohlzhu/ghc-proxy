"""Tests for the 1:1 sticky user<->account binding logic.

The binding service is tested against an in-memory fake repository that
emulates the transactional guarantees the real Postgres repo provides
(atomic idle-account claim, double-unique binding rows). This keeps the
policy logic isolated from SQL.
"""
import pytest

from ghcproxy.router.binding import BindingService, NoAccountAvailable
from tests.fakes import FakeRepo


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def svc(repo):
    return BindingService(repo)


async def test_first_request_binds_user_to_idle_account(svc, repo):
    repo.add_account("acc1", status="idle")
    binding = await svc.get_or_bind("userA")
    assert binding.account_id == "acc1"
    assert repo.accounts["acc1"].status == "bound"


async def test_binding_is_sticky_same_account_returned(svc, repo):
    repo.add_account("acc1", status="idle")
    repo.add_account("acc2", status="idle")
    first = await svc.get_or_bind("userA")
    second = await svc.get_or_bind("userA")
    assert first.account_id == second.account_id


async def test_two_users_get_different_accounts(svc, repo):
    repo.add_account("acc1", status="idle")
    repo.add_account("acc2", status="idle")
    a = await svc.get_or_bind("userA")
    b = await svc.get_or_bind("userB")
    assert a.account_id != b.account_id


async def test_no_idle_account_raises(svc, repo):
    repo.add_account("acc1", status="idle")
    await svc.get_or_bind("userA")  # consumes the only account
    with pytest.raises(NoAccountAvailable):
        await svc.get_or_bind("userB")


async def test_quarantined_account_not_assigned(svc, repo):
    repo.add_account("acc1", status="quarantined")
    with pytest.raises(NoAccountAvailable):
        await svc.get_or_bind("userA")


async def test_rebind_after_quarantine_moves_user_to_new_account(svc, repo):
    repo.add_account("acc1", status="idle")
    repo.add_account("acc2", status="idle")
    first = await svc.get_or_bind("userA")
    # the account backing userA dies
    new = await svc.rebind_away_from(first.account_id, "userA")
    assert new.account_id != first.account_id
    assert repo.accounts[first.account_id].status == "quarantined"
    assert repo.accounts[new.account_id].status == "bound"


async def test_rebind_with_no_spare_raises_and_keeps_user_unbound(svc, repo):
    repo.add_account("acc1", status="idle")
    first = await svc.get_or_bind("userA")
    with pytest.raises(NoAccountAvailable):
        await svc.rebind_away_from(first.account_id, "userA")
    # dead account is quarantined even though no replacement was found
    assert repo.accounts["acc1"].status == "quarantined"
