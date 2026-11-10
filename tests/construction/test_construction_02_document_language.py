"""Local-only document language selection; no document/provider operations."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event

from services.api_identity import ApiIdentityAccessError
from services.construction.construction_document_language import (
    ConstructionDocumentLanguageService as Service,
    ConstructionDocumentLanguageValidationError as Validation,
    ConstructionDocumentLanguageAccessError as Denied,
    ConstructionDocumentLanguageConfigurationError as Configuration,
    ConstructionDocumentLanguageOperationError as Operation,
    ConstructionDocumentLanguageContextChanged as Changed,
    build_construction_document_language_service,
)
from test_construction_02_language_context import language_case, snapshot


@pytest.fixture
def document_case(language_case):
    c = language_case
    # Extend only the existing local SQLite bridge's session interface.
    c.db.expire_all = c.sync.expire_all
    c.db.commit = AsyncMock()
    c.db.rollback = AsyncMock()
    c.documents = Service(session=c.db, language_context=c.service)
    return c


async def prepare(c, languages=("en",), *, actor=None):
    return await c.documents.prepare_for_actor(
        actor=c.actor if actor is None else actor,
        document_languages=languages)


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [
    ("ru",), ("pt",), ("en",), ("es",), ("it",), ("ja",),
    ("pt", "en"), ("en", "pt"),
])
async def test_explicit_document_languages_are_independent_and_ordered(
    document_case, languages,
):
    c = document_case
    before = snapshot(c)
    result = await prepare(c, languages)
    assert result.tenant_id == c.tenant and result.user_id == c.user
    assert result.interface_language == "es"
    assert result.organization_default_language == "pt"
    assert result.organization_row_version == 1
    assert result.document_languages == languages
    assert result.bilingual is (len(languages) == 2)
    assert snapshot(c) == before
    c.db.commit.assert_not_awaited()
    c.db.rollback.assert_not_awaited()
    assert c.identity.require_actor.await_count == 4


@pytest.mark.asyncio
async def test_profile_change_does_not_rewrite_selected_document_languages(document_case):
    c = document_case
    first = await prepare(c, ("pt", "en"))
    c.sync.execute(c.users.update().values(language_code="it"))
    c.sync.commit()
    before = snapshot(c)
    second = await prepare(c, ("pt", "en"))
    assert first.interface_language == "es"
    assert second.interface_language == "it"
    assert second.document_languages == first.document_languages == ("pt", "en")
    assert second.organization_default_language == first.organization_default_language
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_selected_language_is_not_limited_to_interface_or_organization_default(
    document_case,
):
    c = document_case
    result = await prepare(c, ("ja", "en"))
    assert result.interface_language == "es"
    assert result.organization_default_language == "pt"
    assert result.document_languages == ("ja", "en")


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [
    None, (), [], ["en"], "en", ("en", "en"),
    ("en", "pt", "es"), (" en",), ("EN",), (True,), ("",),
])
async def test_explicit_valid_selection_is_required_before_identity_reads(
    document_case, languages,
):
    c = document_case
    calls = c.identity.require_actor.await_count
    before = snapshot(c)
    with pytest.raises(Validation):
        await prepare(c, languages)
    assert c.identity.require_actor.await_count == calls
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["fr", "zz"])
async def test_inactive_or_unknown_language_is_not_replaced_by_a_default(
    document_case, code,
):
    c = document_case
    before = snapshot(c)
    with pytest.raises(Validation):
        await prepare(c, (code,))
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["blocked", "membership", "actor"])
async def test_fresh_platform_identity_and_membership_are_required(document_case, failure):
    c = document_case
    actor = c.actor
    if failure == "blocked":
        c.sync.execute(c.users.update().values(status="blocked"))
    elif failure == "membership":
        c.sync.execute(c.members.update().values(is_active=False))
    else:
        actor = replace(c.actor, status="blocked")
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(Denied):
        await prepare(c, actor=actor)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_revocation_between_context_reads_is_rejected(document_case):
    c = document_case
    current = replace(c.actor, language_code="es")
    c.identity.require_actor.side_effect = [
        current, current, ApiIdentityAccessError("private membership state"),
    ]
    before = snapshot(c)
    with pytest.raises(Denied):
        await prepare(c, ("pt", "en"))
    assert snapshot(c) == before
    c.db.commit.assert_not_awaited()
    c.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_change_during_preparation_requires_reload(document_case):
    c = document_case
    first = replace(c.actor, language_code="es")
    second = replace(c.actor, language_code="it")
    c.identity.require_actor.side_effect = [first, first, second, second]
    before = snapshot(c)
    with pytest.raises(Changed) as captured:
        await prepare(c, ("pt", "en"))
    assert captured.value.code == "DOCUMENT_LANGUAGE_CONTEXT_CHANGED"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_catalog_change_between_reads_is_not_silently_accepted(document_case):
    c = document_case
    original = c.repository.list_language_catalog
    calls = 0

    async def changing(**values):
        nonlocal calls
        calls += 1
        rows = await original(**values)
        return [row for row in rows if row["code"] != "it"] if calls == 2 else rows

    c.repository.list_language_catalog = changing
    with pytest.raises(Changed):
        await prepare(c, ("it",))
    assert calls == 2


@pytest.mark.asyncio
async def test_missing_profile_language_is_not_replaced_by_document_language(document_case):
    c = document_case
    c.sync.execute(c.users.update().values(language_code="missing"))
    c.sync.commit()
    with pytest.raises(Configuration):
        await prepare(c, ("en",))


@pytest.mark.asyncio
async def test_reader_failure_is_sanitized_and_does_not_finish_callers_transaction(
    document_case,
):
    c = document_case
    c.repository.list_language_catalog = AsyncMock(
        side_effect=RuntimeError("private database credentials"))
    before = snapshot(c)
    transaction = c.sync.get_transaction()
    with pytest.raises(Operation) as captured:
        await prepare(c)
    assert "private database credentials" not in str(captured.value)
    assert c.sync.get_transaction() is transaction
    assert snapshot(c) == before
    c.db.commit.assert_not_awaited()
    c.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_platform_changes_are_never_flushed(document_case):
    c = document_case
    c.db.dirty = {object()}
    calls = c.identity.require_actor.await_count
    with pytest.raises(Operation):
        await prepare(c)
    assert c.identity.require_actor.await_count == calls


@pytest.mark.asyncio
async def test_cancellation_propagates_without_committing_or_rolling_back(document_case):
    c = document_case
    c.service.read_for_actor = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await prepare(c)
    c.db.commit.assert_not_awaited()
    c.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_preparation_executes_only_reads_and_returns_immutable_plan(document_case):
    c = document_case
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().upper())

    event.listen(c.engine, "before_cursor_execute", observe)
    try:
        result = await prepare(c, ("pt", "en"))
    finally:
        event.remove(c.engine, "before_cursor_execute", observe)

    assert statements and all(statement.startswith("SELECT") for statement in statements)
    with pytest.raises(FrozenInstanceError):
        result.document_languages = ("es",)
    assert result.document_languages == ("pt", "en")


def test_factory_constructs_shared_readers_without_database_calls(document_case):
    c = document_case
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(c.engine, "before_cursor_execute", observe)
    try:
        service = build_construction_document_language_service(platform_session=c.db)
    finally:
        event.remove(c.engine, "before_cursor_execute", observe)

    assert service.session is c.db
    assert service.language_context.session is c.db
    assert service.language_context.repository.session is c.db
    assert service.language_context.identity.session is c.db
    assert statements == []
