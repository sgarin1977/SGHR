from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_grants import ConstructionAccessContextResolver, ConstructionGrantOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_access_mutation_workflow import pipeline, snapshot


@pytest.mark.asyncio
async def test_resolver_excludes_grant_expired_while_query_waited():
    state = SimpleNamespace(now=datetime(2026, 10, 7, 12, tzinfo=timezone.utc))
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())
    deadline = state.now+timedelta(seconds=1)
    async def rows(**values):
        assert values["as_of"] < deadline
        state.now += timedelta(seconds=2)
        return [SimpleNamespace(
            role="OWNER", scope_type="tenant", scope_id=actor.tenant_id,
            status="active", expires_at=deadline,
        )]
    result = await ConstructionAccessContextResolver(
        repository=SimpleNamespace(list_active_grants=rows), now_provider=lambda: state.now,
    ).resolve(actor=actor)
    assert result.roles == result.permissions == result.grants == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline,status", [("future", "active"), ("none", "active"), ("now", "active"), ("future", "revoked")])
async def test_resolver_preserves_only_current_effective_grants(deadline, status):
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())
    expiry = {"future": now+timedelta(hours=1), "none": None, "now": now}[deadline]
    repo = SimpleNamespace(list_active_grants=AsyncMock(return_value=[SimpleNamespace(
        role="ADMIN", scope_type="tenant", scope_id=actor.tenant_id, status=status, expires_at=expiry,
    )]))
    result = await ConstructionAccessContextResolver(repository=repo, now_provider=lambda: now).resolve(actor=actor)
    assert bool(result.roles) is (status == "active" and deadline != "now")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["clock", "deadline"])
async def test_resolver_fails_closed_on_invalid_time_metadata(failure):
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())
    repo = SimpleNamespace(list_active_grants=AsyncMock(return_value=[SimpleNamespace(
        role="OWNER", scope_type="tenant", scope_id=actor.tenant_id, status="active",
        expires_at=now.replace(tzinfo=None) if failure == "deadline" else None,
    )]))
    with pytest.raises(ConstructionGrantOperationError):
        await ConstructionAccessContextResolver(
            repository=repo, now_provider=lambda: now.replace(tzinfo=None) if failure == "clock" else now,
        ).resolve(actor=actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", ["membership", "permissions_query"])
async def test_replace_denies_actor_access_expired_during_awaited_checks(pipeline, wait):
    c = pipeline
    before = snapshot(c)
    deadline = c.now+timedelta(seconds=1)
    reads = 0
    async def current_grants(**values):
        nonlocal reads
        reads += 1
        if wait == "permissions_query" and reads == 1:
            c.now += timedelta(seconds=2)
        if values["as_of"] >= deadline:
            return []
        return [SimpleNamespace(
            role="OWNER", scope_type="tenant", scope_id=c.actor.tenant_id,
            status="active", expires_at=deadline,
        )]
    c.repo.list_active_grants.side_effect = current_grants
    if wait == "membership":
        async def membership(**values):
            c.now += timedelta(seconds=2)
            return True
        c.repo.has_active_platform_membership.side_effect = membership
    c.db.execute("BEGIN")
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await c.workflow.replace_access(
            actor=c.actor, grant_id=c.original.id, role="SURVEYOR",
            scope_type="project", scope_id=c.project_id, idempotency_key="wait-key",
        )
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()
    c.repo.revoke_if_effective.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_replace_denies_scope_rights_removed_while_membership_was_checked(pipeline):
    c = pipeline
    before = snapshot(c)
    async def membership(**values):
        c.authorized = False
        return True
    c.repo.has_active_platform_membership.side_effect = membership
    c.db.execute("BEGIN")
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await c.workflow.replace_access(
            actor=c.actor, grant_id=c.original.id, role="SURVEYOR",
            scope_type="project", scope_id=c.project_id, idempotency_key="wait-key",
        )
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()
    c.repo.revoke_if_effective.assert_not_called()
