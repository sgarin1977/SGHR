import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from database.models import ConstructionClient
from database.repositories.construction import ConstructionClientRepository
from services.construction.construction_clients import (
    ConstructionClientOperationError, ConstructionClientService, ConstructionClientValidationError,
)
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


@pytest.fixture
def client_create_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4())
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active", permissions=())
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",), ("construction.projects.create",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),))
    c.session = SimpleNamespace(in_transaction=lambda: True, commit=AsyncMock(), rollback=AsyncMock())
    c.client = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, display_name="Transaction client",
        client_type="person", phone="+351 123", email="client@example.com", notes="private-note", deleted_at=None)
    c.repo = SimpleNamespace(session=c.session, create_client=AsyncMock(return_value=c.client))
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock())
    c.service = ConstructionClientService(session=c.session, repository=c.repo, event_repository=c.events)
    return c


async def create(c, **changes):
    values = dict(actor=c.actor, access_context=c.context, display_name="  Transaction client  ",
        client_type=" PERSON ", phone="+351 123", email="client@example.com", notes="private-note",
        trace_id="client-create-transaction")
    values.update(changes)
    return await c.service.create_client(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, True, False])
async def test_create_keeps_normal_contract_and_one_audit_with_selected_transaction_owner(client_create_case, mode):
    c = client_create_case
    assert await create(c, **({} if mode is None else {"manage_transaction": mode})) is c.client
    c.repo.create_client.assert_awaited_once_with(tenant_id=c.tenant,
        display_name="Transaction client", client_type="person",
        phone="+351 123", email="client@example.com", notes="private-note")
    c.events.create_event.assert_awaited_once()
    audit = c.events.create_event.await_args.kwargs
    assert audit["event_type"] == "construction_client_created"
    assert audit["tenant_id"] == c.tenant and audit["user_id"] == c.user
    assert audit["entity_id"] == c.client.id and audit["trace_id"] == "client-create-transaction"
    assert audit["payload"] == {"operation": "create", "changes": [], "reason": None, "related_client_id": str(c.client.id)}
    assert "private-note" not in json.dumps(audit["payload"])
    if mode is False:
        c.session.commit.assert_not_called()
    else:
        c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, 0, 1, "false"])
async def test_non_boolean_transaction_mode_is_rejected_before_writes(client_create_case, mode):
    c = client_create_case
    with pytest.raises(ConstructionClientValidationError):
        await create(c, manage_transaction=mode)
    c.repo.create_client.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "missing_method", "repository_session", "audit_session"])
async def test_caller_mode_requires_active_transaction_and_shared_sessions(client_create_case, failure):
    c = client_create_case
    if failure == "inactive":
        c.session.in_transaction = lambda: False
    elif failure == "missing_method":
        del c.session.in_transaction
    elif failure == "repository_session":
        c.repo.session = object()
    else:
        c.events.session = object()
    with pytest.raises(ConstructionClientOperationError):
        await create(c, manage_transaction=False)
    c.repo.create_client.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"display_name": " "}, {"display_name": None}, {"client_type": "other"}, {"client_type": None},
])
async def test_caller_mode_preserves_required_client_fields(client_create_case, changes):
    c = client_create_case
    with pytest.raises(ConstructionClientValidationError):
        await create(c, manage_transaction=False, **changes)
    c.repo.create_client.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "foreign_tenant", "wrong_user", "project_scope"])
async def test_caller_mode_does_not_bypass_common_permission_and_scope(client_create_case, failure):
    c = client_create_case
    c.context = ConstructionAccessContext(
        uuid4() if failure == "wrong_user" else c.user,
        uuid4() if failure == "foreign_tenant" else c.tenant, ("OWNER",),
        () if failure == "permission" else ("construction.projects.create",),
        (ConstructionGrantContext("OWNER", "project" if failure == "project_scope" else "tenant", c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await create(c, manage_transaction=False)
    c.repo.create_client.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, False])
@pytest.mark.parametrize("failure", ["repository", "audit"])
async def test_failure_leaves_rollback_to_selected_owner(client_create_case, mode, failure):
    c = client_create_case
    target = c.repo.create_client if failure == "repository" else c.events.create_event
    target.side_effect = RuntimeError("private-storage-detail")
    with pytest.raises(ConstructionClientOperationError) as caught:
        await create(c, manage_transaction=mode)
    assert "private-storage-detail" not in str(caught.value)
    c.session.commit.assert_not_called()
    if mode:
        c.session.rollback.assert_awaited_once()
    else:
        c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("tenant_id", uuid4()), ("id", "unparsed"), ("deleted_at", True),
    ("display_name", "Wrong"), ("client_type", "company"), ("phone", "Wrong"),
])
async def test_caller_mode_rejects_invalid_created_binding_before_audit(client_create_case, field, value):
    c = client_create_case
    setattr(c.client, field, value)
    with pytest.raises(ConstructionClientOperationError):
        await create(c, manage_transaction=False)
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_commit_failure_still_rolls_back(client_create_case):
    c = client_create_case
    c.session.commit.side_effect = RuntimeError("private-commit-detail")
    with pytest.raises(ConstructionClientOperationError):
        await create(c)
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_creation_still_supports_collaborators_without_session_attributes(client_create_case):
    c = client_create_case
    del c.repo.session
    del c.events.session
    del c.session.in_transaction
    assert await create(c) is c.client
    c.session.commit.assert_awaited_once()


@pytest.fixture
def sql_client_create(client_create_case):
    c = client_create_case
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO tenants VALUES(?)", (c.tenant.hex,))
        connection.exec_driver_sql("CREATE TABLE client_test_audit(id TEXT PRIMARY KEY,event_type TEXT,payload TEXT)")
        connection.exec_driver_sql("CREATE TABLE client_test_checkpoint(id TEXT PRIMARY KEY)")
    ConstructionClient.__table__.create(engine)
    c.sync = Session(engine, expire_on_commit=False)
    async def flush():
        c.sync.flush()
    c.session = SimpleNamespace(add=c.sync.add, flush=flush, in_transaction=c.sync.in_transaction,
        commit=AsyncMock(side_effect=c.sync.commit), rollback=AsyncMock(side_effect=c.sync.rollback))
    c.repo = ConstructionClientRepository(c.session)
    class Events:
        session = c.session
        fail_after_insert = False
        async def create_event(self, **kwargs):
            c.sync.execute(text("INSERT INTO client_test_audit VALUES(:id,:event,:payload)"),
                dict(id=str(uuid4()), event=kwargs["event_type"], payload=json.dumps(kwargs["payload"])))
            if self.fail_after_insert:
                raise RuntimeError("private-after-audit-write")
    c.events = Events()
    c.service = ConstructionClientService(session=c.session, repository=c.repo, event_repository=c.events)
    try:
        yield c
    finally:
        c.sync.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_real_caller_commits_client_audit_and_later_work_together(sql_client_create):
    c = sql_client_create
    c.sync.begin()
    client = await create(c, manage_transaction=False)
    c.session.commit.assert_not_called()
    c.sync.execute(text("INSERT INTO client_test_checkpoint VALUES(:id)"), dict(id=str(client.id)))
    await c.session.commit()
    assert c.sync.execute(select(ConstructionClient.id)).all() == [(client.id,)]
    assert c.sync.execute(text("SELECT count(*) FROM client_test_audit")).scalar_one() == 1
    assert c.sync.execute(text("SELECT count(*) FROM client_test_checkpoint")).scalar_one() == 1
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "later_work"])
async def test_real_caller_rolls_back_client_and_flushed_audit(sql_client_create, failure):
    c = sql_client_create
    c.events.fail_after_insert = failure == "audit"
    with pytest.raises(ConstructionClientOperationError if failure == "audit" else RuntimeError):
        with c.sync.begin():
            await create(c, manage_transaction=False)
            assert c.sync.execute(text("SELECT count(*) FROM client_test_audit")).scalar_one() == 1
            raise RuntimeError("forced-later-conversion-failure")
    assert c.sync.execute(select(ConstructionClient.id)).all() == []
    assert c.sync.execute(text("SELECT count(*) FROM client_test_audit")).scalar_one() == 0
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_real_legacy_mode_commits_client_and_audit(sql_client_create):
    c = sql_client_create
    client = await create(c)
    assert c.sync.execute(select(ConstructionClient.id)).all() == [(client.id,)]
    assert c.sync.execute(text("SELECT count(*) FROM client_test_audit")).scalar_one() == 1
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()
