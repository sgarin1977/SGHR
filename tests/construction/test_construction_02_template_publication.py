"""Local publication tests; explicit binding doubles are not registry acceptance."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_template_publication import (
    ConstructionTemplatePublicationRepository as Repository,
)
from services.construction.construction_template_publication import (
    ConstructionTemplatePublicationService as Service,
    ConstructionTemplateBindingUnavailable as BindingUnavailable,
    build_construction_template_publication_service,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    MAX_VERSION,
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
def publication(editable):
    c = editable
    repository = Repository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.content_repo, name))
    c.publication_repo = repository
    c.publication = Service(
        session=c.session, repository=repository,
        content_service=c.content, published_service=c.reader)
    yield c


async def check(c, state):
    begin(c)
    return await c.publication.check_draft(
        actor=c.actor, template_id=c.base.template_id,
        version_id=c.draft.version_id,
        expected_row_version=state.version_row_version)


async def publish(c, state, **options):
    begin(c)
    return await c.publication.publish_draft(**(
        dict(
            actor=c.actor, template_id=c.base.template_id,
            version_id=c.draft.version_id,
            expected_row_version=state.version_row_version,
            trace_id="template-publication-test",
        ) | options))


async def bind(c, *, source="ORGANIZATION", policy="CURRENT_PUBLISHED"):
    state = await read(c)
    question = state.sections[0].questions[1]
    c.sync.execute(update(c.publication_repo.questions).where(
        c.publication_repo.questions.c.id == question.id
    ).values(
        work_type_source=source, work_type_id=uuid4(),
        work_type_version_policy=policy,
        pinned_work_type_version_id=uuid4() if policy == "PINNED" else None))
    c.sync.commit()
    return await read(c)


def validator(c, **options):
    # Explicit local collaborator, not a claim of real canonical registry checks.
    return NS(
        session=c.session,
        validate_template_bindings=AsyncMock(**({"return_value": True} | options)))


@pytest.mark.asyncio
async def test_readiness_is_read_only_and_has_exact_current_hash(publication):
    c = publication
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    events = c.events.create_event.await_count
    ready = await check(c, state)
    assert ready.definition_hash == state.definition.content_hash
    assert ready.version_id == state.version_id
    assert ready.version_row_version == state.version_row_version
    assert ready.section_count == 2 and ready.question_count == 3
    assert ready.binding_count == 0
    assert snapshot(c) == before and c.session.commits == commits
    assert c.events.create_event.await_count == events


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_publish_preserves_content_old_version_and_commits_audit_once(publication, role):
    c = publication
    c.grant.role = role
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    published = await publish(c, state)
    assert published.version_id == state.version_id
    assert published.version == 2
    assert published.version_row_version == state.version_row_version + 1
    assert published.content_hash == state.definition.content_hash
    assert published.definition == state.definition
    assert published.sections == state.sections
    assert published.updated_at == published.published_at
    assert c.session.commits == commits + 1

    after = snapshot(c)
    assert after[0] == before[0] and after[2:7] == before[2:7]
    old_before = next(row for row in before[1] if row["id"] == c.base.template_version_id)
    old_after = next(row for row in after[1] if row["id"] == c.base.template_version_id)
    assert old_after == old_before
    assert len(after[-1]) == len(before[-1]) + 1
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_measurement_template_published"
    assert event["trace_id"] == "template-publication-test"
    assert event["payload"]["template_version_id"] == str(state.version_id)
    assert event["payload"]["operation"] == "publish_template_version"

    begin(c)
    assert (await c.reader.get_version(
        actor=c.actor, version_id=state.version_id)) == published
    page = await c.reader.list_templates(actor=c.actor)
    assert page.items[0].version_id == state.version_id
    old = await c.reader.get_version(
        actor=c.actor, version_id=c.base.template_version_id)
    assert old.content_hash == c.base.content_hash
    with pytest.raises(NotFound):
        await read(c)


@pytest.mark.asyncio
async def test_publication_accepts_edited_draft_current_counter(publication):
    c = publication
    state = await read(c)
    edited = (await edit_header(c, state, renamed(state))).draft
    assert edited.version_row_version == 2
    published = await publish(c, edited)
    assert published.version == 2 and published.version_row_version == 3
    assert published.title == "Organization template"


@pytest.mark.asyncio
async def test_old_readiness_does_not_authorize_changed_draft(publication):
    c = publication
    state = await read(c)
    ready = await check(c, state)
    await edit_header(c, state, renamed(state))
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await publish(c, state, expected_row_version=ready.version_row_version)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_published_version_cannot_be_published_or_edited_again(publication):
    c = publication
    state = await read(c)
    await publish(c, state)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await publish(c, state)
    with pytest.raises(NotFound):
        await edit_header(c, state, renamed(state))
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_publication_does_not_enable_inactive_root(publication):
    c = publication
    c.sync.execute(update(c.publication_repo.templates).where(
        c.publication_repo.templates.c.id == c.base.template_id
    ).values(active=False, row_version=2))
    c.sync.commit()
    state = await read(c)
    published = await publish(c, state)
    assert published.active is False and published.template_row_version == 2


@pytest.mark.asyncio
async def test_binding_requires_explicit_canonical_validator(publication):
    c = publication
    state = await bind(c)
    before = snapshot(c)
    with pytest.raises(BindingUnavailable):
        await check(c, state)
    with pytest.raises(BindingUnavailable):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["MASTER", "ORGANIZATION"])
@pytest.mark.parametrize("policy", ["CURRENT_PUBLISHED", "PINNED"])
async def test_binding_contract_is_checked_before_and_after_publication_audit(
    publication, source, policy,
):
    c = publication
    state = await bind(c, source=source, policy=policy)
    c.publication.binding_validator = validator(c)
    ready = await check(c, state)
    assert ready.binding_count == 1
    c.publication.binding_validator.validate_template_bindings.reset_mock()
    result = await publish(c, state)
    calls = c.publication.binding_validator.validate_template_bindings.await_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.kwargs["actor"] is c.actor
        assert call.kwargs["definition"] == state.definition
    assert result.definition == state.definition


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [False, None, 1, "true"])
async def test_non_true_binding_result_does_not_publish(publication, value):
    c = publication
    state = await bind(c)
    c.publication.binding_validator = validator(c, return_value=value)
    before = snapshot(c)
    with pytest.raises(BindingUnavailable):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_binding_failure_after_audit_rolls_back_publication(publication):
    c = publication
    state = await bind(c)
    c.publication.binding_validator = validator(c, side_effect=[True, False])
    before = snapshot(c)
    with pytest.raises(BindingUnavailable):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_invalid_stored_gate_cannot_be_published(publication):
    c = publication
    state = await read(c)
    gate = state.sections[0].questions[0]
    c.sync.execute(update(c.publication_repo.questions).where(
        c.publication_repo.questions.c.id == gate.id).values(required=False))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_exhausted_counter_cannot_be_published(publication):
    c = publication
    state = await read(c)
    c.sync.execute(update(c.publication_repo.versions).where(
        c.publication_repo.versions.c.id == state.version_id
    ).values(row_version=MAX_VERSION))
    c.sync.commit()
    state = await read(c)
    before = snapshot(c)
    with pytest.raises(Operation):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_publication_and_audit_roll_back_together(publication, failure):
    c = publication
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
        await publish(c, state)
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_conditional_publication_conflict_has_no_audit(publication):
    c = publication
    state = await read(c)
    c.publication_repo.publish_draft = AsyncMock(return_value=None)
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_caller_can_rollback_published_header_and_audit(publication):
    c = publication
    state = await read(c)
    before, commits = snapshot(c), c.session.commits
    result = await publish(c, state, manage_transaction=False)
    assert result.version_id == state.version_id and c.session.commits == commits
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_revocation_after_audit_rolls_back_publication(publication):
    c = publication
    state = await read(c)
    c.after_audit = lambda: setattr(c, "membership", False)
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await publish(c, state)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_repository_publication_is_tenant_draft_and_version_conditional():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None))
    repo = Repository(Capture())
    from datetime import datetime, timezone
    tenant, template, version = uuid4(), uuid4(), uuid4()
    assert await repo.publish_draft(
        tenant_id=tenant, template_id=template, version_id=version,
        expected_row_version=7, content_hash="a" * 64,
        as_of=datetime(2026, 11, 6, tzinfo=timezone.utc)) is None
    compiled = statements[0].compile(dialect=postgresql.dialect())
    for value in (tenant, template, version, 7, "DRAFT", "PUBLISHED", "a" * 64):
        assert value in compiled.params.values()
    assert "row_version +" in str(compiled)


def test_binding_validator_requires_same_transaction_session(publication):
    c = publication
    with pytest.raises(ValueError):
        Service(
            session=c.session, repository=c.publication_repo,
            content_service=c.content, published_service=c.reader,
            binding_validator=NS(
                session=object(), validate_template_bindings=AsyncMock()))


def test_factory_binds_shared_services_without_opening_connections(publication):
    c = publication
    service = build_construction_template_publication_service(
        session=c.session, now_provider=lambda: c.now)
    assert service.session is c.session
    assert service.repository.session is c.session
    assert service.content.session is c.session
    assert service.published.session is c.session
    assert service.binding_validator is None
