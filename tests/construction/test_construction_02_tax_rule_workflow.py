"""Shared encrypted receipts + local SQLite flush/rollback, without external operations."""
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
from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_rules import ConstructionTaxRuleOperationError, ConstructionTaxRuleValidationError
from services.construction.construction_tax_rules import ConstructionTaxRuleConflictError
from services.construction.construction_tax_rule_workflow import ConstructionTaxRuleWorkflow, ConstructionTaxRuleInProgressError
from test_construction_02_tax_profiles import case, LocalUTC
from test_construction_02_tax_rules import rule_case, create, begin
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
def rule_workflow_case(rule_case):
    c = rule_case
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalTaxRuleReceipt", (), {})
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
    original_create = c.rule_repo.create_rule
    async def create_row(**values):
        c.order.append("rule_create")
        return await original_create(**values)
    c.rule_repo.create_rule = create_row
    c.records = LocalRecords(c, Record, table)
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode("ascii")),
    )
    c.workflow = ConstructionTaxRuleWorkflow(
        session=c.session, rule_service=c.rule_service, idempotency_service=c.idempotency,
    )
    c.sync.begin()
    yield c
    c.sync.close()
    mapper.dispose()


async def run_create(c, *, key="rule-create", **changes):
    begin(c)
    fields = dict(profile_id=c.profile, category="WORK", rate_percent=Decimal("20.00"))
    fields.update(changes)
    return await c.workflow.create_rule(
        actor=c.actor, idempotency_key=key, trace_id="rule-workflow", **fields,
    )


async def run_update(c, rule, *, key="rule-update", version=None, profile_id=None, rule_id=None, **fields):
    begin(c)
    return await c.workflow.update_rule(
        actor=c.actor, profile_id=rule.tax_profile_id if profile_id is None else profile_id,
        rule_id=rule.id if rule_id is None else rule_id,
        expected_row_version=rule.row_version if version is None else version,
        fields=fields, idempotency_key=key, trace_id="rule-workflow",
    )


def rows(c, table):
    return list(c.sync.execute(select(table).order_by(table.c.id)).mappings())


def snapshot(c):
    return tuple(rows(c, table) for table in (c.rules, c.audits, c.receipts))


@pytest.mark.asyncio
async def test_rule_audit_receipt_share_one_commit_and_rate_is_encrypted(rule_workflow_case):
    c = rule_workflow_case
    secret_key = "raw-idempotency-key-do-not-store-163"
    result = await run_create(c, key=secret_key)
    assert result.changed and result.rule.row_version == 1 and c.session.commits == 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("rule_create")
    assert c.order.index("rule_create") < c.order.index("complete") < c.order.index("commit")
    record = rows(c, c.receipts)[0]
    assert record["status"] == "completed" and record["response_status"] == 201
    payload = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    assert payload["rule"]["id"] == str(result.rule.id)
    assert payload["rule"]["tax_profile_id"] == str(c.profile)
    assert payload["rule"]["rate_percent"] == "20"
    assert str(result.rule.id).encode() not in record["response_ciphertext"]
    assert secret_key not in repr(snapshot(c))


@pytest.mark.asyncio
async def test_replay_keeps_original_create_and_update_after_later_edits(rule_workflow_case):
    c = rule_workflow_case
    created = await run_create(c)
    edited = await run_update(c, created.rule, rate_percent=Decimal("7.125"))
    later = await run_update(c, edited.rule, key="later", category="OTHER")
    before = snapshot(c)
    assert await run_create(c) == created
    assert await run_update(c, created.rule, rate_percent=Decimal("7.125")) == edited
    assert snapshot(c) == before and rows(c, c.rules)[0]["row_version"] == later.rule.row_version == 3


@pytest.mark.asyncio
async def test_decimal_hashing_is_exact_and_ignores_only_insignificant_zeros(rule_workflow_case):
    c = rule_workflow_case
    rate = Decimal("99.123456789012345678901234567890123456789")
    with localcontext() as context:
        context.prec = 2
        first = await run_create(c, rate_percent=rate)
        before = snapshot(c)
        assert await run_create(c, rate_percent=Decimal(str(rate) + "00")) == first
        assert snapshot(c) == before
        with pytest.raises(ApiIdempotencyKeyReusedError):
            await run_create(c, rate_percent=Decimal("99.123456789012345678901234567890123456788"))
    assert first.rule.rate_percent == rate
    assert c.idempotency.response_codec.decrypt(rows(c, c.receipts)[0]["response_ciphertext"])["rule"]["rate_percent"] == str(rate)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rate", "category", "profile"])
async def test_changed_create_payload_with_same_key_conflicts(rule_workflow_case, change):
    c = rule_workflow_case
    await run_create(c)
    before = snapshot(c)
    fields = {
        "rate": {"rate_percent": Decimal("21")},
        "category": {"category": "OTHER"},
        "profile": {"profile_id": c.other_profile},
    }[change]
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run_create(c, **fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rate", "profile", "rule", "version"])
async def test_update_receipt_binds_full_request_context(rule_workflow_case, change):
    c = rule_workflow_case
    created = await run_create(c)
    await run_update(c, created.rule, rate_percent=Decimal("7.125"))
    before = snapshot(c)
    fields = {"rate_percent": Decimal("7.125")}
    fields.update({
        "rate": {"rate_percent": Decimal("8")},
        "profile": {"profile_id": c.other_profile},
        "rule": {"rule_id": uuid4()},
        "version": {"version": 2},
    }[change])
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run_update(c, created.rule, **fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_noop_receipt_replays_after_change_but_new_stale_key_conflicts(rule_workflow_case):
    c = rule_workflow_case
    created = await run_create(c)
    noop = await run_update(c, created.rule, rate_percent=Decimal("20"))
    assert not noop.changed and len(rows(c, c.audits)) == 1
    await run_update(c, created.rule, key="edit", category="OTHER")
    before = snapshot(c)
    assert await run_update(c, created.rule, rate_percent=Decimal("20.000")) == noop
    assert snapshot(c) == before
    with pytest.raises(ConstructionRowVersionConflictError):
        await run_update(c, created.rule, key="new-stale")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "authority"])
async def test_failures_roll_back_rule_audit_and_receipt(rule_workflow_case, operation, failure):
    c = rule_workflow_case
    original = (await create(c)).rule if operation == "update" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail_complete = {"complete": "error", "cancel": "cancel", "authority": "membership"}[failure]
    expected = asyncio.CancelledError if failure == "cancel" else (
        ConstructionPermissionDeniedError if failure == "authority" else ConstructionTaxRuleOperationError
    )
    with pytest.raises(expected):
        if original is None:
            await run_create(c)
        else:
            await run_update(c, original, category="OTHER")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["barrier", "reservation", "scope"])
async def test_replay_requires_fresh_current_authority(rule_workflow_case, stage):
    c = rule_workflow_case
    await run_create(c)
    before = snapshot(c)
    if stage == "barrier":
        method = c.serialization.lock_member.side_effect
        async def lock(**values):
            await method(**values)
            c.membership = False
        c.serialization.lock_member.side_effect = lock
    elif stage == "reservation":
        method = c.idempotency.reserve
        async def reserve(**values):
            result = await method(**values)
            c.membership = False
            return result
        c.idempotency.reserve = reserve
    else:
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_receipt_is_conflict_without_second_write(rule_workflow_case):
    c = rule_workflow_case
    await run_create(c)
    c.sync.execute(c.receipts.update().values(
        status="processing", response_status=None, response_ciphertext=None, completed_at=None,
    ))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionTaxRuleInProgressError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["tenant", "profile", "rate_number", "rate_nan", "version", "changed"])
async def test_malformed_or_wrong_context_receipt_fails_closed(rule_workflow_case, tamper):
    c = rule_workflow_case
    await run_create(c)
    payload = c.idempotency.response_codec.decrypt(rows(c, c.receipts)[0]["response_ciphertext"])
    if tamper == "tenant":
        payload["rule"]["tenant_id"] = str(c.foreign)
    elif tamper == "profile":
        payload["rule"]["tax_profile_id"] = str(c.other_profile)
    elif tamper == "rate_number":
        payload["rule"]["rate_percent"] = 20
    elif tamper == "rate_nan":
        payload["rule"]["rate_percent"] = "NaN"
    elif tamper == "version":
        payload["rule"]["row_version"] = True
    else:
        payload["changed"] = 1
    c.sync.execute(c.receipts.update().values(
        response_ciphertext=c.idempotency.response_codec.encrypt(payload),
    ))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionTaxRuleOperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_shared_retention_renews_expired_reservation(rule_workflow_case):
    c = rule_workflow_case
    first = await run_create(c)
    edited = await run_update(c, first.rule, rate_percent=Decimal("7"))
    c.now += timedelta(hours=25)
    before = snapshot(c)
    with pytest.raises(ConstructionTaxRuleConflictError):
        await run_create(c)
    assert snapshot(c) == before
    second = await run_update(c, edited.rule, rate_percent=Decimal("8"))
    assert second.rule.id == first.rule.id
    assert second.rule.row_version == 3
    assert len(rows(c, c.rules)) == 1
    assert len(rows(c, c.audits)) == 3
    assert len(rows(c, c.receipts)) == 2


@pytest.mark.asyncio
async def test_no_key_needs_no_codec_but_keyed_execution_requires_it(rule_workflow_case):
    c = rule_workflow_case
    c.workflow = ConstructionTaxRuleWorkflow(session=c.session, rule_service=c.rule_service)
    await run_create(c, key=None)
    assert c.session.commits == 1 and rows(c, c.receipts) == []
    before = snapshot(c)
    with pytest.raises(ConstructionTaxRuleOperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", 123, "x" * 256])
async def test_invalid_keys_do_not_reserve_or_write(rule_workflow_case, key):
    c = rule_workflow_case
    before = snapshot(c)
    with pytest.raises(ConstructionTaxRuleValidationError):
        await run_create(c, key=key)
    assert snapshot(c) == before

# con016_canonical_categories_unique_v1
