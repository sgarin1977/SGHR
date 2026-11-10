"""Local progress transactions; explicit test verifier, no runtime DB or providers."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, Table, Uuid, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.schema import CreateTable

from database.models import ConstructionTenantSetting
from database.repositories.construction.construction_onboarding import ConstructionOnboardingRepository
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_onboarding import (
    ConstructionOnboardingService as Service,
    ConstructionOnboardingValidationError as ValidationError,
    ConstructionOnboardingOperationError as OperationError,
    ConstructionOnboardingStateConflictError as StateConflict,
    ConstructionOnboardingCompletionUnavailable, ConstructionOnboardingStepNotReady,
    ConstructionOnboardingStep as Step, ConstructionOnboardingStepStatus as Status,
    STEPS, MAX_VERSION)
from test_construction_02_tax_profiles import case, LocalUTC


@pytest.fixture
def onboarding(case):
    c = case
    c.sync.rollback()
    files = Table("file_storage_objects", c.table.metadata, Column("id", Uuid, primary_key=True))
    files.create(c.sync.get_bind())
    c.settings = ConstructionTenantSetting.__table__.to_metadata(c.table.metadata)
    c.settings.c.onboarding_step_states.type = JSON(none_as_null=True)
    for name in ("created_at", "updated_at"):
        c.settings.c[name].type = LocalUTC()
    # Only local dialect adaptations. Native JSONB/regex CHECK acceptance is not claimed.
    for constraint in list(c.settings.constraints):
        if constraint.name in {
            "ck_construction_tenant_settings_accent_color",
            "ck_construction_tenant_settings_onboarding_step_states",
        }:
            c.settings.constraints.remove(constraint)
    c.settings.create(c.sync.get_bind())
    c.sync.execute(insert(c.settings).values(
        tenant_id=c.foreign, display_name_override="Foreign company",
        created_at=c.now, updated_at=c.now, row_version=1))
    c.sync.commit()
    c.session.get_transaction = c.sync.get_transaction
    c.session.flush = AsyncMock(side_effect=c.sync.flush)

    class LocalRepository(ConstructionOnboardingRepository):
        def _insert(self):
            return sqlite_insert(self.table)

    c.progress_repo = LocalRepository(c.session)
    c.progress_repo.table = c.settings
    c.order = []
    original_lock = c.serialization.lock_member.side_effect

    async def member(**values):
        c.order.append("member")
        await original_lock(**values)

    c.serialization.lock_member.side_effect = member
    original_get = c.progress_repo.get_settings

    async def get(**values):
        c.order.append("settings_lock" if values.get("for_update") else "settings_read")
        return await original_get(**values)

    c.progress_repo.get_settings = AsyncMock(side_effect=get)
    c.progress_repo.update_progress = AsyncMock(wraps=c.progress_repo.update_progress)
    c.progress_repo.create_progress = AsyncMock(wraps=c.progress_repo.create_progress)
    # Explicit server collaborator for local tests; no default successful verifier exists.
    c.verifier = NS(verify_step=AsyncMock(return_value=True))
    c.progress = Service(
        session=c.session, repository=c.progress_repo, access_repository=c.access,
        event_repository=c.events, serialization=c.serialization,
        completion_verifier=c.verifier, now_provider=lambda: c.now)
    c.sync.begin()
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(
        tuple(dict(row) for row in c.sync.execute(select(table).order_by(table.c[0])).mappings())
        for table in (c.settings, c.audits))


async def start(c, version=None, **values):
    begin(c)
    return await c.progress.start_progress(
        actor=c.actor, expected_row_version=version,
        trace_id="onboarding-test", **values)


async def change(c, method, state, step, **values):
    begin(c)
    return await getattr(c.progress, method)(
        actor=c.actor, expected_row_version=state.row_version, step=step,
        trace_id="onboarding-test", **values)


def seed(c, version=7):
    c.sync.execute(insert(c.settings).values(
        tenant_id=c.tenant, display_name_override="Existing company", phone_override="+123",
        email_override="office@example.test", accent_color="#AABBCC",
        created_at=c.now, updated_at=c.now, row_version=version))
    c.sync.commit()


def test_orm_matches_fifteen_columns_nullable_jsonb_and_shared_version():
    table = ConstructionTenantSetting.__table__
    assert len(table.c) == 15 and len(table.constraints) == 9
    assert list(table.c.keys())[-3:] == [
        "onboarding_current_step", "onboarding_step_states", "onboarding_tax_profile_id"]
    for name in ("onboarding_current_step", "onboarding_step_states", "onboarding_tax_profile_id"):
        assert table.c[name].nullable
        assert table.c[name].default is None and table.c[name].server_default is None
    assert table.c.onboarding_step_states.type.compile(dialect=postgresql.dialect()) == "JSONB"
    processor = table.c.onboarding_step_states.type.bind_processor(postgresql.dialect())
    assert processor(None) is None
    assert ConstructionTenantSetting.__mapper__.version_id_col is table.c.row_version
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "COALESCE" in ddl and "jsonb_typeof" in ddl and "'SKIPPED'" in ddl


@pytest.mark.asyncio
async def test_read_does_not_create_settings_or_start_wizard(onboarding):
    c = onboarding
    before = snapshot(c)
    result = await c.progress.read_progress(actor=c.actor)
    assert result.tenant_id == c.tenant and result.row_version is None
    assert not result.started and not result.is_complete and result.states_dict() is None
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_create_noop_and_navigation_are_atomic_and_correlated(onboarding):
    c = onboarding
    result = await start(c)
    state = result.progress
    assert result.changed and result.settings_created and state.row_version == 1
    assert state.current_step is Step.COMPANY and state.started and not state.is_complete
    assert state.states_dict() == {step.value: "PENDING" for step in STEPS}
    assert c.order.index("member") < c.order.index("settings_lock") and c.session.commits == 1
    before = snapshot(c)
    noop = await start(c, 1)
    assert not noop.changed and not noop.settings_created and noop.progress == state
    assert snapshot(c) == before
    moved = await change(c, "move_to_step", state, Step.LOCALIZATION)
    assert moved.progress.row_version == 2 and moved.progress.current_step is Step.LOCALIZATION
    assert moved.progress.step_states == state.step_states
    events = snapshot(c)[1]
    assert len(events) == 2
    assert {event["event_type"] for event in events} == {
        "construction_onboarding_started", "construction_onboarding_progress_updated"}
    assert [call.kwargs["event_type"] for call in c.events.create_event.await_args_list] == [
        "construction_onboarding_started", "construction_onboarding_progress_updated"]
    assert all(row["entity_id"] == c.tenant and row["trace_id"] == "onboarding-test" for row in events)
    c.verifier.verify_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_branding_is_preserved_and_uses_the_same_version(onboarding):
    c = onboarding
    seed(c)
    before = next(row for row in snapshot(c)[0] if row["tenant_id"] == c.tenant)
    started = await start(c, 7)
    assert not started.settings_created and started.progress.row_version == 8
    after = next(row for row in snapshot(c)[0] if row["tenant_id"] == c.tenant)
    for name in ("display_name_override", "phone_override", "email_override", "accent_color", "created_at"):
        assert after[name] == before[name]
    noop = await change(c, "move_to_step", started.progress, Step.COMPANY)
    assert not noop.changed and noop.progress.row_version == 8
    c.sync.rollback()
    from database.repositories.construction.construction_tenant_settings import ConstructionTenantSettingsRepository
    row = c.sync.get(ConstructionTenantSetting, c.tenant)
    await ConstructionTenantSettingsRepository(c.session).update_settings(
        settings=row, values={"display_name_override": "Edited through existing repository"})
    c.sync.commit()
    begin(c)
    current = await c.progress.read_progress(actor=c.actor)
    assert current.row_version == 9 and current.step_states == started.progress.step_states
    assert current.current_step is Step.COMPANY


@pytest.mark.asyncio
async def test_stale_versions_and_wrong_absence_expectation_never_bypass_noop(onboarding):
    c = onboarding
    initial = (await start(c)).progress
    changed = (await change(c, "move_to_step", initial, Step.TAXES)).progress
    before = snapshot(c)
    for version in (None, 1):
        with pytest.raises(ConstructionRowVersionConflictError):
            await start(c, version)
        assert snapshot(c) == before
    with pytest.raises(ConstructionRowVersionConflictError):
        await change(c, "move_to_step", initial, Step.TAXES)
    assert snapshot(c) == before
    noop = await change(c, "move_to_step", changed, Step.TAXES)
    assert not noop.changed


@pytest.mark.asyncio
async def test_full_guide_skips_only_prices_materials_and_can_be_reopened(onboarding):
    c = onboarding
    state = (await start(c)).progress
    for step in STEPS:
        method = "skip_step" if step in (Step.WORK_PRICES, Step.MATERIALS_SUPPLIERS) else "complete_step"
        state = (await change(c, method, state, step)).progress
    assert state.is_complete and state.row_version == 8 and len(snapshot(c)[1]) == 8
    assert state.states_dict()["WORK_PRICES"] == state.states_dict()["MATERIALS_SUPPLIERS"] == "SKIPPED"
    assert c.verifier.verify_step.await_count == 5
    reopened = (await change(c, "move_to_step", state, Step.COMPANY)).progress
    assert reopened.is_complete and reopened.step_states == state.step_states
    reviewed = await change(c, "complete_step", reopened, Step.COMPANY)
    assert reviewed.progress.current_step is Step.LOCALIZATION and reviewed.changed
    c.verifier.verify_step.assert_awaited_with(actor=c.actor, step=Step.COMPANY)


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [
    Step.COMPANY, Step.LOCALIZATION, Step.TAXES,
    Step.PRICING_PROFILES, Step.MEASUREMENT_TEMPLATES,
])
async def test_other_steps_cannot_be_skipped(onboarding, step):
    c = onboarding
    state = (await start(c)).progress
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await change(c, "skip_step", state, step)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_completion_requires_explicit_canonical_verification(onboarding):
    c = onboarding
    state = (await start(c)).progress
    before = snapshot(c)
    c.progress.verifier = None
    with pytest.raises(ConstructionOnboardingCompletionUnavailable):
        await change(c, "complete_step", state, Step.COMPANY)
    assert snapshot(c) == before
    c.progress.verifier = c.verifier
    c.verifier.verify_step.return_value = False
    with pytest.raises(ConstructionOnboardingStepNotReady):
        await change(c, "complete_step", state, Step.COMPANY)
    assert snapshot(c) == before
    c.verifier.verify_step.return_value = 1
    with pytest.raises(OperationError):
        await change(c, "complete_step", state, Step.COMPANY)
    assert snapshot(c) == before
    with pytest.raises(TypeError):
        await c.progress.complete_step(
            actor=c.actor, step=Step.COMPANY,
            expected_row_version=1, completed=True)


@pytest.mark.asyncio
async def test_completion_checks_current_cursor_and_rechecks_authority_after_verifier(onboarding):
    c = onboarding
    state = (await start(c)).progress
    before = snapshot(c)
    with pytest.raises(StateConflict):
        await change(c, "complete_step", state, Step.TAXES)
    c.verifier.verify_step.assert_not_awaited()

    async def revoked(**values):
        c.grant.status = "revoked"
        return True

    c.verifier.verify_step.side_effect = revoked
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await change(c, "complete_step", state, Step.COMPANY)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "authority", "cancel"])
@pytest.mark.parametrize("existing", [False, True])
async def test_progress_and_real_local_audit_roll_back_together(onboarding, failure, existing):
    c = onboarding
    state = (await start(c)).progress if existing else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        real = c.events.create_event.side_effect

        async def fail(**values):
            await real(**values)
            if failure == "cancel":
                raise asyncio.CancelledError()
            c.membership = False

        c.events.create_event.side_effect = fail
    expected = (asyncio.CancelledError,) if failure == "cancel" else (
        (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
        if failure == "authority" else (OperationError,))
    with pytest.raises(expected):
        if state is None:
            await start(c)
        else:
            await change(c, "move_to_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "membership", "revoked", "expired", "project_scope", "foreign_scope",
])
async def test_fresh_existing_settings_permission_and_scope_are_required(onboarding, failure):
    c = onboarding
    before = snapshot(c)
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    elif failure == "project_scope":
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "PROJECT_MANAGER", "project", uuid4()
    else:
        c.grant.scope_id = c.foreign
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await start(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_tenant_owner_and_admin_use_the_existing_permission_matrix(onboarding, role):
    onboarding.grant.role = role
    assert (await start(onboarding)).changed


@pytest.mark.asyncio
async def test_authority_is_fresh_after_waiting_for_membership_barrier(onboarding):
    c = onboarding
    real = c.serialization.lock_member.side_effect

    async def revoke(**values):
        await real(**values)
        c.membership = False

    c.serialization.lock_member.side_effect = revoke
    with pytest.raises(ConstructionPermissionDeniedError):
        await start(c)
    c.progress_repo.get_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_insert_race_and_conditional_update_miss_are_conflicts_without_audit(onboarding):
    c = onboarding
    before = snapshot(c)
    original = c.progress_repo.create_progress
    c.progress_repo.create_progress = AsyncMock(return_value=None)
    with pytest.raises(ConstructionRowVersionConflictError):
        await start(c)
    assert snapshot(c) == before
    c.progress_repo.create_progress = original
    state = (await start(c)).progress
    before = snapshot(c)
    c.progress_repo.update_progress = AsyncMock(return_value=None)
    with pytest.raises(ConstructionRowVersionConflictError):
        await change(c, "move_to_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0, 2**63])
async def test_invalid_versions_fail_before_storage(onboarding, version):
    c = onboarding
    with pytest.raises(ValidationError):
        await start(c, version)
    c.progress_repo.get_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_rechecks_authority_after_query_and_malformed_rows_are_not_exposed(onboarding):
    c = onboarding
    original = c.progress_repo.get_settings

    async def read(**values):
        result = await original(**values)
        c.membership = False
        return result

    c.progress_repo.get_settings = read
    with pytest.raises(ConstructionPermissionDeniedError):
        await c.progress.read_progress(actor=c.actor)
    c.membership = True
    c.progress_repo.get_settings = original
    state = (await start(c)).progress
    row = await original(tenant_id=c.tenant)
    row["onboarding_step_states"] = dict(state.states_dict(), UNKNOWN="COMPLETED")
    c.progress_repo.get_settings = AsyncMock(return_value=row)
    begin(c)
    with pytest.raises(OperationError):
        await c.progress.read_progress(actor=c.actor)


@pytest.mark.asyncio
async def test_preparation_keeps_the_single_final_commit_with_the_caller(onboarding):
    c = onboarding
    result = await start(c, manage_transaction=False)
    assert result.changed and c.sync.in_transaction() and c.session.commits == 0
    assert len(snapshot(c)[1]) == 1
    await c.session.rollback()
    assert all(row["tenant_id"] != c.tenant for row in snapshot(c)[0])
    assert snapshot(c)[1] == ()


@pytest.mark.asyncio
async def test_max_version_allows_only_current_noop(onboarding):
    c = onboarding
    state = (await start(c)).progress
    c.sync.execute(update(c.settings).where(
        c.settings.c.tenant_id == c.tenant).values(row_version=MAX_VERSION))
    c.sync.commit()
    state = replace(state, row_version=MAX_VERSION)
    noop = await change(c, "move_to_step", state, Step.COMPANY)
    assert not noop.changed
    before = snapshot(c)
    with pytest.raises(OperationError):
        await change(c, "move_to_step", state, Step.LOCALIZATION)
    assert snapshot(c) == before

# onboarding_explicit_tax_profile_v1
