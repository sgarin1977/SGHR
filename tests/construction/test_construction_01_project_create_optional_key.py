from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from api.routes.construction_project_read import public_project
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_project_create_workflow import (
    ConstructionProjectCreateWorkflow, build_construction_project_create_workflow,
)
from services.construction.construction_projects import (
    ConstructionProjectOperationError, ConstructionProjectValidationError,
)
from test_construction_01_project_create_transaction import create_case, real_create
from test_construction_01_project_create_workflow import workflow_case, real_workflow, run


async def unkeyed(c, **changes):
    arguments = dict(
        actor=c.actor, access_context=c.context, name="  Transaction project  ",
        address_raw="  Project address  ", comment="Project comment",
        trace_id="project-without-key",
    )
    arguments.update(changes)
    return await c.workflow.create_project(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_none", [False, True])
async def test_absent_key_creates_with_audit_without_reservation(workflow_case, explicit_none):
    c = workflow_case
    options = {"idempotency_key": None} if explicit_none else {}
    result = await unkeyed(c, **options)
    assert result["id"] == str(c.project.id)
    assert result["name"] == "Transaction project"
    assert c.order == ["create", "audit", "commit"]
    assert not c.records.records
    c.events.create_event.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_unkeyed_creation_works_without_idempotency_adapter_or_clock(workflow_case):
    c = workflow_case
    c.workflow = ConstructionProjectCreateWorkflow(
        session=c.session, project_service=c.service, response_projector=public_project,
        now_provider=lambda: (_ for _ in ()).throw(AssertionError("Replay clock used")),
    )
    assert await unkeyed(c)
    assert c.workflow.idempotency is None
    assert c.order == ["create", "audit", "commit"]


@pytest.mark.asyncio
async def test_keyed_call_without_adapter_fails_before_creation(workflow_case):
    c = workflow_case
    c.workflow.idempotency = None
    with pytest.raises(ConstructionProjectOperationError):
        await run(c)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unkeyed_and_keyed_paths_do_not_share_a_reservation(workflow_case):
    c = workflow_case
    await unkeyed(c)
    saved = await run(c)
    assert await run(c) == saved
    assert c.repo.create_project.await_count == 2
    assert c.events.create_event.await_count == 2
    assert len(c.records.records) == 1
    assert c.order == [
        "create", "audit", "commit",
        "reserve", "create", "audit", "complete", "commit",
        "reserve", "commit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", False, 0, [], "x" * 256])
async def test_invalid_supplied_key_is_not_treated_as_absent(workflow_case, key):
    c = workflow_case
    with pytest.raises(ConstructionProjectValidationError):
        await unkeyed(c, idempotency_key=key)
    assert not c.records.records
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"name": " "}, {"address_raw": ""},
    {"client_id": "foreign-id"}, {"responsible_user_id": "foreign-id"},
    {"comment": 42},
])
async def test_unkeyed_input_is_validated_before_creation(workflow_case, changes):
    c = workflow_case
    with pytest.raises(ConstructionProjectValidationError):
        await unkeyed(c, **changes)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "inactive", "foreign_tenant", "wrong_user", "permission", "project_scope", "document_scope",
])
async def test_absent_key_does_not_bypass_actor_permission_or_scope(workflow_case, failure):
    c = workflow_case
    if failure == "inactive":
        c.actor.status = "blocked"
    else:
        tenant = uuid4() if failure == "foreign_tenant" else c.tenant
        user = uuid4() if failure == "wrong_user" else c.user
        grants = c.context.grants
        permissions = () if failure == "permission" else ("construction.projects.create",)
        if failure in ("project_scope", "document_scope"):
            grants = (ConstructionGrantContext(
                "OWNER", "project" if failure == "project_scope" else "document", uuid4(),
            ),)
        c.context = ConstructionAccessContext(user, tenant, ("OWNER",), permissions, grants)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await unkeyed(c)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    assert not c.records.records


@pytest.mark.asyncio
async def test_unkeyed_requires_active_transaction(workflow_case):
    c = workflow_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionProjectOperationError):
        await unkeyed(c)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["repository", "audit", "projection", "commit"])
async def test_unkeyed_failure_rolls_back_and_hides_internal_detail(workflow_case, failure):
    c = workflow_case
    if failure == "repository":
        c.repo.create_project.side_effect = RuntimeError("private-repository-detail")
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-detail")
    elif failure == "projection":
        c.project.row_version = 0
    else:
        c.session.commit.side_effect = RuntimeError("private-commit-detail")
    with pytest.raises(ConstructionProjectOperationError) as caught:
        await unkeyed(c)
    assert "private-" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    assert not c.records.records
    if failure != "commit":
        c.session.commit.assert_not_called()


@pytest.mark.parametrize("collaborator", ["repository", "client_repository", "access_repository", "audit"])
def test_unkeyed_still_requires_shared_transaction_wiring(workflow_case, collaborator):
    c = workflow_case
    target = (
        c.service.audit_logger.event_repository if collaborator == "audit"
        else getattr(c.service, collaborator)
    )
    target.session = object()
    with pytest.raises(ValueError):
        ConstructionProjectCreateWorkflow(
            session=c.session, project_service=c.service, response_projector=public_project,
        )


def test_factory_without_key_does_not_build_idempotency(workflow_case, monkeypatch):
    import services.construction.construction_project_create_workflow as module
    def forbidden(*args, **kwargs):
        raise AssertionError("Encryption/idempotency configured for unkeyed factory")
    monkeypatch.setattr(module, "build_api_idempotency_service", forbidden)
    c = workflow_case
    workflow = build_construction_project_create_workflow(
        session=c.session, response_projector=public_project,
    )
    assert workflow.idempotency is None
    assert workflow.service.repository.session is c.session
    assert workflow.service.client_repository.session is c.session
    assert workflow.service.access_repository.session is c.session
    assert workflow.service.audit_logger.event_repository.session is c.session


@pytest.mark.asyncio
async def test_real_unkeyed_repeat_creates_two_projects_and_audits_without_replay_rows(real_workflow):
    c = real_workflow
    c.workflow.idempotency = None
    c.sql_session.begin()
    first = await unkeyed(c)
    c.sql_session.begin()
    second = await unkeyed(c)
    assert first["id"] != second["id"]
    assert c.sql_session.execute(text("SELECT count(*) FROM construction_projects")).scalar_one() == 2
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 2
    for table in ("tx_test_result", "tx_workflow_response"):
        assert c.sql_session.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 0
    assert c.session.commit.await_count == 2
    assert not c.records.records


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "projection", "commit"])
async def test_real_unkeyed_failure_rolls_back_flushed_business_and_audit(real_workflow, failure):
    c = real_workflow
    c.workflow.idempotency = None
    if failure == "audit":
        c.events.fail_after_insert = True
    elif failure == "projection":
        c.workflow.projector = lambda *args, **kwargs: {"private": "invalid-response"}
    else:
        c.session.commit.side_effect = RuntimeError("private-commit-detail")
    c.sql_session.begin()
    with pytest.raises(ConstructionProjectOperationError):
        await unkeyed(c)
    for table in ("construction_projects", "tx_test_audit", "tx_test_result", "tx_workflow_response"):
        assert c.sql_session.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 0
    c.session.rollback.assert_awaited_once()
    assert not c.records.records
