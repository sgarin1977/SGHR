"""Local SQLite cache and real scoped policy; no runtime network or database."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.repositories.construction.construction_project_translation import (
    ConstructionProjectTranslationRepository,
)
from services.construction.construction_content_translation import (
    ContentTranslationStatus as Status, ContentTranslationUnavailable,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_project_read import ConstructionProjectReadService
from services.construction.construction_projects import ConstructionProjectNotFoundError
from services.construction.construction_project_translation import (
    ConstructionProjectTranslationWorkflow as Workflow,
    ProjectTranslationContextChanged,
    ProjectTranslationOperationError,
    ProjectTranslationValidationError,
)
from test_construction_02_template_translation_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, cache_rows, domain_snapshot,
)


@pytest.fixture
async def project_case(workflow_case):
    c = workflow_case
    c.project = NS(
        id=uuid4(), tenant_id=c.tenant, deleted_at=None, row_version=1,
        name="New home", comment="Call tomorrow", address_raw="Original address")
    async def get_project(*, tenant_id, project_id):
        if tenant_id == c.project.tenant_id and project_id == c.project.id:
            return c.project
        return None
    c.project_repository = NS(
        session=c.session, get_active_project=AsyncMock(side_effect=get_project),
        get_active_project_for_update=AsyncMock(side_effect=get_project))
    class LocalRepository(ConstructionProjectTranslationRepository):
        def _insert(self):
            return sqlite_insert(self.table)
    c.project_cache = LocalRepository(c.session)
    c.project_cache.table = c.translation_table
    c.provider.detect_language = AsyncMock(return_value="en")
    c.project_workflow = Workflow(
        session=c.session, platform_session=c.session,
        project_service=ConstructionProjectReadService(repository=c.project_repository),
        access_repository=c.access, identity=c.identity,
        language_repository=c.languages, serialization=c.serialization,
        cache_repository=c.project_cache, translator=c.translator,
        suite_access=c.suite, module_access=c.module,
        now_provider=lambda: c.now)
    yield c


async def run(c, *, field_name="name", project_id=None):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.project_workflow.read_for_actor(
        actor=c.actor, project_id=c.project.id if project_id is None else project_id,
        field_name=field_name)


@pytest.mark.asyncio
async def test_translate_once_reuse_cache_and_preserve_original(project_case):
    c = project_case
    before, original, commits = domain_snapshot(c), vars(c.project).copy(), c.session.commits
    async def translate(**values):
        assert c.serialization.lock_member.await_count == 0
        assert values == {
            "text": "New home", "source_language": "en", "target_language": "uk"}
        return "Новий дім"
    c.provider.translate.side_effect = translate
    first = await run(c)
    assert first.original_text == "New home" and first.display_text == "Новий дім"
    assert first.target_language == "uk" and first.source_language == "en"
    assert first.translation_status is Status.AUTO_TRANSLATED
    assert first.cache_written and first.cache_row_version == 1
    assert c.session.commits == commits + 1
    assert vars(c.project) == original and domain_snapshot(c) == before
    c.project_repository.get_active_project_for_update.assert_awaited_once()
    rows = cache_rows(c)
    second = await run(c)
    assert second.display_text == first.display_text and not second.cache_written
    assert cache_rows(c) == rows and c.session.commits == commits + 1
    c.provider.translate.assert_awaited_once()
    c.provider.detect_language.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.VERIFIED, Status.STALE])
async def test_verified_is_reused_and_stale_is_replaced(project_case, status):
    c = project_case
    await run(c)
    old, = cache_rows(c)
    c.sync.execute(update(c.translation_table).where(
        c.translation_table.c.id == old["id"]
    ).values(
        translation_status=status.value,
        reviewed_by=c.user if status is Status.VERIFIED else None))
    c.sync.commit()
    c.provider.translate.reset_mock()
    c.provider.detect_language.reset_mock()
    result = await run(c)
    saved, = cache_rows(c)
    assert saved["id"] == old["id"]
    c.provider.detect_language.assert_not_awaited()
    if status is Status.VERIFIED:
        assert result.translation_status is Status.VERIFIED
        assert result.reviewed_by == c.user and not result.cache_written
        c.provider.translate.assert_not_awaited()
    else:
        assert result.translation_status is Status.AUTO_TRANSLATED
        assert result.reviewed_by is None and result.cache_written
        assert saved["row_version"] == 2
        c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_project_version_never_reuses_old_translation(project_case):
    c = project_case
    await run(c)
    old, = cache_rows(c)
    c.project.row_version += 1
    c.project.name = "Changed name"
    c.provider.translate.return_value = "Інша назва"
    result = await run(c)
    assert result.source_version == 2 and result.original_text == "Changed name"
    assert result.display_text == "Інша назва"
    rows = cache_rows(c)
    assert len(rows) == 2 and old in rows
    assert c.provider.translate.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("comment", [None, "", "  "])
async def test_empty_comment_is_preserved_without_detection_or_cache(project_case, comment):
    c = project_case
    c.project.comment = comment
    commits = c.session.commits
    result = await run(c, field_name="comment")
    assert result.original_text == result.display_text == comment
    assert result.source_language is None and result.translation_status is None
    assert not result.cache_written and cache_rows(c) == ()
    assert c.session.commits == commits
    c.provider.detect_language.assert_not_awaited()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_language_preserves_original_without_translation(project_case):
    c = project_case
    c.language = "en"
    result = await run(c)
    assert result.display_text == result.original_text == "New home"
    assert result.translation_status is None and not result.cache_written
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "grant", "profile", "version", "text"])
async def test_changes_during_provider_prevent_cache_commit(project_case, failure):
    c = project_case
    commits = c.session.commits
    async def translate(**values):
        if failure == "membership":
            c.membership = False
        elif failure == "grant":
            c.grant.status = "revoked"
        elif failure == "profile":
            c.language = "it"
        elif failure == "version":
            c.project.row_version += 1
        else:
            c.project.name = "Changed without version"
        return "Переклад"
    c.provider.translate.side_effect = translate
    with pytest.raises((
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        ProjectTranslationContextChanged,
    )):
        await run(c)
    assert cache_rows(c) == () and c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["detect", "translate", "commit", "cancel"])
async def test_failure_or_cancellation_rolls_back_cache(project_case, failure):
    c = project_case
    if failure == "detect":
        c.provider.detect_language.side_effect = RuntimeError("private provider failure")
    elif failure == "translate":
        c.provider.translate.side_effect = RuntimeError("private provider failure")
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.provider.translate.side_effect = asyncio.CancelledError()
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else ProjectTranslationOperationError if failure == "commit"
        else ContentTranslationUnavailable)
    with pytest.raises(expected):
        await run(c)
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_project_manager_uses_project_scope_not_settings_permission(project_case):
    c = project_case
    c.grant.role = "PROJECT_MANAGER"
    c.grant.scope_type = "project"
    c.grant.scope_id = c.project.id
    assert (await run(c)).display_text == "Переклад"
    c.grant.scope_id = uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await run(c)
    assert c.provider.translate.await_count == 1


@pytest.mark.asyncio
async def test_missing_project_is_safe_and_never_reaches_provider(project_case):
    c = project_case
    with pytest.raises(ConstructionProjectNotFoundError):
        await run(c, project_id=uuid4())
    c.provider.detect_language.assert_not_awaited()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["address_raw", "client_name", "phone", "currency"])
async def test_non_text_fields_are_not_accepted(project_case, field):
    c = project_case
    with pytest.raises(ProjectTranslationValidationError):
        await run(c, field_name=field)
    c.provider.detect_language.assert_not_awaited()
    c.provider.translate.assert_not_awaited()
