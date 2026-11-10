"""Local source/cache workflow; no provider network or native race claims."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, Column, String, Table, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.models import ConstructionContentTranslation as Model
from database.repositories.construction.construction_content_translations import (
    ConstructionContentTranslationRepository as Repository,
)
from services.construction.construction_content_translation import (
    ConstructionContentTranslator, ContentTranslationRecord as Record,
    ContentTranslationStatus as Status, ContentTranslationUnavailable,
)
from services.construction.construction_template_translation_workflow import (
    ConstructionTemplateTranslationWorkflow as Workflow,
    TemplateTranslationWorkflowError as Operation,
    TemplateTranslationContextChanged,
    build_construction_template_translation_workflow,
)
from services.construction.construction_template_translation_source import (
    TemplateTranslationLanguageChanged,
)
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_template_translation_source import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    snapshot as domain_snapshot,
)
from test_construction_02_tax_profiles import LocalUTC


@pytest.fixture
async def workflow_case(source_case):
    c = source_case
    c.sync.rollback()
    metadata = c.table.metadata
    languages = Table(
        "languages", metadata, Column("code", String(10), primary_key=True))
    languages.create(c.sync.get_bind())
    c.translation_table = Model.__table__.to_metadata(metadata)
    for constraint in list(c.translation_table.constraints):
        if isinstance(constraint, CheckConstraint) and (
            "~" in str(constraint.sqltext) or "octet_length" in str(constraint.sqltext)
        ):
            c.translation_table.constraints.remove(constraint)
    for name in ("created_at", "updated_at", "translated_at"):
        c.translation_table.c[name].type = LocalUTC()
    c.translation_table.create(c.sync.get_bind())
    c.sync.execute(languages.insert(), [{"code": code} for code in ("en", "uk", "it", "fi")])
    c.sync.commit()

    class LocalRepository(Repository):
        def _insert(self):
            return sqlite_insert(self.table)

    c.translation_repo = LocalRepository(c.session)
    c.translation_repo.table = c.translation_table
    c.provider = NS(translate=AsyncMock(return_value="Переклад"))
    c.suite = NS(repository=NS(session=c.session), require_enabled=AsyncMock(return_value=None))
    c.module = NS(repository=NS(session=c.session), require_enabled=AsyncMock(return_value=None))
    c.translator = ConstructionContentTranslator(
        provider=c.provider, now_provider=lambda: c.now)
    c.translation_workflow = Workflow(
        session=c.session, source_service=c.source_service,
        cache_repository=c.translation_repo, translator=c.translator,
        suite_access=c.suite, module_access=c.module)
    c.serialization.lock_member.reset_mock()
    yield c


async def run(c):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.translation_workflow.read_for_actor(
        actor=c.actor, version_id=c.base.template_version_id, field_name="title")


def cache_rows(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.translation_table).order_by(c.translation_table.c.id)).mappings())


async def seed(c, *, status=Status.AUTO_TRANSLATED, text="Cached"):
    if not c.sync.in_transaction():
        c.sync.begin()
    context = await c.source_service.read_for_actor(
        actor=c.actor, version_id=c.base.template_version_id, field_name="title")
    record = Record(
        key=context.source.key_for(context.target_language),
        translated_text=text, translation_status=Status.AUTO_TRANSLATED,
        translated_at=c.now)
    saved = await c.translation_repo.insert_automatic(
        tenant_id=c.tenant, record=record, as_of=c.now)
    if status is not Status.AUTO_TRANSLATED:
        c.sync.execute(update(c.translation_table).where(
            c.translation_table.c.id == saved["id"]
        ).values(
            translation_status=status.value,
            reviewed_by=c.user if status is Status.VERIFIED else None))
    c.sync.commit()
    return saved


@pytest.mark.asyncio
async def test_cold_translation_is_persisted_once_then_reused(workflow_case):
    c = workflow_case
    before, commits = domain_snapshot(c), c.session.commits

    async def translate(**values):
        assert c.serialization.lock_member.await_count == 0
        assert values == {
            "text": "Starter", "source_language": "en", "target_language": "uk"}
        return "Переклад"
    c.provider.translate.side_effect = translate
    first = await run(c)
    assert first.display_text == "Переклад" and first.original_text == "Starter"
    assert first.origin == "GENERATED" and first.cache_written is True
    assert first.translation_status is Status.AUTO_TRANSLATED
    assert first.cache_row_version == 1
    assert c.session.commits == commits + 1
    assert len(cache_rows(c)) == 1 and domain_snapshot(c) == before

    saved = cache_rows(c)
    second = await run(c)
    assert second.display_text == first.display_text
    assert second.origin == "CACHE" and second.cache_written is False
    assert cache_rows(c) == saved and c.session.commits == commits + 1
    c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.VERIFIED, Status.AUTO_TRANSLATED])
async def test_live_cache_needs_no_provider_or_commit(workflow_case, status):
    c = workflow_case
    await seed(c, status=status, text="Existing translation")
    before, commits = cache_rows(c), c.session.commits
    result = await run(c)
    assert result.display_text == "Existing translation"
    assert result.translation_status is status
    assert result.origin == "CACHE" and result.cache_written is False
    assert cache_rows(c) == before and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_cache_is_conditionally_replaced(workflow_case):
    c = workflow_case
    old = await seed(c, status=Status.STALE)
    result = await run(c)
    saved, = cache_rows(c)
    assert saved["id"] == old["id"]
    assert saved["row_version"] == 2
    assert saved["translation_status"] == "AUTO_TRANSLATED"
    assert result.origin == "GENERATED" and result.cache_row_version == 2
    assert result.cache_written is True


@pytest.mark.asyncio
async def test_original_language_has_no_cache_write_or_provider_call(workflow_case):
    c = workflow_case
    c.language = "en"
    commits = c.session.commits
    result = await run(c)
    assert result.display_text == result.original_text == "Starter"
    assert result.origin == "ORIGINAL" and result.translation_status is None
    assert result.cache_written is False and cache_rows(c) == ()
    assert c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_supplied_published_label_is_used_without_inventing_review_status(workflow_case):
    # Explicit local published-content fixture. Native immutability guards are
    # covered separately; this test does not perform a production publication.
    from services.construction.construction_organization_template_definitions import (
        validate_organization_template_definition,
    )
    c = workflow_case
    if not c.sync.in_transaction():
        c.sync.begin()
    state = await c.reader.get_version(
        actor=c.actor, version_id=c.base.template_version_id)
    body = state.definition.payload()
    body["labels"]["uk"] = {
        key: "uk " + value for key, value in body["labels"]["en"].items()}
    checked = validate_organization_template_definition(body, source_language="en")
    c.sync.execute(update(c.copy_repo.versions).where(
        c.copy_repo.versions.c.id == c.base.template_version_id
    ).values(labels=body["labels"], content_hash=checked.content_hash))
    c.sync.commit()
    result = await run(c)
    assert result.display_text == "uk Starter"
    assert result.origin == "PUBLISHED_LABEL"
    assert result.translation_status is None and result.cache_written is False
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_cache_observed_after_provider_wins_without_overwrite(workflow_case):
    c = workflow_case
    await seed(c, status=Status.VERIFIED, text="Reviewed winner")
    real_get = c.translation_repo.get_exact
    calls = 0

    async def older_first_observation(**values):
        nonlocal calls
        calls += 1
        # Simulate an older initial observation; later reads use real local SQL.
        # This is not an independent PostgreSQL concurrency test.
        if calls == 1:
            return None
        return await real_get(**values)
    c.translation_repo.get_exact = older_first_observation
    before, commits = cache_rows(c), c.session.commits
    result = await run(c)
    assert result.display_text == "Reviewed winner"
    assert result.translation_status is Status.VERIFIED
    assert result.cache_written is False and result.origin == "CACHE"
    assert cache_rows(c) == before and c.session.commits == commits
    c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["language", "membership", "grant", "suite", "core"])
async def test_context_change_during_provider_prevents_persistence(workflow_case, failure):
    c = workflow_case
    async def changed(**values):
        if failure == "language":
            c.language = "it"
        elif failure == "membership":
            c.membership = False
        elif failure == "grant":
            c.grant.status = "revoked"
        elif failure == "suite":
            c.suite.require_enabled.side_effect = SuiteDisabledError()
        else:
            c.module.require_enabled.side_effect = ModuleDisabledError()
        return "Переклад"
    c.provider.translate.side_effect = changed
    expected = (
        TemplateTranslationLanguageChanged if failure == "language"
        else ConstructionPermissionDeniedError if failure in ("membership", "grant")
        else SuiteDisabledError if failure == "suite" else ModuleDisabledError)
    before = domain_snapshot(c)
    with pytest.raises(expected):
        await run(c)
    assert cache_rows(c) == () and domain_snapshot(c) == before


@pytest.mark.asyncio
async def test_source_version_change_is_not_saved_under_old_key(workflow_case):
    c = workflow_case
    original = c.source_service.read_for_actor
    calls = 0

    async def changed_source(**values):
        nonlocal calls
        calls += 1
        context = await original(**values)
        if calls > 1:
            return replace(context, source=replace(
                context.source, source_version=context.source.source_version + 1))
        return context
    c.source_service.read_for_actor = changed_source
    with pytest.raises(TemplateTranslationContextChanged):
        await run(c)
    assert cache_rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["insert", "commit", "cancel", "late_language", "late_membership"])
async def test_real_cache_insert_rolls_back_on_later_failure(workflow_case, failure):
    c = workflow_case
    original = c.translation_repo.insert_automatic

    async def after_insert(**values):
        result = await original(**values)
        if failure == "insert":
            raise RuntimeError("private failure after local SQL")
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "late_language":
            c.language = "it"
        if failure == "late_membership":
            c.membership = False
        return result
    c.translation_repo.insert_automatic = after_insert
    if failure == "commit":
        c.session.fail_commit = True
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else TemplateTranslationLanguageChanged if failure == "late_language"
        else ConstructionPermissionDeniedError if failure == "late_membership"
        else Operation)
    before, commits = domain_snapshot(c), c.session.commits
    with pytest.raises(expected):
        await run(c)
    assert cache_rows(c) == ()
    assert domain_snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_provider_failure_is_not_cached(workflow_case):
    c = workflow_case
    c.provider.translate.side_effect = RuntimeError("private provider details")
    with pytest.raises(ContentTranslationUnavailable) as captured:
        await run(c)
    assert "private provider details" not in str(captured.value)
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_membership_revoked_while_waiting_for_barrier_is_rechecked(workflow_case):
    c = workflow_case
    original = c.serialization.lock_member.side_effect

    async def after_wait(**values):
        await original(**values)
        c.membership = False
    c.serialization.lock_member.side_effect = after_wait
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    assert cache_rows(c) == ()


def test_factory_reuses_platform_provider_interface_and_sessions(workflow_case):
    c = workflow_case
    workflow = build_construction_template_translation_workflow(
        session=c.session, platform_session=c.session,
        provider=c.provider, now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.sources.session is c.session
    assert workflow.sources.platform_session is c.session
    assert workflow.cache.session is c.session
    assert workflow.translator.provider is c.provider
    assert workflow.suite.repository.session is c.session
    assert workflow.module.repository.session is c.session
