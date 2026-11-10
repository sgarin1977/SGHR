"""Local CON-O001 step-4 integration with the actual CON-017 service/repository."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, delete, insert, select
from uuid import uuid4

from database.models import ConstructionPricingProfile
from database.repositories.construction.construction_pricing_profiles import ConstructionPricingProfileRepository
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingPricingVerifier as Verifier,
    ConstructionOnboardingCompletionUnavailable as Unavailable,
    ConstructionOnboardingOperationError as OperationError,
    ConstructionOnboardingStepNotReady as NotReady,
    ConstructionOnboardingStep as Step,
    build_construction_onboarding_service,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
from services.construction.construction_pricing_profiles import ConstructionPricingProfileService, LEVELS
from test_construction_02_onboarding_progress import onboarding, begin, start, change, snapshot
from test_construction_02_pricing_profiles import LocalDecimal
from test_construction_02_tax_profiles import case, LocalUTC


@pytest.fixture
def canonical(onboarding):
    c = onboarding
    c.sync.rollback()
    c.prices = ConstructionPricingProfile.__table__.to_metadata(c.table.metadata)
    for name in ("work_coefficient", "material_markup_percent"):
        c.prices.c[name].type = LocalDecimal()
    for constraint in list(c.prices.constraints):
        if constraint.name in {
            "ck_construction_pricing_profiles_work_coefficient",
            "ck_construction_pricing_profiles_material_markup_percent",
        }:
            c.prices.constraints.remove(constraint)
    c.prices.append_constraint(CheckConstraint(
        "CAST(work_coefficient AS NUMERIC) > 0",
        name="ck_construction_pricing_profiles_work_coefficient"))
    c.prices.append_constraint(CheckConstraint(
        "CAST(material_markup_percent AS NUMERIC) BETWEEN 0 AND 100",
        name="ck_construction_pricing_profiles_material_markup_percent"))
    for name in ("created_at", "updated_at"):
        c.prices.c[name].type = LocalUTC()
    c.prices.create(c.sync.get_bind())
    c.session.get_bind = c.sync.get_bind
    c.price_repository = ConstructionPricingProfileRepository(c.session)
    c.price_repository.table = c.prices
    c.price_service = ConstructionPricingProfileService(
        session=c.session, repository=c.price_repository, access_repository=c.access,
        event_repository=c.events, serialization=c.serialization, now_provider=lambda: c.now)
    c.progress.verifier = Verifier(session=c.session, pricing_service=c.price_service)
    c.sync.begin()
    yield c


def put(c, codes=LEVELS, *, tenant_id=None):
    for code in codes:
        c.sync.execute(insert(c.prices).values(
            id=uuid4(), tenant_id=c.tenant if tenant_id is None else tenant_id, code=code,
            work_coefficient=Decimal("1.12345678901234567890123456789"),
            material_markup_percent=Decimal("0"), row_version=1,
            created_by=c.user, created_at=c.now, updated_at=c.now))
    c.sync.commit()


def everything(c):
    return snapshot(c), tuple(dict(row) for row in c.sync.execute(
        select(c.prices).order_by(c.prices.c.id)).mappings())


async def open_prices(c):
    state = (await start(c)).progress
    return (await change(c, "move_to_step", state, Step.PRICING_PROFILES)).progress


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2, 3])
async def test_completion_uses_saved_three_levels_without_fabricating_prices(canonical, count):
    c = canonical
    put(c, LEVELS[:count])
    state = await open_prices(c)
    before, commits = everything(c), c.session.commits
    if count < 3:
        with pytest.raises(NotReady):
            await change(c, "complete_step", state, Step.PRICING_PROFILES)
        assert everything(c) == before and c.session.commits == commits
    else:
        result = await change(c, "complete_step", state, Step.PRICING_PROFILES)
        assert result.changed and result.progress.row_version == state.row_version + 1
        assert result.progress.current_step is Step.WORK_PRICES
        assert result.progress.states_dict()["PRICING_PROFILES"] == "COMPLETED"
        assert not result.progress.is_complete
        after = everything(c)
        assert after[1] == before[1] and len(after[0][1]) == len(before[0][1]) + 1
        assert c.session.commits == commits + 1
        event = c.events.create_event.await_args.kwargs
        assert event["event_type"] == "construction_onboarding_progress_updated"
        assert event["entity_id"] == c.tenant and event["trace_id"] == "onboarding-test"
        assert event["payload"]["operation"] == "onboarding_complete"


@pytest.mark.asyncio
async def test_foreign_three_levels_do_not_complete_current_organization(canonical):
    c = canonical
    put(c, tenant_id=c.foreign)
    state = await open_prices(c)
    before = everything(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_current_canonical_read_policy_is_used_for_owner_and_admin(canonical, role):
    c = canonical
    c.grant.role = role
    put(c)
    state = await open_prices(c)
    result = await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert result.progress.states_dict()["PRICING_PROFILES"] == "COMPLETED"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "grant"])
async def test_authority_revoked_during_actual_pricing_read_denies_completion(canonical, failure):
    c = canonical
    put(c)
    state = await open_prices(c)
    before = everything(c)
    original = c.price_repository.list_profiles

    async def revoked(**values):
        rows = await original(**values)
        if failure == "membership":
            c.membership = False
        else:
            c.grant.status = "revoked"
        return rows

    c.price_repository.list_profiles = revoked
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_stale_version_is_denied_before_canonical_read(canonical):
    c = canonical
    put(c)
    state = await open_prices(c)
    c.price_service.list_profiles = AsyncMock(wraps=c.price_service.list_profiles)
    before = everything(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await change(c, "complete_step", replace(state, row_version=1), Step.PRICING_PROFILES)
    c.price_service.list_profiles.assert_not_awaited()
    assert everything(c) == before


@pytest.mark.asyncio
async def test_current_noop_rechecks_canonical_profiles_without_second_audit(canonical):
    c = canonical
    put(c)
    state = await open_prices(c)
    state = (await change(c, "complete_step", state, Step.PRICING_PROFILES)).progress
    before = everything(c)
    result = await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert not result.changed and result.progress == state and everything(c) == before
    c.sync.execute(delete(c.prices).where(c.prices.c.code == "PREMIUM"))
    c.sync.commit()
    before = everything(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_completion_failure_rolls_back_progress_and_audit_together(canonical, failure):
    c = canonical
    put(c)
    state = await open_prices(c)
    before = everything(c)
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
        await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [item for item in Step if item is not Step.PRICING_PROFILES])
async def test_unconfigured_steps_are_not_silently_completed(canonical, step):
    c = canonical
    c.price_service.list_profiles = AsyncMock(wraps=c.price_service.list_profiles)
    before = everything(c)
    with pytest.raises(Unavailable):
        await c.progress.verifier.verify_step(actor=c.actor, step=step)
    c.price_service.list_profiles.assert_not_awaited()
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["foreign", "duplicate", "order", "id", "container"])
async def test_malformed_canonical_context_never_confirms_a_step(canonical, malformed):
    c = canonical
    put(c)
    begin(c)
    profiles = await c.price_service.list_profiles(actor=c.actor)
    if malformed == "foreign":
        value = (replace(profiles[0], tenant_id=c.foreign), *profiles[1:])
    elif malformed == "duplicate":
        value = (profiles[0], profiles[0], profiles[2])
    elif malformed == "order":
        value = tuple(reversed(profiles))
    elif malformed == "id":
        value = (profiles[0], replace(profiles[1], id=profiles[0].id), profiles[2])
    else:
        value = list(profiles)
    c.price_service.list_profiles = AsyncMock(return_value=value)
    before = everything(c)
    with pytest.raises(OperationError):
        await c.progress.verifier.verify_step(actor=c.actor, step=Step.PRICING_PROFILES)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_committing_canonical_reader_cannot_escape_progress_transaction(canonical):
    c = canonical
    put(c)
    state = await open_prices(c)
    before = everything(c)
    original = c.price_service.list_profiles

    async def committed(**values):
        profiles = await original(**values)
        await c.session.commit()
        return profiles

    c.price_service.list_profiles = committed
    with pytest.raises(OperationError):
        await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert everything(c) == before


def test_factory_wires_canonical_reader_and_preserves_explicit_verifier(canonical, monkeypatch):
    c = canonical
    import services.construction.construction_pricing_profiles as pricing
    calls = []

    def build(**values):
        assert values == {"session": c.session, "now_provider": c.progress.now_provider}
        calls.append(values)
        return c.price_service

    monkeypatch.setattr(pricing, "build_construction_pricing_profile_service", build)
    built = build_construction_onboarding_service(
        session=c.session, now_provider=c.progress.now_provider)
    from services.construction.construction_onboarding import ConstructionOnboardingSettingsVerifier
    assert isinstance(built.verifier, ConstructionOnboardingSettingsVerifier)
    assert isinstance(built.verifier.pricing_verifier, Verifier)
    assert built.verifier.pricing_verifier.pricing is c.price_service
    assert len(calls) == 1
    explicit = NS(verify_step=AsyncMock(return_value=False))
    other = build_construction_onboarding_service(
        session=c.session, now_provider=c.progress.now_provider, completion_verifier=explicit)
    assert other.verifier is explicit and len(calls) == 1


def test_verifier_rejects_different_session(canonical):
    with pytest.raises(ValueError):
        Verifier(session=object(), pricing_service=canonical.price_service)
