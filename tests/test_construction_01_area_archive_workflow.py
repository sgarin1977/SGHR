from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from services.construction_area_archive_workflow import (
    ConstructionAreaArchiveWorkflow, PUBLIC_AREA_ARCHIVE_FIELDS,
    build_construction_area_archive_workflow,
)
from services.construction_areas import ConstructionProjectAreaNotFoundError, ConstructionProjectAreaOperationError
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_01_area_archive_transaction import archive_case, real_area
from test_construction_01_project_create_transaction import create_case, real_create


@pytest.fixture
def workflow_case(archive_case):
    c = archive_case
    c.repo.session = c.projects.session = c.events.session = c.session
    c.repo.find_active_area_project_id = AsyncMock(return_value=c.project_id)
    c.workflow = ConstructionAreaArchiveWorkflow(session=c.session, area_service=c.service)
    return c


async def run(c, **changes):
    arguments = dict(
        actor=c.actor, access_context=c.context, area_id=c.area.id,
        expected_row_version=7, trace_id="area-archive-workflow",
    )
    arguments.update(changes)
    return await c.workflow.archive_area(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_workflow_derives_scope_and_commits_archive_audit_once(workflow_case, scope):
    c = workflow_case
    if scope == "project":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    c.area.private_marker = "private-area-detail"
    result = await run(c)
    assert set(result) == PUBLIC_AREA_ARCHIVE_FIELDS
    assert result["id"] == str(c.area.id) and result["project_id"] == str(c.project_id)
    assert result["tenant_id"] == str(c.actor.tenant_id) and result["row_version"] == 8
    assert result["deleted_at"] == "2026-10-09T00:00:00Z"
    assert "private-area-detail" not in str(result)
    c.repo.find_active_area_project_id.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, area_id=c.area.id,
        allowed_project_ids=None if scope == "tenant" else (c.project_id,),
    )
    c.events.create_event.assert_awaited_once()
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "inactive", "permission", "foreign_context", "wrong_user", "forged_role",
    "projectless", "document", "foreign_tenant_grant", "illegal_tenant_role",
])
async def test_invalid_actor_or_scope_denies_before_lookup(workflow_case, failure):
    c = workflow_case
    if failure == "inactive":
        c.actor.status = "blocked"
    else:
        tenant = uuid4() if failure == "foreign_context" else c.actor.tenant_id
        user = uuid4() if failure == "wrong_user" else c.actor.user_id
        role = "OBSERVER" if failure == "forged_role" else (
            "PROJECT_MANAGER" if failure == "illegal_tenant_role" else "OWNER"
        )
        permissions = () if failure == "permission" else ("construction.projects.edit",)
        grants = (ConstructionGrantContext(role, "tenant", c.actor.tenant_id),)
        if failure == "projectless":
            grants = ()
        elif failure == "document":
            grants = (ConstructionGrantContext(role, "document", uuid4()),)
        elif failure == "foreign_tenant_grant":
            grants = (ConstructionGrantContext(role, "tenant", uuid4()),)
        c.context = ConstructionAccessContext(user, tenant, (role,), permissions, grants)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    c.repo.find_active_area_project_id.assert_not_called()
    c.repo.soft_delete_area.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [None, "outside_scope"])
async def test_scoped_lookup_miss_or_inconsistent_result_does_not_archive(workflow_case, returned):
    c = workflow_case
    c.context = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("OWNER",), ("construction.projects.edit",),
        (ConstructionGrantContext("OWNER", "project", c.project_id),),
    )
    c.repo.find_active_area_project_id.return_value = None if returned is None else uuid4()
    with pytest.raises(ConstructionProjectAreaNotFoundError):
        await run(c)
    c.projects.get_active_project_for_update.assert_not_called()
    c.repo.soft_delete_area.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("id", "wrong"), ("tenant_id", "wrong"), ("project_id", "wrong"),
    ("row_version", True), ("deleted_at", None),
])
async def test_response_binding_failure_rolls_back_before_commit(workflow_case, field, value):
    c = workflow_case
    original = c.service.archive_area
    async def bad(**kwargs):
        area = await original(**kwargs)
        setattr(area, field, value)
        return area
    c.service.archive_area = bad
    with pytest.raises(ConstructionProjectAreaOperationError):
        await run(c)
    c.events.create_event.assert_awaited_once()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_absent_transaction_denies_before_lookup(workflow_case):
    c = workflow_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionProjectAreaOperationError):
        await run(c)
    c.repo.find_active_area_project_id.assert_not_called()


def test_factory_and_constructor_require_one_shared_session(workflow_case):
    c = workflow_case
    workflow = build_construction_area_archive_workflow(session=c.session)
    assert workflow.service.session is c.session
    assert workflow.service.repository.session is c.session
    assert workflow.service.project_repository.session is c.session
    assert workflow.service.audit_logger.event_repository.session is c.session
    c.events.session = object()
    with pytest.raises(ValueError):
        ConstructionAreaArchiveWorkflow(session=c.session, area_service=c.service)


@pytest.fixture
async def sql_workflow(real_area):
    c = real_area
    c.session.commit.side_effect = c.sql_session.commit
    c.session.rollback.side_effect = c.sql_session.rollback
    c.workflow = ConstructionAreaArchiveWorkflow(session=c.session, area_service=c.area_service)
    return c


@pytest.mark.asyncio
async def test_real_workflow_commits_archive_and_audit_together(sql_workflow):
    c = sql_workflow
    c.sql_session.begin()
    result = await c.workflow.archive_area(
        actor=c.actor, access_context=c.context, area_id=c.area_id,
        expected_row_version=1, trace_id="area-sql-workflow",
    )
    assert result["id"] == str(c.area_id) and result["row_version"] == 2
    state = c.sql_session.execute(text(
        "SELECT deleted_at,row_version FROM construction_project_areas WHERE id=:id"
    ), {"id": c.area_id.hex}).one()
    assert state.deleted_at is not None and state.row_version == 2
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 2
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "response", "commit"])
async def test_real_workflow_failure_restores_archive_and_audit(sql_workflow, failure):
    c = sql_workflow
    def fail(*args, **kwargs):
        assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 2
        raise RuntimeError("private-workflow-detail")
    if failure == "audit":
        c.events.fail_after_insert = True
    elif failure == "response":
        c.workflow._response = fail
    else:
        c.session.commit.side_effect = fail
    c.sql_session.begin()
    with pytest.raises(ConstructionProjectAreaOperationError) as caught:
        await c.workflow.archive_area(
            actor=c.actor, access_context=c.context, area_id=c.area_id, expected_row_version=1,
        )
    assert "private-workflow-detail" not in str(caught.value)
    state = c.sql_session.execute(text(
        "SELECT deleted_at,row_version FROM construction_project_areas WHERE id=:id"
    ), {"id": c.area_id.hex}).one()
    assert state.deleted_at is None and state.row_version == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1
    c.session.rollback.assert_awaited_once()
