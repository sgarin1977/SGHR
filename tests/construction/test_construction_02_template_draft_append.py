"""Local additions with canonical validation, stable old IDs and atomic audit."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from database.repositories.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemRepository as Repository,
)
from services.construction import construction_template_draft_append as module
from services.construction.construction_template_draft_append import (
    ConstructionTemplateDraftAppendService as Service,
    build_construction_template_draft_append_service,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingValidationError as Validation,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_02_template_draft_content import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, editable,
    begin, read, edit as edit_header, renamed, snapshot,
)


@pytest.fixture
def additions(editable):
    c = editable
    repository = Repository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.content_repo, name))
    c.append_repo = repository
    c.append_service = Service(
        session=c.session, repository=repository, content_service=c.content)
    yield c


def question():
    return {
        "code": "NEW_Q", "question_type": "NUMBER", "label_key": "extra",
        "required": False, "active": True, "sort_order": 30,
        "unit_code": "M2", "formula_type": "DIRECT",
        "input_schema": {"type": "string", "format": "decimal"},
        "validation_schema": {"minimum": "0.1234567890123456789"},
        "show_if": {
            "question_code": "DEMO_GATE", "operator": "EQ", "value": "YES"},
    }


def section():
    return {
        "code": "PLASTER", "label_key": "plaster",
        "work_phase_code": "PLASTER_PREP", "sort_order": 15, "active": True,
        "questions": [
            {
                "code": "NEW_GATE", "question_type": "SINGLE_CHOICE",
                "label_key": "gate", "required": True,
                "active": True, "sort_order": 10,
                "options": [
                    {"value": value, "label_key": label}
                    for value, label in (
                        ("NO", "no"), ("YES", "yes"),
                        ("NEEDS_CLARIFICATION", "unclear"))
                ],
            },
            question() | {
                "sort_order": 20,
                "show_if": {
                    "question_code": "NEW_GATE", "operator": "EQ", "value": "YES"},
            },
        ],
    }


def labels(state, *, with_section=False):
    result = state.definition.payload()["labels"]
    result["en"]["extra"] = "Additional quantity"
    if with_section:
        result["en"]["plaster"] = "Plaster preparation"
    return result


async def append(c, state, *, kind="QUESTION", body=None, **options):
    begin(c)
    return await c.append_service.append_item(**(
        dict(
            actor=c.actor, template_id=c.base.template_id,
            version_id=c.draft.version_id,
            expected_row_version=state.version_row_version,
            kind=kind, body=question() if body is None else body,
            section_id=state.sections[0].id if kind == "QUESTION" else None,
            labels=labels(state, with_section=kind == "SECTION"),
            trace_id="draft-append-test",
        ) | options))


def assert_old_rows_preserved(before, after):
    assert after[0] == before[0]
    assert after[4:7] == before[4:7]
    for index in (2, 3):
        current = {row["id"]: row for row in after[index]}
        for row in before[index]:
            assert current[row["id"]] == row


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_question_addition_preserves_old_rows_and_commits_once(additions, role):
    c = additions
    c.grant.role = role
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    result = await append(c, state)
    assert result.section_ids == () and len(result.question_ids) == 1
    assert result.draft.version_row_version == state.version_row_version + 1
    assert c.session.commits == commits + 1
    reference = next(
        q for s in result.draft.sections for q in s.questions if q.code == "NEW_Q")
    assert reference.id == result.question_ids[0] and reference.row_version == 1
    assert result.draft.sections[0].row_version == state.sections[0].row_version
    assert_old_rows_preserved(before, snapshot(c))
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_measurement_template_draft_updated"
    assert event["payload"]["operation"] == "add_draft_question"
    assert event["payload"]["new_question_ids"] == [str(reference.id)]
    assert event["trace_id"] == "draft-append-test"
    stored = await read(c)
    assert stored == result.draft
    new = next(
        q for s in stored.definition.payload()["sections"]
        for q in s["questions"] if q["code"] == "NEW_Q")
    assert new["validation_schema"]["minimum"] == "0.1234567890123456789"


@pytest.mark.asyncio
async def test_section_addition_includes_explicit_gate_and_new_server_ids(additions):
    c = additions
    state = await read(c)
    before = snapshot(c)
    result = await append(c, state, kind="SECTION", body=section())
    assert len(result.section_ids) == 1 and len(result.question_ids) == 2
    assert [s.code for s in result.draft.sections] == ["DEMO", "PLASTER", "PAINT"]
    added = result.draft.sections[1]
    assert added.id == result.section_ids[0] and added.row_version == 1
    assert tuple(q.id for q in added.questions) == result.question_ids
    assert all(q.row_version == 1 for q in added.questions)
    old_ids = {
        ref.id for ref in state.sections
    } | {q.id for ref in state.sections for q in ref.questions}
    assert not old_ids.intersection((*result.section_ids, *result.question_ids))
    assert_old_rows_preserved(before, snapshot(c))


@pytest.mark.asyncio
async def test_existing_label_can_be_reused_without_extra_labels(additions):
    c = additions
    state = await read(c)
    begin(c)
    result = await c.append_service.append_item(
        actor=c.actor, template_id=c.base.template_id,
        version_id=c.draft.version_id, expected_row_version=state.version_row_version,
        kind="QUESTION", section_id=state.sections[0].id,
        body=question() | {"label_key": "area"},
        trace_id="reuse-label")
    assert result.draft.definition.payload()["labels"] == state.definition.payload()["labels"]


@pytest.mark.asyncio
async def test_stale_parent_version_fails_before_insert(additions):
    c = additions
    state = await read(c)
    await edit_header(c, state, renamed(state))
    c.append_repo.insert_content = AsyncMock(wraps=c.append_repo.insert_content)
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await append(c, state)
    c.append_repo.insert_content.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"code": "AREA"},
    {"sort_order": 20},
    {"sort_order": 0},
    {"sort_order": 2**31},
    {"id": str(uuid4())},
    {"tenant_id": str(uuid4())},
    {"template_section_id": str(uuid4())},
    {"row_version": 100},
    {"unit_code": "UNKNOWN"},
    {"formula_type": "UNKNOWN"},
    {"validation_schema": {"minimum": 0.1}},
    {"show_if": {"question_code": "NEW_Q", "operator": "EQ", "value": "1"}},
    {"show_if": {"question_code": "MISSING", "operator": "EQ", "value": "YES"}},
    {"work_type_source": "MASTER"},
])
async def test_invalid_new_question_leaves_no_rows_or_audit(additions, change):
    c = additions
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await append(c, state, body=question() | change)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "gate", "phase", "duplicate", "order"])
async def test_invalid_section_does_not_create_partial_structure(additions, failure):
    c = additions
    state = await read(c)
    body = section()
    if failure == "empty":
        body["questions"] = []
    elif failure == "gate":
        body["questions"][0]["required"] = False
    elif failure == "phase":
        body["work_phase_code"] = "FINAL_SEALING"
    elif failure == "duplicate":
        body["code"] = "DEMO"
    else:
        body["sort_order"] = 10
    before = snapshot(c)
    with pytest.raises(Validation):
        await append(c, state, kind="SECTION", body=body)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_incomplete_labels_are_rejected(additions):
    c = additions
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await append(c, state, labels={"en": {"extra": "Only new caption"}})
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_unknown_section_is_hidden(additions):
    c = additions
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await append(c, state, section_id=uuid4())
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_published_version_cannot_receive_new_items(additions):
    c = additions
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await append(c, state, version_id=c.base.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_server_uuid_collision_fails_before_writes(additions, monkeypatch):
    c = additions
    state = await read(c)
    monkeypatch.setattr(module, "uuid4", lambda: c.draft.version_id)
    before = snapshot(c)
    with pytest.raises(Operation):
        await append(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["insert", "audit", "commit", "cancel"])
async def test_version_new_rows_labels_and_audit_roll_back_together(additions, failure):
    c = additions
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    if failure == "insert":
        original = c.append_repo.insert_content
        async def broken(**values):
            await original(**values)
            raise RuntimeError("private append failure")
        c.append_repo.insert_content = broken
    elif failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        original = c.events.create_event.side_effect
        async def cancelled(**values):
            await original(**values)
            raise asyncio.CancelledError()
        c.events.create_event.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else Operation):
        await append(c, state, kind="SECTION", body=section())
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_conditional_parent_conflict_has_no_new_content(additions):
    c = additions
    state = await read(c)
    c.append_repo.advance_draft = AsyncMock(return_value=None)
    c.append_repo.insert_content = AsyncMock(wraps=c.append_repo.insert_content)
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await append(c, state)
    c.append_repo.insert_content.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_revoked_membership_after_insert_rolls_back_everything(additions):
    c = additions
    state = await read(c)
    original = c.append_repo.insert_content
    async def revoked(**values):
        await original(**values)
        c.membership = False
    c.append_repo.insert_content = revoked
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await append(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_caller_can_rollback_addition_and_audit(additions):
    c = additions
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    result = await append(c, state, manage_transaction=False)
    assert result.question_ids and c.session.commits == commits
    await c.session.rollback()
    assert snapshot(c) == before


def test_factory_binds_one_session_without_opening_connections(additions):
    c = additions
    service = build_construction_template_draft_append_service(
        session=c.session, now_provider=lambda: c.now)
    assert service.session is c.session
    assert service.repository.session is c.session
    assert service.content.session is c.session
    assert service.authorizer.session is c.session
