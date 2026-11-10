"""Local encrypted publication receipts; no native concurrency claims."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4
import asyncio

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import registry

from database.models import ApiIdempotencyRecord
from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyResponseCodec,
    ApiIdempotencyKeyReusedError, IDEMPOTENCY_RETENTION, hash_request_payload,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_template_publication_workflow import (
    ConstructionTemplatePublicationWorkflow as Workflow,
    ConstructionTemplatePublicationInProgressError as InProgress,
    OPERATION, RESPONSE_STATUS, encode_result, decode_result,
    build_construction_template_publication_workflow,
)
from test_construction_02_template_publication import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, editable,
    publication,
)
from test_construction_02_template_draft_content import read, begin
from test_construction_02_template_copy import snapshot as domain_snapshot
from test_construction_02_tax_profiles import LocalUTC
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
def keyed(publication):
    c = publication
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalTemplatePublicationReceipt", (), {})
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
        session=c.session, publication_service=c.publication,
        idempotency_service=c.idempotency)
    original_publish = c.publication.publish_draft

    async def publish(**values):
        c.order.append("publish")
        return await original_publish(**values)
    c.publish_calls = AsyncMock(side_effect=publish)
    c.publication.publish_draft = c.publish_calls
    c.sync.begin()
    try:
        yield c
    finally:
        c.sync.close()
        mapper.dispose()


def rows(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.receipts).order_by(c.receipts.c.id)).mappings())


def snapshot(c):
    return domain_snapshot(c), rows(c)


def request(c, state):
    return {
        "template_id": str(c.base.template_id),
        "version_id": str(c.draft.version_id),
        "expected_row_version": state.version_row_version,
    }


async def run(c, state, *, key="publication-key", **options):
    begin(c)
    return await c.workflow.publish_draft(**(
        dict(
            actor=c.actor, template_id=c.base.template_id,
            version_id=c.draft.version_id,
            expected_row_version=state.version_row_version,
            idempotency_key=key, trace_id="keyed-publication-test",
        ) | options))


@pytest.mark.asyncio
async def test_publication_audit_and_encrypted_receipt_share_one_commit(keyed):
    c = keyed
    state = await read(c)
    commits = c.session.commits
    key = "raw-publication-key-must-not-be-stored"
    result = await run(c, state, key=key)
    assert c.session.commits == commits + 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("publish")
    assert c.order.index("publish") < c.order.index("complete") < c.order.index("commit")
    assert c.publish_calls.await_args.kwargs["manage_transaction"] is False
    receipt = rows(c)[0]
    assert receipt["status"] == "completed"
    assert receipt["response_status"] == RESPONSE_STATUS == 200
    wire = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    assert wire["operation"] == OPERATION
    assert wire["result"]["version_id"] == str(result.version_id)
    assert wire["result"]["published_at"] == result.published_at.isoformat()
    assert "definition" not in wire["result"] and "sections" not in wire["result"]
    assert result.section_count == 2 and result.question_count == 3
    assert str(result.version_id).encode() not in receipt["response_ciphertext"]
    assert key not in repr(snapshot(c))


@pytest.mark.asyncio
async def test_replay_keeps_original_metadata_after_root_changes(keyed):
    c = keyed
    state = await read(c)
    first = await run(c, state)
    c.sync.execute(update(c.publication_repo.templates).where(
        c.publication_repo.templates.c.id == first.template_id
    ).values(code="LATER_NAME", active=False, row_version=2))
    c.sync.commit()
    before = snapshot(c)
    c.reader.get_version = AsyncMock(
        side_effect=AssertionError("Replay must not reload published content."))
    result = await run(c, state)
    assert result == first and snapshot(c) == before
    c.publish_calls.assert_awaited_once()
    c.reader.get_version.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["template_id", "version_id", "expected_row_version"])
async def test_changed_payload_with_same_key_is_platform_conflict(keyed, field):
    c = keyed
    state = await read(c)
    await run(c, state)
    change = {field: 2 if field == "expected_row_version" else uuid4()}
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, state, **change)
    assert snapshot(c) == before
    c.publish_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "grant", "scope"])
async def test_replay_requires_current_authority(keyed, gate):
    c = keyed
    state = await read(c)
    await run(c, state)
    if gate == "membership":
        c.membership = False
    elif gate == "grant":
        c.grant.status = "revoked"
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c, state)
    assert snapshot(c) == before
    c.publish_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_draft_creator_and_authorized_publisher_can_be_different(keyed):
    c = keyed
    state = await read(c)
    creator = state.created_by
    publisher = uuid4()
    c.sync.execute(insert(c.table.metadata.tables["users"]).values(id=publisher))
    c.sync.commit()
    c.user = publisher
    c.actor.user_id = publisher
    c.grant.role = "ADMIN"
    result = await run(c, state)
    assert result.created_by == creator and result.created_by != publisher
    assert rows(c)[0]["principal_id"] == publisher
    assert await run(c, state) == result


@pytest.mark.asyncio
async def test_processing_receipt_does_not_call_publisher(keyed):
    c = keyed
    state = await read(c)
    key = "processing-publication-key"
    c.sync.execute(insert(c.receipts).values(
        id=uuid4(), tenant_id=c.tenant, principal_type="user", principal_id=c.user,
        operation=OPERATION, key_hash=sha256(key.encode()).hexdigest(),
        request_hash=hash_request_payload(request(c, state)),
        status="processing", created_at=c.now,
        expires_at=c.now + IDEMPOTENCY_RETENTION,
        response_status=None, response_ciphertext=None, completed_at=None))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(InProgress):
        await run(c, state, key=key)
    assert snapshot(c) == before
    c.publish_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "membership"])
async def test_publication_audit_and_receipt_roll_back_together(keyed, failure):
    c = keyed
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail_complete = {
            "complete": "error", "cancel": "cancel", "membership": "membership",
        }[failure]
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
        if failure == "membership" else Operation
    )
    with pytest.raises(expected):
        await run(c, state)
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_wrong_persisted_completion_status_rolls_back_domain_changes(keyed):
    c = keyed
    state = await read(c)
    original = c.records.complete_record
    async def wrong_status(**values):
        record = await original(**values)
        c.sync.execute(update(c.receipts).where(
            c.receipts.c.id == record.id).values(response_status=201))
        return record
    c.records.complete_record = wrong_status
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant", "version", "counter", "boolean", "extra"])
async def test_authenticated_but_invalid_receipt_payload_is_rejected(keyed, change):
    c = keyed
    state = await read(c)
    result = await run(c, state)
    wire = deepcopy(encode_result(result))
    if change == "tenant":
        wire["result"]["tenant_id"] = str(c.foreign)
    elif change == "version":
        wire["result"]["version_id"] = str(uuid4())
    elif change == "counter":
        wire["result"]["version_row_version"] += 1
    elif change == "boolean":
        wire["result"]["active"] = 1
    else:
        wire["result"]["unexpected"] = True
    c.sync.execute(update(c.receipts).values(
        response_ciphertext=c.idempotency.response_codec.encrypt(wire)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, state)
    assert snapshot(c) == before
    c.publish_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_reservation_payload_must_match_persisted_ciphertext(keyed):
    c = keyed
    state = await read(c)
    await run(c, state)
    original = c.idempotency.reserve
    async def different_payload(**values):
        reservation = await original(**values)
        wire = deepcopy(reservation.response_payload)
        wire["result"]["active"] = not wire["result"]["active"]
        return replace(reservation, response_payload=wire)
    c.idempotency.reserve = different_payload
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_expired_receipt_does_not_republish_terminal_version(keyed):
    c = keyed
    state = await read(c)
    await run(c, state)
    c.now += IDEMPOTENCY_RETENTION
    before = snapshot(c)
    with pytest.raises(NotFound):
        await run(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_metadata_codec_round_trip_is_exact(keyed):
    c = keyed
    state = await read(c)
    result = await run(c, state)
    wire = encode_result(result)
    assert decode_result(wire, actor=c.actor, request=request(c, state)) == result


def test_factory_uses_existing_encrypted_idempotency_service_without_connections(keyed):
    c = keyed
    workflow = build_construction_template_publication_workflow(
        session=c.session,
        idempotency_encryption_key=Fernet.generate_key().decode("ascii"),
        now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.service.session is c.session
    assert workflow.idempotency.repository.session is c.session
    assert workflow.idempotency.response_codec is not None
