"""Local-only receipt encryption and real SQLite flush/rollback."""
import asyncio
from datetime import timedelta
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select
from sqlalchemy.orm import registry

from database.models import ApiIdempotencyRecord
from services.api_idempotency import ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileValidationError as ValidationError,
    ConstructionPricingProfileConflictError as ConflictError,
    ConstructionPricingProfileOperationError as OperationError)
from services.construction.construction_pricing_profile_workflow import (
    ConstructionPricingProfileWorkflow as Workflow, ConstructionPricingProfileInProgressError)
from test_construction_02_tax_profiles import case, LocalUTC
from test_construction_02_pricing_profiles import pricing, begin, create
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
def workflow_case(pricing):
    c = pricing
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalPricingReceipt", (), {})
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
    original_create = c.price_repository.create_profile
    async def create_row(**values):
        c.order.append("profile_create")
        return await original_create(**values)
    c.price_repository.create_profile = create_row
    c.records = LocalRecords(c, Record, table)
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode("ascii")))
    c.workflow = Workflow(session=c.session, profile_service=c.price_service, idempotency_service=c.idempotency)
    c.sync.begin()
    yield c
    c.sync.close()
    mapper.dispose()


async def run_create(c, *, key="pricing-create", **changes):
    begin(c)
    fields = dict(code="STANDARD", work_coefficient=Decimal("1.00"), material_markup_percent=Decimal("10.00"))
    fields.update(changes)
    return await c.workflow.create_profile(actor=c.actor, idempotency_key=key, trace_id="pricing-workflow", **fields)


async def run_update(c, profile, *, key="pricing-update", version=None, profile_id=None, **fields):
    begin(c)
    return await c.workflow.update_profile(
        actor=c.actor, profile_id=profile.id if profile_id is None else profile_id,
        expected_row_version=profile.row_version if version is None else version,
        fields=fields, idempotency_key=key, trace_id="pricing-workflow")


def rows(c, table):
    return [dict(row) for row in c.sync.execute(select(table).order_by(table.c.id)).mappings()]


def snapshot(c):
    return tuple(rows(c, table) for table in (c.prices, c.audits, c.receipts))


@pytest.mark.asyncio
async def test_profile_audit_and_encrypted_receipt_share_one_commit(workflow_case):
    c = workflow_case
    key = "raw-pricing-key-must-not-be-stored"
    result = await run_create(c, key=key)
    assert result.changed and result.profile.row_version == 1 and c.session.commits == 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("profile_create")
    assert c.order.index("profile_create") < c.order.index("complete") < c.order.index("commit")
    receipt = rows(c, c.receipts)[0]
    assert receipt["status"] == "completed" and receipt["response_status"] == 201
    payload = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    assert payload["profile"]["id"] == str(result.profile.id)
    assert payload["profile"]["work_coefficient"] == "1"
    assert payload["profile"]["material_markup_percent"] == "1E+1"
    assert str(result.profile.id).encode() not in receipt["response_ciphertext"]
    assert key not in repr(snapshot(c))


@pytest.mark.asyncio
async def test_replay_returns_original_create_and_update_after_later_edits(workflow_case):
    c = workflow_case
    created = await run_create(c)
    edited = await run_update(c, created.profile, work_coefficient=Decimal("1.125"))
    later = await run_update(c, edited.profile, key="later", material_markup_percent=Decimal("13"))
    before = snapshot(c)
    assert await run_create(c) == created
    assert await run_update(c, created.profile, work_coefficient=Decimal("1.1250")) == edited
    assert snapshot(c) == before and rows(c, c.prices)[0]["row_version"] == later.profile.row_version == 3


@pytest.mark.asyncio
async def test_decimal_hashing_is_exact_without_context_rounding(workflow_case):
    c = workflow_case
    value = Decimal("1.234567890123456789012345678901234567890123456789")
    with localcontext() as context:
        context.prec = 2
        first = await run_create(c, work_coefficient=value)
        before = snapshot(c)
        assert await run_create(c, work_coefficient=Decimal(str(value) + "00")) == first
        assert snapshot(c) == before
        with pytest.raises(ApiIdempotencyKeyReusedError):
            await run_create(c, work_coefficient=Decimal("1.234567890123456789012345678901234567890123456788"))
    assert first.profile.work_coefficient == value


@pytest.mark.asyncio
async def test_large_positive_exponents_stay_compact_and_exact(workflow_case):
    c = workflow_case
    first = await run_create(c, work_coefficient=Decimal("1E+10000"))
    assert first.profile.work_coefficient == Decimal("1E+10000")
    payload = c.idempotency.response_codec.decrypt(rows(c, c.receipts)[0]["response_ciphertext"])
    assert payload["profile"]["work_coefficient"] == "1E+10000"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"code": "ECONOMY"}, {"work_coefficient": Decimal("2")}, {"material_markup_percent": Decimal("11")}
])
async def test_same_create_key_with_changed_fields_conflicts(workflow_case, changes):
    c = workflow_case
    await run_create(c)
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run_create(c, **changes)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["coefficient", "profile", "version"])
async def test_update_receipt_binds_full_request(workflow_case, change):
    c = workflow_case
    created = await run_create(c)
    await run_update(c, created.profile, work_coefficient=Decimal("2"))
    before = snapshot(c)
    values = {"work_coefficient": Decimal("2")}
    values.update({"coefficient": {"work_coefficient": Decimal("3")},
                   "profile": {"profile_id": uuid4()}, "version": {"version": 2}}[change])
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run_update(c, created.profile, **values)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_noop_receipt_replays_but_new_stale_key_conflicts(workflow_case):
    c = workflow_case
    created = await run_create(c)
    noop = await run_update(c, created.profile, work_coefficient=Decimal("1"))
    assert noop.changed is False and len(rows(c, c.audits)) == 1
    await run_update(c, created.profile, key="real-edit", material_markup_percent=Decimal("11"))
    before = snapshot(c)
    assert await run_update(c, created.profile, work_coefficient=Decimal("1.000")) == noop
    assert snapshot(c) == before
    with pytest.raises(ConstructionRowVersionConflictError):
        await run_update(c, created.profile, key="stale-new")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "authority"])
async def test_failures_roll_back_profile_audit_and_receipt(workflow_case, operation, failure):
    c = workflow_case
    original = (await create(c)).profile if operation == "update" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail_complete = {"complete": "error", "cancel": "cancel", "authority": "membership"}[failure]
    expected = (asyncio.CancelledError if failure == "cancel" else
                ConstructionPermissionDeniedError if failure == "authority" else OperationError)
    with pytest.raises(expected):
        if original is None:
            await run_create(c)
        else:
            await run_update(c, original, work_coefficient=Decimal("2"))
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["barrier", "reservation", "scope"])
async def test_replay_requires_fresh_authority(workflow_case, stage):
    c = workflow_case
    await run_create(c)
    before = snapshot(c)
    if stage == "barrier":
        real = c.serialization.lock_member.side_effect
        async def lock(**values):
            await real(**values)
            c.membership = False
        c.serialization.lock_member.side_effect = lock
    elif stage == "reservation":
        real = c.idempotency.reserve
        async def reserve(**values):
            result = await real(**values)
            c.membership = False
            return result
        c.idempotency.reserve = reserve
    else:
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_receipt_does_not_write_twice(workflow_case):
    c = workflow_case
    await run_create(c)
    c.sync.execute(c.receipts.update().values(
        status="processing", response_status=None, response_ciphertext=None, completed_at=None))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionPricingProfileInProgressError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["tenant", "creator", "number", "nan", "noncanonical", "version", "changed"])
async def test_wrong_context_or_malformed_receipt_fails_closed(workflow_case, tamper):
    c = workflow_case
    await run_create(c)
    payload = c.idempotency.response_codec.decrypt(rows(c, c.receipts)[0]["response_ciphertext"])
    if tamper == "tenant":
        payload["profile"]["tenant_id"] = str(c.foreign)
    elif tamper == "creator":
        payload["profile"]["created_by"] = str(uuid4())
    elif tamper == "number":
        payload["profile"]["work_coefficient"] = 1
    elif tamper == "nan":
        payload["profile"]["work_coefficient"] = "NaN"
    elif tamper == "noncanonical":
        payload["profile"]["work_coefficient"] = "1.00"
    elif tamper == "version":
        payload["profile"]["row_version"] = True
    else:
        payload["changed"] = 1
    c.sync.execute(c.receipts.update().values(response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(OperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_expired_create_receipt_cannot_bypass_unique_price_level(workflow_case):
    c = workflow_case
    await run_create(c)
    c.now += timedelta(hours=25)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_expired_update_receipt_accepts_new_current_version(workflow_case):
    c = workflow_case
    created = await run_create(c)
    edited = await run_update(c, created.profile, work_coefficient=Decimal("2"))
    c.now += timedelta(hours=25)
    latest = await run_update(c, edited.profile, work_coefficient=Decimal("3"))
    assert latest.profile.row_version == 3
    assert len(rows(c, c.prices)) == 1 and len(rows(c, c.audits)) == 3 and len(rows(c, c.receipts)) == 2


@pytest.mark.asyncio
async def test_no_key_does_not_require_receipt_codec(workflow_case):
    c = workflow_case
    c.workflow = Workflow(session=c.session, profile_service=c.price_service)
    await run_create(c, key=None)
    assert c.session.commits == 1 and rows(c, c.receipts) == []
    before = snapshot(c)
    with pytest.raises(OperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", 123, "x" * 256])
async def test_invalid_keys_do_not_write(workflow_case, key):
    c = workflow_case
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await run_create(c, key=key)
    assert snapshot(c) == before
