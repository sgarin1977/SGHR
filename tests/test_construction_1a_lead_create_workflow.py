import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from services.api_idempotency import ApiIdempotencyKeyReusedError, ApiIdempotencyService, ApiIdempotencyResponseCodec
from services.construction_lead_create import ConstructionLeadOperationError
from services.construction_lead_create_workflow import (
    LEAD_CREATE_OPERATION, ConstructionLeadCreateWorkflow, ConstructionLeadIdempotencyInProgressError,
    ConstructionLeadReplayUnavailableError, build_construction_lead_create_workflow,
)
from services.construction_lead_response import PUBLIC_LEAD_CREATED_FIELDS
from services.construction_lead_rules import ConstructionLeadValidationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_lead_create_service import lead_create_case, sql_lead_create
from test_construction_1a_lead_model import TABLE, lead_db


class MemoryRecords:
    def __init__(self, session, order):
        self.session, self.order, self.rows = session, order, {}
        self.fail_complete = False

    async def reserve_record(self, **kwargs):
        self.order.append("reserve")
        key = tuple(kwargs[field] for field in ("tenant_id", "principal_type", "principal_id", "operation", "key_hash"))
        existing = self.rows.get(key)
        if existing is not None and existing.expires_at > kwargs["now"]:
            return existing, False
        fields = {field: value for field, value in kwargs.items() if field != "now"}
        row = SimpleNamespace(**fields, id=uuid4(), status="processing",
            response_status=None, response_ciphertext=None, completed_at=None)
        self.rows[key] = row
        return row, True

    async def complete_record(self, **kwargs):
        self.order.append("complete")
        row = kwargs["record"]
        row.status = "completed"
        for field in ("response_status", "response_ciphertext", "completed_at"):
            setattr(row, field, kwargs[field])
        if self.fail_complete:
            raise RuntimeError("private-completion-error")
        return row


@pytest.fixture
def workflow_case(lead_create_case):
    c = lead_create_case
    c.order = []
    original = c.repo.create_lead.side_effect
    async def create(**kwargs):
        c.order.append("lead")
        row = await original(**kwargs)
        row.internal_note = "private-internal"
        return row
    c.repo.create_lead.side_effect = create
    c.repo.get_active_lead = AsyncMock(side_effect=lambda **kw: c.row)
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
    c.workflow = ConstructionLeadCreateWorkflow(session=c.session, lead_service=c.service,
        idempotency_service=c.idempotency)
    return c


def command(c, **changes):
    result = {field: value for field, value in asdict(c.data).items() if field != "status"}
    result.update(actor=c.actor, access_context=c.context, idempotency_key="lead-test-private-key", trace_id="lead-test")
    result.update(changes)
    return result


async def run(c, **changes):
    return await c.workflow.create_lead(**command(c, **changes))


def stored(c):
    return next(iter(c.records.rows.values()))


@pytest.mark.asyncio
async def test_fresh_creation_commits_lead_audit_and_encrypted_bound_result_once(workflow_case):
    c = workflow_case
    response = await run(c)
    assert set(response) == PUBLIC_LEAD_CREATED_FIELDS
    assert response["id"] == str(c.row.id) and response["tenant_id"] == str(c.tenant)
    assert response["status"] == "new" and response["row_version"] == 1
    assert c.order == ["reserve", "lead", "audit", "complete", "commit"]
    record = stored(c)
    payload = c.codec.decrypt(record.response_ciphertext)
    assert payload["data"] == response
    assert payload["binding"] == dict(tenant_id=str(c.tenant), principal_type="user", principal_id=str(c.user),
        operation=LEAD_CREATE_OPERATION, key_hash=record.key_hash, request_hash=record.request_hash)
    assert record.key_hash == hashlib.sha256(b"lead-test-private-key").hexdigest()
    assert record.response_status == 201 and record.expires_at == c.now + timedelta(hours=24)
    assert all(secret not in record.response_ciphertext for secret in
        (b"private@example.com", b"Private client", b"private-note", b"lead-test-private-key"))
    assert "internal_note" not in response and "binding" not in response
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_replay_returns_original_snapshot_after_later_business_edits(workflow_case):
    c = workflow_case
    original = await run(c)
    c.row.name, c.row.status, c.row.row_version = "Later name", "contacted", 2
    c.order.clear()
    assert await run(c) == original
    assert c.order == ["reserve", "commit"]
    c.repo.create_lead.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    assert c.access.has_active_platform_membership.await_count >= 4
    c.repo.get_active_lead.assert_awaited_once_with(tenant_id=c.tenant, lead_id=UUID(original["id"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"name": "Another"}, {"source": "another_channel"}, {"comment": "another"},
    {"name": " Private client "},
])
async def test_same_key_with_changed_supplied_command_cannot_create_second_lead(workflow_case, changes):
    c = workflow_case
    await run(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, **changes)
    c.repo.create_lead.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    assert c.session.commit.await_count == 1 and c.session.rollback.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   ", True, 17, "x"*256])
async def test_required_key_is_checked_before_reservation(workflow_case, key):
    c = workflow_case
    with pytest.raises(ConstructionLeadValidationError):
        await run(c, idempotency_key=key)
    assert not c.records.rows
    c.repo.create_lead.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_processing_reservation_returns_conflict_without_business_mutation(workflow_case):
    c = workflow_case
    await run(c)
    record = stored(c)
    record.status, record.response_status, record.response_ciphertext = "processing", None, None
    with pytest.raises(ConstructionLeadIdempotencyInProgressError) as caught:
        await run(c)
    assert caught.value.status_code == 409
    c.repo.create_lead.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "actor"])
async def test_current_access_denies_fresh_and_replay_before_reservation(workflow_case, replay, failure):
    c = workflow_case
    if replay:
        await run(c)
    previous = list(c.order)
    if failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "actor":
        c.actor.status = "blocked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.status = "revoked"
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    assert c.order == previous + ["rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "deleted", "foreign"])
async def test_unavailable_created_lead_denies_replay(workflow_case, failure):
    c = workflow_case
    await run(c)
    if failure == "missing":
        c.repo.get_active_lead.side_effect = None
        c.repo.get_active_lead.return_value = None
    elif failure == "deleted":
        c.row.deleted_at = c.now
    else:
        c.row.tenant_id = uuid4()
    with pytest.raises(ConstructionLeadReplayUnavailableError):
        await run(c)
    c.repo.create_lead.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "principal_id", "operation", "key_hash", "request_hash", "expires_at"])
async def test_reservation_cannot_cross_its_context(workflow_case, field):
    c = workflow_case
    await run(c)
    record = stored(c)
    setattr(record, field, uuid4() if field in ("tenant_id", "principal_id") else
        c.now.replace(tzinfo=None) if field == "expires_at" else "wrong")
    with pytest.raises((ConstructionLeadOperationError, ApiIdempotencyKeyReusedError)):
        await run(c)
    c.repo.create_lead.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["binding", "extra", "tenant", "version", "ciphertext"])
async def test_corrupt_or_swapped_snapshot_is_not_returned(workflow_case, failure):
    c = workflow_case
    await run(c)
    record = stored(c)
    payload = c.codec.decrypt(record.response_ciphertext)
    if failure == "binding":
        payload["binding"]["principal_id"] = str(uuid4())
    elif failure == "extra":
        payload["data"]["private_internal"] = "hidden"
    elif failure == "tenant":
        payload["data"]["tenant_id"] = str(uuid4())
    elif failure == "version":
        payload["data"]["row_version"] = True
    else:
        record.response_ciphertext = b"invalid"
    if failure != "ciphertext":
        record.response_ciphertext = c.codec.encrypt(payload)
    with pytest.raises(ConstructionLeadOperationError):
        await run(c)
    assert c.session.commit.await_count == 1
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_ciphertext_from_another_key_is_rejected_and_principal_isolation_preserved(workflow_case):
    c = workflow_case
    await run(c)
    first = stored(c)
    await run(c, idempotency_key="second-key")
    second = list(c.records.rows.values())[1]
    first.response_ciphertext = second.response_ciphertext
    with pytest.raises(ConstructionLeadOperationError):
        await run(c)
    c.actor.user_id = c.user = uuid4()
    c.context = type(c.context)(c.user, c.tenant, c.context.roles, c.context.permissions, c.context.grants)
    await run(c)
    assert len(c.records.rows) == 3
    assert list(c.records.rows.values())[-1].principal_id == c.user


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "projection", "commit", "transaction", "data"])
async def test_failures_roll_back_once_and_never_expose_private_errors(workflow_case, failure):
    c = workflow_case
    if failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit")
    elif failure == "complete":
        c.records.fail_complete = True
    elif failure == "projection":
        original = c.repo.create_lead.side_effect
        async def broken(**kwargs):
            row = await original(**kwargs)
            row.phone = 17
            return row
        c.repo.create_lead.side_effect = broken
    elif failure == "commit":
        c.session.commit.side_effect = RuntimeError("private-commit")
    elif failure == "transaction":
        c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionLeadValidationError if failure == "data" else ConstructionLeadOperationError) as caught:
        await run(c, **({"work_type_description": " "} if failure == "data" else {}))
    assert "private-" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    if failure != "commit":
        c.session.commit.assert_not_called()


def test_factory_requires_real_codec_and_shared_session(workflow_case):
    c = workflow_case
    with pytest.raises(ValueError):
        build_construction_lead_create_workflow(session=c.session, encryption_key="")
    workflow = build_construction_lead_create_workflow(session=c.session,
        encryption_key=Fernet.generate_key().decode(), now_provider=lambda: c.now)
    assert workflow.service.session is c.session and workflow.idempotency.repository.session is c.session
    c.records.session = object()
    with pytest.raises(ValueError):
        ConstructionLeadCreateWorkflow(session=c.session, lead_service=c.service, idempotency_service=c.idempotency)


@pytest.mark.asyncio
async def test_normalized_creation_and_responsible_are_replayed_from_original_raw_command(workflow_case):
    c = workflow_case
    changes = dict(source=" Partner_Portal ", name=" Private client ",
        work_type_description=" Ремонт стін ", phone=" +351 123 ", email=" person@example.com ",
        address_raw=" Lisbon ", comment="  note  ", responsible_user_id=uuid4())
    response = await run(c, **changes)
    assert (response["source"], response["name"], response["work_type_description"], response["phone"],
        response["email"], response["address_raw"], response["comment"]) == (
        "partner_portal", "Private client", "Ремонт стін", "+351 123", "person@example.com", "Lisbon", "note")
    assert response["responsible_user_id"] == str(changes["responsible_user_id"])
    assert await run(c, **changes) == response
    c.repo.create_lead.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", [False, True])
async def test_access_lost_after_reservation_cannot_mutate_or_return_contacts(workflow_case, replay):
    c = workflow_case
    if replay:
        await run(c)
    original = c.records.reserve_record
    async def revoke(**kwargs):
        result = await original(**kwargs)
        c.grant.status = "revoked"
        return result
    c.records.reserve_record = revoke
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    assert c.repo.create_lead.await_count == (1 if replay else 0)
    assert c.events.create_event.await_count == (1 if replay else 0)
    assert c.session.commit.await_count == (1 if replay else 0)
    c.session.rollback.assert_awaited_once()


class SqlRecords:
    def __init__(self, session):
        self.session, self.fail_complete = session, False

    async def reserve_record(self, **kwargs):
        fields = {field: str(kwargs[field]) for field in
            ("tenant_id", "principal_type", "principal_id", "operation", "key_hash", "request_hash")}
        where = "tenant_id=:tenant_id AND principal_type=:principal_type AND principal_id=:principal_id AND operation=:operation AND key_hash=:key_hash"
        result = self.session.sync.execute(text("SELECT * FROM lead_test_idempotency WHERE "+where), fields).mappings().first()
        if result:
            values = dict(result)
            for field in ("id", "tenant_id", "principal_id"):
                values[field] = UUID(values[field])
            values["expires_at"] = datetime.fromisoformat(values["expires_at"])
            return SimpleNamespace(**values), False
        fields.update(id=str(uuid4()), expires_at=kwargs["expires_at"].isoformat(), status="processing")
        self.session.sync.execute(text(
            "INSERT INTO lead_test_idempotency(id,tenant_id,principal_type,principal_id,operation,key_hash,request_hash,expires_at,status)"
            " VALUES(:id,:tenant_id,:principal_type,:principal_id,:operation,:key_hash,:request_hash,:expires_at,:status)"
        ), fields)
        values = dict(fields)
        for field in ("id", "tenant_id", "principal_id"):
            values[field] = UUID(values[field])
        values["expires_at"] = kwargs["expires_at"]
        return SimpleNamespace(**values, response_status=None, response_ciphertext=None), True

    async def complete_record(self, **kwargs):
        row = kwargs["record"]
        self.session.sync.execute(text(
            "UPDATE lead_test_idempotency SET status='completed',response_status=:status,response_ciphertext=:cipher WHERE id=:id"
        ), dict(id=str(row.id), status=kwargs["response_status"], cipher=kwargs["response_ciphertext"]))
        row.status, row.response_status, row.response_ciphertext = "completed", kwargs["response_status"], kwargs["response_ciphertext"]
        if self.fail_complete:
            raise RuntimeError("private-completed-after-write")
        return row


@pytest.fixture
def sql_workflow(sql_lead_create):
    c = sql_lead_create
    c.adapter.sync.execute(text(
        "CREATE TABLE lead_test_idempotency(id TEXT PRIMARY KEY,tenant_id TEXT,principal_type TEXT,"
        "principal_id TEXT,operation TEXT,key_hash TEXT,request_hash TEXT,expires_at TEXT,status TEXT,"
        "response_status INTEGER,response_ciphertext BLOB,UNIQUE(tenant_id,principal_type,principal_id,operation,key_hash))"
    ))
    c.adapter.sync.commit()
    c.records = SqlRecords(c.adapter)
    c.idempotency = ApiIdempotencyService(repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode()))
    c.workflow = ConstructionLeadCreateWorkflow(session=c.adapter, lead_service=c.service, idempotency_service=c.idempotency)
    c.adapter.sync.begin()
    return c


@pytest.mark.asyncio
async def test_real_database_commits_three_records_and_replays_without_duplicates(sql_workflow):
    c = sql_workflow
    first = await run(c)
    assert c.adapter.commit.await_count == 1
    c.adapter.sync.begin()
    assert await run(c) == first
    assert c.adapter.commit.await_count == 2
    assert len(c.adapter.sync.execute(select(TABLE.c.id)).all()) == 1
    for table in ("lead_test_audit", "lead_test_idempotency"):
        assert c.adapter.sync.execute(text("SELECT count(*) FROM "+table)).scalar_one() == 1
    c.adapter.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_real_database_rolls_back_lead_audit_and_reservation_together(sql_workflow, failure):
    c = sql_workflow
    if failure == "audit":
        c.events.fail_after_insert = True
    elif failure == "complete":
        c.records.fail_complete = True
    else:
        c.adapter.commit.side_effect = RuntimeError("private-forced-commit")
    with pytest.raises(ConstructionLeadOperationError):
        await run(c)
    c.adapter.rollback.assert_awaited_once()
    assert c.adapter.sync.execute(select(TABLE.c.id)).all() == []
    for table in ("lead_test_audit", "lead_test_idempotency"):
        assert c.adapter.sync.execute(text("SELECT count(*) FROM "+table)).scalar_one() == 0
