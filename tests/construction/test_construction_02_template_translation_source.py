"""Canonical source integration over local template copies only."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_content_translation import ContentFieldKind
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateNotFoundError,
)
from services.construction.construction_template_translation_source import (
    ConstructionTemplateTranslationSourceService as Service,
    TemplateTranslationSourceError as Operation,
    TemplateTranslationSourceValidationError as Validation,
    TemplateTranslationSourceDenied as Denied,
    TemplateTranslationLanguageError as LanguageError,
    TemplateTranslationLanguageChanged as LanguageChanged,
    build_construction_template_translation_source_service,
)
from test_construction_02_organization_template_read import (
    case, onboarding, catalog_case, copy_case, read_case,
    create_copy, snapshot,
)


@pytest.fixture
async def source_case(read_case):
    c = read_case
    c.base = await create_copy(c)
    c.actor = ApiActorContext(
        c.user, c.tenant, None, ("client",), "untrusted-claim", None, "active")
    c.language = "uk"
    c.language_active = True
    c.identity_calls = 0
    c.deny_at = None
    c.change_at = None

    async def fresh(*, user_id, tenant_id):
        c.identity_calls += 1
        if (
            user_id != c.user or tenant_id != c.tenant or not c.membership
            or c.deny_at is not None and c.identity_calls >= c.deny_at
        ):
            raise ApiIdentityAccessError("Private canonical identity state")
        language = (
            "it" if c.change_at is not None and c.identity_calls >= c.change_at
            else c.language)
        return replace(c.actor, language_code=language)

    c.identity = NS(
        session=c.session, require_actor=AsyncMock(side_effect=fresh))
    c.languages = NS(
        session=c.session,
        is_active_language=AsyncMock(
            side_effect=lambda code: (
                c.language_active and code in ("en", "uk", "it", "fi"))))
    c.source_service = Service(
        session=c.session,
        platform_session=c.session,
        template_service=c.reader,
        identity=c.identity,
        language_repository=c.languages)
    yield c


async def read(c, *, field_name="title", version_id=None, actor=None):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.source_service.read_for_actor(
        actor=c.actor if actor is None else actor,
        version_id=c.base.template_version_id if version_id is None else version_id,
        field_name=field_name)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("field_name", ["title", "description"])
async def test_exact_source_uses_profile_target_and_stored_original_language(
    source_case, role, field_name,
):
    c = source_case
    c.grant.role = role
    before, commits = snapshot(c), c.session.commits
    result = await read(c, field_name=field_name)
    assert result.user_id == c.user and result.tenant_id == c.tenant
    assert result.version_id == c.base.template_version_id
    assert result.template_id == c.base.template_id
    assert result.target_language == "uk"
    assert result.field_name == field_name
    assert result.source.source_language == "en"
    assert result.source.entity_id == c.base.template_version_id
    assert result.source.entity_type == "construction.measurement_template_version"
    assert result.source.field_name == field_name
    assert result.source.field_kind is ContentFieldKind.TEXT
    assert result.source.source_version == 1
    assert result.source.original_text
    assert result.supplied_text is None
    assert c.identity.require_actor.await_count == 2
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_existing_source_language_text_is_returned_as_supplied_content(source_case):
    c = source_case
    c.language = "en"
    result = await read(c)
    assert result.target_language == "en"
    assert result.supplied_text == result.source.original_text
    assert not hasattr(result, "reviewed_by")
    assert not hasattr(result, "translation_status")


@pytest.mark.asyncio
async def test_profile_change_is_observed_on_next_read_without_changing_source(source_case):
    c = source_case
    first = await read(c)
    c.language = "it"
    second = await read(c)
    assert first.target_language == "uk" and second.target_language == "it"
    assert first.source == second.source
    assert first.content_hash == second.content_hash


@pytest.mark.asyncio
async def test_language_change_during_read_requires_new_context(source_case):
    c = source_case
    c.change_at = 2
    before = snapshot(c)
    with pytest.raises(LanguageChanged):
        await read(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_inactive_profile_language_never_falls_back_to_organization(source_case):
    c = source_case
    c.language_active = False
    before = snapshot(c)
    with pytest.raises(LanguageError):
        await read(c)
    assert c.identity.require_actor.await_count == 1
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_identity_is_rechecked_after_canonical_read(source_case):
    c = source_case
    c.deny_at = 2
    before = snapshot(c)
    with pytest.raises(Denied):
        await read(c)
    assert c.identity.require_actor.await_count == 2
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "grant", "scope"])
async def test_existing_settings_authority_is_not_expanded(source_case, gate):
    c = source_case
    if gate == "membership":
        c.membership = False
    elif gate == "grant":
        c.grant.status = "revoked"
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_grant_revoked_after_template_read_is_rechecked(source_case):
    c = source_case
    original = c.reader.get_version

    async def revoke_after_read(**values):
        result = await original(**values)
        c.grant.status = "revoked"
        return result
    c.reader.get_version = revoke_after_read
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_missing_or_foreign_version_is_safe_not_found(source_case):
    c = source_case
    before = snapshot(c)
    with pytest.raises(ConstructionOrganizationTemplateNotFoundError):
        await read(c, version_id=uuid4())
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["address_raw", "phone", "legal_name", "formula", "labels.title"])
async def test_only_declared_text_fields_are_read(source_case, field_name):
    c = source_case
    before = snapshot(c)
    with pytest.raises(Validation):
        await read(c, field_name=field_name)
    c.identity.require_actor.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_identity_cannot_be_used_for_source_projection(source_case):
    c = source_case
    actor = replace(c.actor, tenant_id=c.foreign)
    before = snapshot(c)
    with pytest.raises(Denied):
        await read(c, actor=actor)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["tenant", "version", "text"])
async def test_inconsistent_canonical_result_is_rejected(source_case, corruption):
    c = source_case
    if not c.sync.in_transaction():
        c.sync.begin()
    original = await c.reader.get_version(
        actor=c.actor, version_id=c.base.template_version_id)
    changes = (
        {"tenant_id": c.foreign} if corruption == "tenant"
        else {"version_id": uuid4()} if corruption == "version"
        else {"title": "Injected different title"})
    c.reader.get_version = AsyncMock(return_value=replace(original, **changes))
    before = snapshot(c)
    with pytest.raises(Operation):
        await read(c)
    assert snapshot(c) == before


def test_default_factory_binds_sessions_without_opening_connections(source_case):
    c = source_case
    service = build_construction_template_translation_source_service(
        session=c.session,
        platform_session=c.session,
        now_provider=lambda: c.now)
    assert service.session is c.session
    assert service.platform_session is c.session
    assert service.templates.session is c.session
    assert service.identity.session is c.session
    assert service.languages.session is c.session
