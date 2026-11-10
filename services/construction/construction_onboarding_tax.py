"""Explicit profile selection and CON-O001 tax-step verification.

No automatic profile choice, rates, document defaults or historical rewrites.
Selection shares the existing settings version, authorization and audit.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from services.construction.construction_audit import ConstructionAuditChange
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingService,
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingStateConflictError as StateConflict,
    ConstructionOnboardingCompletionUnavailable as Unavailable,
    MAX_VERSION, _step, _version,
)
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileService, ConstructionTaxProfileState,
    ConstructionTaxProfileNotFoundError,
)


class ConstructionOnboardingTaxProfileUnavailable(StateConflict):
    pass


@dataclass(frozen=True)
class ConstructionOnboardingTaxSelection:
    tenant_id: UUID
    row_version: int
    onboarding_tax_profile_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConstructionOnboardingTaxSelectionResult:
    selection: ConstructionOnboardingTaxSelection
    profile: ConstructionTaxProfileState
    changed: bool


class ConstructionOnboardingTaxService:
    def __init__(
        self, *, session, repository, onboarding_service, profile_service,
    ):
        if (
            not isinstance(onboarding_service, ConstructionOnboardingService)
            or not isinstance(profile_service, ConstructionTaxProfileService)
            or onboarding_service.session is not session
            or profile_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None))
                   for name in ("get_selection", "set_profile"))
        ):
            raise ValueError("Tax selection requires shared-session canonical services.")
        self.session = session
        self.repository = repository
        self.onboarding = onboarding_service
        self.profiles = profile_service

    @staticmethod
    def _state(row, tenant_id):
        if row is None:
            return None
        try:
            if not isinstance(row, Mapping) or set(row) != {
                "tenant_id", "row_version", "onboarding_tax_profile_id",
                "created_at", "updated_at",
            }:
                raise ValueError()
            state = ConstructionOnboardingTaxSelection(**row)
            if (
                not isinstance(state.tenant_id, UUID)
                or state.tenant_id != tenant_id
                or _version(state.row_version) != state.row_version
                or (
                    state.onboarding_tax_profile_id is not None
                    and not isinstance(state.onboarding_tax_profile_id, UUID)
                )
                or any(
                    not isinstance(value, datetime)
                    or value.tzinfo is None or value.utcoffset() is None
                    for value in (state.created_at, state.updated_at)
                )
            ):
                raise ValueError()
            return state
        except (ValueError, TypeError):
            raise Operation("Stored onboarding tax selection is invalid.") from None

    async def _profile(self, *, actor, profile_id, transaction):
        try:
            profile = await self.profiles.get_profile(
                actor=actor, profile_id=profile_id, lock_for_review=True)
        except ConstructionTaxProfileNotFoundError:
            raise ConstructionOnboardingTaxProfileUnavailable(
                "Selected tax profile is unavailable.") from None
        self.onboarding._same_transaction(transaction)
        if not isinstance(profile, ConstructionTaxProfileState):
            raise Operation("Selected tax profile projection is invalid.")
        profile = ConstructionTaxProfileService._state(
            asdict(profile), tenant_id=actor.tenant_id, profile_id=profile_id)
        await self.onboarding._authorize(actor)
        self.onboarding._same_transaction(transaction)
        return profile

    async def read_selection(self, *, actor):
        async with self.onboarding._operation(actor, write=False) as transaction:
            row = await self.repository.get_selection(tenant_id=actor.tenant_id)
            await self.onboarding._authorize(actor)
            self.onboarding._same_transaction(transaction)
            return self._state(row, actor.tenant_id)

    async def select_profile(
        self, *, actor, tax_profile_id, expected_row_version,
        trace_id=None, manage_transaction=True,
    ):
        if not isinstance(tax_profile_id, UUID) or type(manage_transaction) is not bool:
            raise Validation("An explicit profile UUID and transaction mode are required.")
        version = _version(expected_row_version)
        async with self.onboarding._operation(actor, write=True) as transaction:
            row = await self.repository.get_selection(
                tenant_id=actor.tenant_id, for_update=True)
            await self.onboarding._authorize(actor)
            self.onboarding._same_transaction(transaction)
            before = self._state(row, actor.tenant_id)
            if before is None:
                raise StateConflict("Create onboarding settings before selecting a profile.")
            require_expected_row_version(
                actual_row_version=before.row_version,
                expected_row_version=version)

            profile = await self._profile(
                actor=actor, profile_id=tax_profile_id, transaction=transaction)
            if before.onboarding_tax_profile_id == tax_profile_id:
                if manage_transaction:
                    await self.session.commit()
                return ConstructionOnboardingTaxSelectionResult(before, profile, False)

            if before.row_version == MAX_VERSION:
                raise Operation("Settings version is exhausted.")
            saved = await self.repository.set_profile(
                tenant_id=actor.tenant_id,
                expected_row_version=before.row_version,
                previous_profile_id=before.onboarding_tax_profile_id,
                tax_profile_id=tax_profile_id,
                as_of=self.onboarding._now())
            self.onboarding._same_transaction(transaction)
            if saved is None:
                raise ConstructionRowVersionConflictError()
            state = self._state(saved, actor.tenant_id)
            if (
                state is None
                or state.row_version != before.row_version + 1
                or state.onboarding_tax_profile_id != tax_profile_id
                or state.created_at != before.created_at
            ):
                raise Operation("Saved onboarding tax selection differs.")

            await self.onboarding.audit.record_operation(
                actor=actor,
                event_type="construction_onboarding_progress_updated",
                entity_type="construction_tenant_settings",
                entity_id=actor.tenant_id,
                operation="onboarding_select_tax_profile",
                trace_id=trace_id,
                changes=(ConstructionAuditChange(
                    "onboarding_tax_profile_id",
                    None if before.onboarding_tax_profile_id is None
                    else str(before.onboarding_tax_profile_id),
                    str(tax_profile_id),
                ),),
                details={
                    "row_version": state.row_version,
                    "previous_row_version": before.row_version,
                    "tax_profile_row_version": profile.row_version,
                    "client_price_tax_mode": profile.client_price_tax_mode,
                    "material_cost_basis": profile.material_cost_basis,
                },
            )
            await self.onboarding._authorize(actor)
            self.onboarding._same_transaction(transaction)
            if manage_transaction:
                await self.session.commit()
            return ConstructionOnboardingTaxSelectionResult(state, profile, True)

    async def selected_profile_ready(self, *, actor):
        async with self.onboarding._operation(actor, write=False) as transaction:
            row = await self.repository.get_selection(
                tenant_id=actor.tenant_id, for_share=True)
            await self.onboarding._authorize(actor)
            self.onboarding._same_transaction(transaction)
            state = self._state(row, actor.tenant_id)
            if state is None or state.onboarding_tax_profile_id is None:
                return False
            await self._profile(
                actor=actor, profile_id=state.onboarding_tax_profile_id,
                transaction=transaction)
            return True


class ConstructionOnboardingTaxVerifier:
    def __init__(self, *, session, tax_service):
        if (
            not isinstance(tax_service, ConstructionOnboardingTaxService)
            or tax_service.session is not session
        ):
            raise ValueError("Tax verification requires the shared progress session.")
        self.session = session
        self.tax = tax_service

    async def verify_step(self, *, actor, step):
        if _step(step) is not Step.TAXES:
            raise Unavailable("This verifier handles only the tax onboarding step.")
        return await self.tax.selected_profile_ready(actor=actor)


def build_construction_onboarding_tax_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_onboarding import ConstructionOnboardingRepository
    from database.repositories.construction.construction_onboarding_tax import ConstructionOnboardingTaxRepository
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    from services.construction.construction_tax_profiles import build_construction_tax_profile_service

    # Reuse the existing authorizer/transaction/audit contract. This helper does
    # not complete steps and does not install a successful default verifier.
    onboarding = ConstructionOnboardingService(
        session=session, repository=ConstructionOnboardingRepository(session),
        access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session),
        now_provider=now_provider)
    return ConstructionOnboardingTaxService(
        session=session,
        repository=ConstructionOnboardingTaxRepository(session),
        onboarding_service=onboarding,
        profile_service=build_construction_tax_profile_service(
            session=session, now_provider=now_provider))

# onboarding_explicit_tax_profile_v1
