import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, UUID

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_lead_conversion import (
    ConstructionLeadConversionNotFoundError, ConstructionLeadConversionOperationError,
)
from services.construction.construction_lead_conversion_workflow import (
    LEAD_CONVERT_OPERATION, CONVERTED_RESPONSE_FIELDS, ConstructionLeadConversionWorkflow,
    ConstructionLeadConversionInProgressError, build_construction_lead_conversion_workflow,
)
from services.construction.construction_lead_rules import ConstructionLeadValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_lead_conversion_service import conversion_case, sql_conversion, counts
from test_construction_1a_lead_create_workflow import MemoryRecords, SqlRecords



def _with_project_currency_fixture(value):
    """Explicit organization currency for pre-existing isolated service doubles."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    target = getattr(value, "service", value)
    target.currency_reader = SimpleNamespace(
        session=target.session, get_for_tenant=AsyncMock(return_value="EUR"),
    )
    return value

@pytest.fixture
def workflow_case(conversion_case):
    c = conversion_case
    c.order = []
    original = c.repo.complete_conversion.side_effect
    async def complete(**kwargs):
        c.order.append("convert")
        c.lead = await original(**kwargs)
        c.repo.get_active_lead_for_update.return_value = c.lead
        return c.lead
    c.repo.complete_conversion.side_effect = complete
    async def audit(**kwargs):
        c.order.append("audit")
    async def commit():
        c.order.append("commit")
    async def rollback():
        c.order.append("rollback")
    c.events.create_event.side_effect = audit
    c.session.commit.side_effect, c.session.rollback.side_effect = commit, rollback
    c.records = MemoryRecords(c.session, c.order)
    c.codec = ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode())
    c.idempotency = ApiIdempotencyService(repository=c.records, response_codec=c.codec)
    c.workflow = ConstructionLeadConversionWorkflow(session=c.session, conversion_service=c.service,
        idempotency_service=c.idempotency)
    return c


def command(c, mode="new", **changes):
    values = dict(actor=c.actor, access_context=c.context, lead_id=c.lead.id,
        expected_row_version=4, client_id=None if mode == "new" else c.client.id,
        project_id=c.project.id if mode == "existing" else None,
        client_type="person" if mode == "new" else None,
        project_name=None if mode == "existing" else "Project name",
        project_address_raw=None, idempotency_key="conversion-private-key", trace_id="convert-trace")
    values.update(changes)
    return values


async def run(c, mode="new", **changes):
    return await c.workflow.convert_lead(**command(c, mode, **changes))


def stored(c):
    return next(iter(c.records.rows.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
async def test_conversion_and_encrypted_bound_completion_commit_once(workflow_case, mode):
    c = workflow_case
    response = await run(c, mode)
    assert set(response) == CONVERTED_RESPONSE_FIELDS
    assert response == dict(lead_id=str(c.lead.id), tenant_id=str(c.tenant), client_id=str(c.client.id),
        project_id=str(c.project.id), status="won", row_version=5)
    assert c.order == ["reserve", "convert", "audit", "complete", "commit"]
    record = stored(c)
    assert record.response_status == 200 and record.expires_at == c.now + timedelta(hours=24)
    snapshot = c.codec.decrypt(record.response_ciphertext)
    assert snapshot["data"] == response
    assert snapshot["binding"] == dict(tenant_id=str(c.tenant), principal_type="user",
        principal_id=str(c.user), operation=LEAD_CONVERT_OPERATION,
        key_hash=record.key_hash, request_hash=record.request_hash)
    assert "binding" not in response and "response_ciphertext" not in response
    assert b"conversion-private-key" not in record.response_ciphertext
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_replay_returns_original_snapshot_after_later_lead_version_change(workflow_case):
    c = workflow_case
    first = await run(c)
    c.lead.row_version = 9
    c.order.clear()
    assert await run(c) == first
    assert c.order == ["reserve", "commit"]
    c.repo.complete_conversion.assert_awaited_once()
    c.clients.create_client.assert_awaited_once()
    c.projects.create_project.assert_awaited_once()
    assert c.events.create_event.await_count == 1
    assert c.repo.get_active_lead_for_update.await_count >= 3


@pytest.mark.asyncio
async def test_different_key_on_already_won_lead_records_replay_without_domain_mutation(workflow_case):
    c = workflow_case
    first = await run(c)
    c.order.clear()
    second = await run(c, idempotency_key="another-key")
    assert first == second
    assert c.order == ["reserve", "complete", "commit"]
    assert len(c.records.rows) == 2
    c.repo.complete_conversion.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"project_name": "Changed"}, {"project_name": " Project name "},
    {"expected_row_version": 5}, {"project_address_raw": "Other address"}, {"client_type": "company"},
    {"lead_id": uuid4()},
])
async def test_same_key_different_raw_body_or_path_returns_key_reused(workflow_case, changes):
    c = workflow_case
    await run(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, **changes)
    assert c.session.commit.await_count == 1 and c.session.rollback.await_count == 1
    c.repo.complete_conversion.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", " ", True, 1, "x"*256])
async def test_required_key_is_checked_before_reservation(workflow_case, key):
    c = workflow_case
    with pytest.raises(ConstructionLeadValidationError):
        await run(c, idempotency_key=key)
    assert not c.records.rows
    c.repo.complete_conversion.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_processing_reservation_returns_409_without_another_conversion(workflow_case):
    c = workflow_case
    await run(c)
    row = stored(c)
    row.status, row.response_status, row.response_ciphertext = "processing", None, None
    with pytest.raises(ConstructionLeadConversionInProgressError) as caught:
        await run(c)
    assert caught.value.status_code == 409
    c.repo.complete_conversion.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "actor"])
async def test_current_access_denies_before_reservation(workflow_case, replay, failure):
    c = workflow_case
    if replay:
        await run(c)
    previous = list(c.order)
    if failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "actor":
        c.actor.status = "blocked"
    elif failure == "revoked":
        c.access.list_active_grants.return_value[0].status = "revoked"
    else:
        c.access.list_active_grants.return_value[0].expires_at = c.now
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    assert c.order == previous + ["rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", [False, True])
async def test_revocation_after_reservation_is_checked_again(workflow_case, replay):
    c = workflow_case
    if replay:
        await run(c)
    original = c.records.reserve_record
    async def revoke(**kwargs):
        result = await original(**kwargs)
        c.access.list_active_grants.return_value[0].status = "revoked"
        return result
    c.records.reserve_record = revoke
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    assert c.repo.complete_conversion.await_count == (1 if replay else 0)
    assert c.session.commit.await_count == (1 if replay else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["lead", "client", "project"])
@pytest.mark.parametrize("failure", ["missing", "deleted", "foreign"])
async def test_replay_checks_availability_of_every_linked_resource(workflow_case, target, failure):
    c = workflow_case
    await run(c)
    row = getattr(c, target)
    method = {"lead": c.repo.get_active_lead_for_update,
        "client": c.clients.get_active_client_for_update, "project": c.projects.get_active_project_for_update}[target]
    if failure == "missing":
        method.return_value = None
    elif failure == "deleted":
        row.deleted_at = c.now
    else:
        row.tenant_id = uuid4()
    with pytest.raises(ConstructionLeadConversionNotFoundError):
        await run(c)
    c.repo.complete_conversion.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["not_won", "client_link", "project_link", "regressed_version", "ownership"])
async def test_replay_cannot_mutate_if_live_relationships_do_not_match_snapshot(workflow_case, failure):
    c = workflow_case
    await run(c)
    if failure == "not_won":
        c.lead.status = "contacted"
    elif failure == "client_link":
        c.lead.converted_client_id = uuid4()
    elif failure == "project_link":
        c.lead.converted_project_id = uuid4()
    elif failure == "regressed_version":
        c.lead.row_version = 1
    else:
        c.project.client_id = uuid4()
    with pytest.raises((ConstructionLeadConversionOperationError, ConstructionLeadValidationError)):
        await run(c)
    c.repo.complete_conversion.assert_awaited_once()
    c.clients.create_client.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "tenant_id", "principal_type", "principal_id", "operation",
    "key_hash", "request_hash", "expires_at"])
async def test_reservation_context_must_match_current_command(workflow_case, field):
    c = workflow_case
    await run(c)
    row = stored(c)
    setattr(row, field, None if field == "id" else uuid4() if field in ("tenant_id", "principal_id") else
        c.now.replace(tzinfo=None) if field == "expires_at" else "other")
    with pytest.raises((ConstructionLeadConversionOperationError, ApiIdempotencyKeyReusedError)):
        await run(c)
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["binding", "extra", "tenant", "lead", "version", "ciphertext",
    "client_id", "project_id", "status"])
async def test_corrupt_or_swapped_snapshot_is_rejected(workflow_case, failure):
    c = workflow_case
    await run(c)
    row = stored(c)
    snapshot = c.codec.decrypt(row.response_ciphertext)
    if failure == "binding":
        snapshot["binding"]["key_hash"] = "other"
    elif failure == "extra":
        snapshot["data"]["secret"] = "hidden"
    elif failure in ("tenant", "lead", "client_id", "project_id"):
        name = {"tenant": "tenant_id", "lead": "lead_id"}.get(failure, failure)
        snapshot["data"][name] = str(uuid4())
    elif failure == "version":
        snapshot["data"]["row_version"] = True
    elif failure == "status":
        snapshot["data"]["status"] = "lost"
    else:
        row.response_ciphertext = b"corrupt"
    if failure != "ciphertext":
        row.response_ciphertext = c.codec.encrypt(snapshot)
    with pytest.raises(ConstructionLeadConversionOperationError):
        await run(c)
    c.repo.complete_conversion.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reserve", "audit", "complete", "commit", "bad_complete", "stale", "result"])
async def test_failure_rolls_back_and_never_commits_incomplete_conversion(workflow_case, failure):
    c = workflow_case
    if failure == "reserve":
        c.records.reserve_record = AsyncMock(side_effect=RuntimeError("private-error"))
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-error")
    elif failure == "complete":
        c.records.fail_complete = True
    elif failure == "commit":
        c.session.commit.side_effect = RuntimeError("private-error")
    elif failure == "bad_complete":
        original = c.records.complete_record
        async def corrupt(**kwargs):
            row = await original(**kwargs)
            row.response_ciphertext = c.codec.encrypt({"binding": {}, "data": {}})
            return row
        c.records.complete_record = corrupt
    elif failure == "stale":
        c.lead.row_version = 5
    else:
        original = c.service.convert_lead
        async def corrupt(**kwargs):
            result = await original(**kwargs)
            result.project.client_id = uuid4()
            return result
        c.service.convert_lead = corrupt
    with pytest.raises(ConstructionRowVersionConflictError if failure == "stale" else ConstructionLeadConversionOperationError) as caught:
        await run(c)
    assert "private-error" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    assert c.session.commit.await_count == (failure == "commit")


def test_factory_wires_one_session_and_requires_encryption(workflow_case):
    c = workflow_case
    c.session.execute, c.session.flush, c.session.add = AsyncMock(), AsyncMock(), Mock()
    with pytest.raises(ValueError):
        _with_project_currency_fixture(build_construction_lead_conversion_workflow(session=c.session, encryption_key=""))
    workflow = _with_project_currency_fixture(build_construction_lead_conversion_workflow(session=c.session,
        encryption_key=Fernet.generate_key().decode(), now_provider=lambda: c.now))
    assert workflow.session is workflow.service.session is workflow.idempotency.repository.session is c.session


@pytest.mark.parametrize("target", ["lead", "client", "project", "access", "event", "idempotency"])
def test_mixed_sessions_are_rejected(workflow_case, target):
    c = workflow_case
    item = {"lead": c.repo, "client": c.clients, "project": c.projects, "access": c.access,
        "event": c.events, "idempotency": c.records}[target]
    item.session = object()
    with pytest.raises(ValueError):
        ConstructionLeadConversionWorkflow(session=c.session, conversion_service=c.service,
            idempotency_service=c.idempotency)


@pytest.fixture
def sql_workflow(sql_conversion):
    c = sql_conversion
    c.sync.execute(text(
        "CREATE TABLE lead_test_idempotency(id TEXT PRIMARY KEY,tenant_id TEXT,principal_type TEXT,"
        "principal_id TEXT,operation TEXT,key_hash TEXT,request_hash TEXT,expires_at TEXT,status TEXT,"
        "response_status INTEGER,response_ciphertext BLOB,UNIQUE(tenant_id,principal_type,principal_id,operation,key_hash))"))
    c.sync.commit()
    c.records = SqlRecords(SimpleNamespace(session=c.session, sync=c.sync))
    c.records.session = c.session
    # Reuse the local SQL ledger helper with the actual transaction-owning adapter.
    c.session.sync = c.sync
    c.codec = ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode())
    c.idempotency = ApiIdempotencyService(repository=c.records, response_codec=c.codec)
    c.workflow = ConstructionLeadConversionWorkflow(session=c.session, conversion_service=c.service,
        idempotency_service=c.idempotency)
    c.sync.begin()
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
async def test_sql_commits_targets_lead_single_audit_and_ledger_then_replays(sql_workflow, mode):
    c = sql_workflow
    first = await run(c, mode)
    assert counts(c) == (2 if mode == "new" else 1, 1 if mode == "existing" else 2, 1, 0)
    assert c.sync.execute(text("SELECT count(*) FROM lead_test_idempotency")).scalar_one() == 1
    c.sync.rollback()
    c.sync.begin()
    assert await run(c, mode) == first
    assert c.session.commit.await_count == 2
    assert counts(c) == (2 if mode == "new" else 1, 1 if mode == "existing" else 2, 1, 0)
    c.sync.rollback()
    c.sync.begin()
    assert await run(c, mode, idempotency_key="second-key") == first
    assert c.sync.execute(text("SELECT count(*) FROM lead_test_idempotency")).scalar_one() == 2
    assert counts(c)[2] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "conditional"])
async def test_sql_failure_rolls_back_targets_lead_audit_and_completed_reservation(sql_workflow, failure):
    c = sql_workflow
    if failure == "audit":
        c.events.fail_after_insert = True
    elif failure == "complete":
        c.records.fail_complete = True
    elif failure == "conditional":
        c.repo.fail_conditional = True
    else:
        c.session.commit.side_effect = RuntimeError("private-error")
    with pytest.raises(ConstructionLeadConversionOperationError):
        await run(c)
    assert counts(c) == (1, 1, 0, 0)
    assert c.sync.execute(text("SELECT count(*) FROM lead_test_idempotency")).scalar_one() == 0
    from database.models import ConstructionLead
    lead = c.sync.get(ConstructionLead, c.lead.id)
    assert lead.status == "contacted" and lead.row_version == 4
    assert lead.converted_client_id is None and lead.converted_project_id is None
    c.session.rollback.assert_awaited_once()
