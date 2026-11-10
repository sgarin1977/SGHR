"""Local draft item writes with full-definition validation and atomic audit."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemRepository as Repository,
)
from services.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemService as Service,
    build_construction_template_draft_item_service,
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
def items(editable):
    c = editable
    repository = Repository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.content_repo, name))
    c.item_repo = repository
    c.items = Service(
        session=c.session, repository=repository, content_service=c.content)
    yield c


def target(state, kind):
    if kind == "SECTION":
        return state.sections[0]
    return state.sections[0].questions[1]


async def update(c, state, fields, *, kind="QUESTION", **options):
    begin(c)
    return await c.items.edit_item(**(
        dict(
            actor=c.actor, template_id=c.base.template_id,
            version_id=c.draft.version_id, kind=kind,
            item_id=target(state, kind).id,
            expected_row_version=state.version_row_version,
            fields=fields, trace_id="draft-item-test",
        ) | options))


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_edit_preserves_ids_and_published_history(items, role, kind):
    c = items
    c.grant.role = role
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    fields = {"sort_order": 5} if kind == "SECTION" else {
        "required": False, "validation_schema": {"minimum": "0.125"}}
    result = await update(c, state, fields, kind=kind)
    assert result.changed
    assert result.draft.version_row_version == state.version_row_version + 1
    assert target(result.draft, kind).id == target(state, kind).id
    assert target(result.draft, kind).row_version == target(state, kind).row_version + 1
    assert c.session.commits == commits + 1
    after = snapshot(c)
    assert after[0] == before[0] and after[4:7] == before[4:7]
    for index in (1, 2, 3):
        for old in before[index]:
            belongs_to_base = (
                old["id"] == c.base.template_version_id if index == 1
                else old["template_version_id"] == c.base.template_version_id)
            if belongs_to_base:
                assert next(row for row in after[index] if row["id"] == old["id"]) == old
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_measurement_template_draft_updated"
    assert event["payload"]["operation"] == "edit_draft_" + kind.lower()
    assert event["payload"]["item_id"] == str(target(state, kind).id)
    assert event["trace_id"] == "draft-item-test"
    assert (await read(c)) == result.draft


@pytest.mark.asyncio
async def test_label_key_and_corresponding_labels_change_atomically(items):
    c = items
    state = await read(c)
    labels = state.definition.payload()["labels"]
    labels["en"]["customArea"] = labels["en"].pop("area")
    result = await update(
        c, state, {"label_key": "customArea"}, labels=labels)
    question = result.draft.definition.payload()["sections"][0]["questions"][1]
    assert question["label_key"] == "customArea"
    assert result.draft.definition.payload()["labels"]["en"]["customArea"] == "Measured area"


@pytest.mark.asyncio
async def test_question_code_can_change_when_no_condition_references_it(items):
    c = items
    state = await read(c)
    result = await update(c, state, {"code": "CUSTOM_AREA"})
    reference = result.draft.sections[0].questions[1]
    assert reference.id == state.sections[0].questions[1].id
    assert reference.code == "CUSTOM_AREA"


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"required": True}, {"sort_order": 20}])
async def test_current_noop_has_no_new_audit_or_version(items, fields):
    c = items
    state = await read(c)
    before = snapshot(c)
    events = c.events.create_event.await_count
    result = await update(c, state, fields)
    assert not result.changed and result.draft == state
    assert snapshot(c) == before and c.events.create_event.await_count == events


@pytest.mark.asyncio
async def test_header_and_item_writers_share_the_parent_version(items):
    c = items
    state = await read(c)
    await edit_header(c, state, renamed(state))
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await update(c, state, {})
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"id": str(uuid4())},
    {"template_section_id": str(uuid4())},
    {"row_version": 100},
    {"question_type": "NUMBER", "options": [{"value": "YES", "label_key": "yes"}]},
    {"unit_code": "UNKNOWN"},
    {"formula_type": "UNKNOWN"},
    {"validation_schema": {"minimum": 0.1}},
    {"show_if": {"question_code": "AREA", "operator": "EQ", "value": "1"}},
    {"show_if": {"question_code": "MISSING", "operator": "EQ", "value": "YES"}},
    {"sort_order": 0},
    {"label_key": "missingLabel"},
    {"work_type_source": "ORGANIZATION"},
])
async def test_invalid_question_update_cannot_change_any_rows(items, fields):
    c = items
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await update(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"sort_order": 20},
    {"sort_order": 30},
    {"work_phase_code": "UNKNOWN_PHASE"},
    {"questions": []},
])
async def test_section_update_preserves_order_and_closed_fields(items, fields):
    c = items
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await update(c, state, fields, kind="SECTION")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_phase_gate_cannot_be_disabled(items):
    c = items
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await update(
            c, state, {"active": False},
            item_id=state.sections[0].questions[0].id)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_or_unknown_item_is_hidden(items):
    c = items
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await update(c, state, {"required": False}, item_id=uuid4())
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_published_version_cannot_be_updated(items):
    c = items
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await update(
            c, state, {"required": False},
            version_id=c.base.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["item", "audit", "commit", "cancel"])
async def test_parent_item_and_audit_roll_back_together(items, failure):
    c = items
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    if failure == "item":
        original = c.item_repo.update_item
        async def broken(**values):
            await original(**values)
            raise RuntimeError("private item failure")
        c.item_repo.update_item = broken
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
        await update(c, state, {"required": False})
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["advance_draft", "update_item"])
async def test_conditional_conflict_rolls_back_parent_and_item(items, method):
    c = items
    state = await read(c)
    setattr(c.item_repo, method, AsyncMock(return_value=None))
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await update(c, state, {"required": False})
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_caller_can_rollback_complete_operation(items):
    c = items
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    result = await update(c, state, {"required": False}, manage_transaction=False)
    assert result.changed and c.session.commits == commits
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_fresh_membership_is_required_after_audit(items):
    c = items
    state = await read(c)
    c.after_audit = lambda: setattr(c, "membership", False)
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await update(c, state, {"required": False})
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_repository_update_requires_correct_tenant_draft_and_versions():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None))
    repository = Repository(Capture())
    from datetime import datetime, timezone
    tenant, template, version, item = (uuid4() for _ in range(4))
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert await repository.advance_draft(
        tenant_id=tenant, template_id=template, version_id=version,
        expected_row_version=7, labels={}, as_of=now) is None
    assert await repository.update_item(
        tenant_id=tenant, template_id=template, version_id=version,
        kind="QUESTION", item_id=item, expected_item_row_version=3,
        expected_version_row_version=8, fields={"required": False},
        as_of=now) is None
    queries = [q.compile(dialect=postgresql.dialect()) for q in statements]
    for query in queries:
        assert tenant in query.params.values()
        assert template in query.params.values()
        assert version in query.params.values()
        assert "DRAFT" in query.params.values()
    assert "EXISTS" in str(queries[1])
    assert item in queries[1].params.values()
    assert 3 in queries[1].params.values() and 8 in queries[1].params.values()


def test_factory_binds_one_transaction_session(items):
    c = items
    service = build_construction_template_draft_item_service(
        session=c.session, now_provider=lambda: c.now)
    assert service.session is c.session
    assert service.repository.session is c.session
    assert service.content.session is c.session
    assert service.authorizer.session is c.session
