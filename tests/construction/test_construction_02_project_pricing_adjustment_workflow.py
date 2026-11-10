"""Shared encrypted receipts and real local flush/rollback; no native acceptance."""
import asyncio
from datetime import timedelta
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import registry

from database.models import ApiIdempotencyRecord
from services.api_idempotency import ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentValidationError as ValidationError,
    ConstructionProjectPricingAdjustmentNotFoundError as NotFoundError,
    ConstructionProjectPricingAdjustmentOperationError as OperationError)
from services.construction.construction_project_pricing_adjustment_workflow import (
    ConstructionProjectPricingAdjustmentWorkflow as Workflow,
    ConstructionProjectPricingAdjustmentInProgressError)
from test_construction_02_tax_profiles import case, LocalUTC
from test_construction_02_project_pricing_adjustments import adjustment_case, begin, create, snapshot as domain_snapshot
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
def adjustment_workflow_case(adjustment_case):
    c = adjustment_case
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalAdjustmentReceipt", (), {})
    mapper.map_imperatively(Record, table)
    c.receipts, c.order, c.fail_complete = table, [], None
    c.session.flush = AsyncMock(side_effect=c.sync.flush)
    original_commit = c.session.commit
    async def commit():
        c.order.append("commit")
        await original_commit()
    c.session.commit = commit
    original_create = c.adjustment_repo.create_adjustment
    async def insert_row(**values):
        c.order.append("adjustment_create")
        return await original_create(**values)
    c.adjustment_repo.create_adjustment = insert_row
    c.records = LocalRecords(c, Record, table)
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode("ascii")))
    c.workflow = Workflow(session=c.session, adjustment_service=c.adjustment_service, idempotency_service=c.idempotency)
    c.sync.begin()
    yield c
    c.sync.close()
    mapper.dispose()


async def run_create(c, *, key="adjustment-create", project_id=None, **changes):
    begin(c)
    fields = dict(name="Urgency", coefficient=Decimal("1.10"), enabled=True)
    fields.update(changes)
    return await c.workflow.create_adjustment(
        actor=c.actor, project_id=c.project if project_id is None else project_id,
        **fields, idempotency_key=key, trace_id="adjustment-workflow")


async def run_update(c, state, *, key="adjustment-update", project_id=None, adjustment_id=None, version=None, **fields):
    begin(c)
    return await c.workflow.update_adjustment(
        actor=c.actor, project_id=state.project_id if project_id is None else project_id,
        adjustment_id=state.id if adjustment_id is None else adjustment_id,
        expected_row_version=state.row_version if version is None else version,
        fields=fields, idempotency_key=key, trace_id="adjustment-workflow")


def receipt_rows(c):
    return [dict(row) for row in c.sync.execute(select(c.receipts).order_by(c.receipts.c.id)).mappings()]


def snapshot(c):
    return (*domain_snapshot(c), tuple(receipt_rows(c)))


@pytest.mark.asyncio
async def test_domain_audit_and_encrypted_receipt_share_one_commit(adjustment_workflow_case):
    c = adjustment_workflow_case
    key = "raw-adjustment-key-must-not-be-stored"
    result = await run_create(c, key=key)
    assert result.changed and result.adjustment.row_version == 1 and c.session.commits == 1
    assert c.order.index("member") < c.order.index("reserve") < c.order.index("project_lock")
    assert c.order.index("project_lock") < c.order.index("adjustment_create") < c.order.index("complete") < c.order.index("commit")
    receipt = receipt_rows(c)[0]
    assert receipt["status"] == "completed" and receipt["response_status"] == 201
    payload = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    assert payload["adjustment"]["id"] == str(result.adjustment.id)
    assert payload["adjustment"]["project_id"] == str(c.project) and payload["adjustment"]["coefficient"] == "1.1"
    assert str(result.adjustment.id).encode() not in receipt["response_ciphertext"]
    assert key not in repr(snapshot(c))


@pytest.mark.asyncio
async def test_original_create_update_and_noop_replay_after_later_edit(adjustment_workflow_case):
    c = adjustment_workflow_case
    created = await run_create(c)
    noop = await run_update(c, created.adjustment, key="noop", coefficient=Decimal("1.100"))
    assert not noop.changed and len(snapshot(c)[1]) == 1
    edited = await run_update(c, created.adjustment, coefficient=Decimal("1.25"))
    await run_update(c, edited.adjustment, key="later", enabled=False)
    before = snapshot(c)
    c.order.clear()
    assert await run_create(c) == created
    assert await run_update(c, created.adjustment, coefficient=Decimal("1.250")) == edited
    assert await run_update(c, created.adjustment, key="noop", coefficient=Decimal("1.1")) == noop
    assert snapshot(c) == before and "project_lock" not in c.order and "adjustment_create" not in c.order
    with pytest.raises(ConstructionRowVersionConflictError):
        await run_update(c, created.adjustment, key="new-stale")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_request_hashing_preserves_decimal_precision_and_normalizes_names(adjustment_workflow_case):
    c = adjustment_workflow_case
    value = Decimal("1.234567890123456789012345678901234567890123456789")
    with localcontext() as context:
        context.prec = 2
        first = await run_create(c, name="  Access  ", coefficient=value)
        before = snapshot(c)
        assert await run_create(c, name="Access", coefficient=Decimal(str(value) + "00")) == first
        with pytest.raises(ApiIdempotencyKeyReusedError):
            await run_create(c, name="Access", coefficient=Decimal("1.234567890123456789012345678901234567890123456788"))
    assert first.adjustment.coefficient == value and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"name": "Another"}, {"coefficient": Decimal("2")}, {"enabled": False}, {"project_id": "other"},
])
async def test_same_create_key_binds_fields_and_project(adjustment_workflow_case, changes):
    c = adjustment_workflow_case
    await run_create(c)
    before = snapshot(c)
    if changes.get("project_id") == "other":
        changes = {"project_id": c.other_project}
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run_create(c, **changes)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["field", "project", "adjustment", "version"])
async def test_update_receipt_binds_exact_target_and_expected_version(adjustment_workflow_case, change):
    c = adjustment_workflow_case
    original = await run_create(c)
    await run_update(c, original.adjustment, coefficient=Decimal("2"))
    before = snapshot(c)
    values = {"coefficient": Decimal("2")}
    values.update({"field": {"coefficient": Decimal("3")}, "project": {"project_id": c.other_project},
                   "adjustment": {"adjustment_id": uuid4()}, "version": {"version": 2}}[change])
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run_update(c, original.adjustment, **values)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "authority"])
async def test_real_changes_audit_and_receipt_roll_back_together(adjustment_workflow_case, operation, failure):
    c = adjustment_workflow_case
    original = (await create(c)).adjustment if operation == "update" else None
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
            await run_update(c, original, enabled=False)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["member", "reservation", "scope"])
async def test_replay_requires_fresh_current_project_edit_authority(adjustment_workflow_case, stage):
    c = adjustment_workflow_case
    await run_create(c)
    before = snapshot(c)
    if stage == "member":
        real = c.serialization.lock_member.side_effect
        async def member(**values):
            await real(**values)
            c.membership = False
        c.serialization.lock_member.side_effect = member
    elif stage == "reservation":
        real = c.idempotency.reserve
        async def reserve(**values):
            result = await real(**values)
            c.membership = False
            return result
        c.idempotency.reserve = reserve
    else:
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "PROJECT_MANAGER", "project", c.project
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_receipt_does_not_create_another_adjustment(adjustment_workflow_case):
    c = adjustment_workflow_case
    await run_create(c)
    c.sync.execute(c.receipts.update().values(
        status="processing", response_status=None, response_ciphertext=None, completed_at=None))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionProjectPricingAdjustmentInProgressError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["tenant", "project", "creator", "number", "nan", "enabled", "version", "changed", "noncanonical"])
async def test_malformed_receipt_fails_without_mutation(adjustment_workflow_case, tamper):
    c = adjustment_workflow_case
    await run_create(c)
    payload = c.idempotency.response_codec.decrypt(receipt_rows(c)[0]["response_ciphertext"])
    fields = payload["adjustment"]
    if tamper == "tenant":
        fields["tenant_id"] = str(c.foreign)
    elif tamper == "project":
        fields["project_id"] = str(c.other_project)
    elif tamper == "creator":
        fields["created_by"] = str(uuid4())
    elif tamper == "number":
        fields["coefficient"] = 1.1
    elif tamper == "nan":
        fields["coefficient"] = "NaN"
    elif tamper == "enabled":
        fields["enabled"] = 1
    elif tamper == "version":
        fields["row_version"] = True
    elif tamper == "changed":
        payload["changed"] = 1
    else:
        fields["coefficient"] = "1.10"
    c.sync.execute(c.receipts.update().values(response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(OperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_terminal_project_replays_original_receipt_but_rejects_new_mutation(adjustment_workflow_case):
    c = adjustment_workflow_case
    first = await run_create(c)
    c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(status="completed"))
    c.sync.commit()
    before = snapshot(c)
    assert await run_create(c) == first
    with pytest.raises(ConstructionProjectTerminalError):
        await run_create(c, key="new-request")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_shared_retention_renews_expired_key_as_a_new_command(adjustment_workflow_case):
    c = adjustment_workflow_case
    first = await run_create(c)
    c.now += timedelta(hours=25)
    second = await run_create(c)
    assert second.adjustment.id != first.adjustment.id
    state = snapshot(c)
    assert len(state[0]) == len(state[1]) == 2 and len(state[3]) == 1


@pytest.mark.asyncio
async def test_unkeyed_execution_needs_no_replay_codec(adjustment_workflow_case):
    c = adjustment_workflow_case
    c.workflow = Workflow(session=c.session, adjustment_service=c.adjustment_service)
    await run_create(c, key=None)
    assert c.session.commits == 1 and receipt_rows(c) == []
    before = snapshot(c)
    with pytest.raises(OperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", 123, "x" * 256])
async def test_invalid_keys_do_not_write(adjustment_workflow_case, key):
    c = adjustment_workflow_case
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await run_create(c, key=key)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["foreign", "missing"])
async def test_unavailable_project_rolls_back_new_receipt_reservation(adjustment_workflow_case, context):
    c = adjustment_workflow_case
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        await run_create(c, project_id=c.foreign_project if context == "foreign" else uuid4())
    assert snapshot(c) == before
