"""Explicit selected profile completes onboarding without requiring tax rules."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql

from database.models import ConstructionTenantSetting
from database.repositories.construction.construction_onboarding_tax import ConstructionOnboardingTaxRepository
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingStepNotReady as NotReady,
    build_construction_onboarding_service,
)
from services.construction.construction_onboarding_tax import (
    ConstructionOnboardingTaxService as Service,
    ConstructionOnboardingTaxVerifier as Verifier,
    ConstructionOnboardingTaxProfileUnavailable as Unavailable,
)
from test_construction_02_tax_profiles import case
from test_construction_02_onboarding_progress import (
    onboarding, begin, start, change, snapshot,
)


@pytest.fixture
def selected_tax(onboarding):
    c = onboarding
    c.profile, c.alternate, c.foreign_profile = uuid4(), uuid4(), uuid4()
    c.sync.execute(insert(c.table), [
        dict(
            id=profile_id, tenant_id=tenant, name="Explicit fixture profile",
            client_price_tax_mode="TAX_EXCLUSIVE", material_cost_basis="NET",
            row_version=7, created_by=c.user,
            created_at=c.now, updated_at=c.now,
        )
        for profile_id, tenant in (
            (c.profile, c.tenant), (c.alternate, c.tenant),
            (c.foreign_profile, c.foreign),
        )
    ])
    c.sync.commit()
    c.selection_repo = ConstructionOnboardingTaxRepository(c.session)
    c.selection_repo.table = c.settings
    c.service.get_profile = AsyncMock(wraps=c.service.get_profile)
    c.selection = Service(
        session=c.session, repository=c.selection_repo,
        onboarding_service=c.progress, profile_service=c.service)
    c.progress.verifier = Verifier(session=c.session, tax_service=c.selection)
    c.sync.begin()
    yield c


async def open_taxes(c):
    state = (await start(c)).progress
    return (await change(c, "move_to_step", state, Step.TAXES)).progress


async def choose(c, state, profile_id=None, **kwargs):
    begin(c)
    return await c.selection.select_profile(
        actor=c.actor,
        tax_profile_id=c.profile if profile_id is None else profile_id,
        expected_row_version=state.row_version,
        trace_id="tax-selection", **kwargs)


async def progress(c):
    begin(c)
    return await c.progress.read_progress(actor=c.actor)


def everything(c):
    return snapshot(c), tuple(
        dict(row) for row in c.sync.execute(
            select(c.table).order_by(c.table.c.id)
        ).mappings())


def test_model_has_nullable_tenant_bound_selection():
    table = ConstructionTenantSetting.__table__
    assert len(table.c) == 15 and len(table.constraints) == 9
    column = table.c.onboarding_tax_profile_id
    assert column.nullable and column.default is None and column.server_default is None
    constraint = next(
        item for item in table.foreign_key_constraints
        if item.name == "fk_construction_settings_onboarding_tax_profile")
    assert list(constraint.column_keys) == ["tenant_id", "onboarding_tax_profile_id"]
    assert [item.target_fullname for item in constraint.elements] == [
        "construction_tax_profiles.tenant_id", "construction_tax_profiles.id"]
    assert constraint.ondelete == "RESTRICT"


@pytest.mark.asyncio
async def test_existing_profiles_are_not_automatically_selected(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    before = everything(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.TAXES)
    assert everything(c) == before
    c.service.get_profile.assert_not_awaited()
    begin(c)
    selection = await c.selection.read_selection(actor=c.actor)
    assert selection.onboarding_tax_profile_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("client_mode", ["TAX_EXCLUSIVE", "TAX_INCLUSIVE"])
@pytest.mark.parametrize("material_basis", ["NET", "GROSS"])
async def test_explicit_profile_completes_step_without_any_rules(
    selected_tax, client_mode, material_basis,
):
    c = selected_tax
    c.sync.execute(update(c.table).where(c.table.c.id == c.profile).values(
        client_price_tax_mode=client_mode, material_cost_basis=material_basis))
    c.sync.commit()
    state = await open_taxes(c)
    profiles_before = everything(c)[1]
    selected = await choose(c, state)
    assert selected.changed
    assert selected.selection.row_version == state.row_version + 1
    current = await progress(c)
    assert current.states_dict()["TAXES"] == "PENDING"
    completed = await change(c, "complete_step", current, Step.TAXES)
    assert completed.progress.states_dict()["TAXES"] == "COMPLETED"
    assert completed.progress.current_step is Step.PRICING_PROFILES
    assert everything(c)[1] == profiles_before
    assert "construction_tax_rules" not in c.table.metadata.tables
    assert any(
        call.kwargs.get("lock_for_review") is True
        and call.kwargs["profile_id"] == c.profile
        for call in c.service.get_profile.await_args_list)


@pytest.mark.asyncio
async def test_choice_audit_is_correlated_and_does_not_change_progress(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    selected = await choose(c, state)
    event = c.events.create_event.await_args.kwargs
    assert event["tenant_id"] == c.tenant and event["user_id"] == c.user
    assert event["entity_id"] == c.tenant and event["trace_id"] == "tax-selection"
    assert event["payload"]["operation"] == "onboarding_select_tax_profile"
    assert event["payload"]["changes"] == [{
        "field": "onboarding_tax_profile_id",
        "old_value": None, "new_value": str(c.profile),
    }]
    assert event["payload"]["tax_profile_row_version"] == 7
    current = await progress(c)
    assert current.current_step == state.current_step
    assert current.step_states == state.step_states
    assert current.row_version == selected.selection.row_version


@pytest.mark.asyncio
async def test_current_noop_stale_noop_and_explicit_replacement(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    await choose(c, state)
    current = await progress(c)
    before = everything(c)
    noop = await choose(c, current)
    assert not noop.changed and everything(c) == before
    with pytest.raises(ConstructionRowVersionConflictError):
        await choose(c, state)
    assert everything(c) == before
    replacement = await choose(c, current, c.alternate)
    assert replacement.changed
    assert replacement.selection.onboarding_tax_profile_id == c.alternate
    assert replacement.selection.row_version == current.row_version + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_missing_and_foreign_profiles_do_not_change_selection(selected_tax, foreign):
    c = selected_tax
    state = await open_taxes(c)
    before = everything(c)
    with pytest.raises(Unavailable):
        await choose(c, state, c.foreign_profile if foreign else uuid4())
    assert everything(c) == before


@pytest.mark.asyncio
async def test_database_reference_rejects_foreign_profile(selected_tax):
    c = selected_tax
    await open_taxes(c)
    before = everything(c)
    with pytest.raises(IntegrityError):
        c.sync.execute(update(c.settings).where(
            c.settings.c.tenant_id == c.tenant
        ).values(onboarding_tax_profile_id=c.foreign_profile))
    c.sync.rollback()
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "authority", "cancel"])
async def test_selection_version_and_real_audit_roll_back_together(selected_tax, failure):
    c = selected_tax
    state = await open_taxes(c)
    before = everything(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    elif failure == "authority":
        c.after_audit = lambda: setattr(c, "membership", False)
    else:
        original = c.events.create_event.side_effect
        async def cancelled(**values):
            await original(**values)
            raise asyncio.CancelledError()
        c.events.create_event.side_effect = cancelled
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else ConstructionPermissionDeniedError if failure == "authority"
        else Operation
    )
    with pytest.raises(expected):
        await choose(c, state)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_caller_can_roll_back_prepared_selection_and_audit(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    before, commits = everything(c), c.session.commits
    selected = await choose(c, state, manage_transaction=False)
    assert selected.changed and c.session.commits == commits
    await c.session.rollback()
    assert everything(c) == before


@pytest.mark.asyncio
async def test_conditional_update_miss_is_conflict_without_audit(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    before = everything(c)
    c.selection_repo.set_profile = AsyncMock(return_value=None)
    with pytest.raises(ConstructionRowVersionConflictError):
        await choose(c, state)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "scope"])
async def test_selection_preserves_fresh_settings_authority(selected_tax, failure):
    c = selected_tax
    state = await open_taxes(c)
    before = everything(c)
    if failure == "membership":
        c.membership = False
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await choose(c, state)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_profile_authority_is_rechecked_after_share_lock_wait(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    original = c.service.get_profile
    async def revoked(**values):
        result = await original(**values)
        c.membership = False
        return result
    c.service.get_profile = revoked
    before = everything(c)
    with pytest.raises(ConstructionPermissionDeniedError):
        await choose(c, state)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_malformed_profile_cannot_complete_step(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    await choose(c, state)
    current = await progress(c)
    original = c.service.get_profile
    async def malformed(**values):
        return replace(await original(**values), client_price_tax_mode="AUTO")
    c.service.get_profile = malformed
    before = everything(c)
    with pytest.raises(Operation):
        await change(c, "complete_step", current, Step.TAXES)
    assert everything(c) == before


@pytest.mark.asyncio
async def test_committing_profile_reader_is_rejected(selected_tax):
    c = selected_tax
    state = await open_taxes(c)
    original = c.service.get_profile
    async def committed(**values):
        result = await original(**values)
        await c.session.commit()
        return result
    c.service.get_profile = committed
    before = everything(c)
    with pytest.raises(Operation):
        await choose(c, state)
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, True, "1", 0, 2**63])
async def test_invalid_versions_fail_before_lock(selected_tax, version):
    c = selected_tax
    c.serialization.lock_member.reset_mock()
    with pytest.raises(Validation):
        await c.selection.select_profile(
            actor=c.actor, tax_profile_id=c.profile,
            expected_row_version=version)
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_share_lock_is_tenant_scoped():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None))
    repo = ConstructionOnboardingTaxRepository(Capture())
    tenant = uuid4()
    assert await repo.get_selection(tenant_id=tenant, for_share=True) is None
    sql = statements[-1].compile(dialect=postgresql.dialect())
    assert "FOR SHARE" in str(sql) and tenant in sql.params.values()
    with pytest.raises(ValueError):
        await repo.get_selection(tenant_id=tenant, for_update=True, for_share=True)


def test_default_factory_wires_tax_verifier_without_opening_connections(selected_tax):
    c = selected_tax
    # Match the real AsyncSession interface; no PostgreSQL lock is executed here.
    c.session.get_bind = c.sync.get_bind
    built = build_construction_onboarding_service(
        session=c.session, now_provider=lambda: c.now)
    assert isinstance(built.verifier.tax_verifier, Verifier)
    assert built.verifier.tax_verifier.session is c.session
    assert built.verifier.tax_verifier.tax.session is c.session
    assert built.verifier.tax_verifier.tax.onboarding.verifier is None

# onboarding_explicit_tax_profile_v1
