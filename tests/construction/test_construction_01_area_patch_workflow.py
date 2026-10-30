from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from services.construction.construction_area_patch_workflow import (
    ConstructionAreaPatchWorkflow, PUBLIC_AREA_PATCH_FIELDS, build_construction_area_patch_workflow,
)
from services.construction.construction_areas import (
    ConstructionProjectAreaNotFoundError, ConstructionProjectAreaOperationError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_01_area_patch_service import patch_case
from test_construction_01_area_archive_transaction import archive_case, real_area
from test_construction_01_project_create_transaction import create_case, real_create


@pytest.fixture
def patch_workflow_case(patch_case):
    c = patch_case
    c.repo.session = c.projects.session = c.events.session = c.session
    c.repo.find_active_area_project_id = AsyncMock(return_value=c.project_id)
    c.workflow = ConstructionAreaPatchWorkflow(session=c.session, area_service=c.service)
    return c


async def run(c, **changes):
    arguments = dict(
        actor=c.actor, access_context=c.context, area_id=c.area.id, expected_row_version=7,
        fields={"name": " New room ", "sort_order": None}, trace_id="area-patch-workflow",
    )
    arguments.update(changes)
    return await c.workflow.patch_area(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_scoped_patch_commits_once_and_returns_only_public_ack(patch_workflow_case, scope):
    c = patch_workflow_case
    if scope == "project":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    c.area.private_marker = "private-detail"
    result = await run(c)
    assert set(result) == PUBLIC_AREA_PATCH_FIELDS
    assert result == dict(id=str(c.area.id), tenant_id=str(c.actor.tenant_id),
        project_id=str(c.project_id), name="New room", sort_order=None, row_version=8)
    assert c.area.status == "confirmed"
    assert "private-detail" not in str(result)
    c.repo.find_active_area_project_id.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, area_id=c.area.id,
        allowed_project_ids=None if scope == "tenant" else (c.project_id,),
    )
    c.events.create_event.assert_awaited_once()
    assert c.events.create_event.await_args.kwargs["trace_id"] == "area-patch-workflow"
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_no_op_preserves_version_and_audit(patch_workflow_case):
    c = patch_workflow_case
    result = await run(c, fields={"name": " Original room ", "sort_order": 4})
    assert result["row_version"] == 7
    c.repo.update_area_fields.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "permission", "tenant", "user", "role", "document", "illegal_tenant"])
async def test_bad_access_fails_before_lookup(patch_workflow_case, failure):
    c = patch_workflow_case
    if failure == "inactive":
        c.actor.status = "blocked"
    else:
        role = "OBSERVER" if failure == "role" else "PROJECT_MANAGER" if failure == "illegal_tenant" else "OWNER"
        grants = (ConstructionGrantContext(role, "document" if failure == "document" else "tenant",
                    uuid4() if failure == "document" else c.actor.tenant_id),)
        c.context = ConstructionAccessContext(
            uuid4() if failure == "user" else c.actor.user_id,
            uuid4() if failure == "tenant" else c.actor.tenant_id,
            (role,), () if failure == "permission" else ("construction.projects.edit",), grants,
        )
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    c.repo.find_active_area_project_id.assert_not_called()
    c.repo.update_area_fields.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [None, "outside"])
async def test_lookup_miss_or_outside_scope_never_reaches_mutation(patch_workflow_case, returned):
    c = patch_workflow_case
    c.context = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
        (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
    )
    c.repo.find_active_area_project_id.return_value = None if returned is None else uuid4()
    with pytest.raises(ConstructionProjectAreaNotFoundError):
        await run(c)
    c.projects.get_active_project_for_update.assert_not_called()
    c.repo.update_area_fields.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"expected_row_version": True}, {"area_id": "not-uuid"}, {"fields": {"tenant_id": uuid4()}},
    {"fields": {"name": None}}, {"fields": {"name": "  "}}, {"fields": {"sort_order": True}},
])
async def test_invalid_input_is_rejected_before_lookup(patch_workflow_case, changes):
    c = patch_workflow_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await run(c, **changes)
    c.repo.find_active_area_project_id.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("id", "wrong"), ("tenant_id", "wrong"), ("project_id", "wrong"),
    ("deleted_at", "archived"), ("row_version", True), ("row_version", 9),
    ("name", "unexpected"), ("sort_order", True),
])
async def test_response_is_bound_and_checked_before_commit(patch_workflow_case, field, value):
    c = patch_workflow_case
    original = c.service.patch_area
    async def bad(**kwargs):
        area = await original(**kwargs)
        setattr(area, field, value)
        return area
    c.service.patch_area = bad
    with pytest.raises(ConstructionProjectAreaOperationError):
        await run(c)
    c.events.create_event.assert_awaited_once()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_op_with_stale_version_still_conflicts(patch_workflow_case):
    c = patch_workflow_case
    c.area.row_version = 8
    with pytest.raises(ConstructionRowVersionConflictError):
        await run(c, fields={})
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_no_active_transaction_denies_before_lookup(patch_workflow_case):
    c = patch_workflow_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionProjectAreaOperationError):
        await run(c)
    c.repo.find_active_area_project_id.assert_not_called()


def test_factory_and_constructor_require_one_session(patch_workflow_case):
    c = patch_workflow_case
    workflow = build_construction_area_patch_workflow(session=c.session)
    assert all(value.session is c.session for value in (
        workflow.service, workflow.service.repository, workflow.service.project_repository,
        workflow.service.audit_logger.event_repository,
    ))
    c.events.session = object()
    with pytest.raises(ValueError):
        ConstructionAreaPatchWorkflow(session=c.session, area_service=c.service)


@pytest.fixture
async def sql_patch_workflow(real_area):
    c = real_area
    c.session.commit.side_effect = c.sql_session.commit
    c.session.rollback.side_effect = c.sql_session.rollback
    c.workflow = ConstructionAreaPatchWorkflow(session=c.session, area_service=c.area_service)
    return c


async def real_run(c, fields):
    c.sql_session.begin()
    return await c.workflow.patch_area(
        actor=c.actor, access_context=c.context, area_id=c.area_id,
        expected_row_version=1, fields=fields,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fields,version,audits", [
    ({"name": " New ", "sort_order": 8}, 2, 2),
    ({"name": " Archive room ", "sort_order": None}, 1, 1),
])
async def test_real_commit_and_no_op(sql_patch_workflow, fields, version, audits):
    c = sql_patch_workflow
    result = await real_run(c, fields)
    state = c.sql_session.execute(text(
        "SELECT name,sort_order,row_version FROM construction_project_areas WHERE id=:id"
    ), {"id": c.area_id.hex}).one()
    assert state.name == result["name"] and state.sort_order == result["sort_order"]
    assert state.row_version == result["row_version"] == version
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == audits
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "response", "commit"])
async def test_real_failure_restores_fields_version_and_audit(sql_patch_workflow, failure):
    c = sql_patch_workflow
    def fail(*args, **kwargs):
        state = c.sql_session.execute(text(
            "SELECT name,sort_order,row_version FROM construction_project_areas WHERE id=:id"
        ), {"id": c.area_id.hex}).one()
        assert tuple(state) == ("New", 8, 2)
        assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 2
        raise RuntimeError("private-workflow-detail")
    if failure == "audit":
        c.events.fail_after_insert = True
    elif failure == "response":
        c.workflow._response = fail
    else:
        c.session.commit.side_effect = fail
    with pytest.raises(ConstructionProjectAreaOperationError) as caught:
        await real_run(c, {"name": "New", "sort_order": 8})
    assert "private-workflow-detail" not in str(caught.value)
    state = c.sql_session.execute(text(
        "SELECT name,sort_order,row_version FROM construction_project_areas WHERE id=:id"
    ), {"id": c.area_id.hex}).one()
    assert tuple(state) == ("Archive room", None, 1)
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1
    c.session.rollback.assert_awaited_once()
