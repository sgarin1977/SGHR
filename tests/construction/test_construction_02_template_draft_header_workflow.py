"""Local sparse header edits, encrypted receipts and real rollback."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import insert, select, update

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingValidationError as Validation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_template_draft_header_workflow import (
    ConstructionTemplateDraftHeaderWorkflow as Workflow,
    OPERATION, encode_result, decode_result,
    build_construction_template_draft_header_workflow,
)
from test_construction_02_template_draft_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    begin, local_content, snapshot,
)


@pytest.fixture
async def header(keyed):
    c = keyed
    c.draft = await c.workflow.create_draft(
        actor=c.actor, template_id=c.base.template_id,
        base_version_id=c.base.template_version_id,
        expected_template_row_version=c.base.template_row_version,
        idempotency_key="seed-draft-key", trace_id="seed-draft")
    c.content = local_content(c)
    c.header_workflow = Workflow(
        session=c.session, content_service=c.content,
        idempotency_service=c.idempotency)
    original_edit = c.content.edit_header

    async def edit(**values):
        c.order.append("edit")
        return await original_edit(**values)
    c.edit_calls = AsyncMock(side_effect=edit)
    c.content.edit_header = c.edit_calls
    c.order.clear()
    yield c


async def read(c):
    begin(c)
    return await c.content.read_draft(
        actor=c.actor, template_id=c.base.template_id,
        version_id=c.draft.version_id)


def renamed(state, title="Organization template"):
    labels = state.definition.payload()["labels"]
    labels[state.source_language]["title"] = title
    return {"labels": labels}


async def run(c, state, fields, *, key="header-key", **options):
    begin(c)
    return await c.header_workflow.edit_header(**(
        dict(
            actor=c.actor, template_id=c.base.template_id,
            version_id=c.draft.version_id,
            expected_row_version=state.version_row_version,
            fields=fields, idempotency_key=key,
            trace_id="keyed-header-test",
        ) | options))


def records(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.receipts).where(
            c.receipts.c.operation == OPERATION).order_by(c.receipts.c.id)
    ).mappings())


def request(c, state, fields):
    return {
        "template_id": str(c.base.template_id),
        "version_id": str(c.draft.version_id),
        "expected_row_version": state.version_row_version,
        "fields": fields,
    }


@pytest.mark.asyncio
async def test_header_audit_and_receipt_share_one_commit(header):
    c = header
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    fields = renamed(state)
    key = "raw-header-key-must-not-be-stored"
    result = await run(c, state, fields, key=key)
    assert result.changed and result.title == "Organization template"
    assert result.version_row_version == state.version_row_version + 1
    assert c.session.commits == commits + 1
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("edit")
    assert c.order.index("edit") < c.order.index("complete") < c.order.index("commit")
    assert c.edit_calls.await_args.kwargs["manage_transaction"] is False
    after = snapshot(c)
    assert after[0][0] == before[0][0]
    assert after[0][2:7] == before[0][2:7]
    receipt = records(c)[0]
    assert receipt["response_status"] == 200 and receipt["status"] == "completed"
    payload = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    assert payload["result"]["title"] == result.title
    assert payload["result"]["definition_hash"] == result.definition_hash
    assert "definition" not in payload["result"] and "sections" not in payload["result"]
    assert key not in repr(after)


@pytest.mark.asyncio
async def test_replay_returns_original_after_later_edit(header):
    c = header
    state = await read(c)
    fields = renamed(state)
    first = await run(c, state, fields)
    current = await read(c)
    await run(c, current, renamed(current, "Later title"), key="later-key")
    before = snapshot(c)
    replay = await run(c, state, fields)
    assert replay == first and replay.title == "Organization template"
    assert snapshot(c) == before
    assert c.edit_calls.await_count == 2


@pytest.mark.asyncio
async def test_replay_after_publication_does_not_edit_or_reopen_version(header):
    c = header
    state = await read(c)
    fields = renamed(state)
    first = await run(c, state, fields)
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
        content_service=c.content, published_service=c.reader)
    begin(c)
    published = await publisher.publish_draft(
        actor=c.actor, template_id=c.base.template_id,
        version_id=c.draft.version_id,
        expected_row_version=first.version_row_version,
        trace_id="later-publication")
    assert published.version_row_version == first.version_row_version + 1
    before = snapshot(c)
    assert await run(c, state, fields) == first
    assert snapshot(c) == before
    c.edit_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_noop_receipt_does_not_increment_version_or_add_audit(header):
    c = header
    state = await read(c)
    before = snapshot(c)
    events = c.events.create_event.await_count
    first = await run(c, state, {})
    assert first.changed is False
    assert first.version_row_version == state.version_row_version
    assert snapshot(c)[0] == before[0]
    assert c.events.create_event.await_count == events
    after = snapshot(c)
    assert await run(c, state, {}) == first
    assert snapshot(c) == after
    c.edit_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_noop_with_new_key_is_conflict_and_has_no_receipt(header):
    c = header
    state = await read(c)
    await run(c, state, renamed(state))
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await run(c, state, {}, key="stale-noop")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"fields": {}},
    {"expected_row_version": 2},
    {"template_id": uuid4()},
    {"version_id": uuid4()},
])
async def test_same_key_changed_request_is_platform_conflict(header, change):
    c = header
    state = await read(c)
    fields = renamed(state)
    await run(c, state, fields)
    before = snapshot(c)
    changes = dict(change)
    requested = changes.pop("fields", fields)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, state, requested, **changes)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_explicit_null_and_companion_labels_are_preserved(header):
    c = header
    state = await read(c)
    labels = state.definition.payload()["labels"]
    labels[state.source_language].pop("description")
    fields = {"description_key": None, "labels": labels}
    result = await run(c, state, fields)
    assert result.changed and result.description is None
    assert await run(c, state, fields) == result


@pytest.mark.asyncio
async def test_json_object_key_order_does_not_change_request_identity(header):
    c = header
    state = await read(c)
    fields = renamed(state)
    first = await run(c, state, fields)
    reordered = {
        "labels": {
            language: dict(reversed(tuple(values.items())))
            for language, values in fields["labels"].items()
        }
    }
    assert await run(c, state, reordered) == first
    c.edit_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_draft_author_and_authorized_editor_can_be_different(header):
    c = header
    state = await read(c)
    creator = state.created_by
    editor = uuid4()
    c.sync.execute(insert(c.table.metadata.tables["users"]).values(id=editor))
    c.sync.commit()
    c.user = editor
    c.actor.user_id = editor
    c.grant.role = "ADMIN"
    result = await run(c, state, renamed(state))
    assert result.created_by == creator and creator != editor
    assert records(c)[0]["principal_id"] == editor


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "grant", "scope"])
async def test_replay_requires_fresh_settings_authority(header, gate):
    c = header
    state = await read(c)
    fields = renamed(state)
    await run(c, state, fields)
    if gate == "membership":
        c.membership = False
    elif gate == "grant":
        c.grant.status = "revoked"
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"status": "PUBLISHED"},
    {"source_language": None},
    {"title_key": None},
    {"labels": None},
    {"labels": {"en": {"title": 42}}},
    {"labels": {"en": {"title": "Incomplete"}}},
])
async def test_invalid_or_incomplete_fields_leave_no_changes(header, fields):
    c = header
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await run(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "membership"])
async def test_edit_audit_and_receipt_roll_back_together(header, failure):
    c = header
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
        await run(c, state, renamed(state))
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant", "counter", "changed", "extra"])
async def test_invalid_encrypted_response_is_not_replayed(header, change):
    c = header
    state = await read(c)
    fields = renamed(state)
    result = await run(c, state, fields)
    payload = deepcopy(encode_result(result))
    if change == "tenant":
        payload["result"]["tenant_id"] = str(c.foreign)
    elif change == "counter":
        payload["result"]["version_row_version"] += 1
    elif change == "changed":
        payload["result"]["changed"] = False
    else:
        payload["result"]["unexpected"] = True
    c.sync.execute(update(c.receipts).where(
        c.receipts.c.operation == OPERATION
    ).values(response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_metadata_codec_round_trip_is_exact(header):
    c = header
    state = await read(c)
    fields = renamed(state)
    result = await run(c, state, fields)
    assert decode_result(
        encode_result(result), actor=c.actor,
        request=request(c, state, fields)) == result


def test_factory_binds_one_session_without_opening_connections(header):
    c = header
    workflow = build_construction_template_draft_header_workflow(
        session=c.session,
        idempotency_encryption_key=Fernet.generate_key().decode("ascii"),
        now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.service.session is c.session
    assert workflow.runner.session is c.session
    assert workflow.idempotency.repository.session is c.session
