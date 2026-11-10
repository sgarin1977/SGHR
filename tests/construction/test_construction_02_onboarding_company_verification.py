"""Local step-1 progress uses the existing Platform company profile."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update

from database.models import TenantCompanyProfile
from database.repositories.company_profiles import TenantCompanyProfileRepository
from services.platform_company_profiles import TenantCompanyProfileService
from services.construction.construction_onboarding import (
    ConstructionOnboardingSettingsVerifier as Verifier,
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingStepNotReady as NotReady,
    ConstructionOnboardingOperationError as OperationError,
    ConstructionOnboardingCompletionUnavailable as Unavailable,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
from test_construction_02_onboarding_pricing_verification import canonical, put, open_prices
from test_construction_02_onboarding_progress import onboarding, begin, start, change, snapshot
from test_construction_02_tax_profiles import case, LocalUTC


@pytest.fixture
def company(canonical):
    c = canonical
    c.sync.rollback()
    c.companies = TenantCompanyProfile.__table__.to_metadata(c.table.metadata)
    for name in ("created_at", "updated_at"):
        c.companies.c[name].type = LocalUTC()
    c.companies.create(c.sync.get_bind())
    c.company_repository = TenantCompanyProfileRepository(c.session)
    c.company_service = TenantCompanyProfileService(
        session=c.session, repository=c.company_repository, event_repository=c.events)
    c.progress.verifier = Verifier(
        session=c.session, company_service=c.company_service,
        pricing_verifier=c.progress.verifier)
    c.sync.begin()
    yield c


def put_company(c, *, tenant_id=None):
    c.sync.execute(insert(c.companies).values(
        id=uuid4(), tenant_id=c.tenant if tenant_id is None else tenant_id,
        legal_name="Existing Platform company", commercial_name=None,
        phone=None, email=None, website=None, logo_file_id=None,
        row_version=7, created_at=c.now, updated_at=c.now))
    c.sync.commit()


def everything(c):
    return snapshot(c), tuple(dict(row) for row in c.sync.execute(
        select(c.companies).order_by(c.companies.c.id)).mappings())


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_saved_profile_completes_company_without_optional_branding_or_requisites(company, role):
    c = company
    c.grant.role = role
    put_company(c)
    state = (await start(c)).progress
    before, commits = everything(c), c.session.commits
    result = await change(c, "complete_step", state, Step.COMPANY)
    assert result.changed and result.progress.row_version == state.row_version + 1
    assert result.progress.current_step is Step.LOCALIZATION and not result.progress.is_complete
    states = result.progress.states_dict()
    assert states["COMPANY"] == "COMPLETED"
    assert all(value == "PENDING" for step, value in states.items() if step != "COMPANY")
    after = everything(c)
    assert after[1] == before[1] and after[1][0]["row_version"] == 7
    assert len(after[0][1]) == len(before[0][1]) + 1 and c.session.commits == commits + 1
    event = c.events.create_event.await_args.kwargs
    assert event["entity_id"] == c.tenant and event["trace_id"] == "onboarding-test"
    assert event["payload"]["operation"] == "onboarding_complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_exists", [False, True])
async def test_missing_own_profile_is_not_replaced_by_foreign_data_or_copied(company, foreign_exists):
    c = company
    if foreign_exists:
        put_company(c, tenant_id=c.foreign)
    state = (await start(c)).progress
    before = everything(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.COMPANY)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_company_completion_preserves_existing_construction_overrides(company):
    c = company
    put_company(c)
    state = (await start(c)).progress
    c.sync.execute(update(c.settings).where(c.settings.c.tenant_id == c.tenant).values(
        display_name_override="Construction name", phone_override="+123",
        accent_color="#AABBCC"))
    c.sync.commit()
    before = everything(c)
    result = await change(c, "complete_step", state, Step.COMPANY)
    saved = next(row for row in everything(c)[0][0] if row["tenant_id"] == c.tenant)
    original = next(row for row in before[0][0] if row["tenant_id"] == c.tenant)
    for key in ("display_name_override", "phone_override", "accent_color", "created_at"):
        assert saved[key] == original[key]
    assert result.progress.states_dict()["COMPANY"] == "COMPLETED"
    assert everything(c)[1] == before[1]


@pytest.mark.asyncio
async def test_current_noop_rechecks_profile_and_does_not_rewrite_old_progress(company):
    c = company
    put_company(c)
    state = (await start(c)).progress
    state = (await change(c, "complete_step", state, Step.COMPANY)).progress
    before = everything(c)
    noop = await change(c, "complete_step", state, Step.COMPANY)
    assert not noop.changed and noop.progress == state and everything(c) == before
    c.sync.execute(delete(c.companies).where(c.companies.c.tenant_id == c.tenant))
    c.sync.commit()
    before = everything(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.COMPANY)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_stale_version_is_rejected_before_company_read(company):
    c = company
    put_company(c)
    state = (await start(c)).progress
    await change(c, "move_to_step", state, Step.LOCALIZATION)
    c.company_service.get_profile = AsyncMock(wraps=c.company_service.get_profile)
    before = everything(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await change(c, "complete_step", state, Step.COMPANY)
    c.company_service.get_profile.assert_not_awaited()
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "grant"])
async def test_authority_is_rechecked_after_canonical_company_read(company, failure):
    c = company
    put_company(c)
    state = (await start(c)).progress
    before = everything(c)
    original = c.company_service.get_profile

    async def revoked(**values):
        profile = await original(**values)
        if failure == "membership":
            c.membership = False
        else:
            c.grant.status = "revoked"
        return profile

    c.company_service.get_profile = revoked
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await change(c, "complete_step", state, Step.COMPANY)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_completion_keeps_profile_progress_and_audit_atomic(company, failure):
    c = company
    put_company(c)
    state = (await start(c)).progress
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
        await change(c, "complete_step", state, Step.COMPANY)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"tenant_id": "wrong"}, {"tenant_id": None}, {"id": "wrong"},
    {"row_version": True}, {"row_version": 0},
    {"legal_name": " "}, {"legal_name": "x" * 256}, {"email": "invalid"},
])
async def test_malformed_company_result_cannot_confirm_step(company, bad):
    c = company
    state = (await start(c)).progress
    values = dict(id=uuid4(), tenant_id=c.tenant, row_version=1, legal_name="Company",
                  email=None, website=None, phone=None)
    values.update(bad)
    c.company_service.get_profile = AsyncMock(return_value=NS(**values))
    before = everything(c)
    with pytest.raises(OperationError):
        await change(c, "complete_step", state, Step.COMPANY)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_company_reader_cannot_commit_the_progress_transaction(company):
    c = company
    put_company(c)
    state = (await start(c)).progress
    before = everything(c)
    original = c.company_service.get_profile

    async def committed(**values):
        profile = await original(**values)
        await c.session.commit()
        return profile

    c.company_service.get_profile = committed
    with pytest.raises(OperationError):
        await change(c, "complete_step", state, Step.COMPANY)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_pricing_step_still_uses_the_canonical_con017_verifier(company):
    c = company
    put(c)
    state = await open_prices(c)
    companies = everything(c)[1]
    result = await change(c, "complete_step", state, Step.PRICING_PROFILES)
    assert result.progress.current_step is Step.WORK_PRICES
    assert result.progress.states_dict()["PRICING_PROFILES"] == "COMPLETED"
    assert everything(c)[1] == companies


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [
    Step.LOCALIZATION, Step.TAXES, Step.WORK_PRICES,
    Step.MATERIALS_SUPPLIERS, Step.MEASUREMENT_TEMPLATES,
])
async def test_other_unconfigured_steps_remain_unavailable(company, step):
    c = company
    before = everything(c)
    with pytest.raises(Unavailable):
        await c.progress.verifier.verify_step(actor=c.actor, step=step)
    assert everything(c) == before
