"""Exact cache metadata over local SQLite; no provider or external database."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from services.construction.construction_content_translation import (
    ContentTranslationRecord as Record,
    ContentTranslationStatus as Status,
)
from services.construction.construction_template_translation_state import (
    ConstructionTemplateTranslationStateService as Service,
    TemplateTranslationStateError,
)
from services.construction.construction_template_translation_workflow import (
    ConstructionTemplateTranslationWorkflow,
    TemplateTranslationWorkflowError,
    TemplateTranslationContextChanged,
)
from services.construction.construction_template_translation_source import (
    template_label_field,
    TemplateTranslationLanguageChanged,
)
from services.construction.construction_starter_translation import (
    ConstructionStarterTranslationSourceService,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_template_translation_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, cache_rows, domain_snapshot,
)


@pytest.fixture(params=["organization", "starter"])
def state_case(request, workflow_case):
    c = workflow_case
    c.state_kind = request.param
    if request.param == "organization":
        workflow = c.translation_workflow
        c.state_version = c.base.template_version_id
    else:
        source = ConstructionStarterTranslationSourceService(
            session=c.session, platform_session=c.session,
            starter_service=c.catalog,
            identity=c.identity, language_repository=c.languages)
        workflow = ConstructionTemplateTranslationWorkflow(
            session=c.session, source_service=source,
            cache_repository=c.translation_repo, translator=c.translator,
            suite_access=c.suite, module_access=c.module)
        c.state_version = UUID(int=2001)
    c.state_workflow = workflow
    c.states = Service(workflow=workflow)
    return c


async def read(c, field="title"):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.states.read_for_actor(
        actor=c.actor, version_id=c.state_version, field_name=field)


async def seed(c, *, status=Status.AUTO_TRANSLATED, field="title", difference=None):
    if not c.sync.in_transaction():
        c.sync.begin()
    context = await c.state_workflow.sources.read_for_actor(
        actor=c.actor, version_id=c.state_version, field_name=field)
    key = context.source.key_for(context.target_language)
    tenant = c.tenant
    if difference == "version":
        key = replace(key, source_version=key.source_version + 1)
    elif difference == "hash":
        key = replace(key, source_text_hash="0" * 64)
    elif difference == "target":
        key = replace(key, target_language="fi")
    elif difference == "source_language":
        key = replace(key, source_language="it")
    elif difference == "entity":
        key = replace(key, entity_id=uuid4())
    elif difference == "tenant":
        tenant = c.foreign

    record = Record(
        key=key, translated_text="Cached text",
        translation_status=Status.AUTO_TRANSLATED,
        translated_at=c.now)
    saved = await c.translation_repo.insert_automatic(
        tenant_id=tenant, record=record, as_of=c.now)
    if status is not Status.AUTO_TRANSLATED:
        c.sync.execute(update(c.translation_table).where(
            c.translation_table.c.id == saved["id"]
        ).values(
            translation_status=status.value,
            reviewed_by=c.user if status is Status.VERIFIED else None))
    c.sync.commit()
    return saved


@pytest.mark.asyncio
async def test_missing_cache_has_no_invented_status_and_no_provider_call(state_case):
    c = state_case
    before, commits = domain_snapshot(c), c.session.commits
    result = await read(c)
    assert result.tenant_id == c.tenant and result.user_id == c.user
    assert result.version_id == c.state_version and result.field_name == "title"
    assert result.target_language == "uk" and result.source_language == "en"
    assert result.source_version == 1
    assert len(result.source_text_hash) == 64
    assert result.cache_present is False and result.usable is False
    assert result.cache_id is None and result.translation_status is None
    assert result.translated_at is None and result.reviewed_by is None
    assert result.cache_row_version is None
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits
    c.provider.translate.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    with pytest.raises(FrozenInstanceError):
        result.usable = True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    Status.VERIFIED, Status.AUTO_TRANSLATED, Status.STALE,
])
@pytest.mark.parametrize("field", ["title", template_label_field("area")])
async def test_exact_persisted_status_date_and_reviewer_are_preserved(
    state_case, status, field,
):
    c = state_case
    saved = await seed(c, status=status, field=field)
    before, domain, commits = cache_rows(c), domain_snapshot(c), c.session.commits
    result = await read(c, field)
    assert result.cache_present is True
    assert result.translation_status is status
    assert result.cache_id == saved["id"] and result.cache_row_version == 1
    assert result.source_text_hash == saved["source_text_hash"]
    assert result.translated_at == c.now
    assert result.reviewed_by == (c.user if status is Status.VERIFIED else None)
    assert result.usable is (status is not Status.STALE)
    assert not hasattr(result, "translated_text")
    assert not hasattr(result, "original_text")
    assert cache_rows(c) == before and domain_snapshot(c) == domain
    assert c.session.commits == commits
    c.provider.translate.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", [
    "version", "hash", "target", "source_language", "entity", "tenant",
])
async def test_other_cache_identity_is_never_reported_as_current(state_case, difference):
    c = state_case
    await seed(c, status=Status.VERIFIED, difference=difference)
    before, commits = cache_rows(c), c.session.commits
    result = await read(c)
    assert result.cache_present is False
    assert result.translation_status is None and result.reviewed_by is None
    assert result.cache_id is None and result.usable is False
    assert cache_rows(c) == before and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_language_needs_no_cache_query(state_case):
    c = state_case
    await seed(c)
    before = cache_rows(c)
    c.language = "en"
    c.translation_repo.get_exact = AsyncMock(wraps=c.translation_repo.get_exact)
    result = await read(c)
    assert result.target_language == result.source_language == "en"
    assert result.cache_present is False and result.translation_status is None
    c.translation_repo.get_exact.assert_not_awaited()
    c.provider.translate.assert_not_awaited()
    assert cache_rows(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["language", "membership", "grant", "suite", "core"])
async def test_fresh_authority_and_language_after_cache_read_are_required(
    state_case, change,
):
    c = state_case
    await seed(c)
    original = c.translation_repo.get_exact

    async def changed(**values):
        row = await original(**values)
        if change == "language":
            c.language = "it"
        elif change == "membership":
            c.membership = False
        elif change == "grant":
            c.grant.status = "revoked"
        elif change == "suite":
            c.suite.require_enabled.side_effect = SuiteDisabledError()
        else:
            c.module.require_enabled.side_effect = ModuleDisabledError()
        return row

    c.translation_repo.get_exact = changed
    expected = (
        TemplateTranslationLanguageChanged if change == "language"
        else ConstructionPermissionDeniedError if change in ("membership", "grant")
        else SuiteDisabledError if change == "suite" else ModuleDisabledError)
    before, commits = cache_rows(c), c.session.commits
    with pytest.raises(expected):
        await read(c)
    assert cache_rows(c) == before and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_source_context_is_not_reported_under_previous_identity(state_case):
    c = state_case
    original = c.state_workflow.sources.read_for_actor
    calls = 0

    async def changed(**values):
        nonlocal calls
        calls += 1
        context = await original(**values)
        return context if calls == 1 else replace(context, content_hash="0" * 64)

    c.state_workflow.sources.read_for_actor = changed
    with pytest.raises(TemplateTranslationContextChanged):
        await read(c)
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["tenant", "version", "status"])
async def test_malformed_returned_cache_is_rejected(state_case, corruption):
    c = state_case
    await seed(c)
    original = c.translation_repo.get_exact

    async def corrupt(**values):
        row = await original(**values)
        if corruption == "tenant":
            row["tenant_id"] = c.foreign
        elif corruption == "version":
            row["row_version"] = 0
        else:
            row["translation_status"] = "UNKNOWN"
        return row

    c.translation_repo.get_exact = corrupt
    with pytest.raises(TemplateTranslationWorkflowError):
        await read(c)
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_failure_is_sanitized_and_cancellation_rolls_back(state_case):
    c = state_case
    c.translation_repo.get_exact = AsyncMock(
        side_effect=RuntimeError("private database details"))
    with pytest.raises(TemplateTranslationStateError) as captured:
        await read(c)
    assert "private database details" not in str(captured.value)
    assert c.sync.in_transaction() is False

    c.translation_repo.get_exact.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await read(c)
    assert c.sync.in_transaction() is False
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_active_outer_transaction_is_required(state_case):
    c = state_case
    c.sync.rollback()
    with pytest.raises(TemplateTranslationStateError, match="clean active"):
        await c.states.read_for_actor(
            actor=c.actor, version_id=c.state_version, field_name="title")
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_description_has_no_source_or_cache_metadata(workflow_case):
    from services.construction.construction_organization_template_definitions import (
        validate_organization_template_definition,
    )
    c = workflow_case
    if not c.sync.in_transaction():
        c.sync.begin()
    state = await c.reader.get_version(
        actor=c.actor, version_id=c.base.template_version_id)
    body = state.definition.payload()
    key = body["description_key"]
    body["description_key"] = None
    for labels in body["labels"].values():
        labels.pop(key, None)
    checked = validate_organization_template_definition(body, source_language="en")
    c.sync.execute(update(c.copy_repo.versions).where(
        c.copy_repo.versions.c.id == c.base.template_version_id
    ).values(
        description_key=None, labels=body["labels"],
        content_hash=checked.content_hash))
    c.sync.commit()
    c.sync.begin()

    service = Service(workflow=c.translation_workflow)
    result = await service.read_for_actor(
        actor=c.actor, version_id=c.base.template_version_id,
        field_name="description")
    assert result.source_language is None and result.source_version is None
    assert result.source_text_hash is None and result.cache_id is None
    assert result.translation_status is None and result.cache_present is False
    c.provider.translate.assert_not_awaited()
