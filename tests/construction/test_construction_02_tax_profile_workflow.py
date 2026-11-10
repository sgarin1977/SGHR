"""Shared encrypted idempotency + real local ORM/SQLite rollback; no native concurrency claim."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import registry

from database.models import ApiIdempotencyRecord
from database.repositories.api_idempotency import ApiIdempotencyRepository
from services.api_idempotency import ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileValidationError, ConstructionTaxProfileOperationError,
)
from services.construction.construction_tax_profile_workflow import (
    ConstructionTaxProfileWorkflow, ConstructionTaxProfileInProgressError,
)
from test_construction_02_tax_profiles import case, LocalUTC, create, rows


class LocalRecords(ApiIdempotencyRepository):
    """Only reservation SQL is adapted to SQLite; shared service/codec/ORM completion stay real."""
    def __init__(self, c, Record, table):
        super().__init__(c.session)
        self.c, self.Record, self.table = c, Record, table

    async def reserve_record(self, **values):
        self.c.order.append("reserve")
        assert self.c.barrier_taken
        fields = {name: values[name] for name in
                  ("tenant_id", "principal_type", "principal_id", "operation", "key_hash", "request_hash", "expires_at")}
        reset = dict(request_hash=values["request_hash"], status="processing", response_status=None,
                     response_ciphertext=None, completed_at=None, created_at=values["now"], expires_at=values["expires_at"])
        statement = insert(self.Record).values(**(fields | reset | {"id": uuid4()})).on_conflict_do_update(
            index_elements=["tenant_id", "principal_type", "principal_id", "operation", "key_hash"],
            set_=reset, where=self.table.c.expires_at <= values["now"],
        ).returning(self.Record).execution_options(populate_existing=True)
        record = (await self.session.execute(statement)).scalar_one_or_none()
        if record is not None:
            return record, True
        query = {name: values[name] for name in
                 ("tenant_id", "principal_type", "principal_id", "operation", "key_hash", "now")}
        return await self.get_active_record_for_update(**query), False

    async def get_active_record_for_update(self, **values):
        statement = select(self.Record).where(
            *(getattr(self.Record, name) == values[name] for name in
              ("tenant_id", "principal_type", "principal_id", "operation", "key_hash")),
            self.Record.expires_at > values["now"],
        ).execution_options(populate_existing=True)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def complete_record(self, **values):
        self.c.order.append("complete")
        record = await super().complete_record(**values)
        if self.c.fail_complete == "error":
            raise RuntimeError("private failure after real receipt flush")
        if self.c.fail_complete == "cancel":
            raise asyncio.CancelledError()
        if self.c.fail_complete == "membership":
            self.c.membership = False
        return record


@pytest.fixture
def workflow_case(case):
    c = case
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalTaxReceipt", (), {})
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
    original_create = c.repository.create_profile
    async def create_row(**values):
        c.order.append("profile_create")
        return await original_create(**values)
    c.repository.create_profile = create_row
    c.records = LocalRecords(c, Record, table)
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode("ascii")),
    )
    c.workflow = ConstructionTaxProfileWorkflow(
        session=c.session, profile_service=c.service, idempotency_service=c.idempotency)
    yield c
    c.sync.close()
    mapper.dispose()


async def run_create(c, *, key="tax-create-key", **changes):
    if not c.sync.in_transaction():
        c.sync.begin()
    fields = dict(name="Workflow tax", client_price_tax_mode="TAX_EXCLUSIVE", material_cost_basis="NET")
    fields.update(changes)
    return await c.workflow.create_profile(actor=c.actor, idempotency_key=key, trace_id="tax-workflow", **fields)


async def run_update(c, profile, *, key="tax-update-key", version=None, **fields):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.workflow.update_profile(
        actor=c.actor, profile_id=profile.id,
        expected_row_version=profile.row_version if version is None else version,
        fields=fields, idempotency_key=key, trace_id="tax-workflow")


def snapshot(c):
    return tuple(list(rows(c, table)) for table in (c.table, c.audits, c.receipts))


@pytest.mark.asyncio
async def test_profile_audit_and_encrypted_receipt_share_one_commit(workflow_case):
    c = workflow_case
    result = await run_create(c, key="raw-key-must-not-be-stored-921")
    assert result.changed and result.profile.row_version == 1
    assert c.session.commits == 1 and c.session.rollbacks == 0
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("profile_create")
    assert c.order.index("profile_create") < c.order.index("complete") < c.order.index("commit")
    record = rows(c, c.receipts)[0]
    assert record["status"] == "completed" and record["response_status"] == 201
    assert record["response_ciphertext"] and b"Workflow tax" not in record["response_ciphertext"]
    decoded = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    assert decoded["profile"]["id"] == str(result.profile.id)
    assert "raw-key-must-not-be-stored-921" not in repr(snapshot(c))


@pytest.mark.asyncio
async def test_create_and_update_replay_return_original_results_after_later_changes(workflow_case):
    c = workflow_case
    created = await run_create(c)
    edited = await run_update(c, created.profile, material_cost_basis="GROSS")
    assert c.session.commits == 2
    later = await run_update(c, edited.profile, key="later", name="Later name")
    before = snapshot(c)
    assert await run_create(c) == created
    assert await run_update(c, created.profile, material_cost_basis="GROSS") == edited
    assert snapshot(c) == before
    assert rows(c, c.table)[0]["row_version"] == later.profile.row_version == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "update"])
async def test_changed_request_with_same_key_uses_platform_conflict(workflow_case, mode):
    c = workflow_case
    created = await run_create(c)
    if mode == "update":
        await run_update(c, created.profile, name="First edit")
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        if mode == "create":
            await run_create(c, name="Different")
        else:
            await run_update(c, created.profile, name="Different")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_sparse_noop_receipt_requires_current_version_and_replays_without_audit(workflow_case):
    c = workflow_case
    created = await run_create(c)
    noop = await run_update(c, created.profile, name="Workflow tax")
    assert noop.changed is False and noop.profile.row_version == 1
    assert c.session.commits == 2
    assert len(rows(c, c.audits)) == 1
    await run_update(c, created.profile, key="real-change", name="Updated")
    assert await run_update(c, created.profile, name="Workflow tax") == noop
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await run_update(c, created.profile, key="new-stale-request")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "membership_after_complete"])
async def test_every_failure_rolls_back_profile_audit_and_receipt(workflow_case, mode, failure):
    c = workflow_case
    profile = (await create(c)).profile if mode == "update" else None
    before = snapshot(c)
    if failure == "audit": c.fail_audit = True
    elif failure == "commit": c.session.fail_commit = True
    else: c.fail_complete = {"complete": "error", "cancel": "cancel", "membership_after_complete": "membership"}[failure]
    expected = asyncio.CancelledError if failure == "cancel" else (
        ConstructionPermissionDeniedError if failure == "membership_after_complete" else ConstructionTaxProfileOperationError)
    with pytest.raises(expected):
        if mode == "create": await run_create(c)
        else: await run_update(c, profile, name="Must roll back")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["barrier", "reservation"])
async def test_replay_requires_fresh_membership_after_waits(workflow_case, stage):
    c = workflow_case
    await run_create(c)
    before = snapshot(c)
    if stage == "barrier":
        original = c.serialization.lock_member.side_effect
        async def lock(**values):
            await original(**values)
            c.membership = False
        c.serialization.lock_member.side_effect = lock
    else:
        original = c.idempotency.reserve
        async def reserve(**values):
            result = await original(**values)
            c.membership = False
            return result
        c.idempotency.reserve = reserve
    with pytest.raises(ConstructionPermissionDeniedError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_receipt_cannot_replace_current_tax_edit_authority(workflow_case):
    c = workflow_case
    await run_create(c)
    before = snapshot(c)
    c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_receipt_is_conflict_without_second_write(workflow_case):
    c = workflow_case
    await run_create(c)
    c.sync.execute(c.receipts.update().values(status="processing", response_status=None, response_ciphertext=None))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionTaxProfileInProgressError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_invalid_encrypted_snapshot_fails_closed(workflow_case):
    c = workflow_case
    await run_create(c)
    record = rows(c, c.receipts)[0]
    payload = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    payload["profile"]["tenant_id"] = str(c.foreign)
    c.sync.execute(c.receipts.update().values(response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionTaxProfileOperationError):
        await run_create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_shared_retention_renews_expired_reservation_without_rewriting_profiles(workflow_case):
    c = workflow_case
    first = await run_create(c)
    c.now += timedelta(hours=25)
    second = await run_create(c)
    assert second.profile.id != first.profile.id
    assert len(rows(c, c.table)) == len(rows(c, c.audits)) == 2
    assert len(rows(c, c.receipts)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["actor", "tenant"])
async def test_same_key_is_bound_to_current_actor_and_tenant(workflow_case, scope):
    c = workflow_case
    first = await run_create(c)
    c.sync.rollback()
    if scope == "actor":
        c.user = uuid4()
        c.sync.execute(c.table.metadata.tables["users"].insert().values(id=c.user))
        c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active")
    else:
        c.tenant = c.foreign
        c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active")
        c.grant.scope_id = c.tenant
    c.sync.commit()
    second = await run_create(c)
    assert first.profile.id != second.profile.id
    assert len(rows(c, c.receipts)) == 2


@pytest.mark.asyncio
async def test_no_key_needs_no_codec_and_composition_never_commits_early(workflow_case):
    c = workflow_case
    c.workflow = ConstructionTaxProfileWorkflow(session=c.session, profile_service=c.service)
    result = await run_create(c, key=None)
    assert result.changed and c.session.commits == 1
    assert rows(c, c.receipts) == []
    before = snapshot(c)
    with pytest.raises(ConstructionTaxProfileOperationError):
        await run_create(c)
    assert snapshot(c) == before
    c.sync.rollback()
    c.sync.begin()
    prepared = await c.service.update_profile(actor=c.actor, profile_id=result.profile.id,
        expected_row_version=1, fields={"name": "Prepared only"}, manage_transaction=False)
    assert prepared.profile.row_version == 2 and c.session.commits == 1
    await c.session.rollback()
    assert rows(c, c.table)[0]["row_version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", 123, "x"*256])
async def test_invalid_keys_do_not_reserve_or_mutate(workflow_case, key):
    c = workflow_case
    before = snapshot(c)
    with pytest.raises(ConstructionTaxProfileValidationError):
        await run_create(c, key=key)
    assert snapshot(c) == before
