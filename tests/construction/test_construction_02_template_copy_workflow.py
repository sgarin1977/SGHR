"""Shared encrypted receipts and local SQLite rollback; no native race claims."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import registry

from database.models import ApiIdempotencyRecord
from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyResponseCodec,
    ApiIdempotencyKeyReusedError, IDEMPOTENCY_RETENTION,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_template_copy import (
    ConstructionTemplateCopyValidationError as Validation,
    ConstructionTemplateCopyConflictError as Conflict,
)
from services.construction.construction_template_copy_workflow import (
    ConstructionTemplateCopyWorkflow as Workflow,
    ConstructionTemplateCopyInProgressError,
    OPERATION, RECORD_FIELDS, build_construction_template_copy_workflow,
)
from test_construction_02_template_copy import (
    case, onboarding, catalog_case, copy_case, put,
    snapshot as copy_snapshot,
)
from test_construction_02_tax_profiles import LocalUTC
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
def keyed_case(copy_case):
    c = copy_case
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalTemplateCopyReceipt", (), {})
    mapper.map_imperatively(Record, table)
    c.receipts, c.order, c.fail_complete = table, [], None
    c.session.flush = AsyncMock(side_effect=c.sync.flush)
    original_lock = c.serialization.lock_member.side_effect

    async def lock(**values):
        c.order.append("barrier")
        await original_lock(**values)
    c.serialization.lock_member.side_effect = lock
    original_commit = c.session.commit

    async def commit():
        c.order.append("commit")
        await original_commit()
    c.session.commit = commit
    c.records = LocalRecords(c, Record, table)
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(
            encryption_key=Fernet.generate_key().decode("ascii")))
    c.workflow = Workflow(
        session=c.session, copy_service=c.copier,
        idempotency_service=c.idempotency)
    original_copy = c.copier.copy_template

    async def perform_copy(**values):
        c.order.append("copy")
        return await original_copy(**values)
    c.copy_calls = AsyncMock(side_effect=perform_copy)
    c.copier.copy_template = c.copy_calls
    c.sync.begin()
    try:
        yield c
    finally:
        c.sync.close()
        mapper.dispose()


def rows(c, table):
    return [
        dict(row) for row in c.sync.execute(
            select(table).order_by(table.c.id)).mappings()
    ]


def snapshot(c):
    return copy_snapshot(c), rows(c, c.receipts)


async def run(c, *, key="template-copy-key", **changes):
    if not c.sync.in_transaction():
        c.sync.begin()
    values = dict(
        actor=c.actor, source_version_id=UUID(int=1001),
        idempotency_key=key, trace_id="template-copy-workflow")
    values.update(changes)
    return await c.workflow.copy_template(**values)


@pytest.mark.asyncio
async def test_copy_audit_and_encrypted_receipt_have_one_final_commit(keyed_case):
    c = keyed_case
    put(c)
    key = "raw-copy-key-must-not-be-stored"
    result = await run(c, key=key)
    assert c.session.commits == 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("copy")
    assert c.order.index("copy") < c.order.index("complete") < c.order.index("commit")
    assert c.copy_calls.await_args.kwargs["manage_transaction"] is False
    receipt = rows(c, c.receipts)[0]
    assert receipt["status"] == "completed" and receipt["response_status"] == 201
    wire = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    assert wire["result"]["template_id"] == str(result.template_id)
    assert wire["result"]["published_at"] == result.published_at.isoformat()
    assert str(result.template_id).encode() not in receipt["response_ciphertext"]
    assert key not in repr(snapshot(c))
    assert len(rows(c, c.copy_repo.templates)) == len(rows(c, c.audits)) == 1


@pytest.mark.asyncio
async def test_replay_returns_original_after_source_disable_and_copy_rename(keyed_case):
    c = keyed_case
    put(c)
    first = await run(c)
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == first.template_id
    ).values(code="LATER_NAME", active=False, row_version=2))
    c.sync.execute(update(c.catalog_templates).where(
        c.catalog_templates.c.id == UUID(int=101)).values(active=False))
    c.sync.commit()
    before = snapshot(c)
    c.catalog.get_version = AsyncMock(
        side_effect=AssertionError("Replay must not read the current source."))
    assert await run(c) == first
    assert snapshot(c) == before
    c.copy_calls.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    c.catalog.get_version.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"source_version_id": UUID(int=1002)}, {"code": "ANOTHER_COPY"},
])
async def test_changed_request_with_same_key_has_platform_conflict(keyed_case, changes):
    c = keyed_case
    put(c)
    await run(c)
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, **changes)
    assert snapshot(c) == before
    c.copy_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["actor", "tenant"])
async def test_same_key_is_scoped_to_canonical_actor_and_tenant(keyed_case, scope):
    c = keyed_case
    put(c)
    first = await run(c)
    if scope == "tenant":
        c.tenant = c.foreign
        c.actor.tenant_id = c.foreign
        c.grant.scope_id = c.foreign
        changes = {}
    else:
        c.user = uuid4()
        c.sync.execute(insert(c.table.metadata.tables["users"]).values(id=c.user))
        c.sync.commit()
        c.actor.user_id = c.user
        changes = {"code": "OTHER_ACTOR_COPY"}
    second = await run(c, **changes)
    assert second.template_id != first.template_id
    assert second.tenant_id == c.actor.tenant_id
    assert second.created_by == c.actor.user_id
    assert len(rows(c, c.receipts)) == len(rows(c, c.audits)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "scope", "expiry", "actor"])
async def test_replay_still_requires_fresh_settings_authority(keyed_case, gate):
    c = keyed_case
    put(c)
    await run(c)
    if gate == "membership":
        c.membership = False
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    elif gate == "expiry":
        c.grant.expires_at = c.now
    else:
        c.actor.status = "blocked"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    assert snapshot(c) == before
    c.copy_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "audit", "complete", "complete_cancel",
    "complete_authority", "ignored_complete", "commit",
])
async def test_failures_rollback_copy_audit_and_receipt(keyed_case, failure):
    c = keyed_case
    put(c)
    before = snapshot(c)
    expected = Operation
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    elif failure == "complete_cancel":
        import asyncio
        c.fail_complete = "cancel"
        expected = asyncio.CancelledError
    elif failure == "complete_authority":
        c.fail_complete = "membership"
        expected = ConstructionPermissionDeniedError
    elif failure == "ignored_complete":
        c.idempotency.complete = AsyncMock(return_value=None)
    else:
        c.session.fail_commit = True
    with pytest.raises(expected):
        await run(c)
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "tenant", "actor", "source", "code", "counter",
    "count", "time", "schema", "extra",
])
async def test_invalid_encrypted_receipt_context_or_schema_fails_closed(keyed_case, bad):
    c = keyed_case
    put(c)
    await run(c, code="MY_COPY")
    receipt = rows(c, c.receipts)[0]
    wire = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    fields = wire["result"]
    if bad == "tenant":
        fields["tenant_id"] = str(c.foreign)
    elif bad == "actor":
        fields["created_by"] = str(uuid4())
    elif bad == "source":
        fields["source_starter_version_id"] = str(UUID(int=1002))
    elif bad == "code":
        fields["code"] = "OTHER_COPY"
    elif bad == "counter":
        fields["template_row_version"] = True
    elif bad == "count":
        fields["question_count"] = 0
    elif bad == "time":
        fields["published_at"] = "2026-11-06T10:00:00"
    elif bad == "schema":
        wire["schema_version"] = True
    else:
        wire["unexpected"] = "invalid"
    ciphertext = c.idempotency.response_codec.encrypt(wire)
    c.sync.execute(update(c.receipts).where(
        c.receipts.c.id == receipt["id"]).values(response_ciphertext=ciphertext))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, code="MY_COPY")
    assert snapshot(c) == before
    c.copy_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreign_reservation_context_is_rejected_before_copy(keyed_case):
    c = keyed_case
    put(c)
    before = snapshot(c)
    original = c.idempotency.reserve

    async def foreign(**values):
        reservation = await original(**values)
        data = {name: getattr(reservation.record, name) for name in RECORD_FIELDS}
        data["principal_id"] = uuid4()
        return replace(reservation, record=NS(**data))
    c.idempotency.reserve = foreign
    with pytest.raises(Operation):
        await run(c)
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_processing_receipt_does_not_create_another_copy(keyed_case):
    c = keyed_case
    put(c)
    if not c.sync.in_transaction():
        c.sync.begin()
    await c.serialization.lock_member(tenant_id=c.tenant, user_id=c.user)
    await c.idempotency.reserve(
        tenant_id=c.tenant, principal_type="user", principal_id=c.user,
        operation=OPERATION, idempotency_key="processing-key",
        payload={"source_version_id": str(UUID(int=1001)), "code": None},
        now=c.now)
    await c.session.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionTemplateCopyInProgressError):
        await run(c, key="processing-key")
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_window_uses_shared_rules_without_implicit_overwrite(keyed_case):
    c = keyed_case
    put(c)
    first = await run(c)
    c.now += IDEMPOTENCY_RETENTION + timedelta(seconds=1)
    before = snapshot(c)
    with pytest.raises(Conflict):
        await run(c)
    assert snapshot(c) == before
    second = await run(c, code="EXPLICIT_NEW_COPY")
    assert second.template_id != first.template_id
    assert len(rows(c, c.copy_repo.templates)) == len(rows(c, c.audits)) == 2
    assert len(rows(c, c.receipts)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   ", "x" * 256, 123])
async def test_invalid_keys_do_not_reserve_or_copy(keyed_case, key):
    c = keyed_case
    put(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await run(c, key=key)
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()
    assert "reserve" not in c.order


def test_factory_uses_explicit_encryption_without_queries(keyed_case):
    c = keyed_case
    execute = AsyncMock(wraps=c.session.execute)
    c.session.execute = execute
    workflow = build_construction_template_copy_workflow(
        session=c.session, now_provider=lambda: c.now,
        idempotency_encryption_key=Fernet.generate_key().decode("ascii"))
    assert workflow.service.session is c.session
    assert workflow.idempotency.repository.session is c.session
    execute.assert_not_awaited()
    with pytest.raises(ValueError):
        build_construction_template_copy_workflow(
            session=c.session, idempotency_encryption_key=None)
    execute.assert_not_awaited()
