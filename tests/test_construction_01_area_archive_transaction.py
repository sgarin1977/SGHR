from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from database.repositories.construction import ConstructionProjectAreaRepository, ConstructionProjectRepository
from database.models import ConstructionProjectArea
from services.construction_areas import (
    ConstructionProjectAreaService, ConstructionProjectAreaValidationError,
    ConstructionProjectAreaOperationError, ConstructionProjectAreaTransitionError,
    ConstructionProjectAreaHasActiveElementsError,
)
from services.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from test_construction_01_project_create_transaction import create_case, real_create


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project", "empty"])
async def test_lookup_is_tenant_bound_scope_filtered_and_does_not_flush(scope):
    tenant, area, project = uuid4(), uuid4(), uuid4()
    statements = []
    async def execute(statement):
        statements.append(statement)
        return SimpleNamespace(scalar_one_or_none=lambda: project)
    session = SimpleNamespace(execute=execute, commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
    allowed = None if scope == "tenant" else (project,) if scope == "project" else ()
    result = await ConstructionProjectAreaRepository(session).find_active_area_project_id(
        tenant_id=tenant, area_id=area, allowed_project_ids=allowed,
    )
    assert result == (None if scope == "empty" else project)
    if scope == "empty":
        assert not statements
    else:
        statement = statements[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "construction_project_areas.tenant_id =" in sql
        assert "construction_project_areas.id =" in sql and "deleted_at IS NULL" in sql
        assert tenant in compiled.params.values() and area in compiled.params.values()
        assert ("project_id IN" in sql) == (scope == "project")
        assert statement.get_execution_options()["autoflush"] is False
        assert "FOR UPDATE" not in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_does_not_return_project_outside_the_verified_scope():
    project = uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: project)))
    result = await ConstructionProjectAreaRepository(session).find_active_area_project_id(
        tenant_id=uuid4(), area_id=uuid4(), allowed_project_ids=(uuid4(),),
    )
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [[], ("not-uuid",), False])
async def test_invalid_scope_filter_is_rejected_before_query(scope):
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError):
        await ConstructionProjectAreaRepository(session).find_active_area_project_id(
            tenant_id=uuid4(), area_id=uuid4(), allowed_project_ids=scope,
        )
    session.execute.assert_not_called()


@pytest.fixture
def archive_case():
    tenant, user, project = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(user_id=user, tenant_id=tenant, status="active")
    context = ConstructionAccessContext(
        user, tenant, ("OWNER",), ("construction.projects.edit",),
        (ConstructionGrantContext("OWNER", "tenant", tenant),),
    )
    area = SimpleNamespace(id=uuid4(), tenant_id=tenant, project_id=project,
                           status="draft", deleted_at=None, row_version=7)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), in_transaction=lambda: True)
    repository = SimpleNamespace(
        get_active_area_for_update=AsyncMock(return_value=area),
        has_active_elements=AsyncMock(return_value=False),
        soft_delete_area=AsyncMock(),
    )
    async def archive(**kwargs):
        area.deleted_at = kwargs["deleted_at"]
        area.row_version += 1
        return area
    repository.soft_delete_area.side_effect = archive
    projects = SimpleNamespace(get_active_project_for_update=AsyncMock(
        return_value=SimpleNamespace(id=project, tenant_id=tenant, status="active"),
    ))
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionProjectAreaService(
        session=session, repository=repository, project_repository=projects,
        event_repository=events, now_provider=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc),
    )
    return SimpleNamespace(actor=actor, context=context, area=area, project_id=project,
                           session=session, repo=repository, projects=projects,
                           events=events, service=service)


async def archive(c, **options):
    return await c.service.archive_area(
        actor=c.actor, access_context=c.context, project_id=c.project_id,
        area_id=c.area.id, expected_row_version=7, trace_id="area-archive-test", **options,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, False])
async def test_archive_preserves_audit_and_selected_transaction_owner(archive_case, mode):
    c = archive_case
    assert await archive(c, manage_transaction=mode) is c.area
    assert c.area.row_version == 8 and c.area.deleted_at is not None
    c.events.create_event.assert_awaited_once()
    audit = c.events.create_event.await_args.kwargs
    assert audit["event_type"] == "construction_project_area_archived"
    assert audit["tenant_id"] == c.actor.tenant_id and audit["user_id"] == c.actor.user_id
    assert audit["trace_id"] == "area-archive-test"
    assert audit["payload"]["changes"][0]["field"] == "deleted_at"
    assert c.session.commit.await_count == (1 if mode else 0)
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, False])
@pytest.mark.parametrize("failure", ["terminal", "confirmed", "elements", "stale", "audit"])
async def test_archive_failure_keeps_existing_guards_and_rollback_owner(archive_case, mode, failure):
    c = archive_case
    expected = ConstructionProjectAreaOperationError
    if failure == "terminal":
        c.projects.get_active_project_for_update.return_value.status = "completed"
        expected = ConstructionProjectTerminalError
    elif failure == "confirmed":
        c.area.status = "confirmed"
        expected = ConstructionProjectAreaTransitionError
    elif failure == "elements":
        c.repo.has_active_elements.return_value = True
        expected = ConstructionProjectAreaHasActiveElementsError
    elif failure == "stale":
        c.area.row_version = 8
        expected = ConstructionRowVersionConflictError
    else:
        c.events.create_event.side_effect = RuntimeError("private-audit-error")
    with pytest.raises(expected):
        await archive(c, manage_transaction=mode)
    c.session.commit.assert_not_called()
    assert c.session.rollback.await_count == (1 if mode else 0)
    if failure != "audit":
        c.repo.soft_delete_area.assert_not_called()
        c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, 0, "false"])
async def test_invalid_transaction_mode_is_rejected_before_queries(archive_case, mode):
    c = archive_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await archive(c, manage_transaction=mode)
    c.projects.get_active_project_for_update.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_borrowed_archive_requires_active_caller_transaction(archive_case):
    c = archive_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionProjectAreaOperationError):
        await archive(c, manage_transaction=False)
    c.projects.get_active_project_for_update.assert_not_called()


@pytest.fixture
async def real_area(real_create):
    c = real_create
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.create", "construction.projects.edit"),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    ConstructionProjectArea.__table__.create(c.sql_session.get_bind())
    with c.sql_session.get_bind().begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE construction_area_elements "
            "(id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), project_area_id CHAR(32), deleted_at DATETIME)"
        )
    async def execute(statement):
        return c.sql_session.execute(statement)
    c.session.execute = execute
    c.areas = ConstructionProjectAreaRepository(c.session)
    with c.sql_session.begin():
        project = await c.service.create_project(
            actor=c.actor, access_context=c.context, name="Archive project", address_raw="Address",
            client_id=None, responsible_user_id=None, comment=None, manage_transaction=False,
        )
        c.project_id = project.id
        area = await c.areas.create_area(
            tenant_id=c.tenant, project_id=project.id, area_type="ROOM",
            name="Archive room", sort_order=None, created_by=c.user,
        )
        c.area_id = area.id
    c.area_service = ConstructionProjectAreaService(
        session=c.session, repository=c.areas, project_repository=ConstructionProjectRepository(c.session),
        event_repository=c.events,
    )
    return c


@pytest.mark.asyncio
async def test_real_caller_commits_archive_audit_and_later_result_together(real_area):
    c = real_area
    with c.sql_session.begin():
        area = await c.area_service.archive_area(
            actor=c.actor, access_context=c.context, project_id=c.project_id,
            area_id=c.area_id, expected_row_version=1, manage_transaction=False,
        )
        assert area.row_version == 2 and area.deleted_at is not None
        c.sql_session.execute(text("INSERT INTO tx_test_result(id) VALUES(:id)"), {"id": str(area.id)})
        c.session.commit.assert_not_called()
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 2
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_result")).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "later_workflow"])
async def test_real_caller_rollback_restores_area_and_removes_new_audit(real_area, failure):
    c = real_area
    c.events.fail_after_insert = failure == "audit"
    with pytest.raises(ConstructionProjectAreaOperationError if failure == "audit" else RuntimeError):
        with c.sql_session.begin():
            await c.area_service.archive_area(
                actor=c.actor, access_context=c.context, project_id=c.project_id,
                area_id=c.area_id, expected_row_version=1, manage_transaction=False,
            )
            c.sql_session.execute(text("INSERT INTO tx_test_result(id) VALUES(:id)"), {"id": str(c.area_id)})
            raise RuntimeError("forced-later-workflow-failure")
    state = c.sql_session.execute(text(
        "SELECT deleted_at, row_version FROM construction_project_areas WHERE id=:id"
    ), {"id": c.area_id.hex}).one()
    assert state.deleted_at is None and state.row_version == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_result")).scalar_one() == 0
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_real_lookup_hides_foreign_tenant_and_unassigned_project(real_area):
    c = real_area
    with c.sql_session.begin():
        assert await c.areas.find_active_area_project_id(
            tenant_id=c.tenant, area_id=c.area_id, allowed_project_ids=(c.project_id,),
        ) == c.project_id
        assert await c.areas.find_active_area_project_id(
            tenant_id=uuid4(), area_id=c.area_id, allowed_project_ids=None,
        ) is None
        assert await c.areas.find_active_area_project_id(
            tenant_id=c.tenant, area_id=c.area_id, allowed_project_ids=(uuid4(),),
        ) is None
        assert await c.areas.find_active_area_project_id(
            tenant_id=c.tenant, area_id=uuid4(), allowed_project_ids=None,
        ) is None
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
