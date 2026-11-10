"""Draft reads and header edits with local SQLite and mandatory audit."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentRepository as Repository,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentService as Service,
    build_construction_template_draft_content_service,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingValidationError as Validation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_02_template_drafts import (
    case, onboarding, catalog_case, copy_case, read_case, drafts,
    begin, create, create_copy, snapshot,
)


@pytest.fixture
async def editable(drafts):
    c = drafts
    c.base = await create_copy(c)
    c.draft = await create(c, c.base)
    repository = Repository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    c.content_repo = repository
    c.content = Service(
        session=c.session, repository=repository, draft_service=c.drafts)
    yield c


async def read(c, **options):
    begin(c)
    return await c.content.read_draft(**(
        dict(actor=c.actor, template_id=c.base.template_id,
             version_id=c.draft.version_id) | options))


async def edit(c, state, fields, **options):
    begin(c)
    return await c.content.edit_header(**(
        dict(actor=c.actor, template_id=c.base.template_id,
             version_id=c.draft.version_id,
             expected_row_version=state.version_row_version,
             fields=fields, trace_id="draft-header-test") | options))


def renamed(state):
    labels = deepcopy(state.definition.payload()["labels"])
    labels[state.source_language]["title"] = "Organization template"
    return {"labels": labels}


@pytest.mark.asyncio
async def test_read_returns_draft_definition_and_local_references_without_writes(editable):
    c = editable
    before, commits = snapshot(c), c.session.commits
    events = c.events.create_event.await_count
    state = await read(c)
    assert state.status == "DRAFT" and state.version_row_version == 1
    assert state.version_id == c.draft.version_id
    assert state.source_starter_version_id == c.base.source_starter_version_id
    assert len(state.sections) == 2
    assert sum(len(section.questions) for section in state.sections) == 3
    assert state.definition.payload()["labels"]["en"]["title"] == "Starter"
    assert snapshot(c) == before and c.session.commits == commits
    assert c.events.create_event.await_count == events


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_header_edit_preserves_published_content_references_and_root(editable, role):
    c = editable
    c.grant.role = role
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    result = await edit(c, state, renamed(state))
    assert result.changed and result.draft.title == "Organization template"
    assert result.draft.version_row_version == 2
    assert result.draft.sections == state.sections
    assert result.draft.version == state.version
    assert result.draft.created_at == state.created_at
    assert result.draft.created_by == state.created_by
    assert c.session.commits == commits + 1
    after = snapshot(c)
    assert after[0] == before[0]
    assert after[2:7] == before[2:7]
    old_base = next(row for row in before[1] if row["id"] == c.base.template_version_id)
    new_base = next(row for row in after[1] if row["id"] == c.base.template_version_id)
    assert new_base == old_base
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_measurement_template_draft_updated"
    assert event["entity_id"] == c.base.template_id
    assert event["trace_id"] == "draft-header-test"
    assert event["payload"]["operation"] == "edit_draft_header"
    assert event["payload"]["template_version_id"] == str(c.draft.version_id)
    assert [change["field"] for change in event["payload"]["changes"]] == ["labels"]
    assert (await read(c)) == result.draft


@pytest.mark.asyncio
async def test_explicit_source_language_uses_supplied_labels_without_translation(editable):
    c = editable
    state = await read(c)
    labels = state.definition.payload()["labels"]
    labels["uk"] = dict(labels["en"])
    labels["uk"]["title"] = "Шаблон організації"
    result = await edit(c, state, {"source_language": "uk", "labels": labels})
    assert result.draft.source_language == "uk"
    assert result.draft.title == "Шаблон організації"
    assert result.draft.sections == state.sections


@pytest.mark.asyncio
async def test_description_can_be_removed_with_coherent_source_labels(editable):
    c = editable
    state = await read(c)
    labels = state.definition.payload()["labels"]
    labels["en"].pop("description")
    result = await edit(c, state, {"description_key": None, "labels": labels})
    assert result.changed and result.draft.description is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "same_labels", "same_language"])
async def test_current_noop_has_no_version_increment_or_new_audit(editable, kind):
    c = editable
    state = await read(c)
    fields = {}
    if kind == "same_labels":
        fields = {"labels": state.definition.payload()["labels"]}
    elif kind == "same_language":
        fields = {"source_language": state.source_language}
    before = snapshot(c)
    events = c.events.create_event.await_count
    result = await edit(c, state, fields)
    assert not result.changed and result.draft == state
    assert snapshot(c) == before and c.events.create_event.await_count == events


@pytest.mark.asyncio
async def test_stale_noop_is_a_conflict(editable):
    c = editable
    old = await read(c)
    await edit(c, old, renamed(old))
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await edit(c, old, {})
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"status": "PUBLISHED"},
    {"tenant_id": "foreign"},
    {"content_hash": "0" * 64},
    {"row_version": 99},
    {"sections": []},
    {"labels": {"en": {"title": "Incomplete"}}},
    {"source_language": "uk"},
    {"title_key": "missing_title"},
])
async def test_invalid_fields_or_incomplete_labels_do_not_write(editable, fields):
    c = editable
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Validation):
        await edit(c, state, fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["missing_template", "missing_version", "published"])
async def test_unavailable_draft_has_safe_not_found(editable, target):
    c = editable
    options = {}
    if target == "missing_template":
        options["template_id"] = uuid4()
    elif target == "missing_version":
        options["version_id"] = uuid4()
    else:
        options["version_id"] = c.base.template_version_id
    before = snapshot(c)
    with pytest.raises(NotFound, match="Organization draft is unavailable"):
        await read(c, **options)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_edit_cannot_modify_published_version(editable):
    c = editable
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await edit(c, state, renamed(state), version_id=c.base.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_header_and_audit_roll_back_together(editable, failure):
    c = editable
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    if failure == "audit":
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
        await edit(c, state, renamed(state))
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_caller_owned_transaction_can_rollback_header_and_audit(editable):
    c = editable
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    result = await edit(c, state, renamed(state), manage_transaction=False)
    assert result.changed and c.session.commits == commits
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_revocation_after_update_audit_rolls_back_header(editable):
    c = editable
    state = await read(c)
    c.after_audit = lambda: setattr(c, "membership", False)
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await edit(c, state, renamed(state))
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_zero_conditional_update_is_conflict_without_audit(editable):
    c = editable
    state = await read(c)
    c.content_repo.update_header = AsyncMock(return_value=None)
    before = snapshot(c)
    events = c.events.create_event.await_count
    with pytest.raises(ConstructionRowVersionConflictError):
        await edit(c, state, renamed(state))
    assert snapshot(c) == before
    assert c.events.create_event.await_count == events


@pytest.mark.asyncio
async def test_repository_lock_modes_and_update_predicates():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None))
    repo = Repository(Capture())
    tenant, template, version = uuid4(), uuid4(), uuid4()
    for write in (False, True):
        await repo.lock_template(tenant_id=tenant, template_id=template, write=write)
        await repo.lock_draft(
            tenant_id=tenant, template_id=template,
            version_id=version, write=write)
    from datetime import datetime, timezone
    await repo.update_header(
        tenant_id=tenant, template_id=template, version_id=version,
        expected_row_version=7, fields={"title_key": "newTitle"},
        as_of=datetime(2026, 11, 6, tzinfo=timezone.utc))
    queries = [q.compile(dialect=postgresql.dialect()) for q in statements]
    assert "FOR SHARE" in str(queries[0]) and "FOR SHARE" in str(queries[1])
    assert "FOR UPDATE" in str(queries[2]) and "FOR UPDATE" in str(queries[3])
    for query in queries:
        assert tenant in query.params.values()
        assert template in query.params.values()
    update_query = queries[-1]
    assert version in update_query.params.values()
    assert "DRAFT" in update_query.params.values()
    assert 7 in update_query.params.values()
    assert "row_version +" in str(update_query)


def test_factory_binds_one_session_without_opening_connections(editable):
    c = editable
    service = build_construction_template_draft_content_service(
        session=c.session, now_provider=lambda: c.now)
    assert service.session is c.session
    assert service.repository.session is c.session
    assert service.authorizer.session is c.session
    assert service.authorizer.verifier is None
