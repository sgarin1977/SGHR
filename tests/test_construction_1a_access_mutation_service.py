from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_access_mutations import (
    ConstructionAccessMutationService, build_construction_access_mutation_service,
)
from services.construction_grants import ConstructionGrantOperationError
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


def context(actor, *grants):
    roles = tuple(sorted({grant.role for grant in grants}))
    permissions = tuple(sorted(set().union(*(CONSTRUCTION_ROLE_PERMISSIONS[role] for role in roles))))
    return ConstructionAccessContext(actor.user_id, actor.tenant_id, roles, permissions, grants)


@pytest.fixture
def case():
    c = SimpleNamespace(now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc), calls=[], events=[], created=[])
    c.actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status="active")
    c.access = context(c.actor, ConstructionGrantContext("OWNER", "tenant", c.actor.tenant_id))
    c.project_id = uuid4()
    c.grant = SimpleNamespace(
        id=uuid4(), tenant_id=c.actor.tenant_id, user_id=uuid4(), role="ESTIMATOR",
        scope_type="project", scope_id=c.project_id, expires_at=None,
        granted_at=c.now-timedelta(days=1), granted_by_user_id=uuid4(), status="active",
        revoked_at=None, revoked_by_user_id=None,
    )
    c.original = deepcopy(vars(c.grant))
    c.active_transaction = True
    async def rollback():
        c.calls.append("rollback")
        vars(c.grant).clear()
        vars(c.grant).update(deepcopy(c.original))
        c.events.clear()
        c.created.clear()
    c.session = SimpleNamespace(
        in_transaction=lambda: c.active_transaction,
        commit=AsyncMock(side_effect=AssertionError("Workflow owns commit.")),
        rollback=AsyncMock(side_effect=rollback),
    )
    async def lock(**values):
        c.calls.append("lock")
        assert values == {"tenant_id": c.actor.tenant_id, "grant_id": c.grant.id}
        return c.grant
    async def membership(**values):
        c.calls.append("membership")
        assert values == {"tenant_id": c.actor.tenant_id, "user_id": c.grant.user_id}
        return True
    async def revoke(**values):
        c.calls.append("revoke")
        assert values == {"tenant_id": c.actor.tenant_id, "grant_id": c.grant.id,
                          "revoked_by_user_id": c.actor.user_id, "as_of": c.now}
        c.grant.status = "revoked"
        c.grant.revoked_by_user_id = c.actor.user_id
        c.grant.revoked_at = values["as_of"]
        return c.grant
    async def create(**values):
        c.calls.append("create")
        result = SimpleNamespace(id=uuid4(), status="active", revoked_at=None, revoked_by_user_id=None, **values)
        c.created.append(result)
        return result
    async def project(**values):
        c.calls.append("project")
        return SimpleNamespace(id=values["project_id"], tenant_id=c.actor.tenant_id, deleted_at=None)
    async def event(**values):
        c.calls.append("audit")
        c.events.append(values)
        return SimpleNamespace(**values)
    c.repo = SimpleNamespace(
        session=c.session, get_grant_for_update=AsyncMock(side_effect=lock),
        list_active_grants=AsyncMock(side_effect=lambda **values: list(c.access.grants)),
        has_active_platform_membership=AsyncMock(side_effect=membership),
        revoke_if_effective=AsyncMock(side_effect=revoke), create_grant=AsyncMock(side_effect=create),
    )
    c.projects = SimpleNamespace(session=c.session, get_active_project_for_update=AsyncMock(side_effect=project))
    c.event_repo = SimpleNamespace(session=c.session, create_event=AsyncMock(side_effect=event))
    c.service = ConstructionAccessMutationService(
        session=c.session, repository=c.repo, event_repository=c.event_repo,
        project_repository=c.projects, now_provider=lambda: c.now,
    )
    return c


async def replace(c, **changes):
    values = dict(actor=c.actor, access_context=c.access, grant_id=c.grant.id, role="SURVEYOR",
                  scope_type="project", scope_id=c.project_id, trace_id="grant-replace-test")
    values.update(changes)
    return await c.service.replace_access(**values)


async def revoke(c):
    return await c.service.revoke_access(actor=c.actor, access_context=c.access, grant_id=c.grant.id)


def unchanged(c):
    assert vars(c.grant) == c.original and c.events == [] and c.created == []
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_replacement_preserves_history_and_prepares_two_audits_in_one_session(case):
    c = case
    result = await replace(c, role=" surveyor ", expires_at=c.now+timedelta(hours=2))
    assert c.calls == ["lock", "project", "membership", "revoke", "audit", "create", "audit"]
    assert result.id != c.grant.id and result.user_id == c.grant.user_id
    assert result.role == "SURVEYOR" and result.granted_at == c.now
    assert result.expires_at == c.now+timedelta(hours=2)
    for field in ("role", "scope_type", "scope_id", "expires_at", "granted_at", "granted_by_user_id"):
        assert getattr(c.grant, field) == c.original[field]
    assert c.grant.status == "revoked"
    assert [event["event_type"] for event in c.events] == [
        "construction_access_grant_revoked", "construction_access_grant_created",
    ]
    assert [event["entity_id"] for event in c.events] == [c.grant.id, result.id]
    assert all(event["tenant_id"] == c.actor.tenant_id and event["user_id"] == c.actor.user_id
               and event["trace_id"] == "grant-replace-test" for event in c.events)
    assert c.events[1]["payload"]["replaces_grant_id"] == str(c.grant.id)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_is_conditional_and_retry_creates_no_extra_audit(case):
    c = case
    assert await revoke(c) is c.grant
    assert [change["field"] for change in c.events[0]["payload"]["changes"]] == [
        "status", "revoked_by_user_id", "revoked_at",
    ]
    assert await revoke(c) is c.grant
    assert len(c.events) == 1 and c.repo.revoke_if_effective.await_count == 1
    c.session.commit.assert_not_called()
    c.repo.has_active_platform_membership.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["create", "first_audit", "second_audit"])
async def test_any_flush_failure_rolls_back_history_and_audits(case, failure):
    c = case
    if failure == "create":
        c.repo.create_grant.side_effect = RuntimeError("private SQL detail")
    else:
        count = 0
        original = c.event_repo.create_event.side_effect
        async def failing_event(**values):
            nonlocal count
            count += 1
            result = await original(**values)
            if count == (1 if failure == "first_audit" else 2):
                raise RuntimeError("private SQL detail")
            return result
        c.event_repo.create_event.side_effect = failing_event
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await replace(c)
    assert "private SQL detail" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["revoked", "expired", "conditional_miss"])
async def test_changed_original_is_conflict_and_never_creates_replacement(case, state):
    c = case
    if state == "revoked":
        c.grant.status = "revoked"
        c.grant.revoked_at = c.now
        c.grant.revoked_by_user_id = c.actor.user_id
    elif state == "expired":
        c.grant.expires_at = c.now
    else:
        c.repo.revoke_if_effective.side_effect = None
        c.repo.revoke_if_effective.return_value = None
    c.original = deepcopy(vars(c.grant))
    with pytest.raises(ConstructionAccessGrantConflictError) as caught:
        await replace(c)
    assert caught.value.status_code == 409 and caught.value.code == "ACCESS_GRANT_CONFLICT"
    c.repo.create_grant.assert_not_called()
    unchanged(c)


@pytest.mark.asyncio
async def test_clock_is_read_after_lock_and_membership_wait(case):
    c = case
    c.grant.expires_at = c.now+timedelta(seconds=1)
    c.original = deepcopy(vars(c.grant))
    async def membership(**values):
        c.now += timedelta(seconds=2)
        return True
    c.repo.has_active_platform_membership.side_effect = membership
    with pytest.raises(ConstructionAccessGrantConflictError):
        await replace(c)
    c.repo.revoke_if_effective.assert_not_called()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign", "inactive", "no_transaction",
                                    "no_permission", "foreign_context", "foreign_scope", "undelegable"])
async def test_authorization_denies_without_history_writes(case, failure):
    c = case
    if failure == "missing":
        c.repo.get_grant_for_update.side_effect = None
        c.repo.get_grant_for_update.return_value = None
    elif failure == "foreign":
        c.grant.tenant_id = uuid4()
    elif failure == "inactive":
        c.actor.status = "suspended"
    elif failure == "no_transaction":
        c.active_transaction = False
    elif failure == "no_permission":
        c.access = context(c.actor, ConstructionGrantContext("OBSERVER", "project", c.project_id))
    elif failure == "foreign_context":
        c.access = context(SimpleNamespace(user_id=uuid4(), tenant_id=c.actor.tenant_id),
                           ConstructionGrantContext("OWNER", "tenant", c.actor.tenant_id))
    elif failure == "foreign_scope":
        c.access = context(c.actor, ConstructionGrantContext("ADMIN", "project", uuid4()))
    else:
        c.access = context(c.actor, ConstructionGrantContext("ADMIN", "project", c.project_id),
                           ConstructionGrantContext("OWNER", "project", uuid4()))
    c.original = deepcopy(vars(c.grant))
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await replace(c, role="OWNER" if failure == "undelegable" else "SURVEYOR")
    c.repo.revoke_if_effective.assert_not_called()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["document", "foreign_tenant", "bad_role", "deleted_project",
                                    "foreign_project", "missing_membership", "naive", "past", "bad_expiry"])
async def test_replacement_target_is_validated_before_revoke(case, failure):
    c = case
    values = {}
    if failure == "document":
        values["scope_type"] = "document"
    elif failure == "foreign_tenant":
        values.update(role="ADMIN", scope_type="tenant", scope_id=uuid4())
    elif failure == "bad_role":
        values["role"] = "CUSTOM"
    elif failure in ("deleted_project", "foreign_project"):
        c.projects.get_active_project_for_update.side_effect = None
        c.projects.get_active_project_for_update.return_value = SimpleNamespace(
            id=c.project_id, tenant_id=uuid4() if failure == "foreign_project" else c.actor.tenant_id,
            deleted_at=c.now if failure == "deleted_project" else None,
        )
    elif failure == "missing_membership":
        c.repo.has_active_platform_membership.side_effect = None
        c.repo.has_active_platform_membership.return_value = False
    else:
        values["expires_at"] = {"naive": datetime(2030, 1, 1), "past": c.now, "bad_expiry": "tomorrow"}[failure]
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ValueError)):
        await replace(c, **values)
    c.repo.revoke_if_effective.assert_not_called()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,name,expected", [
    ("23P01", "ex_construction_access_grants_active_period", ConstructionAccessGrantConflictError),
    ("23P01", "unrelated_constraint", ConstructionGrantOperationError),
    ("23503", "ex_construction_access_grants_active_period", ConstructionGrantOperationError),
])
async def test_only_approved_overlap_constraint_maps_to_conflict(case, code, name, expected):
    c = case
    driver_error = RuntimeError("private constraint diagnostic")
    driver_error.sqlstate = code
    driver_error.constraint_name = name
    adapter_error = RuntimeError("driver adapter")
    adapter_error.__cause__ = driver_error
    c.repo.create_grant.side_effect = IntegrityError("private SQL", {}, adapter_error)
    with pytest.raises(expected):
        await replace(c)
    unchanged(c)


def test_factory_shares_one_session_and_rejects_mixed_wiring(case):
    service = build_construction_access_mutation_service(case.session)
    assert service.repository.session is case.session
    assert service.project_repository.session is case.session
    assert service.audit_logger.event_repository.session is case.session
    with pytest.raises(ValueError):
        ConstructionAccessMutationService(
            session=case.session, repository=case.repo, event_repository=SimpleNamespace(session=object()),
            project_repository=case.projects,
        )
