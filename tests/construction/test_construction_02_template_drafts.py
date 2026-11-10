"""Draft creation on local SQLite; no native or deployed acceptance claim."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_template_drafts import (
    ConstructionTemplateDraftRepository as Repository,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingValidationError as Validation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateNotFoundError,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftService as Service,
    ConstructionTemplateDraftNotFound as NotFound,
    build_construction_template_draft_service,
)
from test_construction_02_organization_template_read import (
    case, onboarding, catalog_case, copy_case, read_case, add_version,
)
from test_construction_02_template_copy import copy as create_copy, snapshot


@pytest.fixture
def drafts(read_case):
    c = read_case
    c.draft_repo = Repository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(c.draft_repo, name, getattr(c.copy_repo, name))
    c.drafts = Service(
        session=c.session, repository=c.draft_repo, template_service=c.reader)
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


async def create(c, base, **options):
    begin(c)
    return await c.drafts.create_draft(**(
        dict(
            actor=c.actor, template_id=base.template_id,
            base_version_id=base.template_version_id,
            expected_template_row_version=base.template_row_version,
            trace_id="template-draft-test")
        | options))


async def stored(c, base, draft):
    begin(c)
    return await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=base.template_id,
        version_id=draft.version_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_new_draft_preserves_published_version_and_rekeys_content(drafts, role):
    c = drafts
    c.grant.role = role
    base = await create_copy(c)
    begin(c)
    original = await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=base.template_id,
        version_id=base.template_version_id)
    before, commits = snapshot(c), c.session.commits
    result = await create(c, base)
    assert result.template_id == base.template_id
    assert result.base_version_id == base.template_version_id
    assert result.version_id != base.template_version_id
    assert result.version == 2 and result.version_row_version == 1
    assert result.status == "DRAFT"
    assert result.template_row_version == base.template_row_version
    assert result.source_starter_version_id == base.source_starter_version_id
    assert c.session.commits == commits + 1

    draft = await stored(c, base, result)
    assert draft["template"] == original["template"]
    assert draft["version"]["status"] == "DRAFT"
    assert draft["version"]["published_at"] is None
    assert draft["version"]["content_hash"] is None
    assert draft["version"]["labels"] == original["version"]["labels"]
    assert draft["version"]["created_by"] == c.user
    assert not ({row["id"] for row in draft["sections"]}
                & {row["id"] for row in original["sections"]})
    assert not ({row["id"] for row in draft["questions"]}
                & {row["id"] for row in original["questions"]})
    original_questions = {row["code"]: row for row in original["questions"]}
    for row in draft["questions"]:
        old = original_questions[row["code"]]
        for field in (
            "question_type", "required", "unit_code", "formula_type",
            "input_schema", "validation_schema", "options", "show_if",
            "photo_policy", "work_type_source", "work_type_id",
            "work_type_version_policy", "pinned_work_type_version_id",
        ):
            assert row[field] == old[field]
    unchanged = await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=base.template_id,
        version_id=base.template_version_id)
    assert unchanged == original
    after = snapshot(c)
    assert after[0] == before[0] and after[4:7] == before[4:7]
    assert len(after[-1]) == len(before[-1]) + 1
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_measurement_template_draft_created"
    assert event["entity_id"] == base.template_id
    assert event["trace_id"] == "template-draft-test"
    assert event["payload"]["operation"] == "create_draft"
    assert event["payload"]["base_version_id"] == str(base.template_version_id)


@pytest.mark.asyncio
async def test_drafts_are_numbered_after_all_existing_versions(drafts):
    c = drafts
    base = await create_copy(c)
    first = await create(c, base)
    second = await create(c, base)
    assert (first.version, second.version) == (2, 3)
    assert first.version_id != second.version_id
    begin(c)
    page = await c.reader.list_templates(actor=c.actor)
    assert page.items[0].version_id == base.template_version_id
    with pytest.raises(ConstructionOrganizationTemplateNotFoundError):
        await c.reader.get_version(actor=c.actor, version_id=second.version_id)


@pytest.mark.asyncio
async def test_disabled_root_can_be_prepared_without_being_enabled(drafts):
    c = drafts
    base = await create_copy(c)
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == base.template_id
    ).values(active=False, row_version=2))
    c.sync.commit()
    result = await create(c, base, expected_template_row_version=2)
    draft = await stored(c, base, result)
    assert draft["template"]["active"] is False
    assert draft["template"]["row_version"] == 2
    assert draft["version"]["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_exact_old_version_can_be_base_without_following_new_publication(drafts):
    c = drafts
    base = await create_copy(c)
    c.source_id = base.source_starter_version_id
    await add_version(c, base, number=2, org=True)
    result = await create(c, base)
    assert result.version == 3
    assert result.base_version_id == base.template_version_id
    assert result.source_starter_version_id == base.source_starter_version_id


@pytest.mark.asyncio
async def test_organization_work_bindings_survive_draft_clone(drafts):
    c = drafts
    base = await create_copy(c)
    own_version = await add_version(c, base, number=2, org=True)
    result = await create(c, base, base_version_id=own_version)
    draft = await stored(c, base, result)
    question = next(row for row in draft["questions"] if row["code"] == "AREA")
    assert question["work_type_source"] == "ORGANIZATION"
    assert question["work_type_version_policy"] == "PINNED"
    assert question["work_type_id"] is not None
    assert question["pinned_work_type_version_id"] is not None
    assert result.source_starter_version_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, False, 0, "1", 2**63])
async def test_invalid_expected_version_fails_before_locks(drafts, value):
    c = drafts
    base = await create_copy(c)
    c.serialization.lock_member.reset_mock()
    before = snapshot(c)
    with pytest.raises(Validation):
        await create(c, base, expected_template_row_version=value)
    c.serialization.lock_member.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_stale_root_version_fails_before_base_read(drafts):
    c = drafts
    base = await create_copy(c)
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == base.template_id).values(row_version=2))
    c.sync.commit()
    c.reader.get_version = AsyncMock(wraps=c.reader.get_version)
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await create(c, base)
    c.reader.get_version.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_other_template_base_is_rejected_without_creating_draft(drafts):
    c = drafts
    base = await create_copy(c)
    other = await create_copy(c, code="OTHER_TEMPLATE")
    before = snapshot(c)
    with pytest.raises(NotFound):
        await create(c, base, base_version_id=other.template_version_id)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_missing_root_has_safe_not_found_result(drafts):
    c = drafts
    base = await create_copy(c)
    before = snapshot(c)
    with pytest.raises(NotFound, match="Organization template is unavailable"):
        await create(c, base, template_id=uuid4())
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [None, True, 0, 2**31 - 1])
async def test_invalid_or_exhausted_number_does_not_write(drafts, maximum):
    c = drafts
    base = await create_copy(c)
    c.draft_repo.latest_version_number = AsyncMock(return_value=maximum)
    before = snapshot(c)
    with pytest.raises(Operation):
        await create(c, base)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["content", "audit", "commit", "cancel"])
async def test_draft_content_and_audit_roll_back_together(drafts, failure):
    c = drafts
    base = await create_copy(c)
    before, commits = snapshot(c), c.session.commits
    if failure == "content":
        original = c.draft_repo.insert_content
        async def broken(**values):
            await original(**values)
            raise RuntimeError("private insert failure")
        c.draft_repo.insert_content = broken
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
        await create(c, base)
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_caller_owned_transaction_can_rollback_draft_and_audit(drafts):
    c = drafts
    base = await create_copy(c)
    before, commits = snapshot(c), c.session.commits
    result = await create(c, base, manage_transaction=False)
    assert result.status == "DRAFT" and c.session.commits == commits
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "grant"])
async def test_authority_is_rechecked_after_content_insert(drafts, failure):
    c = drafts
    base = await create_copy(c)
    original = c.draft_repo.insert_content
    async def revoked(**values):
        await original(**values)
        if failure == "membership":
            c.membership = False
        else:
            c.grant.status = "revoked"
    c.draft_repo.insert_content = revoked
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await create(c, base)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_insufficient_role_cannot_create_draft(drafts):
    c = drafts
    base = await create_copy(c)
    c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await create(c, base)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_corrupt_base_is_not_cloned(drafts):
    c = drafts
    base = await create_copy(c)
    c.sync.execute(update(c.copy_repo.versions).where(
        c.copy_repo.versions.c.id == base.template_version_id
    ).values(content_hash="0" * 64))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Operation):
        await create(c, base)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_repository_root_lock_and_number_query_are_tenant_scoped():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(
                mappings=lambda: NS(one_or_none=lambda: None),
                scalar_one=lambda: 3)
    repository = Repository(Capture())
    tenant, template = uuid4(), uuid4()
    assert await repository.get_template_for_update(
        tenant_id=tenant, template_id=template) is None
    assert await repository.latest_version_number(
        tenant_id=tenant, template_id=template) == 3
    compiled = [
        statement.compile(dialect=postgresql.dialect())
        for statement in statements]
    assert "FOR UPDATE" in str(compiled[0])
    assert "max(" in str(compiled[1])
    for query in compiled:
        assert tenant in query.params.values()
        assert template in query.params.values()


def test_factory_uses_shared_bare_authorizer_without_opening_connections(drafts):
    c = drafts
    built = build_construction_template_draft_service(
        session=c.session, now_provider=lambda: c.now)
    assert built.session is c.session
    assert built.repository.session is c.session
    assert built.templates.session is c.session
    assert built.authorizer.verifier is None
