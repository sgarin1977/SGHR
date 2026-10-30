from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import ConstructionProject
from database.repositories.construction import ConstructionProjectRepository
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_project_read import (
    ConstructionProjectReadService, build_construction_project_read_service,
)
from services.construction.construction_projects import (
    ConstructionProjectNotFoundError, ConstructionProjectOperationError,
    ConstructionProjectValidationError,
)


@pytest.fixture
def read_case():
    c = SimpleNamespace(tenant=uuid4(), foreign=uuid4(), user=uuid4(), project_id=uuid4())
    c.actor = SimpleNamespace(tenant_id=c.tenant, user_id=c.user, status="active")
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.read",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    c.project = SimpleNamespace(id=c.project_id, tenant_id=c.tenant, deleted_at=None)
    c.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
    c.repo = SimpleNamespace(
        session=c.session, get_active_project=AsyncMock(return_value=c.project),
        create_project=AsyncMock(), update_project=AsyncMock(),
    )
    c.service = ConstructionProjectReadService(repository=c.repo)
    return c


async def read(c):
    return await c.service.get_project(
        actor=c.actor, access_context=c.context, project_id=c.project_id,
    )


def assert_no_mutation(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.session.flush.assert_not_called()
    c.repo.create_project.assert_not_called()
    c.repo.update_project.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_read_accepts_current_tenant_or_exact_project_scope(read_case, scope):
    c = read_case
    if scope == "project":
        c.context = ConstructionAccessContext(
            c.user, c.tenant, ("PROJECT_MANAGER",), ("construction.projects.read",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    assert await read(c) is c.project
    c.repo.get_active_project.assert_awaited_once_with(tenant_id=c.tenant, project_id=c.project_id)
    assert_no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "inactive", "missing_status", "actor_user", "actor_tenant", "context_type",
    "foreign_context", "wrong_user", "permission", "roles", "wrong_project",
    "wrong_tenant_grant", "document_only", "permission_from_other_scope",
])
async def test_read_denies_before_repository(read_case, failure):
    c = read_case
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
        elif failure == "wrong_project": grants = (ConstructionGrantContext("OWNER", "project", uuid4()),)
        elif failure == "wrong_tenant_grant": grants = (ConstructionGrantContext("OWNER", "tenant", c.foreign),)
        elif failure == "document_only": grants = (ConstructionGrantContext("OWNER", "document", uuid4()),)
        else:
            roles = ("OWNER", "EXECUTOR")
            grants = (
                ConstructionGrantContext("OWNER", "project", uuid4()),
                ConstructionGrantContext("EXECUTOR", "document", uuid4()),
            )
        c.context = ConstructionAccessContext(user, tenant, roles, permissions, grants)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.repo.get_active_project.assert_not_called()
    assert_no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", [None, "not-a-uuid", True, 42])
async def test_read_rejects_invalid_identifier_without_query(read_case, project_id):
    c = read_case
    c.project_id = project_id
    with pytest.raises(ConstructionProjectValidationError):
        await read(c)
    c.repo.get_active_project.assert_not_called()
    assert_no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign_tenant", "wrong_id", "deleted", "no_deletion_field"])
async def test_read_never_returns_missing_foreign_deleted_or_malformed_result(read_case, failure):
    c = read_case
    if failure == "missing": c.repo.get_active_project.return_value = None
    elif failure == "foreign_tenant": c.project.tenant_id = c.foreign
    elif failure == "wrong_id": c.project.id = uuid4()
    elif failure == "deleted": c.project.deleted_at = datetime.now(timezone.utc)
    else: del c.project.deleted_at
    with pytest.raises(ConstructionProjectNotFoundError):
        await read(c)
    assert_no_mutation(c)


@pytest.mark.asyncio
async def test_read_wraps_database_failure_without_putting_details_in_public_message(read_case):
    c = read_case
    c.repo.get_active_project.side_effect = RuntimeError("private-database-detail")
    with pytest.raises(ConstructionProjectOperationError) as caught:
        await read(c)
    assert str(caught.value) == "Construction project read failed."
    assert_no_mutation(c)


def test_read_factory_reuses_supplied_session_and_requires_repository():
    session = object()
    service = build_construction_project_read_service(session)
    assert isinstance(service.repository, ConstructionProjectRepository)
    assert service.repository.session is session
    with pytest.raises(ValueError):
        ConstructionProjectReadService(repository=object())


@pytest.mark.asyncio
async def test_repository_read_has_tenant_id_soft_delete_and_fresh_no_autoflush_options(read_case):
    c = read_case
    result = SimpleNamespace(scalar_one_or_none=lambda: c.project)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    repo = ConstructionProjectRepository(session)
    assert await repo.get_active_project(tenant_id=c.tenant, project_id=c.project_id) is c.project
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "construction_projects.tenant_id =" in sql
    assert "construction_projects.id =" in sql
    assert "construction_projects.deleted_at is null" in sql
    assert "limit" in sql and "for update" not in sql
    assert {c.tenant, c.project_id} <= set(statement.compile().params.values())
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement.get_execution_options()["autoflush"] is False


@pytest.fixture
def real_read(read_case):
    c = read_case
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def foreign_keys(db, _record):
        db.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE users (id CHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE construction_clients (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), UNIQUE(tenant_id,id))"
        )
        conn.exec_driver_sql("INSERT INTO tenants(id) VALUES (?), (?)", (c.tenant.hex, c.foreign.hex))
        conn.exec_driver_sql("INSERT INTO users(id) VALUES (?)", (c.user.hex,))
    ConstructionProject.__table__.create(engine)
    c.sql_session = Session(engine)
    c.sql_session.add(ConstructionProject(
        id=c.project_id, tenant_id=c.tenant, name="Saved project", address_raw="Saved address",
        created_by=c.user, client_id=None, responsible_user_id=None, comment=None,
    ))
    c.sql_session.commit()
    c.flushes = []
    event.listen(c.sql_session, "before_flush", lambda *args: c.flushes.append(True))
    async def execute(statement):
        return c.sql_session.execute(statement)
    c.session.execute = AsyncMock(side_effect=execute)
    c.repo = ConstructionProjectRepository(c.session)
    c.service = ConstructionProjectReadService(repository=c.repo)
    try:
        yield c
    finally:
        c.sql_session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_real_read_refreshes_cached_project_without_flushing_dirty_state(real_read):
    c = real_read
    cached = c.sql_session.get(ConstructionProject, c.project_id)
    c.sql_session.execute(
        update(ConstructionProject.__table__).where(ConstructionProject.id == c.project_id)
        .values(name="Current database value").execution_options(autoflush=False)
    )
    cached.name = "Unsaved local value"
    before = dict(c.sql_session.execute(select(ConstructionProject.__table__)).mappings().one())
    project = await read(c)
    after = dict(c.sql_session.execute(select(ConstructionProject.__table__)).mappings().one())
    assert project is cached and project.name == "Current database value"
    assert before == after and c.flushes == []
    c.session.commit.assert_not_called()
    c.session.flush.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_tenant", "deleted"])
async def test_real_read_does_not_return_cached_project_after_database_scope_change(real_read, failure):
    c = real_read
    cached = c.sql_session.get(ConstructionProject, c.project_id)
    values = {"tenant_id": c.foreign} if failure == "foreign_tenant" else {"deleted_at": datetime.now(timezone.utc)}
    c.sql_session.execute(
        update(ConstructionProject.__table__).where(ConstructionProject.id == c.project_id)
        .values(**values).execution_options(autoflush=False)
    )
    with pytest.raises(ConstructionProjectNotFoundError):
        await read(c)
    assert cached.tenant_id == c.tenant and cached.deleted_at is None
    assert c.flushes == []
    c.session.commit.assert_not_called()
