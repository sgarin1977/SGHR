"""Local keyed item edits: canonical requests, stable IDs and atomic receipts."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select, update

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingValidationError as Validation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_units import UnitCode
from database.repositories.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemRepository,
)
from services.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemService, UNSET,
)
from services.construction.construction_template_draft_item_workflow import (
    ConstructionTemplateDraftItemWorkflow as Workflow,
    OPERATION, encode_result, decode_result,
    build_construction_template_draft_item_workflow,
)
from test_construction_02_template_draft_header_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    header, begin, read, snapshot,
)


@pytest.fixture
def flow(header):
    c = header
    repository = ConstructionTemplateDraftItemRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    c.item_repo = repository
    c.items = ConstructionTemplateDraftItemService(
        session=c.session, repository=repository, content_service=c.content)
    c.item_workflow = Workflow(
        session=c.session, item_service=c.items,
        idempotency_service=c.idempotency)
    original = c.items.edit_item

    async def edit(**values):
        c.order.append("item")
        return await original(**values)
    c.item_calls = AsyncMock(side_effect=edit)
    c.items.edit_item = c.item_calls
    c.order.clear()
    yield c


def target(state, kind):
    return state.sections[0] if kind == "SECTION" else state.sections[0].questions[1]


async def run(c, state, fields, *, kind="QUESTION", key="item-key", labels=UNSET, **options):
    begin(c)
    values = dict(
        actor=c.actor, template_id=c.base.template_id,
        version_id=c.draft.version_id, kind=kind,
        item_id=target(state, kind).id,
        expected_row_version=state.version_row_version,
        fields=fields, idempotency_key=key, trace_id="keyed-item-test")
    if labels is not UNSET:
        values["labels"] = labels
    values.update(options)
    return await c.item_workflow.edit_item(**values)


def records(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.receipts).where(c.receipts.c.operation == OPERATION)
        .order_by(c.receipts.c.id)).mappings())


def request(c, state, fields, *, kind="QUESTION"):
    return {
        "template_id": str(c.base.template_id),
        "version_id": str(c.draft.version_id),
        "kind": kind,
        "item_id": str(target(state, kind).id),
        "expected_row_version": state.version_row_version,
        "fields": fields,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_item_version_audit_and_receipt_share_one_commit(flow, role, kind):
    c = flow
    c.grant.role = role
    state = await read(c)
    fields = {"sort_order": 5} if kind == "SECTION" else {"required": False}
    before, commits = snapshot(c), c.session.commits
    result = await run(c, state, fields, kind=kind, key="raw-item-key-not-stored")
    assert result.kind == kind and result.item_id == target(state, kind).id
    assert result.draft.changed is True
    assert result.draft.version_row_version == state.version_row_version + 1
    assert c.session.commits == commits + 1
    assert c.item_calls.await_args.kwargs["manage_transaction"] is False
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("item")
    assert c.order.index("item") < c.order.index("complete") < c.order.index("commit")
    current = await read(c)
    assert target(current, kind).id == target(state, kind).id
    assert target(current, kind).row_version == target(state, kind).row_version + 1
    after = snapshot(c)
    assert after[0][0] == before[0][0] and after[0][4:7] == before[0][4:7]
    receipt = records(c)[0]
    assert receipt["response_status"] == 200
    payload = c.idempotency.response_codec.decrypt(receipt["response_ciphertext"])
    assert payload["result"]["item_id"] == str(result.item_id)
    assert payload["result"]["kind"] == kind
    assert "definition" not in payload["result"]["draft"]
    assert "raw-item-key-not-stored" not in repr(after)
    replay_before = snapshot(c)
    assert await run(c, state, fields, kind=kind, key="raw-item-key-not-stored") == result
    assert snapshot(c) == replay_before
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_label_change_and_item_change_are_atomic(flow):
    c = flow
    state = await read(c)
    labels = state.definition.payload()["labels"]
    labels["en"]["customArea"] = labels["en"].pop("area")
    result = await run(c, state, {"label_key": "customArea"}, labels=labels)
    assert result.draft.changed
    current = await read(c)
    assert current.definition.payload()["sections"][0]["questions"][1]["label_key"] == "customArea"


@pytest.mark.asyncio
async def test_labels_only_change_preserves_item_counter(flow):
    c = flow
    state = await read(c)
    labels = state.definition.payload()["labels"]
    labels["en"]["title"] = "Updated labels"
    result = await run(c, state, {}, labels=labels)
    assert result.draft.changed and result.draft.title == "Updated labels"
    current = await read(c)
    assert target(current, "QUESTION").row_version == target(state, "QUESTION").row_version


@pytest.mark.asyncio
async def test_noop_receipt_does_not_increment_versions_or_audit(flow):
    c = flow
    state = await read(c)
    before = snapshot(c)
    events = c.events.create_event.await_count
    result = await run(c, state, {})
    assert result.draft.changed is False
    assert result.draft.version_row_version == state.version_row_version
    assert snapshot(c)[0] == before[0]
    assert c.events.create_event.await_count == events
    after = snapshot(c)
    assert await run(c, state, {}) == result
    assert snapshot(c) == after
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_noop_with_new_key_is_conflict(flow):
    c = flow
    state = await read(c)
    await run(c, state, {"required": False})
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await run(c, state, {}, key="stale-noop")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["item", "kind", "version", "fields", "labels"])
async def test_changed_request_with_same_key_is_platform_conflict(flow, change):
    c = flow
    state = await read(c)
    fields = {"active": False}
    await run(c, state, fields)
    before = snapshot(c)
    options = {}
    updated = fields
    if change == "item":
        options["item_id"] = uuid4()
    elif change == "kind":
        options["kind"] = "SECTION"
    elif change == "version":
        options["expected_row_version"] = 2
    elif change == "fields":
        updated = {"active": True}
    else:
        options["labels"] = state.definition.payload()["labels"]
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, state, updated, **options)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_unit_enum_and_json_unit_have_same_request_identity(flow):
    c = flow
    state = await read(c)
    first = await run(c, state, {"unit_code": UnitCode.M2})
    assert first.draft.changed is False
    assert await run(c, state, {"unit_code": "M2"}) == first
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_uuid_aliases_are_canonical_before_request_hashing(flow):
    c = flow
    state = await read(c)
    identity = uuid4()
    fields = {
        "work_type_source": "ORGANIZATION",
        "work_type_id": identity,
        "work_type_version_policy": "CURRENT_PUBLISHED",
    }
    first = await run(c, state, fields)
    canonical = fields | {"work_type_id": str(identity).upper()}
    assert await run(c, state, canonical) == first
    assert c.item_calls.await_args.kwargs["fields"]["work_type_id"] == str(identity)
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_replay_after_publication_does_not_edit_published_content(flow):
    c = flow
    state = await read(c)
    fields = {"required": False}
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
    await publisher.publish_draft(
        actor=c.actor, template_id=c.base.template_id,
        version_id=c.draft.version_id,
        expected_row_version=first.draft.version_row_version,
        trace_id="later-item-publication")
    before = snapshot(c)
    assert await run(c, state, fields) == first
    assert snapshot(c) == before
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"id": str(uuid4())},
    {"template_section_id": str(uuid4())},
    {"sort_order": True},
    {"sort_order": -1},
    {"sort_order": 2**31},
    {"validation_schema": {"minimum": 0.1}},
    {"work_type_id": "not-a-uuid"},
    {"show_if": {"question_code": "AREA", "operator": "EQ", "value": "1"}},
])
async def test_invalid_item_data_leaves_no_mutations_or_receipt(flow, fields):
    c = flow
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await run(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_unknown_item_is_hidden_and_has_no_receipt(flow):
    c = flow
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await run(c, state, {}, item_id=uuid4())
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "grant", "scope"])
async def test_replay_requires_fresh_authority(flow, gate):
    c = flow
    state = await read(c)
    fields = {"required": False}
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
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "membership"])
async def test_item_parent_audit_and_receipt_roll_back_together(flow, failure):
    c = flow
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
        await run(c, state, {"required": False})
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["item", "kind", "counter"])
async def test_invalid_encrypted_target_or_version_is_not_replayed(flow, change):
    c = flow
    state = await read(c)
    fields = {"required": False}
    result = await run(c, state, fields)
    payload = deepcopy(encode_result(result))
    if change == "item":
        payload["result"]["item_id"] = str(uuid4())
    elif change == "kind":
        payload["result"]["kind"] = "SECTION"
    else:
        payload["result"]["draft"]["version_row_version"] += 1
    c.sync.execute(update(c.receipts).where(
        c.receipts.c.operation == OPERATION
    ).values(response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_item_codec_round_trip_is_exact(flow):
    c = flow
    state = await read(c)
    fields = {"required": False}
    result = await run(c, state, fields)
    assert decode_result(
        encode_result(result), actor=c.actor,
        request=request(c, state, fields)) == result


def test_factory_binds_one_session_without_connections(flow):
    c = flow
    workflow = build_construction_template_draft_item_workflow(
        session=c.session,
        idempotency_encryption_key=Fernet.generate_key().decode("ascii"),
        now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.service.session is c.session
    assert workflow.runner.session is c.session
    assert workflow.idempotency.repository.session is c.session
