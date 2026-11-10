"""Local encrypted receipts and SQLite rollback; no runtime database access."""
import asyncio
from datetime import timedelta
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select
from sqlalchemy.orm import registry

from database.models import ApiIdempotencyRecord
from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsValidationError as Validation,
    ConstructionCalculationSettingsConflictError as Conflict,
    ConstructionCalculationSettingsOperationError as Operation,
)
from services.construction.construction_calculation_settings_workflow import (
    ConstructionCalculationSettingsWorkflow as Workflow,
    ConstructionCalculationSettingsInProgressError,
)
from test_construction_02_calculation_settings import calculation, case, LocalUTC, begin, create
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
def workflow_case(calculation):
    c = calculation
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalCalculationReceipt", (), {})
    mapper.map_imperatively(Record, table)
    c.receipts, c.order, c.fail_complete = table, [], None
    c.session.flush = AsyncMock(side_effect=c.sync.flush)
    original_lock = c.serialization.lock_member.side_effect
    async def lock(**values):
        c.order.append("barrier")
        await original_lock(**values)
    c.serialization.lock_member.side_effect = lock
    original_create = c.calc_repository.create_settings
    async def create_row(**values):
        c.order.append("settings_create")
        return await original_create(**values)
    c.calc_repository.create_settings = create_row
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
        session=c.session, settings_service=c.calc,
        idempotency_service=c.idempotency)
    c.sync.begin()
    yield c
    c.sync.close()
    mapper.dispose()


def rows(c, table):
    return [
        dict(row) for row in c.sync.execute(
            select(table).order_by(*table.primary_key.columns)
        ).mappings()
    ]


def snapshot(c):
    return tuple(rows(c, table) for table in (c.calculations, c.audits, c.receipts))


async def run_create(c, *, key="calculation-create", **changes):
    begin(c)
    return await c.workflow.create_settings(
        actor=c.actor, idempotency_key=key, trace_id="calculation-workflow",
        fields={"money_input_precision": 4, "supplier_stale_price_days": 0} | changes)


async def run_update(c, state, *, key="calculation-update", version=None, **fields):
    begin(c)
    return await c.workflow.update_settings(
        actor=c.actor,
        expected_row_version=state.row_version if version is None else version,
        fields=fields, idempotency_key=key, trace_id="calculation-workflow")


@pytest.mark.asyncio
async def test_settings_audit_and_encrypted_receipt_share_one_commit(workflow_case):
    c = workflow_case
    key = "raw-calculation-key-not-for-storage"
    created = await run_create(c, key=key)
    assert created.settings.row_version == 1 and c.session.commits == 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("settings_create")
    assert c.order.index("settings_create") < c.order.index("complete") < c.order.index("commit")
    record = rows(c, c.receipts)[0]
    assert record["response_status"] == 201 and record["status"] == "completed"
    decoded = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    assert decoded["settings"]["tenant_id"] == str(c.tenant)
    assert decoded["settings"]["minimum_job_amount"] is None
    assert str(c.tenant).encode() not in record["response_ciphertext"]
    assert key not in repr(snapshot(c))


@pytest.mark.asyncio
async def test_original_create_edit_and_noop_receipts_survive_later_edits(workflow_case):
    c = workflow_case
    created = await run_create(c)
    edited = await run_update(c, created.settings, quantity_precision=5)
    noop = await run_update(c, edited.settings, key="noop", quantity_precision=5)
    assert noop.changed is False
    later = await run_update(c, edited.settings, key="later", money_input_precision=6)
    before = snapshot(c)
    assert await run_create(c) == created
    assert await run_update(c, created.settings, quantity_precision=5) == edited
    assert await run_update(c, edited.settings, key="noop", quantity_precision=5) == noop
    assert snapshot(c) == before
    assert rows(c, c.calculations)[0]["row_version"] == later.settings.row_version == 3
    with pytest.raises(ConstructionRowVersionConflictError):
        await run_update(c, created.settings, key="new-stale-key")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_decimal_request_hashing_is_exact_and_context_independent(workflow_case):
    c = workflow_case
    value = Decimal("123456789012345678901234567890.123456789")
    with localcontext() as context:
        context.prec = 2
        first = await run_create(c, minimum_job_amount=value, minimum_job_currency="EUR")
        before = snapshot(c)
        assert await run_create(
            c, minimum_job_amount=Decimal(str(value) + "00"),
            minimum_job_currency="EUR") == first
        assert snapshot(c) == before
        with pytest.raises(ApiIdempotencyKeyReusedError):
            await run_create(
                c, minimum_job_amount=Decimal("123456789012345678901234567890.123456788"),
                minimum_job_currency="EUR")
    assert first.settings.minimum_job_amount == value
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "update"])
async def test_changed_payload_with_same_key_is_platform_conflict(workflow_case, mode):
    c = workflow_case
    created = await run_create(c)
    if mode == "update":
        await run_update(c, created.settings, quantity_precision=5)
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        if mode == "create":
            await run_create(c, money_input_precision=2)
        else:
            await run_update(c, created.settings, quantity_precision=6)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "authority"])
async def test_domain_audit_and_receipt_failures_roll_back_together(workflow_case, mode, failure):
    c = workflow_case
    original = (await create(c)).settings if mode == "update" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail_complete = {
            "complete": "error", "cancel": "cancel", "authority": "membership",
        }[failure]
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else ConstructionPermissionDeniedError if failure == "authority"
        else Operation
    )
    with pytest.raises(expected):
        if original is None:
            await run_create(c)
        else:
            await run_update(c, original, quantity_precision=5)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["barrier", "reservation", "scope"])
async def test_replay_rechecks_fresh_authority(workflow_case, stage):
    c = workflow_case
    await run_create(c)
    before = snapshot(c)
    if stage == "barrier":
        original = c.serialization.lock_member.side_effect
        async def lock(**values):
            await original(**values)
            c.membership = False
        c.serialization.lock_member.side_effect = lock
    elif stage == "reservation":
        original = c.idempotency.reserve
        async def reserve(**values):
            result = await original(**values)
            c.membership = False
            return result
        c.idempotency.reserve = reserve
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_receipt_does_not_perform_a_second_write(workflow_case):
    c = workflow_case
    await run_create(c)
    c.sync.execute(c.receipts.update().values(
        status="processing", response_status=None,
        response_ciphertext=None, completed_at=None))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionCalculationSettingsInProgressError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["tenant", "number", "nan", "version", "changed", "noncanonical"])
async def test_malformed_encrypted_receipt_fails_closed(workflow_case, tamper):
    c = workflow_case
    await run_create(c, minimum_job_amount=Decimal("100"), minimum_job_currency="EUR")
    record = rows(c, c.receipts)[0]
    payload = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    state = payload["settings"]
    if tamper == "tenant":
        state["tenant_id"] = str(c.foreign)
    elif tamper == "number":
        state["minimum_job_amount"] = 100
    elif tamper == "nan":
        state["minimum_job_amount"] = "NaN"
    elif tamper == "version":
        state["row_version"] = True
    elif tamper == "changed":
        payload["changed"] = 1
    else:
        state["minimum_job_amount"] = "100.00"
    c.sync.execute(c.receipts.update().values(
        response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run_create(c, minimum_job_amount=Decimal("100"), minimum_job_currency="EUR")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_expired_receipt_does_not_recreate_existing_settings(workflow_case):
    c = workflow_case
    await run_create(c)
    c.now += timedelta(hours=25)
    before = snapshot(c)
    with pytest.raises(Conflict):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_no_key_needs_no_codec_or_receipt(workflow_case):
    c = workflow_case
    c.workflow = Workflow(session=c.session, settings_service=c.calc)
    await run_create(c, key=None)
    assert rows(c, c.receipts) == [] and c.session.commits == 1
    before = snapshot(c)
    with pytest.raises(Operation):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", 123, "x" * 256])
async def test_invalid_keys_do_not_write(workflow_case, key):
    c = workflow_case
    before = snapshot(c)
    with pytest.raises(Validation):
        await run_create(c, key=key)
    assert snapshot(c) == before
