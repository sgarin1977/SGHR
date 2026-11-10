"""Encrypted draft receipts with local SQLite; no native concurrency claim."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4

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
from services.construction.construction_template_draft_workflow import (
    ConstructionTemplateDraftWorkflow as Workflow,
    ConstructionTemplateOperationInProgressError as InProgress,
    OPERATION, encode_result, decode_result,
    build_construction_template_draft_workflow,
)
from test_construction_02_template_drafts import (
    case, onboarding, catalog_case, copy_case, read_case, drafts,
    begin, create_copy, snapshot as domain_snapshot,
)
from test_construction_02_organization_template_read import add_version
from test_construction_02_tax_profiles import LocalUTC
from test_construction_02_tax_profile_workflow import LocalRecords


@pytest.fixture
async def keyed(drafts):
    c = drafts
    c.base = await create_copy(c)
    c.sync.rollback()
    table = ApiIdempotencyRecord.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "completed_at", "expires_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    mapper = registry()
    Record = type("LocalTemplateDraftReceipt", (), {})
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
        session=c.session, draft_service=c.drafts,
        idempotency_service=c.idempotency)
    original_create = c.drafts.create_draft

    async def create(**values):
        c.order.append("draft")
        return await original_create(**values)
    c.create_calls = AsyncMock(side_effect=create)
    c.drafts.create_draft = c.create_calls
    c.sync.begin()
    try:
        yield c
    finally:
        c.sync.close()
        mapper.dispose()


def receipts(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.receipts).order_by(c.receipts.c.id)).mappings())


def snapshot(c):
    return domain_snapshot(c), receipts(c)


def request(c):
    return {
        "template_id": str(c.base.template_id),
        "base_version_id": str(c.base.template_version_id),
        "expected_template_row_version": c.base.template_row_version,
    }


async def run(c, *, key="draft-creation-key", **options):
    begin(c)
    return await c.workflow.create_draft(**(
        dict(
            actor=c.actor, template_id=c.base.template_id,
            base_version_id=c.base.template_version_id,
            expected_template_row_version=c.base.template_row_version,
            idempotency_key=key, trace_id="keyed-draft-test",
        ) | options))


def local_content(c):
    from database.repositories.construction.construction_template_draft_content import (
        ConstructionTemplateDraftContentRepository,
    )
    from services.construction.construction_template_draft_content import (
        ConstructionTemplateDraftContentService,
    )
    repository = ConstructionTemplateDraftContentRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    return ConstructionTemplateDraftContentService(
        session=c.session, repository=repository, draft_service=c.drafts)


@pytest.mark.asyncio
async def test_draft_content_audit_and_encrypted_receipt_share_one_commit(keyed):
    c = keyed
    before, commits = snapshot(c), c.session.commits
    key = "raw-draft-key-must-not-be-stored"
    result = await run(c, key=key)
    assert c.session.commits == commits + 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("draft")
    assert c.order.index("draft") < c.order.index("complete") < c.order.index("commit")
    assert c.create_calls.await_args.kwargs["manage_transaction"] is False
    assert result.status == "DRAFT" and result.version_row_version == 1
    after = snapshot(c)
    assert len(after[0][1]) == len(before[0][1]) + 1
    assert len(after[0][2]) == len(before[0][2]) + 2
    assert len(after[0][3]) == len(before[0][3]) + 3
    assert len(after[0][-1]) == len(before[0][-1]) + 1
    record = receipts(c)[0]
    assert record["status"] == "completed" and record["response_status"] == 201
    payload = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    assert payload["operation"] == OPERATION
    assert payload["result"]["version_id"] == str(result.version_id)
    assert payload["result"]["created_at"] == result.created_at.isoformat()
    assert str(result.version_id).encode() not in record["response_ciphertext"]
    assert key not in repr(after)


@pytest.mark.asyncio
async def test_same_key_returns_same_ids_without_another_draft_or_audit(keyed):
    c = keyed
    first = await run(c)
    before = snapshot(c)
    assert await run(c) == first
    assert snapshot(c) == before
    c.create_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_key_creates_a_separate_next_version(keyed):
    c = keyed
    first = await run(c)
    second = await run(c, key="another-draft-key")
    assert second.version == first.version + 1
    assert second.version_id != first.version_id
    assert len(receipts(c)) == 2


@pytest.mark.asyncio
async def test_replay_after_actual_publication_keeps_original_creation_receipt(keyed):
    c = keyed
    first = await run(c)
    from database.repositories.construction.construction_template_publication import (
        ConstructionTemplatePublicationRepository,
    )
    from services.construction.construction_template_publication import (
        ConstructionTemplatePublicationService,
    )
    repository = ConstructionTemplatePublicationRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    publisher = ConstructionTemplatePublicationService(
        session=c.session, repository=repository,
        content_service=local_content(c), published_service=c.reader)
    begin(c)
    published = await publisher.publish_draft(
        actor=c.actor, template_id=first.template_id,
        version_id=first.version_id, expected_row_version=1,
        trace_id="later-publication")
    assert published.version_row_version == 2
    c.sync.execute(update(c.draft_repo.templates).where(
        c.draft_repo.templates.c.id == first.template_id
    ).values(code="LATER_NAME", active=False, row_version=2))
    c.sync.commit()
    before = snapshot(c)
    c.reader.get_version = AsyncMock(
        side_effect=AssertionError("Replay must not reread the current base."))
    replay = await run(c)
    assert replay == first and replay.status == "DRAFT"
    assert replay.version_row_version == 1
    assert snapshot(c) == before
    c.create_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "template_id", "base_version_id", "expected_template_row_version",
])
async def test_changed_request_with_same_key_is_platform_conflict(keyed, field):
    c = keyed
    await run(c)
    change = {field: 2 if field == "expected_template_row_version" else uuid4()}
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, **change)
    assert snapshot(c) == before
    c.create_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "grant", "scope"])
async def test_replay_requires_current_authority(keyed, gate):
    c = keyed
    await run(c)
    if gate == "membership":
        c.membership = False
    elif gate == "grant":
        c.grant.status = "revoked"
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_reservation_does_not_create_another_draft(keyed):
    c = keyed
    key = "pending-draft-key"
    c.sync.execute(insert(c.receipts).values(
        id=uuid4(), tenant_id=c.tenant, principal_type="user", principal_id=c.user,
        operation=OPERATION, key_hash=sha256(key.encode()).hexdigest(),
        request_hash=hash_request_payload(request(c)),
        status="processing", created_at=c.now,
        expires_at=c.now + IDEMPOTENCY_RETENTION,
        response_status=None, response_ciphertext=None, completed_at=None))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(InProgress):
        await run(c, key=key)
    assert snapshot(c) == before
    c.create_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "membership"])
async def test_all_draft_rows_audit_and_receipt_roll_back_together(keyed, failure):
    c = keyed
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
        await run(c)
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_wrong_persisted_receipt_status_rolls_back_created_draft(keyed):
    c = keyed
    original = c.records.complete_record
    async def wrong_status(**values):
        record = await original(**values)
        c.sync.execute(update(c.receipts).where(
            c.receipts.c.id == record.id).values(response_status=200))
        return record
    c.records.complete_record = wrong_status
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant", "creator", "base", "counter", "status", "extra"])
async def test_invalid_encrypted_receipt_is_not_replayed(keyed, change):
    c = keyed
    result = await run(c)
    payload = deepcopy(encode_result(result))
    if change == "tenant":
        payload["result"]["tenant_id"] = str(c.foreign)
    elif change == "creator":
        payload["result"]["created_by"] = str(uuid4())
    elif change == "base":
        payload["result"]["base_version_id"] = str(uuid4())
    elif change == "counter":
        payload["result"]["version_row_version"] = 2
    elif change == "status":
        payload["result"]["status"] = "PUBLISHED"
    else:
        payload["result"]["unexpected"] = True
    c.sync.execute(update(c.receipts).values(
        response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c)
    assert snapshot(c) == before
    c.create_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_payload_returned_by_reservation_must_match_stored_ciphertext(keyed):
    c = keyed
    await run(c)
    original = c.idempotency.reserve
    async def different_payload(**values):
        reservation = await original(**values)
        payload = deepcopy(reservation.response_payload)
        payload["result"]["section_count"] += 1
        return replace(reservation, response_payload=payload)
    c.idempotency.reserve = different_payload
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_nullable_starter_origin_survives_actual_draft_creation(keyed):
    c = keyed
    own_base = await add_version(c, c.base, number=2, org=True)
    result = await run(c, base_version_id=own_base)
    assert result.source_starter_version_id is None
    assert await run(c, base_version_id=own_base) == result


@pytest.mark.asyncio
async def test_codec_round_trip_is_exact(keyed):
    c = keyed
    result = await run(c)
    assert decode_result(
        encode_result(result), actor=c.actor, request=request(c)) == result


def test_factory_binds_one_session_and_existing_encrypted_receipt_service(keyed):
    c = keyed
    workflow = build_construction_template_draft_workflow(
        session=c.session,
        idempotency_encryption_key=Fernet.generate_key().decode("ascii"),
        now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.service.session is c.session
    assert workflow.runner.session is c.session
    assert workflow.idempotency.repository.session is c.session
