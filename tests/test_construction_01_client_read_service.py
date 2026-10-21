from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import ConstructionClient
from database.repositories.construction import ConstructionClientRepository
from services.construction_client_read import (
    ConstructionClientReadService, build_construction_client_read_service,
)
from services.construction_clients import (
    ConstructionClientNotFoundError, ConstructionClientOperationError,
    ConstructionClientValidationError,
)
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


@pytest.fixture
def client_case():
    c = SimpleNamespace(tenant=uuid4(), foreign=uuid4(), user=uuid4(), client_id=uuid4())
    c.actor = SimpleNamespace(tenant_id=c.tenant, user_id=c.user, status="active")
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.read",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    c.client = SimpleNamespace(id=c.client_id, tenant_id=c.tenant, deleted_at=None)
    c.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
    c.repo = SimpleNamespace(
        session=c.session, get_active_client=AsyncMock(return_value=c.client),
        create_client=AsyncMock(), update_client=AsyncMock(), soft_delete_client=AsyncMock(),
    )
    c.service = ConstructionClientReadService(repository=c.repo)
    return c


async def read(c):
    return await c.service.get_client(
        actor=c.actor, access_context=c.context, client_id=c.client_id,
    )


def unchanged(c):
    for name in ("commit", "rollback", "flush"):
        getattr(c.session, name).assert_not_called()
    for name in ("create_client", "update_client", "soft_delete_client"):
        if isinstance(getattr(c.repo, name, None), AsyncMock):
            getattr(c.repo, name).assert_not_called()


@pytest.mark.asyncio
async def test_authorized_read_uses_actor_tenant_and_performs_no_writes(client_case):
    c = client_case
    assert await read(c) is c.client
    c.repo.get_active_client.assert_awaited_once_with(tenant_id=c.tenant, client_id=c.client_id)
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "inactive", "missing_status", "actor_user", "actor_tenant", "context_type",
    "foreign_context", "wrong_user", "permission", "roles",
    "project_only", "document_only", "wrong_tenant_grant", "permission_from_other_scope",
])
async def test_unauthorized_read_is_denied_before_repository(client_case, failure):
    c = client_case
    if failure == "inactive": c.actor.status = "blocked"
    elif failure == "missing_status": del c.actor.status
    elif failure == "actor_user": c.actor.user_id = str(c.user)
    elif failure == "actor_tenant": c.actor.tenant_id = str(c.tenant)
    elif failure == "context_type": c.context = object()
    else:
        tenant, user = c.tenant, c.user
        roles, permissions, grants = ("OWNER",), ("construction.projects.read",), c.context.grants
        if failure == "foreign_context": tenant = c.foreign
        elif failure == "wrong_user": user = uuid4()
        elif failure == "permission": permissions = ()
        elif failure == "roles": roles = ("UNKNOWN",)
        elif failure in ("project_only", "document_only"):
            scope = "project" if failure == "project_only" else "document"
            grants = (ConstructionGrantContext("OWNER", scope, uuid4()),)
        elif failure == "wrong_tenant_grant":
            grants = (ConstructionGrantContext("OWNER", "tenant", c.foreign),)
        else:
            roles = ("OWNER", "UNKNOWN")
            grants = (
                ConstructionGrantContext("OWNER", "project", uuid4()),
                ConstructionGrantContext("UNKNOWN", "tenant", c.tenant),
            )
        c.context = ConstructionAccessContext(user, tenant, roles, permissions, grants)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.repo.get_active_client.assert_not_called()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_id", [None, "not-a-uuid", True, 42])
async def test_invalid_client_identifier_does_not_query(client_case, client_id):
    c = client_case
    c.client_id = client_id
    with pytest.raises(ConstructionClientValidationError):
        await read(c)
    c.repo.get_active_client.assert_not_called()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign_tenant", "wrong_id", "deleted", "missing_deletion_field"])
async def test_unavailable_or_malformed_result_is_never_returned(client_case, failure):
    c = client_case
    if failure == "missing": c.repo.get_active_client.return_value = None
    elif failure == "foreign_tenant": c.client.tenant_id = c.foreign
    elif failure == "wrong_id": c.client.id = uuid4()
    elif failure == "deleted": c.client.deleted_at = datetime.now(timezone.utc)
    else: del c.client.deleted_at
    with pytest.raises(ConstructionClientNotFoundError):
        await read(c)
    unchanged(c)


@pytest.mark.asyncio
async def test_database_failure_has_a_stable_public_message(client_case):
    c = client_case
    c.repo.get_active_client.side_effect = RuntimeError("private-database-detail")
    with pytest.raises(ConstructionClientOperationError) as caught:
        await read(c)
    assert str(caught.value) == "Construction client read failed."
    assert isinstance(caught.value.__cause__, RuntimeError)
    unchanged(c)


def test_factory_reuses_session_and_requires_a_read_repository():
    session = object()
    service = build_construction_client_read_service(session)
    assert isinstance(service.repository, ConstructionClientRepository)
    assert service.repository.session is session
    with pytest.raises(ValueError):
        ConstructionClientReadService(repository=object())


@pytest.mark.asyncio
async def test_repository_filters_tenant_id_and_soft_delete_without_locking_or_autoflush(client_case):
    c = client_case
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: c.client,
    )))
    repo = ConstructionClientRepository(session)
    assert await repo.get_active_client(tenant_id=c.tenant, client_id=c.client_id) is c.client
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "construction_clients.tenant_id =" in sql
    assert "construction_clients.id =" in sql
    assert "construction_clients.deleted_at is null" in sql
    assert "limit" in sql and "for update" not in sql
    assert {c.tenant, c.client_id} <= set(statement.compile().params.values())
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement.get_execution_options()["autoflush"] is False


@pytest.fixture
def real_client(client_case):
    c = client_case
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def foreign_keys(db, _record):
        db.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id CHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO tenants(id) VALUES (?), (?)", (c.tenant.hex, c.foreign.hex))
    ConstructionClient.__table__.create(engine)
    c.sql_session = Session(engine)
    c.sql_session.add(ConstructionClient(
        id=c.client_id, tenant_id=c.tenant, display_name="Saved client", client_type="person",
    ))
    c.sql_session.commit()
    c.flushes = []
    event.listen(c.sql_session, "before_flush", lambda *args: c.flushes.append(True))
    async def execute(statement):
        return c.sql_session.execute(statement)
    c.session.execute = AsyncMock(side_effect=execute)
    c.repo = ConstructionClientRepository(c.session)
    c.service = ConstructionClientReadService(repository=c.repo)
    try:
        yield c
    finally:
        c.sql_session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_real_read_refreshes_cached_values_without_flushing_dirty_state(real_client):
    c = real_client
    cached = c.sql_session.get(ConstructionClient, c.client_id)
    c.sql_session.execute(
        update(ConstructionClient.__table__).where(ConstructionClient.id == c.client_id)
        .values(display_name="Current database value").execution_options(autoflush=False)
    )
    cached.display_name = "Unsaved local value"
    before = dict(c.sql_session.execute(select(ConstructionClient.__table__)).mappings().one())
    client = await read(c)
    after = dict(c.sql_session.execute(select(ConstructionClient.__table__)).mappings().one())
    assert client is cached and client.display_name == "Current database value"
    assert before == after and c.flushes == []
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_tenant", "deleted"])
async def test_real_read_does_not_return_cached_foreign_or_deleted_client(real_client, failure):
    c = real_client
    cached = c.sql_session.get(ConstructionClient, c.client_id)
    values = {"tenant_id": c.foreign} if failure == "foreign_tenant" else {"deleted_at": datetime.now(timezone.utc)}
    c.sql_session.execute(
        update(ConstructionClient.__table__).where(ConstructionClient.id == c.client_id)
        .values(**values).execution_options(autoflush=False)
    )
    with pytest.raises(ConstructionClientNotFoundError):
        await read(c)
    assert cached.tenant_id == c.tenant and cached.deleted_at is None
    assert c.flushes == []
    unchanged(c)
