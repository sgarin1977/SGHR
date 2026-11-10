"""Local canonical readers and progress; PostgreSQL snapshot setup is a SQL contract."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Boolean, Column, String, Table, Uuid, select, update

from database.repositories.platform_localization import PlatformLocalizationRepository
from database.repositories.platform_organization_currency import PlatformOrganizationCurrencyRepository
from services.api_identity import ApiActorContext
from services.construction.construction_language_context import ConstructionLanguageContextService
from services.construction.construction_currency_settings import ConstructionCurrencySettingsService
from services.construction.construction_onboarding import (
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingStepNotReady as NotReady,
    ConstructionOnboardingOperationError as OperationError,
    ConstructionOnboardingStateConflictError as StateConflict,
    build_construction_onboarding_service,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
import services.construction.construction_onboarding_localization as localization_module
from services.construction.construction_onboarding_localization import (
    ConstructionOnboardingLocalizationVerifier as Verifier,
    platform_localization_read_context,
)
from test_construction_02_onboarding_company_verification import company
from test_construction_02_onboarding_pricing_verification import canonical
from test_construction_02_onboarding_progress import onboarding, begin, start, change, snapshot
from test_construction_02_tax_profiles import case
from test_construction_02_organization_region import region


@pytest.fixture
def localized(company, region):
    c, p = company, region
    p.sync.rollback()
    metadata = p.tenants.metadata
    p.languages = Table("languages", metadata,
        Column("code", String, primary_key=True), Column("name", String),
        Column("native_name", String), Column("is_active", Boolean))
    p.tenant_languages = Table("tenant_languages", metadata,
        Column("tenant_id", Uuid, primary_key=True),
        Column("language_code", String, primary_key=True), Column("is_active", Boolean))
    p.languages.create(p.sync.get_bind())
    p.tenant_languages.create(p.sync.get_bind())
    old_tenant, old_foreign = p.tenant, p.foreign
    p.sync.execute(update(p.tenants).where(p.tenants.c.id == old_tenant).values(
        id=c.tenant, default_language="uk", default_currency="UAH", row_version=4,
        metadata={"keep": True, "regional_settings": {"country_code": "UA", "timezone": "Europe/Kyiv"}}))
    p.sync.execute(update(p.tenants).where(p.tenants.c.id == old_foreign).values(id=c.foreign))
    p.tenant, p.foreign, p.user = c.tenant, c.foreign, c.user
    p.grant.scope_id = c.tenant
    p.actor = ApiActorContext(c.user, c.tenant, None, ("client",), "en", None, "active")
    c.actor = p.actor
    p.sync.execute(p.languages.insert(), [
        {"code": "uk", "name": "Ukrainian", "native_name": None, "is_active": True},
        {"code": "en", "name": "English", "native_name": None, "is_active": True},
        {"code": "fr", "name": "French", "native_name": None, "is_active": False},
    ])
    p.sync.execute(p.tenant_languages.insert().values(
        tenant_id=c.tenant, language_code="uk", is_active=True))
    p.sync.commit()
    p.commands = []
    original_execute = p.session.execute

    class Result:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            rows = self.value.mappings()
            def adapt(row):
                if row is None:
                    return None
                return {key: value.replace(tzinfo=timezone.utc)
                        if isinstance(value, datetime) and value.tzinfo is None else value
                        for key, value in row.items()}
            return NS(one_or_none=lambda: adapt(rows.one_or_none()),
                      all=lambda: [adapt(row) for row in rows.all()])

        def scalars(self):
            return self.value.scalars()

    async def execute(statement):
        if str(statement).startswith("SET TRANSACTION"):
            p.commands.append(str(statement))
            p.sync.connection().exec_driver_sql("PRAGMA query_only = ON")
            return NS()
        return Result(await original_execute(statement))

    async def begin_platform():
        p.sync.begin()

    p.session.execute = execute
    p.session.begin = begin_platform
    # Explicit test adapter: execute real readers in local SQLite, capture the
    # production PostgreSQL SET statement, and enforce SQLite query_only.
    p.session.get_bind = lambda: NS(dialect=NS(name="postgresql"))

    @asynccontextmanager
    async def platform_session():
        if p.sync.in_transaction():
            p.sync.rollback()
        try:
            yield p.session
        finally:
            if p.sync.in_transaction():
                await p.session.rollback()
            connection = p.sync.get_bind().raw_connection()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("PRAGMA query_only = OFF")
                finally:
                    cursor.close()
            finally:
                connection.close()

    identity = p.authorizer.identity
    language = ConstructionLanguageContextService(
        session=p.session, repository=PlatformLocalizationRepository(p.session), identity=identity)
    currency = ConstructionCurrencySettingsService(
        session=p.session, repository=PlatformOrganizationCurrencyRepository(p.session),
        identity=identity, authorizer=p.authorizer, events=p.events)
    c.readers = (language, currency, p.service)
    for reader in c.readers:
        reader.read_for_actor = AsyncMock(wraps=reader.read_for_actor)
    c.localization = Verifier(
        session=c.session,
        read_context_factory=lambda: platform_localization_read_context(session_context=platform_session),
        readers_builder=lambda session: c.readers)
    c.progress.verifier.localization_verifier = c.localization
    c.platform = p
    yield c


def platform_rows(c):
    p = c.platform
    return tuple(tuple(dict(row) for row in p.sync.execute(
        select(table).order_by(table.c[0])).mappings())
        for table in (p.tenants, p.countries, p.languages, p.tenant_languages, p.audits))


async def open_localization(c):
    state = (await start(c)).progress
    return (await change(c, "move_to_step", state, Step.LOCALIZATION)).progress


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_real_readers_complete_step_without_mutating_platform_or_other_steps(localized, role):
    c, p = localized, localized.platform
    c.grant.role = p.grant.role = role
    state = await open_localization(c)
    before, platform_before = snapshot(c), platform_rows(c)
    commits = c.session.commits
    result = await change(c, "complete_step", state, Step.LOCALIZATION)
    assert result.changed and result.progress.row_version == state.row_version + 1
    assert result.progress.current_step is Step.TAXES
    assert result.progress.states_dict()["LOCALIZATION"] == "COMPLETED"
    assert result.progress.states_dict()["COMPANY"] == "PENDING"
    assert not result.progress.is_complete
    assert len(snapshot(c)[1]) == len(before[1]) + 1 and c.session.commits == commits + 1
    assert platform_rows(c) == platform_before and p.session.commits == 0
    assert p.commands == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"]
    assert p.actor.language_code == "en"
    assert "translation_cache" not in p.tenants.metadata.tables
    for reader in c.readers:
        reader.read_for_actor.assert_awaited_once_with(actor=c.actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "region_absent", "country_inactive", "tenant_language_disabled",
    "default_language_inactive", "profile_language_inactive", "currency_unknown",
])
async def test_missing_configuration_does_not_complete_or_invent_defaults(localized, failure):
    c, p = localized, localized.platform
    if failure == "region_absent":
        p.sync.execute(update(p.tenants).where(p.tenants.c.id == c.tenant).values(metadata={"keep": True}))
    elif failure == "country_inactive":
        p.sync.execute(update(p.countries).where(p.countries.c.code == "UA").values(is_active=False))
    elif failure == "tenant_language_disabled":
        p.sync.execute(update(p.tenant_languages).values(is_active=False))
    elif failure == "default_language_inactive":
        p.sync.execute(update(p.languages).where(p.languages.c.code == "uk").values(is_active=False))
    elif failure == "profile_language_inactive":
        p.actor = replace(p.actor, language_code="fr")
    else:
        p.sync.execute(update(p.tenants).where(p.tenants.c.id == c.tenant).values(default_currency="ZZZ"))
    p.sync.commit()
    state = await open_localization(c)
    before, platform_before = snapshot(c), platform_rows(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before and platform_rows(c) == platform_before
    assert p.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["platform_membership", "platform_grant", "late_construction_grant"])
async def test_fresh_platform_and_progress_authority_remain_required(localized, failure):
    c, p = localized, localized.platform
    state = await open_localization(c)
    before, platform_before = snapshot(c), platform_rows(c)
    if failure == "platform_membership":
        p.membership = False
    elif failure == "platform_grant":
        p.grant.status = "revoked"
    else:
        original = c.readers[2].read_for_actor

        async def revoked(**values):
            result = await original(**values)
            c.grant.status = "revoked"
            return result

        c.readers[2].read_for_actor = revoked
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before and platform_rows(c) == platform_before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "user", "version", "version_type", "time"])
async def test_inconsistent_canonical_projection_is_not_accepted(localized, failure):
    c = localized
    state = await open_localization(c)
    index = 0 if failure in {"tenant", "user"} else 1
    reader = c.readers[index]
    original = reader.read_for_actor

    async def invalid(**values):
        result = await original(**values)
        changes = {
            "tenant": {"tenant_id": c.foreign}, "user": {"user_id": uuid4()},
            "version": {"row_version": 5}, "version_type": {"row_version": True},
            "time": {"updated_at": c.platform.now.replace(tzinfo=None)},
        }[failure]
        return replace(result, **changes)

    reader.read_for_actor = invalid
    before, platform_before = snapshot(c), platform_rows(c)
    with pytest.raises(StateConflict if failure == "version" else OperationError):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before and platform_rows(c) == platform_before


@pytest.mark.asyncio
async def test_invalid_unit_catalog_does_not_complete_step(localized, monkeypatch):
    c = localized
    state = await open_localization(c)
    before, platform_before = snapshot(c), platform_rows(c)
    monkeypatch.setattr(localization_module, "list_construction_units", lambda: ())
    with pytest.raises(OperationError):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before and platform_rows(c) == platform_before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_progress_and_mandatory_audit_still_roll_back_together(localized, failure):
    c = localized
    state = await open_localization(c)
    before, platform_before = snapshot(c), platform_rows(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        original = c.events.create_event.side_effect

        async def cancel(**values):
            await original(**values)
            raise asyncio.CancelledError()

        c.events.create_event.side_effect = cancel
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else OperationError):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before and platform_rows(c) == platform_before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["commit", "write"])
async def test_read_boundary_rejects_transaction_change_or_write(localized, failure):
    c, p = localized, localized.platform
    state = await open_localization(c)
    before, platform_before = snapshot(c), platform_rows(c)
    reader = c.readers[1]
    original = reader.read_for_actor

    async def invalid(**values):
        result = await original(**values)
        if failure == "commit":
            await p.session.commit()
        else:
            p.sync.execute(update(p.tenants).where(p.tenants.c.id == c.tenant).values(default_currency="USD"))
        return result

    reader.read_for_actor = invalid
    with pytest.raises(OperationError):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before and platform_rows(c) == platform_before


@pytest.mark.asyncio
async def test_current_noop_rechecks_data_without_new_version_or_audit(localized):
    c, p = localized, localized.platform
    state = await open_localization(c)
    state = (await change(c, "complete_step", state, Step.LOCALIZATION)).progress
    before = snapshot(c)
    noop = await change(c, "complete_step", state, Step.LOCALIZATION)
    assert not noop.changed and noop.progress == state and snapshot(c) == before
    p.sync.execute(update(p.tenants).where(p.tenants.c.id == c.tenant).values(metadata={"keep": True}))
    p.sync.commit()
    before = snapshot(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before


def test_factory_attaches_lazy_localization_verifier(localized, monkeypatch):
    c = localized
    calls = []

    def forbidden_context():
        calls.append(True)
        raise AssertionError("Factory must not open Platform connections.")

    monkeypatch.setattr(localization_module, "platform_localization_read_context", forbidden_context)
    import services.construction.construction_pricing_profiles as pricing
    monkeypatch.setattr(pricing, "build_construction_pricing_profile_service", lambda **values: c.price_service)
    service = build_construction_onboarding_service(session=c.session, now_provider=lambda: c.now)
    assert isinstance(service.verifier.localization_verifier, Verifier)
    assert service.verifier.localization_verifier.session is c.session
    assert service.verifier.localization_verifier.read_context_factory is forbidden_context
    assert calls == []


@pytest.mark.asyncio
async def test_stale_progress_version_is_rejected_before_platform_read(localized):
    c = localized
    state = await open_localization(c)
    before = snapshot(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await change(c, "complete_step", replace(state, row_version=1), Step.LOCALIZATION)
    assert snapshot(c) == before and c.platform.commands == []
    for reader in c.readers:
        reader.read_for_actor.assert_not_awaited()
