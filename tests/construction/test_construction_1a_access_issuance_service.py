from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from services.construction.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction.construction_access_issuance import (
    ConstructionAccessIssuanceService, build_construction_access_issuance_service,
)
from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_access_mutation_workflow import pipeline, snapshot


@pytest.fixture
def issuance(pipeline):
    c = pipeline
    c.issuance = ConstructionAccessIssuanceService(
        session=c.session, repository=c.repo, project_repository=c.service.project_repository,
        event_repository=c.service.audit_logger.event_repository, now_provider=lambda: c.now,
    )
    return c


async def grant(c, **changes):
    if not c.db.in_transaction:
        c.db.execute("BEGIN")
    values = dict(
        actor=c.actor, target_user_id=c.original.user_id, role="SURVEYOR",
        scope_type="project", scope_id=c.project_id, expires_at=None, trace_id="grant-issuance",
    )
    values.update(changes)
    return await c.issuance.grant_access(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type,role", [("project", "SURVEYOR"), ("tenant", "ADMIN")])
async def test_issuance_prepares_one_new_grant_and_exact_audit_without_commit(issuance, scope_type, role):
    c = issuance
    previous = vars(c.rows("grant")[0]).copy()
    deadline = (c.now+timedelta(hours=2)).astimezone(timezone(timedelta(hours=3)))
    scope_id = c.project_id if scope_type == "project" else c.actor.tenant_id
    result = await grant(c, role=" "+role.lower()+" ", scope_type=scope_type, scope_id=scope_id, expires_at=deadline)
    assert result.id != c.original.id and len(c.rows("grant")) == 2
    assert vars(next(g for g in c.rows("grant") if g.id == c.original.id)) == previous
    assert result.status == "active" and result.expires_at == deadline.astimezone(timezone.utc)
    assert result.revoked_at is None and result.granted_by_user_id == c.actor.user_id
    audits = c.rows("audit")
    assert len(audits) == 1 and audits[0].entity_id == str(result.id)
    assert audits[0].event_type == "construction_access_grant_created"
    assert audits[0].tenant_id == c.actor.tenant_id and audits[0].user_id == c.actor.user_id
    assert audits[0].trace_id == "grant-issuance"
    assert audits[0].payload == {
        "operation": "create", "changes": [], "reason": None,
        "target_user_id": str(c.original.user_id), "role": role,
        "scope_type": scope_type, "scope_id": str(scope_id),
        "expires_at": deadline.astimezone(timezone.utc).isoformat(),
    }
    assert c.repo.list_active_grants.await_count == 2
    c.repo.has_active_platform_membership.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, user_id=c.original.user_id,
    )
    c.repo.revoke_if_effective.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "create", "invalid_result"])
async def test_issuance_failure_rolls_back_new_grant_and_audit(issuance, failure):
    c = issuance
    before = snapshot(c)
    if failure == "audit":
        original = c.service.audit_logger.event_repository.create_event.side_effect
        async def audit(**fields):
            await original(**fields)
            raise RuntimeError("private audit diagnostic")
        c.service.audit_logger.event_repository.create_event.side_effect = audit
    elif failure == "create":
        c.repo.create_grant.side_effect = RuntimeError("private insert diagnostic")
    else:
        original = c.repo.create_grant.side_effect
        async def create(**fields):
            result = await original(**fields)
            result.user_id = uuid4()
            return result
        c.repo.create_grant.side_effect = create
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await grant(c)
    assert "private" not in str(caught.value) and snapshot(c) == before
    c.session.rollback.assert_awaited()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "inactive", "no_authority", "invalid_user", "foreign_tenant_scope", "document", "bad_role",
    "tenant_role", "missing_project", "foreign_project", "deleted_project", "no_membership",
])
async def test_issuance_denies_unavailable_actor_target_or_scope_before_creation(issuance, failure):
    c = issuance
    before = snapshot(c)
    values = {}
    if failure == "inactive":
        c.actor.status = "suspended"
    elif failure == "no_authority":
        c.authorized = False
    elif failure == "invalid_user":
        values["target_user_id"] = "untrusted-id"
    elif failure == "foreign_tenant_scope":
        values.update(role="ADMIN", scope_type="tenant", scope_id=uuid4())
    elif failure == "document":
        values["scope_type"] = "document"
    elif failure == "bad_role":
        values["role"] = "CUSTOM"
    elif failure == "tenant_role":
        values.update(scope_type="tenant", scope_id=c.actor.tenant_id)
    elif failure in ("missing_project", "foreign_project", "deleted_project"):
        projects = c.service.project_repository
        projects.get_active_project_for_update.return_value = (
            None if failure == "missing_project" else SimpleNamespace(
                id=c.project_id, tenant_id=uuid4() if failure == "foreign_project" else c.actor.tenant_id,
                deleted_at=c.now if failure == "deleted_project" else None,
            )
        )
    else:
        c.repo.has_active_platform_membership.return_value = False
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await grant(c, **values)
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["naive", "past", "numeric", "clock_advance"])
async def test_issuance_checks_expiry_against_time_after_waits(issuance, failure):
    c = issuance
    before = snapshot(c)
    deadline = c.now+timedelta(seconds=1)
    if failure == "naive":
        deadline = deadline.replace(tzinfo=None)
    elif failure == "past":
        deadline = c.now
    elif failure == "numeric":
        deadline = 123
    else:
        async def membership(**fields):
            c.now += timedelta(seconds=2)
            return True
        c.repo.has_active_platform_membership.side_effect = membership
    with pytest.raises(ValueError):
        await grant(c, expires_at=deadline)
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
async def test_issuance_refreshes_authority_after_membership_wait(issuance):
    c = issuance
    before = snapshot(c)
    async def membership(**fields):
        c.authorized = False
        return True
    c.repo.has_active_platform_membership.side_effect = membership
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await grant(c)
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
async def test_issuance_cannot_delegate_owner_from_an_unrelated_project(issuance):
    c = issuance
    before = snapshot(c)
    c.repo.list_active_grants.side_effect = None
    c.repo.list_active_grants.return_value = [
        SimpleNamespace(role="ADMIN", scope_type="project", scope_id=c.project_id),
        SimpleNamespace(role="OWNER", scope_type="project", scope_id=uuid4()),
    ]
    with pytest.raises(ConstructionPermissionDeniedError):
        await grant(c, role="OWNER")
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint,expected", [
    ("ex_construction_access_grants_active_period", ConstructionAccessGrantConflictError),
    ("unrelated_constraint", ConstructionGrantOperationError),
])
async def test_issuance_maps_only_approved_effective_grant_overlap(issuance, constraint, expected):
    c = issuance
    before = snapshot(c)
    driver = RuntimeError("private database diagnostic")
    driver.sqlstate = "23P01"
    driver.constraint_name = constraint
    c.repo.create_grant.side_effect = IntegrityError("private SQL", {}, driver)
    with pytest.raises(expected):
        await grant(c)
    assert snapshot(c) == before
    c.session.commit.assert_not_called()


def test_issuance_requires_shared_session_and_repository_contract(issuance):
    c = issuance
    factory = build_construction_access_issuance_service(session=c.session, now_provider=lambda: c.now)
    assert factory.repository.session is c.session
    assert factory.project_repository.session is c.session
    assert factory.audit_logger.event_repository.session is c.session
    assert factory.resolver.repository.session is c.session
    with pytest.raises(ValueError):
        ConstructionAccessIssuanceService(
            session=c.session, repository=c.repo, project_repository=c.service.project_repository,
            event_repository=SimpleNamespace(session=object()),
        )


@pytest.mark.asyncio
async def test_prepared_grant_and_audit_can_be_rolled_back_by_owner(issuance):
    c = issuance
    before = snapshot(c)
    await grant(c)
    assert snapshot(c) != before and c.db.in_transaction
    await c.session.rollback()
    assert snapshot(c) == before
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_project_admin_can_issue_allowed_project_role(issuance):
    c = issuance
    c.repo.list_active_grants.side_effect = None
    c.repo.list_active_grants.return_value = [
        SimpleNamespace(role="ADMIN", scope_type="project", scope_id=c.project_id),
    ]
    result = await grant(c)
    assert result.role == "SURVEYOR" and result.scope_id == c.project_id
    c.session.commit.assert_not_called()
