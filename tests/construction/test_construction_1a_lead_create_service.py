import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from services.construction.construction_audit import CONSTRUCTION_STAGE_1A_AUDIT_EVENT_TYPES
from services.construction.construction_lead_create import (
    ConstructionLeadCreateService, ConstructionLeadOperationError, build_construction_lead_create_service,
)
from services.construction.construction_lead_rules import ConstructionLeadInput, ConstructionLeadValidationError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from database.repositories.construction.construction_leads import ConstructionLeadRepository
from test_construction_1a_lead_model import TABLE, lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def lead_create_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active", permissions=())
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",), ("construction.projects.create",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),))
    c.data = ConstructionLeadInput(source="partner_portal", name="Private client",
        work_type_description="Private work description", email="private@example.com", comment="private-note")
    c.session = SimpleNamespace(in_transaction=lambda: True, commit=AsyncMock(), rollback=AsyncMock(),
        execute=AsyncMock(), add=Mock(), flush=AsyncMock())
    c.repo = SimpleNamespace(session=c.session, create_lead=AsyncMock())
    c.grant = SimpleNamespace(role="OWNER", scope_type="tenant", scope_id=c.tenant, status="active", expires_at=None)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock())
    async def create(**kwargs):
        c.row = SimpleNamespace(**asdict(kwargs["data"]))
        c.row.id, c.row.tenant_id, c.row.row_version = uuid4(), kwargs["tenant_id"], 1
        c.row.created_at = c.row.updated_at = kwargs["as_of"]
        c.row.deleted_at = c.row.converted_client_id = c.row.converted_project_id = None
        return c.row
    c.repo.create_lead.side_effect = create
    c.service = ConstructionLeadCreateService(session=c.session, repository=c.repo, access_repository=c.access,
        event_repository=c.events, now_provider=lambda: c.now)
    return c


async def create(c, **changes):
    fields = dict(actor=c.actor, access_context=c.context, data=c.data, trace_id="lead-create-test")
    fields.update(changes)
    return await c.service.create_lead(**fields)


def caller_owns_transaction(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("assigned", [False, True])
async def test_create_uses_shared_permission_membership_server_fields_and_one_audit(lead_create_case, role, assigned):
    c = lead_create_case
    c.grant.role = role
    c.context = ConstructionAccessContext(c.user, c.tenant, (role,), ("construction.projects.create",),
        (ConstructionGrantContext(role, "tenant", c.tenant),))
    if assigned:
        c.data = ConstructionLeadInput(source="new_channel", name="Client", work_type_description="Repair",
            phone="123", responsible_user_id=uuid4())
    row = await create(c)
    assert row is c.row and row.tenant_id == c.tenant and row.row_version == 1 and row.status == "new"
    assert row.created_at == row.updated_at == c.now
    assert row.work_type_description == c.data.work_type_description
    assert c.repo.create_lead.await_args.kwargs["as_of"].tzinfo is timezone.utc
    assert c.access.has_active_platform_membership.await_args_list[0].kwargs == dict(tenant_id=c.tenant, user_id=c.user)
    assert c.access.has_active_platform_membership.await_count == (2 if assigned else 1)
    if assigned:
        assert c.access.has_active_platform_membership.await_args_list[1].kwargs == dict(
            tenant_id=c.tenant, user_id=c.data.responsible_user_id)
    c.access.list_active_grants.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user, as_of=c.now)
    c.events.create_event.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_lead_created" and event["entity_type"] == "construction_lead"
    assert event["entity_id"] == row.id and event["tenant_id"] == c.tenant and event["user_id"] == c.user
    assert event["platform"] == "api" and event["trace_id"] == "lead-create-test"
    assert event["payload"] == {"operation": "create", "changes": [], "reason": None}
    assert "private" not in json.dumps(event["payload"]).lower()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "permission", "tenant", "user", "project", "document", "illegal_tenant"])
async def test_denial_happens_before_membership_or_business_queries(lead_create_case, failure):
    c = lead_create_case
    if failure == "inactive":
        c.actor.status = "blocked"
    else:
        role = "PROJECT_MANAGER" if failure == "illegal_tenant" else "OWNER"
        scope = failure if failure in ("project", "document") else "tenant"
        c.context = ConstructionAccessContext(uuid4() if failure == "user" else c.user,
            uuid4() if failure == "tenant" else c.tenant, (role,),
            () if failure == "permission" else ("construction.projects.create",),
            (ConstructionGrantContext(role, scope, c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await create(c)
    c.access.has_active_platform_membership.assert_not_called()
    c.repo.create_lead.assert_not_called()
    c.events.create_event.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["actor_membership", "responsible", "expired", "revoked"])
async def test_current_membership_and_fresh_grants_are_required_before_insert(lead_create_case, failure):
    c = lead_create_case
    expected = ConstructionPermissionDeniedError
    if failure == "actor_membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "responsible":
        c.data = ConstructionLeadInput(source="manual", name="Client", work_type_description="Repair",
            phone="123", responsible_user_id=uuid4())
        c.access.has_active_platform_membership.side_effect = [True, False]
        expected = ConstructionLeadValidationError
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.status = "revoked"
    with pytest.raises(expected):
        await create(c)
    c.repo.create_lead.assert_not_called()
    c.events.create_event.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["repo", "audit", "clock", "transaction", "input"])
async def test_failures_are_sanitized_and_leave_transaction_to_caller(lead_create_case, failure):
    c = lead_create_case
    expected = ConstructionLeadOperationError
    if failure == "repo":
        c.repo.create_lead.side_effect = RuntimeError("private-repository-detail")
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-detail")
    elif failure == "clock":
        c.service.now_provider = lambda: datetime(2026, 10, 12)
    elif failure == "transaction":
        c.session.in_transaction = lambda: False
    else:
        expected = ConstructionLeadValidationError
    with pytest.raises(expected) as caught:
        await create(c, **({"data": {}} if failure == "input" else {}))
    assert "private-" not in str(caught.value)
    if failure != "audit":
        c.events.create_event.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("id", "unparsed"), ("tenant_id", "foreign"), ("status", "won"), ("row_version", True),
    ("converted_client_id", uuid4()), ("work_type_description", "wrong"),
])
async def test_invalid_created_binding_cannot_produce_audit(lead_create_case, field, value):
    c = lead_create_case
    original = c.repo.create_lead.side_effect
    async def bad(**kwargs):
        row = await original(**kwargs)
        setattr(row, field, value)
        return row
    c.repo.create_lead.side_effect = bad
    with pytest.raises(ConstructionLeadOperationError):
        await create(c)
    c.events.create_event.assert_not_called()
    caller_owns_transaction(c)


def test_factory_registry_and_shared_session_guard(lead_create_case):
    c = lead_create_case
    service = build_construction_lead_create_service(session=c.session, now_provider=lambda: c.now)
    assert all(item.session is c.session for item in (
        service, service.repository, service.access_repository, service.audit_logger.event_repository))
    assert "construction_lead_created" in CONSTRUCTION_STAGE_1A_AUDIT_EVENT_TYPES
    c.events.session = object()
    with pytest.raises(ValueError):
        ConstructionLeadCreateService(session=c.session, repository=c.repo,
            access_repository=c.access, event_repository=c.events)


@pytest.fixture
def sql_lead_create(lead_db, lead_create_case):
    engine, ids = lead_db
    c = lead_create_case
    c.tenant, c.user = ids["a"], ids["user"]
    c.actor.tenant_id, c.actor.user_id = c.tenant, c.user
    c.grant.scope_id = c.tenant
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",), ("construction.projects.create",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE lead_test_audit (id TEXT PRIMARY KEY, event_type TEXT, payload TEXT)")
    c.adapter = AsyncSessionAdapter(engine)
    c.access.session = c.adapter
    class Events:
        session = c.adapter
        fail_after_insert = False
        async def create_event(self, **kwargs):
            c.adapter.sync.execute(text(
                "INSERT INTO lead_test_audit(id,event_type,payload) VALUES(:id,:event,:payload)"
            ), dict(id=str(uuid4()), event=kwargs["event_type"], payload=json.dumps(kwargs["payload"])))
            if self.fail_after_insert:
                raise RuntimeError("private-audit-storage-detail")
    c.events = Events()
    c.service = ConstructionLeadCreateService(session=c.adapter, repository=ConstructionLeadRepository(c.adapter),
        access_repository=c.access, event_repository=c.events, now_provider=lambda: c.now)
    try:
        yield c
    finally:
        c.adapter.sync.close()


@pytest.mark.asyncio
async def test_real_caller_commits_lead_and_audit_together(sql_lead_create):
    c = sql_lead_create
    with c.adapter.sync.begin():
        row = await create(c)
        identifier = row.id
    stored = c.adapter.sync.execute(select(TABLE).where(TABLE.c.id == identifier)).one()
    assert stored.work_type_description == c.data.work_type_description and stored.row_version == 1
    assert c.adapter.sync.execute(text("SELECT count(*) FROM lead_test_audit")).scalar_one() == 1
    c.adapter.commit.assert_not_called()
    c.adapter.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "later_workflow"])
async def test_real_caller_rolls_back_flushed_lead_and_audit(sql_lead_create, failure):
    c = sql_lead_create
    c.events.fail_after_insert = failure == "audit"
    expected = ConstructionLeadOperationError if failure == "audit" else RuntimeError
    with pytest.raises(expected):
        with c.adapter.sync.begin():
            await create(c)
            assert c.adapter.sync.execute(text("SELECT count(*) FROM lead_test_audit")).scalar_one() == 1
            raise RuntimeError("forced-later-workflow-failure")
    assert c.adapter.sync.execute(select(TABLE.c.id)).all() == []
    assert c.adapter.sync.execute(text("SELECT count(*) FROM lead_test_audit")).scalar_one() == 0
    c.adapter.commit.assert_not_called()
    c.adapter.rollback.assert_not_called()
