"""Local SQLite batch behavior; no external provider or native race claims."""
import asyncio
from dataclasses import replace
from uuid import UUID

import pytest

from services.construction.construction_content_translation import (
    ContentTranslationStatus as Status,
    ContentTranslationUnavailable,
)
from services.construction.construction_template_translation_batch import (
    ConstructionTemplateTranslationBatchWorkflow as Batch,
    MAX_BATCH_FIELDS,
    build_construction_template_translation_batch_workflow,
    build_construction_starter_translation_batch_workflow,
)
from services.construction.construction_template_translation_source import (
    template_label_field,
    TemplateTranslationSourceValidationError as Validation,
    TemplateTranslationLanguageChanged,
)
from services.construction.construction_template_translation_workflow import (
    ConstructionTemplateTranslationWorkflow,
    TemplateTranslationWorkflowError as Operation,
    TemplateTranslationContextChanged,
    TemplateTranslationCacheConflict,
)
from services.construction.construction_starter_translation import (
    ConstructionStarterTranslationSourceService,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateNotFoundError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_template_translation_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, cache_rows, seed, domain_snapshot,
)

FIELDS = (
    "description", template_label_field("yes"),
    "title", template_label_field("area"),
)
ORIGINALS = (
    "Structure without prices", "Yes", "Starter", "Measured area",
)


@pytest.fixture
def batch_case(workflow_case):
    c = workflow_case
    c.batch = Batch(workflow=c.translation_workflow)
    return c


async def run(c, fields=FIELDS, *, version_id=None):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.batch.read_for_actor(
        actor=c.actor,
        version_id=c.base.template_version_id if version_id is None else version_id,
        field_names=fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_one_commit_and_ordered_results_then_cache_only(batch_case, role):
    c = batch_case
    c.grant.role = role
    before, commits = domain_snapshot(c), c.session.commits

    async def translate(**values):
        assert c.serialization.lock_member.await_count == 0
        assert values["source_language"] == "en"
        assert values["target_language"] == "uk"
        return "uk " + values["text"]
    c.provider.translate.side_effect = translate

    insert = c.translation_repo.insert_automatic
    async def all_prepared_before_insert(**values):
        assert c.provider.translate.await_count == len(FIELDS)
        return await insert(**values)
    c.translation_repo.insert_automatic = all_prepared_before_insert

    results = await run(c)
    assert tuple(item.field_name for item in results) == FIELDS
    assert tuple(item.original_text for item in results) == ORIGINALS
    assert tuple(item.display_text for item in results) == tuple(
        "uk " + text for text in ORIGINALS)
    assert all(item.origin == "GENERATED" and item.cache_written for item in results)
    assert all(item.target_language == "uk" for item in results)
    assert all(item.translation_status is Status.AUTO_TRANSLATED for item in results)
    assert len(cache_rows(c)) == len(FIELDS)
    assert c.session.commits == commits + 1
    assert domain_snapshot(c) == before
    c.serialization.lock_member.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)

    saved = cache_rows(c)
    again = await run(c)
    assert all(item.origin == "CACHE" and not item.cache_written for item in again)
    assert tuple(item.display_text for item in again) == tuple(
        item.display_text for item in results)
    assert cache_rows(c) == saved and domain_snapshot(c) == before
    assert c.session.commits == commits + 1
    assert c.provider.translate.await_count == len(FIELDS)
    assert c.serialization.lock_member.await_count == 1


@pytest.mark.asyncio
async def test_original_language_needs_no_provider_lock_or_cache_commit(batch_case):
    c = batch_case
    c.language = "en"
    before, commits = domain_snapshot(c), c.session.commits
    result = await run(c)
    assert tuple(item.display_text for item in result) == ORIGINALS
    assert all(item.origin == "ORIGINAL" and not item.cache_written for item in result)
    assert all(item.translation_status is None for item in result)
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits
    c.provider.translate.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_field_is_reused_while_other_fields_are_generated(batch_case):
    c = batch_case
    await seed(c, status=Status.VERIFIED, text="Reviewed title")
    commits = c.session.commits
    result = await run(c)
    title = result[FIELDS.index("title")]
    assert title.display_text == "Reviewed title"
    assert title.translation_status is Status.VERIFIED
    assert title.origin == "CACHE" and not title.cache_written
    assert c.provider.translate.await_count == len(FIELDS) - 1
    assert all(
        call.kwargs["text"] != "Starter"
        for call in c.provider.translate.await_args_list)
    assert len(cache_rows(c)) == len(FIELDS)
    assert c.session.commits == commits + 1


@pytest.mark.asyncio
async def test_stale_field_is_replaced_in_same_commit_as_new_fields(batch_case):
    c = batch_case
    old = await seed(c, status=Status.STALE)
    commits = c.session.commits
    result = await run(c, ("title", "description"))
    assert result[0].cache_row_version == 2
    assert result[1].cache_row_version == 1
    assert all(item.cache_written for item in result)
    title_row, = (
        row for row in cache_rows(c) if row["field_name"] == "title")
    assert title_row["id"] == old["id"]
    assert title_row["created_at"] == old["created_at"]
    assert c.session.commits == commits + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["language", "membership", "grant", "suite", "core", "provider"])
async def test_changed_context_or_provider_failure_prevents_all_writes(
    batch_case, failure,
):
    c = batch_case
    async def translate(**values):
        if failure == "language":
            c.language = "it"
        elif failure == "membership":
            c.membership = False
        elif failure == "grant":
            c.grant.status = "revoked"
        elif failure == "suite":
            c.suite.require_enabled.side_effect = SuiteDisabledError()
        elif failure == "core":
            c.module.require_enabled.side_effect = ModuleDisabledError()
        else:
            raise RuntimeError("private provider information")
        return "Translated"
    c.provider.translate.side_effect = translate
    expected = (
        TemplateTranslationLanguageChanged if failure == "language"
        else ConstructionPermissionDeniedError if failure in ("membership", "grant")
        else SuiteDisabledError if failure == "suite"
        else ModuleDisabledError if failure == "core"
        else ContentTranslationUnavailable)
    before, commits = domain_snapshot(c), c.session.commits
    with pytest.raises(expected):
        await run(c)
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["second_insert", "commit", "cancel", "late_language", "late_membership"])
async def test_two_real_local_inserts_roll_back_together(batch_case, failure):
    c = batch_case
    insert = c.translation_repo.insert_automatic
    inserted = []
    async def after_insert(**values):
        saved = await insert(**values)
        inserted.append(saved)
        if len(inserted) == 2:
            assert len(cache_rows(c)) == 2
            if failure == "second_insert":
                raise RuntimeError("private failure after two inserts")
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "late_language":
                c.language = "it"
            if failure == "late_membership":
                c.membership = False
        return saved
    c.translation_repo.insert_automatic = after_insert
    c.session.fail_commit = failure == "commit"
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else TemplateTranslationLanguageChanged if failure == "late_language"
        else ConstructionPermissionDeniedError if failure == "late_membership"
        else Operation)
    before, commits = domain_snapshot(c), c.session.commits
    with pytest.raises(expected):
        await run(c, ("title", "description"))
    assert len(inserted) == 2
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits


@pytest.mark.asyncio
async def test_unresolved_second_cache_conflict_rolls_back_first_insert(batch_case):
    c = batch_case
    insert = c.translation_repo.insert_automatic
    calls = 0
    async def conflicting(**values):
        nonlocal calls
        calls += 1
        if calls == 2:
            return None
        return await insert(**values)
    c.translation_repo.insert_automatic = conflicting
    with pytest.raises(TemplateTranslationCacheConflict):
        await run(c, ("title", "description"))
    assert calls == 2 and cache_rows(c) == ()


@pytest.mark.asyncio
async def test_cache_lock_order_is_independent_of_requested_order(batch_case):
    c = batch_case
    original = c.translation_repo.get_exact
    locked = []
    async def observe(**values):
        if values.get("for_update"):
            locked.append(values["key"].field_name)
        return await original(**values)
    c.translation_repo.get_exact = observe
    await run(c, tuple(reversed(FIELDS)))
    assert locked == sorted(locked)
    assert set(locked) == set(FIELDS)


@pytest.mark.asyncio
async def test_source_context_change_during_provider_is_not_cached(batch_case):
    c = batch_case
    original = c.source_service.read_for_actor
    changed = False
    async def source(**values):
        context = await original(**values)
        return replace(context, content_hash="0" * 64) if changed else context
    async def translate(**values):
        nonlocal changed
        changed = True
        return "Translated"
    c.source_service.read_for_actor = source
    c.provider.translate.side_effect = translate
    with pytest.raises(TemplateTranslationContextChanged):
        await run(c)
    assert cache_rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    (), ["title"], ("title", "title"), ("phone",), (True,),
    ("title", []),
    tuple(template_label_field("key" + str(i)) for i in range(MAX_BATCH_FIELDS + 1)),
])
async def test_invalid_batch_is_rejected_before_identity_or_provider(batch_case, fields):
    c = batch_case
    calls, commits = c.identity_calls, c.session.commits
    with pytest.raises(Validation):
        await run(c, fields)
    assert c.identity_calls == calls and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_label_does_not_trigger_partial_translation(batch_case):
    c = batch_case
    if not c.sync.in_transaction():
        c.sync.begin()
    with pytest.raises(ConstructionOrganizationTemplateNotFoundError):
        await c.batch.read_labels_for_actor(
            actor=c.actor, version_id=c.base.template_version_id,
            label_keys=("yes", "missing"))
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_outer_transaction_is_required(batch_case):
    c = batch_case
    c.sync.rollback()
    with pytest.raises(Operation, match="clean active"):
        await c.batch.read_for_actor(
            actor=c.actor, version_id=c.base.template_version_id,
            field_names=("title",))
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_batch_service_handles_global_starter_without_copy(batch_case):
    c = batch_case
    source = ConstructionStarterTranslationSourceService(
        session=c.session, platform_session=c.session,
        starter_service=c.catalog,
        identity=c.identity, language_repository=c.languages)
    c.batch = Batch(workflow=ConstructionTemplateTranslationWorkflow(
        session=c.session, source_service=source,
        cache_repository=c.translation_repo, translator=c.translator,
        suite_access=c.suite, module_access=c.module))
    before, commits = domain_snapshot(c), c.session.commits
    if not c.sync.in_transaction():
        c.sync.begin()
    results = await c.batch.read_labels_for_actor(
        actor=c.actor, version_id=UUID(int=2001),
        label_keys=("yes", "area"))
    assert tuple(item.original_text for item in results) == ("Yes", "Measured area")
    assert len(cache_rows(c)) == 2
    assert all(
        row["entity_type"] == "construction.starter_measurement_template_version"
        and row["entity_id"] == UUID(int=2001)
        for row in cache_rows(c))
    assert domain_snapshot(c) == before
    assert c.session.commits == commits + 1


def test_factories_reuse_existing_sessions_and_provider(batch_case):
    c = batch_case
    before = c.session.commits
    for factory in (
        build_construction_template_translation_batch_workflow,
        build_construction_starter_translation_batch_workflow,
    ):
        batch = factory(
            session=c.session, platform_session=c.session,
            provider=c.provider, now_provider=lambda: c.now)
        assert batch.session is c.session
        assert batch.workflow.sources.platform_session is c.session
        assert batch.workflow.cache.session is c.session
        assert batch.workflow.translator.provider is c.provider
    assert c.session.commits == before
    c.provider.translate.assert_not_awaited()
