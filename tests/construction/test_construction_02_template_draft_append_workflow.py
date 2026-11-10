"""Local atomic append receipts; no native concurrency claim."""
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
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_template_draft_append import (
    ConstructionTemplateDraftAppendService, UNSET,
)
from services.construction.construction_template_draft_append_workflow import (
    ConstructionTemplateDraftAppendWorkflow as Workflow,
    OPERATION, encode_result, decode_result, normalize_append_request,
    build_construction_template_draft_append_workflow,
)
from database.repositories.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemRepository,
)
from test_construction_02_template_draft_header_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    header, begin, read, snapshot,
)
from test_construction_02_template_draft_append import (
    question, section, labels, assert_old_rows_preserved,
)


@pytest.fixture
def appends(header):
    c = header
    repository = ConstructionTemplateDraftItemRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    c.append_service = ConstructionTemplateDraftAppendService(
        session=c.session, repository=repository, content_service=c.content)
    c.append_workflow = Workflow(
        session=c.session, append_service=c.append_service,
        idempotency_service=c.idempotency)
    original = c.append_service.append_item

    async def append(**values):
        c.order.append("append")
        return await original(**values)
    c.append_calls = AsyncMock(side_effect=append)
    c.append_service.append_item = c.append_calls
    c.order.clear()
    yield c


def values(c, state, *, kind="QUESTION", body=None, **overrides):
    result = {
        "actor": c.actor,
        "template_id": c.base.template_id,
        "version_id": c.draft.version_id,
        "expected_row_version": state.version_row_version,
        "kind": kind,
        "body": (
            question() if kind == "QUESTION" else section()
        ) if body is None else body,
        "section_id": state.sections[0].id if kind == "QUESTION" else None,
        "labels": labels(state, with_section=kind == "SECTION"),
        "trace_id": "keyed-append-test",
    }
    result.update(overrides)
    if result.get("labels") is UNSET:
        result.pop("labels")
    return result


async def run(c, state, *, key="append-key", **options):
    begin(c)
    return await c.append_workflow.append_item(
        **values(c, state, **options), idempotency_key=key)


def request(c, state, **options):
    source = values(c, state, **options)
    optional = {"labels": source["labels"]} if "labels" in source else {}
    body, normalized_labels = normalize_append_request(
        kind=source["kind"], body=source["body"], **optional)
    result = {
        "template_id": str(source["template_id"]),
        "version_id": str(source["version_id"]),
        "expected_row_version": source["expected_row_version"],
        "kind": source["kind"],
        "section_id": (
            None if source["section_id"] is None else str(source["section_id"])),
        "body": body,
    }
    if normalized_labels is not UNSET:
        result["labels"] = normalized_labels
    return result


def records(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.receipts).where(
            c.receipts.c.operation == OPERATION).order_by(c.receipts.c.id)
    ).mappings())


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_append_ids_audit_and_encrypted_receipt_share_one_commit(appends, role, kind):
    c = appends
    c.grant.role = role
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    key = "raw-append-key-not-for-storage"
    first = await run(c, state, kind=kind, key=key)
    assert c.session.commits == commits + 1
    assert first.draft.changed is True
    assert first.draft.version_row_version == state.version_row_version + 1
    assert first.draft.created_by == state.created_by
    assert len(first.section_ids) == (1 if kind == "SECTION" else 0)
    assert len(first.question_ids) == (2 if kind == "SECTION" else 1)
    assert len(set(first.section_ids + first.question_ids)) == (
        len(first.section_ids) + len(first.question_ids))
    assert c.order.index("barrier") < c.order.index("reserve") < c.order.index("append")
    assert c.order.index("append") < c.order.index("complete") < c.order.index("commit")
    assert c.append_calls.await_args.kwargs["manage_transaction"] is False
    assert_old_rows_preserved(before[0], snapshot(c)[0])
    saved = await read(c)
    saved_ids = {q.id for s in saved.sections for q in s.questions}
    assert set(first.question_ids).issubset(saved_ids)
    assert all(
        q.row_version == 1
        for s in saved.sections for q in s.questions
        if q.id in first.question_ids)

    record, = records(c)
    assert record["status"] == "completed" and record["response_status"] == 201
    assert key not in repr(record)
    assert isinstance(record["response_ciphertext"], bytes)
    payload = c.idempotency.response_codec.decrypt(record["response_ciphertext"])
    assert payload == encode_result(first)
    assert "definition" not in payload["result"]["draft"]
    assert "body" not in payload["result"]
    event = c.events.create_event.await_args.kwargs
    assert event["trace_id"] == "keyed-append-test"
    assert event["payload"]["operation"] == "add_draft_" + kind.lower()
    assert event["payload"]["new_question_ids"] == [
        str(value) for value in first.question_ids]

    after = snapshot(c)
    assert await run(c, state, kind=kind, key=key) == first
    assert snapshot(c) == after
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_omitted_labels_reuse_existing_dictionary(appends):
    c = appends
    state = await read(c)
    body = question() | {"label_key": "area"}
    first = await run(c, state, body=body, labels=UNSET)
    assert "labels" not in c.append_calls.await_args.kwargs
    saved = await read(c)
    assert saved.definition.payload()["labels"] == state.definition.payload()["labels"]
    after = snapshot(c)
    assert await run(c, state, body=body, labels=UNSET) == first
    assert snapshot(c) == after


@pytest.mark.asyncio
async def test_duplicate_code_with_new_key_is_not_an_implicit_noop(appends):
    c = appends
    state = await read(c)
    await run(c, state)
    current = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await run(c, current, key="second-addition-key")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_stale_expected_version_rolls_back_reservation(appends):
    c = appends
    state = await read(c)
    await run(c, state)
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await run(c, state, key="stale-append-key", body=question() | {"code": "OTHER_Q"})
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "body", "labels", "parent", "kind", "version", "template", "draft",
])
async def test_reused_key_binds_complete_addition_context(appends, change):
    c = appends
    state = await read(c)
    await run(c, state)
    options = {}
    if change == "body":
        options["body"] = question() | {"required": True}
    elif change == "labels":
        altered = labels(state)
        altered["en"]["extra"] = "Different label"
        options["labels"] = altered
    elif change == "parent":
        options["section_id"] = state.sections[1].id
    elif change == "kind":
        options["kind"] = "SECTION"
    elif change == "version":
        options["expected_row_version"] = state.version_row_version + 1
    elif change == "template":
        options["template_id"] = uuid4()
    else:
        options["version_id"] = uuid4()
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, state, **options)
    assert snapshot(c) == before
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_uuid_and_enum_inputs_have_stable_request_identity(appends):
    from services.construction.construction_units import UnitCode
    c = appends
    state = await read(c)
    work_id = uuid4()
    body = question() | {
        "unit_code": UnitCode.M2,
        "work_type_source": "ORGANIZATION",
        "work_type_id": work_id,
        "work_type_version_policy": "CURRENT_PUBLISHED",
    }
    first = await run(c, state, body=body)
    after = snapshot(c)
    equivalent = body | {
        "unit_code": "M2",
        "work_type_id": str(work_id).upper(),
    }
    assert await run(c, state, body=equivalent) == first
    assert snapshot(c) == after
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_replay_after_publication_does_not_generate_new_ids(appends):
    from database.repositories.construction.construction_template_publication import (
        ConstructionTemplatePublicationRepository,
    )
    from services.construction.construction_template_publication import (
        ConstructionTemplatePublicationService,
    )
    c = appends
    state = await read(c)
    first = await run(c, state, kind="SECTION")
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
        trace_id="append-later-publication")
    before = snapshot(c)
    assert await run(c, state, kind="SECTION") == first
    assert snapshot(c) == before
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "grant", "scope"])
async def test_replay_requires_current_membership_and_authority(appends, gate):
    c = appends
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
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel", "membership"])
async def test_added_rows_version_audit_and_receipt_roll_back_together(appends, failure):
    c = appends
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
        if failure == "membership" else Operation)
    with pytest.raises(expected):
        await run(c, state, kind="SECTION")
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    question() | {"id": str(uuid4())},
    question() | {"template_section_id": str(uuid4())},
    question() | {"sort_order": True},
    question() | {"sort_order": 2**31},
    question() | {"validation_schema": {"minimum": 0.1}},
    question() | {"work_type_id": "invalid"},
    question() | {"code": "AREA"},
])
async def test_invalid_question_has_no_partial_rows_or_receipt(appends, body):
    c = appends
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await run(c, state, body=body)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_invalid_section_leaves_no_partial_content(appends):
    c = appends
    state = await read(c)
    body = section()
    body["questions"][0]["required"] = False
    before = snapshot(c)
    with pytest.raises(Validation):
        await run(c, state, kind="SECTION", body=body)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_parent_is_hidden_without_receipt(appends):
    c = appends
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await run(c, state, section_id=uuid4())
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "kind", "parent", "duplicate", "collision", "counter", "changed",
])
async def test_malformed_encrypted_receipt_is_not_replayed(appends, change):
    c = appends
    state = await read(c)
    first = await run(c, state, kind="SECTION")
    payload = deepcopy(encode_result(first))
    result = payload["result"]
    if change == "kind":
        result["kind"] = "QUESTION"
    elif change == "parent":
        result["parent_section_id"] = str(uuid4())
    elif change == "duplicate":
        result["question_ids"][1] = result["question_ids"][0]
    elif change == "collision":
        result["question_ids"][0] = str(c.draft.version_id)
    elif change == "counter":
        result["draft"]["version_row_version"] += 1
    else:
        result["draft"]["changed"] = False
    c.sync.execute(update(c.receipts).where(
        c.receipts.c.operation == OPERATION
    ).values(response_ciphertext=c.idempotency.response_codec.encrypt(payload)))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await run(c, state, kind="SECTION")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_codec_round_trip_retains_original_ids_and_metadata(appends):
    c = appends
    state = await read(c)
    first = await run(c, state, kind="SECTION")
    assert decode_result(
        encode_result(first), actor=c.actor,
        request=request(c, state, kind="SECTION")) == first


def test_factory_binds_existing_shared_session_without_connections(appends):
    c = appends
    workflow = build_construction_template_draft_append_workflow(
        session=c.session,
        idempotency_encryption_key=Fernet.generate_key().decode("ascii"),
        now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.service.session is c.session
    assert workflow.service.repository.session is c.session
    assert workflow.runner.session is c.session
    assert workflow.idempotency.repository.session is c.session
