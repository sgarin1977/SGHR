import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from api.routes.construction_project_read import ProjectReadResponse, public_project
from services.api_idempotency import ApiIdempotencyKeyReusedError, ApiIdempotencyService
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionPermissionDeniedError
from services.construction.construction_project_create_workflow import (
    ConstructionProjectCreateWorkflow, ConstructionProjectIdempotencyInProgressError,
    PROJECT_CREATE_OPERATION, PUBLIC_PROJECT_FIELDS, build_construction_project_create_workflow,
)
from services.construction.construction_projects import (
    ConstructionProjectNotFoundError, ConstructionProjectOperationError, ConstructionProjectValidationError,
)
from test_construction_01_project_create_transaction import create_case, real_create



def _with_project_currency_fixture(value):
    """Explicit organization currency for pre-existing isolated service doubles."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    target = getattr(value, "service", value)
    target.currency_reader = SimpleNamespace(
        session=target.session, get_for_tenant=AsyncMock(return_value="EUR"),
    )
    return value

class JsonCodec:
    def encrypt(self, value):
        return json.dumps(value).encode("utf-8")

    def decrypt(self, value):
        return json.loads(value.decode("utf-8"))


class MemoryRecords:
    def __init__(self, session, order):
        self.session, self.order, self.records = session, order, {}

    async def reserve_record(self, **kwargs):
        self.order.append("reserve")
        key = tuple(kwargs[name] for name in (
            "tenant_id", "principal_type", "principal_id", "operation", "key_hash",
        ))
        if key in self.records:
            return self.records[key], False
        record = SimpleNamespace(
            **kwargs, id=uuid4(), status="processing", response_status=None,
            response_ciphertext=None, completed_at=None,
        )
        self.records[key] = record
        return record, True

    async def complete_record(self, **kwargs):
        self.order.append("complete")
        record = kwargs.pop("record")
        for name, value in kwargs.items():
            setattr(record, name, value)
        record.status = "completed"
        return record


@pytest.fixture
def workflow_case(create_case):
    c = create_case
    c.order = []
    c.clients.session = c.access.session = c.session
    now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
    for name in (
        "address_formatted", "country_code", "region", "city", "postal_code",
        "latitude", "longitude", "address_provider", "provider_place_id",
        "client_id", "responsible_user_id",
    ):
        setattr(c.project, name, None)
    c.project.created_by = c.user
    c.project.created_at = c.project.updated_at = now
    c.project.deleted_at = None
    c.project.address_verification_status = "pending"
    c.project.private_marker = "private-orm-field"
    async def persist(**kwargs):
        c.order.append("create")
        for name in ("name", "address_raw", "client_id", "responsible_user_id", "comment", "created_by"):
            setattr(c.project, name, kwargs[name])
        return c.project
    c.repo.create_project.side_effect = persist
    c.repo.get_active_project = AsyncMock(return_value=c.project)
    c.events.create_event.side_effect = lambda **kwargs: c.order.append("audit")
    c.session.commit.side_effect = lambda: c.order.append("commit")
    c.session.rollback.side_effect = lambda: c.order.append("rollback")
    c.records = MemoryRecords(c.session, c.order)
    c.idempotency = ApiIdempotencyService(repository=c.records, response_codec=JsonCodec())
    c.workflow = ConstructionProjectCreateWorkflow(
        session=c.session, project_service=c.service, idempotency_service=c.idempotency,
        response_projector=public_project, now_provider=lambda: now,
    )
    return c


async def run(c, **changes):
    arguments = dict(
        actor=c.actor, access_context=c.context,
        name="  Transaction project  ", address_raw="  Project address  ",
        comment="Project comment", idempotency_key="project-key", trace_id="project-workflow",
    )
    arguments.update(changes)
    return await c.workflow.create_project(**arguments)


@pytest.mark.asyncio
async def test_fresh_project_audit_and_response_have_one_commit(workflow_case):
    c = workflow_case
    result = await run(c)
    assert c.order == ["reserve", "create", "audit", "complete", "commit"]
    assert set(result) == PUBLIC_PROJECT_FIELDS == set(ProjectReadResponse.model_fields)
    assert result["id"] == str(c.project.id) and result["row_version"] == 1
    assert "private-orm-field" not in json.dumps(result)
    record = next(iter(c.records.records.values()))
    assert record.operation == PROJECT_CREATE_OPERATION
    assert record.principal_id == c.user and record.tenant_id == c.tenant
    assert record.response_status == 201
    assert c.idempotency.response_codec.decrypt(record.response_ciphertext) == result
    assert "project-key" not in json.dumps(c.events.create_event.await_args.kwargs, default=str)
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_replay_returns_original_response_after_project_update_without_duplicate_audit(workflow_case):
    c = workflow_case
    original = await run(c)
    c.project.name, c.project.row_version, c.project.status = "Changed later", 2, "active"
    assert await run(c) == original
    c.repo.create_project.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    assert c.order == ["reserve", "create", "audit", "complete", "commit", "reserve", "commit"]
    c.repo.get_active_project.assert_awaited_once_with(tenant_id=c.tenant, project_id=c.project.id)


@pytest.mark.asyncio
async def test_same_key_with_changed_body_uses_existing_platform_conflict(workflow_case):
    c = workflow_case
    await run(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, name="Different request")
    c.repo.create_project.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_processing_reservation_does_not_create_a_project(workflow_case):
    c = workflow_case
    result = await run(c)
    record = next(iter(c.records.records.values()))
    record.status, record.response_status = "processing", None
    with pytest.raises(ConstructionProjectIdempotencyInProgressError) as caught:
        await run(c)
    assert caught.value.code == "idempotency_in_progress"
    assert result["id"] == str(c.project.id)
    c.repo.create_project.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_lost_permission_denies_replay_before_reservation(workflow_case):
    c = workflow_case
    await run(c)
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",), (), c.context.grants)
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    assert c.order.count("reserve") == 1
    c.repo.get_active_project.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign", "deleted", "creator", "wrong_id"])
async def test_replay_rejects_unavailable_project_without_recreation(workflow_case, failure):
    c = workflow_case
    await run(c)
    if failure == "missing": c.repo.get_active_project.return_value = None
    elif failure == "foreign": c.project.tenant_id = uuid4()
    elif failure == "deleted": c.project.deleted_at = c.project.updated_at
    elif failure == "creator": c.project.created_by = uuid4()
    else: c.project.id = uuid4()
    with pytest.raises(ConstructionProjectNotFoundError):
        await run(c)
    c.repo.create_project.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("tenant_id", "foreign"), ("principal_id", "foreign"), ("operation", "other"),
    ("key_hash", "other"),
])
async def test_reservation_identity_is_rechecked(workflow_case, field, value):
    c = workflow_case
    await run(c)
    record = next(iter(c.records.records.values()))
    setattr(record, field, value)
    with pytest.raises(ConstructionProjectOperationError):
        await run(c)
    c.repo.create_project.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra_field", "wrong_name", "wrong_version", "invalid_date"])
async def test_corrupt_saved_response_is_not_returned(workflow_case, failure):
    c = workflow_case
    saved = await run(c)
    if failure == "extra_field": saved["private_field"] = "private-detail"
    elif failure == "wrong_name": saved["name"] = "Other project"
    elif failure == "wrong_version": saved["row_version"] = True
    else: saved["created_at"] = "invalid-date"
    record = next(iter(c.records.records.values()))
    record.response_ciphertext = JsonCodec().encrypt(saved)
    with pytest.raises(ConstructionProjectOperationError):
        await run(c)
    c.repo.get_active_project.assert_not_called()
    c.repo.create_project.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"idempotency_key": False}, {"idempotency_key": ""}, {"name": " "},
    {"address_raw": ""}, {"client_id": "not-a-uuid"}, {"comment": 42},
])
async def test_keyed_workflow_rejects_invalid_input_before_reservation(workflow_case, changes):
    c = workflow_case
    with pytest.raises(ConstructionProjectValidationError):
        await run(c, **changes)
    assert "reserve" not in c.order
    c.repo.create_project.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["complete", "projection", "commit"])
async def test_failure_before_or_at_commit_is_rolled_back(workflow_case, failure):
    c = workflow_case
    if failure == "complete":
        c.idempotency.complete = AsyncMock(side_effect=RuntimeError("private-completion-detail"))
    elif failure == "projection":
        c.project.row_version = 0
    else:
        c.session.commit.side_effect = RuntimeError("private-commit-detail")
    with pytest.raises(ConstructionProjectOperationError) as caught:
        await run(c)
    assert "private-" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    if failure != "commit": c.session.commit.assert_not_called()


def test_factory_wires_every_repository_to_the_same_session(workflow_case):
    c = workflow_case
    workflow = _with_project_currency_fixture(build_construction_project_create_workflow(
        session=c.session, idempotency_encryption_key=Fernet.generate_key().decode(),
        response_projector=public_project,
    ))
    assert workflow.service.session is c.session
    assert workflow.service.repository.session is c.session
    assert workflow.service.client_repository.session is c.session
    assert workflow.service.access_repository.session is c.session
    assert workflow.service.audit_logger.event_repository.session is c.session
    assert workflow.idempotency.repository.session is c.session
    c.idempotency.repository.session = object()
    with pytest.raises(ValueError):
        ConstructionProjectCreateWorkflow(
            session=c.session, project_service=c.service,
            idempotency_service=c.idempotency, response_projector=public_project,
        )


@pytest.fixture
def real_workflow(real_create):
    from database.repositories.construction import ConstructionAccessRepository, ConstructionClientRepository
    c = real_create
    c.order = []
    c.service.client_repository = ConstructionClientRepository(c.session)
    c.service.access_repository = ConstructionAccessRepository(c.session)
    c.session.commit.side_effect = c.sql_session.commit
    c.session.rollback.side_effect = c.sql_session.rollback
    with c.sql_session.begin():
        c.sql_session.execute(text("CREATE TABLE tx_workflow_response (payload BLOB)"))
    class SqlMarkers(MemoryRecords):
        fail_completion = False
        async def reserve_record(self, **kwargs):
            record, fresh = await super().reserve_record(**kwargs)
            if fresh:
                c.sql_session.execute(text("INSERT INTO tx_test_result(id) VALUES(:id)"), {"id": str(record.id)})
            return record, fresh
        async def complete_record(self, **kwargs):
            record = await super().complete_record(**kwargs)
            c.sql_session.execute(
                text("INSERT INTO tx_workflow_response(payload) VALUES(:payload)"),
                {"payload": record.response_ciphertext},
            )
            if self.fail_completion:
                raise RuntimeError("forced-completion-failure")
            return record
    c.records = SqlMarkers(c.session, c.order)
    c.idempotency = ApiIdempotencyService(repository=c.records, response_codec=JsonCodec())
    c.workflow = ConstructionProjectCreateWorkflow(
        session=c.session, project_service=c.service,
        idempotency_service=c.idempotency, response_projector=public_project,
    )
    return c


@pytest.mark.asyncio
async def test_real_transaction_commits_project_audit_reservation_and_response(real_workflow):
    c = real_workflow
    c.sql_session.begin()
    response = await run(c)
    for table in ("construction_projects", "tx_test_audit", "tx_test_result", "tx_workflow_response"):
        assert c.sql_session.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 1
    saved = c.sql_session.execute(text("SELECT payload FROM tx_workflow_response")).scalar_one()
    assert JsonCodec().decrypt(saved) == response
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "completion"])
async def test_real_transaction_rolls_back_project_audit_and_replay_markers(real_workflow, failure):
    c = real_workflow
    if failure == "audit": c.events.fail_after_insert = True
    else: c.records.fail_completion = True
    c.sql_session.begin()
    with pytest.raises(ConstructionProjectOperationError):
        await run(c)
    for table in ("construction_projects", "tx_test_audit", "tx_test_result", "tx_workflow_response"):
        assert c.sql_session.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 0
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "foreign_context", "wrong_user", "project_only", "document_only"])
async def test_workflow_denies_invalid_actor_or_scope_before_reservation(workflow_case, failure):
    from services.construction.construction_permissions import ConstructionGrantContext, ConstructionScopeDeniedError
    c = workflow_case
    if failure == "inactive":
        c.actor.status = "blocked"
    else:
        tenant = uuid4() if failure == "foreign_context" else c.tenant
        user = uuid4() if failure == "wrong_user" else c.user
        grants = c.context.grants
        if failure in ("project_only", "document_only"):
            scope = "project" if failure == "project_only" else "document"
            grants = (ConstructionGrantContext("OWNER", scope, uuid4()),)
        c.context = ConstructionAccessContext(user, tenant, ("OWNER",), ("construction.projects.create",), grants)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    assert "reserve" not in c.order
    c.repo.create_project.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_requires_active_transaction_before_reservation(workflow_case):
    c = workflow_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionProjectOperationError):
        await run(c)
    assert "reserve" not in c.order
    c.repo.create_project.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_rejects_naive_clock_before_reservation(workflow_case):
    c = workflow_case
    c.workflow.now_provider = lambda: datetime(2026, 10, 8)
    with pytest.raises(ConstructionProjectOperationError):
        await run(c)
    assert "reserve" not in c.order
    c.repo.create_project.assert_not_called()
