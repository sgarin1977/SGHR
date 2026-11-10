"""CON-O001/O004 progress metadata; business readiness is checked separately.

Trusted tenant session. Writes share the membership barrier and settings row lock.
Step completion requires an explicitly wired read-only canonical verifier.
No client completion flags, module activation, grants, templates or prices are created.
"""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access)

MAX_VERSION = 2**63 - 1


class ConstructionOnboardingValidationError(ValueError):
    pass


class ConstructionOnboardingOperationError(Exception):
    pass


class ConstructionOnboardingStateConflictError(Exception):
    pass


class ConstructionOnboardingCompletionUnavailable(ConstructionOnboardingOperationError):
    pass


class ConstructionOnboardingStepNotReady(ConstructionOnboardingStateConflictError):
    pass


class ConstructionOnboardingStep(str, Enum):
    COMPANY = "COMPANY"
    LOCALIZATION = "LOCALIZATION"
    TAXES = "TAXES"
    PRICING_PROFILES = "PRICING_PROFILES"
    WORK_PRICES = "WORK_PRICES"
    MATERIALS_SUPPLIERS = "MATERIALS_SUPPLIERS"
    MEASUREMENT_TEMPLATES = "MEASUREMENT_TEMPLATES"


class ConstructionOnboardingStepStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


Step, Status = ConstructionOnboardingStep, ConstructionOnboardingStepStatus
STEPS = tuple(Step)
SKIPPABLE = frozenset({Step.WORK_PRICES, Step.MATERIALS_SUPPLIERS})


def _step(value):
    if not isinstance(value, str):
        raise ConstructionOnboardingValidationError("An exact onboarding step is required.")
    try:
        return Step(value)
    except ValueError:
        raise ConstructionOnboardingValidationError("Unknown onboarding step.") from None


def _version(value, *, allow_absent=False):
    if value is None and allow_absent:
        return None
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ConstructionOnboardingValidationError("A positive BIGINT version is required.")
    return value


def _progress(current_step, states):
    if current_step is None and states is None:
        return None, None
    if (current_step is None or not isinstance(states, Mapping)
            or any(type(key) is not str for key in states)
            or set(states) != {step.value for step in STEPS}):
        raise ConstructionOnboardingValidationError("A closed seven-step state is required.")
    current = _step(current_step)
    result = []
    for step in STEPS:
        value = states[step.value]
        if type(value) is not str:
            raise ConstructionOnboardingValidationError("A step status must be an exact code.")
        try:
            status = Status(value)
        except ValueError:
            raise ConstructionOnboardingValidationError("Unknown onboarding step status.") from None
        if status is Status.SKIPPED and step not in SKIPPABLE:
            raise ConstructionOnboardingValidationError("This onboarding step cannot be skipped.")
        result.append((step, status))
    return current, tuple(result)


@dataclass(frozen=True)
class ConstructionOnboardingState:
    tenant_id: UUID
    row_version: int | None
    current_step: Step | None
    step_states: tuple[tuple[Step, Status], ...] | None
    created_at: datetime | None
    updated_at: datetime | None

    @property
    def started(self):
        return self.current_step is not None

    @property
    def is_complete(self):
        # Completion of the guide does not grant access or commercial readiness.
        return self.started and all(status in (Status.COMPLETED, Status.SKIPPED) for _, status in self.step_states)

    def states_dict(self):
        return None if self.step_states is None else {step.value: status.value for step, status in self.step_states}


@dataclass(frozen=True)
class ConstructionOnboardingResult:
    progress: ConstructionOnboardingState
    changed: bool
    settings_created: bool


class ConstructionOnboardingService:
    def __init__(self, *, session, repository, access_repository, event_repository,
                 serialization, completion_verifier=None, now_provider=None):
        collaborators = (
            (repository, ("get_settings", "create_progress", "update_progress")),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)), (serialization, ("lock_member",)),
        )
        if (session is None or not hasattr(session, "no_autoflush")
                or any(not callable(getattr(session, name, None)) for name in (
                    "in_transaction", "get_transaction", "get_nested_transaction", "expire_all", "commit", "rollback"))
                or any(getattr(obj, "session", None) is not session
                       or any(not callable(getattr(obj, name, None)) for name in names)
                       for obj, names in collaborators)
                or completion_verifier is not None and not callable(getattr(completion_verifier, "verify_step", None))
                or now_provider is not None and not callable(now_provider)):
            raise ValueError("Onboarding requires shared-session transaction collaborators.")
        self.session, self.repository, self.access = session, repository, access_repository
        self.serialization, self.verifier = serialization, completion_verifier
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionOnboardingOperationError("Onboarding clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "user_id", None), UUID)
                or not isinstance(getattr(actor, "tenant_id", None), UUID)):
            raise ConstructionPermissionDeniedError("Onboarding identity is unavailable.")

    async def _authorize(self, actor):
        self._actor(actor)
        self.session.expire_all()
        if await self.access.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id
        ) is not True:
            raise ConstructionPermissionDeniedError("Onboarding membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        require_construction_access(actor=actor, access_context=context, permission_code="construction.settings.manage")

    def _same_transaction(self, transaction):
        if (self.session.get_transaction() is not transaction or not self.session.in_transaction()
                or self.session.get_nested_transaction() is not None):
            raise ConstructionOnboardingOperationError("Onboarding transaction changed.")

    @asynccontextmanager
    async def _operation(self, actor, *, write):
        self._actor(actor)
        transaction = self.session.get_transaction()
        if (self.session.in_transaction() is not True or transaction is None
                or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionOnboardingOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if write:
                    await self.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self._authorize(actor)
                self._same_transaction(transaction)
                yield transaction
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionOnboardingOperationError("Onboarding rollback failed.") from None
            if isinstance(error, (
                ConstructionOnboardingValidationError, ConstructionOnboardingOperationError,
                ConstructionOnboardingStateConflictError, ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
            )) or not isinstance(error, Exception):
                raise
            raise ConstructionOnboardingOperationError("Onboarding operation failed.") from None

    @staticmethod
    def _state(row, tenant_id):
        if row is None:
            return ConstructionOnboardingState(tenant_id, None, None, None, None, None)
        try:
            if (not isinstance(row, Mapping) or set(row) != {
                    "tenant_id", "row_version", "onboarding_current_step", "onboarding_step_states", "created_at", "updated_at"}
                    or not isinstance(row["tenant_id"], UUID) or row["tenant_id"] != tenant_id
                    or any(not isinstance(row[key], datetime) or row[key].tzinfo is None or row[key].utcoffset() is None
                           for key in ("created_at", "updated_at"))):
                raise ValueError()
            current, states = _progress(row["onboarding_current_step"], row["onboarding_step_states"])
            return ConstructionOnboardingState(tenant_id, _version(row["row_version"]), current, states,
                                                row["created_at"], row["updated_at"])
        except (ValueError, TypeError):
            raise ConstructionOnboardingOperationError("Stored onboarding progress is invalid.") from None

    async def read_progress(self, *, actor):
        async with self._operation(actor, write=False) as transaction:
            row = await self.repository.get_settings(tenant_id=actor.tenant_id)
            await self._authorize(actor)
            self._same_transaction(transaction)
            return self._state(row, actor.tenant_id)

    async def start_progress(self, *, actor, expected_row_version, trace_id=None, manage_transaction=True):
        version = _version(expected_row_version, allow_absent=True)
        return await self._change(actor=actor, operation="start", step=None, version=version,
                                  trace_id=trace_id, manage_transaction=manage_transaction)

    async def move_to_step(self, *, actor, step, expected_row_version, trace_id=None, manage_transaction=True):
        return await self._change(actor=actor, operation="move", step=_step(step), version=_version(expected_row_version),
                                  trace_id=trace_id, manage_transaction=manage_transaction)

    async def skip_step(self, *, actor, step, expected_row_version, trace_id=None, manage_transaction=True):
        step = _step(step)
        if step not in SKIPPABLE:
            raise ConstructionOnboardingValidationError("Only work prices and materials may be skipped.")
        return await self._change(actor=actor, operation="skip", step=step, version=_version(expected_row_version),
                                  trace_id=trace_id, manage_transaction=manage_transaction)

    async def complete_step(self, *, actor, step, expected_row_version, trace_id=None, manage_transaction=True):
        return await self._change(actor=actor, operation="complete", step=_step(step), version=_version(expected_row_version),
                                  trace_id=trace_id, manage_transaction=manage_transaction)

    async def _change(self, *, actor, operation, step, version, trace_id, manage_transaction):
        if type(manage_transaction) is not bool:
            raise ConstructionOnboardingValidationError("Transaction mode is invalid.")
        async with self._operation(actor, write=True) as transaction:
            row = await self.repository.get_settings(tenant_id=actor.tenant_id, for_update=True)
            await self._authorize(actor)
            self._same_transaction(transaction)
            before = self._state(row, actor.tenant_id)
            if before.row_version is None:
                if operation != "start":
                    raise ConstructionOnboardingStateConflictError("Onboarding has not started.")
                if version is not None:
                    raise ConstructionRowVersionConflictError()
            elif version is None:
                raise ConstructionRowVersionConflictError()
            else:
                require_expected_row_version(actual_row_version=before.row_version, expected_row_version=version)

            current, states = before.current_step, before.states_dict()
            if operation == "start":
                if not before.started:
                    current, states = STEPS[0], {item.value: Status.PENDING.value for item in STEPS}
            else:
                if not before.started:
                    raise ConstructionOnboardingStateConflictError("Onboarding has not started.")
                if operation == "move":
                    current = step
                else:
                    target_status = Status.SKIPPED if operation == "skip" else Status.COMPLETED
                    if states[step.value] != target_status.value and before.current_step is not step:
                        raise ConstructionOnboardingStateConflictError("Open this onboarding step first.")
                    if operation == "complete":
                        if self.verifier is None:
                            raise ConstructionOnboardingCompletionUnavailable("Canonical step verification is not configured.")
                        verified = await self.verifier.verify_step(actor=actor, step=step)
                        self._same_transaction(transaction)
                        await self._authorize(actor)
                        if verified is False:
                            raise ConstructionOnboardingStepNotReady("Saved step data is not ready.")
                        if verified is not True:
                            raise ConstructionOnboardingOperationError("Step verification response is invalid.")
                    if states[step.value] != target_status.value:
                        states[step.value] = target_status.value
                    if before.current_step is step:
                        current = STEPS[min(STEPS.index(step) + 1, len(STEPS) - 1)]

            current, normalized = _progress(current.value if current is not None else None, states)
            states = None if normalized is None else {key.value: value.value for key, value in normalized}
            changed = current != before.current_step or states != before.states_dict()
            if not changed:
                await self._authorize(actor)
                self._same_transaction(transaction)
                if manage_transaction:
                    await self.session.commit()
                return ConstructionOnboardingResult(before, False, False)
            if before.row_version == MAX_VERSION:
                raise ConstructionOnboardingOperationError("Settings version is exhausted.")
            values = dict(
                tenant_id=actor.tenant_id, current_step=current.value,
                step_states=states, as_of=self._now())
            created = before.row_version is None
            if created:
                saved = await self.repository.create_progress(**values)
            else:
                saved = await self.repository.update_progress(expected_row_version=before.row_version, **values)
            await self._authorize(actor)
            self._same_transaction(transaction)
            if saved is None:
                raise ConstructionRowVersionConflictError()
            result = self._state(saved, actor.tenant_id)
            if (result.row_version != (1 if created else before.row_version + 1)
                    or result.current_step != current or result.states_dict() != states
                    or not created and result.created_at != before.created_at):
                raise ConstructionOnboardingOperationError("Saved onboarding progress differs.")
            await self.audit.record_operation(
                actor=actor,
                event_type="construction_onboarding_started" if operation == "start" else "construction_onboarding_progress_updated",
                entity_type="construction_tenant_settings", entity_id=actor.tenant_id,
                operation="onboarding_" + operation, trace_id=trace_id,
                changes=tuple(ConstructionAuditChange(name, old, new) for name, old, new in (
                    ("onboarding_current_step", None if before.current_step is None else before.current_step.value, current.value),
                    ("onboarding_step_states", before.states_dict(), states),
                ) if old != new),
                details={
                    "row_version": result.row_version,
                    "previous_row_version": before.row_version,
                    "settings_created": created,
                    "step": None if step is None else step.value})
            await self._authorize(actor)
            self._same_transaction(transaction)
            if manage_transaction:
                await self.session.commit()
            return ConstructionOnboardingResult(result, True, created)


class ConstructionOnboardingSettingsVerifier:
    """Company data comes from Platform; branding overrides remain optional."""

    def __init__(self, *, session, company_service, pricing_verifier, localization_verifier=None, tax_verifier=None, template_verifier=None):
        if (session is None or getattr(company_service, "session", None) is not session
                or getattr(pricing_verifier, "session", None) is not session
                or not callable(getattr(company_service, "get_profile", None))
                or not callable(getattr(pricing_verifier, "verify_step", None))
                or any(not callable(getattr(session, name, None)) for name in (
                    "in_transaction", "get_transaction", "get_nested_transaction", "expire_all"))):
            raise ValueError("Canonical onboarding checks require the shared session.")
        self.session = session
        self.company = company_service
        self.pricing_verifier = pricing_verifier
        if localization_verifier is not None and (
                getattr(localization_verifier, "session", None) is not session
                or not callable(getattr(localization_verifier, "verify_step", None))):
            raise ValueError("Localization verification must bind the progress session.")
        self.localization_verifier = localization_verifier
        if tax_verifier is not None and (
                getattr(tax_verifier, "session", None) is not session
                or not callable(getattr(tax_verifier, "verify_step", None))):
            raise ValueError("Tax verification must bind the progress session.")
        self.tax_verifier = tax_verifier
        if template_verifier is not None and (
                getattr(template_verifier, "session", None) is not session
                or not callable(getattr(template_verifier, "verify_step", None))):
            raise ValueError("Template verification must bind the progress session.")
        self.template_verifier = template_verifier

    async def verify_step(self, *, actor, step):
        from services.platform_company_profiles import (
            CompanyProfileValidationError, normalize_company_profile_fields)
        from services.platform_company_profile_access import CompanyProfilePermissionDeniedError

        ConstructionOnboardingService._actor(actor)
        step = _step(step)
        if step is Step.MEASUREMENT_TEMPLATES and self.template_verifier is not None:
            return await self.template_verifier.verify_step(actor=actor, step=step)
        if step is Step.TAXES and self.tax_verifier is not None:
            return await self.tax_verifier.verify_step(actor=actor, step=step)
        if step is Step.LOCALIZATION and self.localization_verifier is not None:
            return await self.localization_verifier.verify_step(actor=actor, step=step)
        if step is not Step.COMPANY:
            return await self.pricing_verifier.verify_step(actor=actor, step=step)
        transaction = self.session.get_transaction()
        if (transaction is None or self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionOnboardingOperationError("A clean active outer transaction is required.")
        self.session.expire_all()
        try:
            profile = await self.company.get_profile(actor=actor)
        except CompanyProfilePermissionDeniedError:
            raise ConstructionPermissionDeniedError("Company profile is unavailable.") from None
        if (self.session.get_transaction() is not transaction
                or self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None):
            raise ConstructionOnboardingOperationError("Company verification changed the transaction.")
        if profile is None:
            return False
        if (not isinstance(getattr(profile, "id", None), UUID)
                or not isinstance(getattr(profile, "tenant_id", None), UUID)
                or profile.tenant_id != actor.tenant_id):
            raise ConstructionOnboardingOperationError("Canonical company context is invalid.")
        try:
            _version(getattr(profile, "row_version", None))
            normalize_company_profile_fields(
                legal_name=getattr(profile, "legal_name", None),
                email=getattr(profile, "email", None),
                website=getattr(profile, "website", None),
                phone=getattr(profile, "phone", None))
        except (CompanyProfileValidationError, ConstructionOnboardingValidationError):
            raise ConstructionOnboardingOperationError("Canonical company data is invalid.") from None
        # Logo, bank details, identifiers, signatory and Construction overrides
        # are not made mandatory by completing this guide step.
        return True


class ConstructionOnboardingPricingVerifier:
    """Verify step 4 through CON-017; other steps need their own canonical checks."""

    def __init__(self, *, session, pricing_service):
        if (session is None or getattr(pricing_service, "session", None) is not session
                or not callable(getattr(pricing_service, "list_profiles", None))
                or any(not callable(getattr(session, name, None)) for name in (
                    "in_transaction", "get_transaction", "get_nested_transaction"))):
            raise ValueError("Canonical pricing verification requires the shared session.")
        self.session, self.pricing = session, pricing_service

    async def verify_step(self, *, actor, step):
        from services.construction.construction_pricing_profiles import (
            ConstructionPricingProfileState, LEVELS)

        ConstructionOnboardingService._actor(actor)
        step = _step(step)
        if step is not Step.PRICING_PROFILES:
            raise ConstructionOnboardingCompletionUnavailable(
                "Canonical verification for this step is not configured.")
        transaction = self.session.get_transaction()
        if (transaction is None or self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None):
            raise ConstructionOnboardingOperationError("An active outer transaction is required.")
        profiles = await self.pricing.list_profiles(actor=actor)
        if (self.session.get_transaction() is not transaction
                or self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None):
            raise ConstructionOnboardingOperationError("Pricing verification changed the transaction.")
        # CON-017 validates stored Decimal values, versions and fresh read authority.
        if (not isinstance(profiles, tuple) or len(profiles) > len(LEVELS)
                or any(not isinstance(item, ConstructionPricingProfileState)
                       or item.tenant_id != actor.tenant_id for item in profiles)):
            raise ConstructionOnboardingOperationError("Canonical pricing context is invalid.")
        codes = tuple(item.code for item in profiles)
        if (any(code not in LEVELS for code in codes) or len(set(codes)) != len(codes)
                or codes != tuple(code for code in LEVELS if code in codes)
                or len({item.id for item in profiles}) != len(profiles)):
            raise ConstructionOnboardingOperationError("Canonical pricing levels are invalid.")
        return codes == LEVELS


def build_construction_onboarding_service(*, session, completion_verifier=None, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_onboarding import ConstructionOnboardingRepository
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    if completion_verifier is None:
        from services.construction.construction_onboarding_templates import build_construction_onboarding_template_verifier
        from services.construction.construction_onboarding_localization import ConstructionOnboardingLocalizationVerifier
        from services.construction.construction_onboarding_tax import (
            ConstructionOnboardingTaxVerifier, build_construction_onboarding_tax_service)
        from services.construction.construction_pricing_profiles import build_construction_pricing_profile_service
        from services.platform_company_profiles import build_tenant_company_profile_service
        completion_verifier = ConstructionOnboardingSettingsVerifier(
            session=session, company_service=build_tenant_company_profile_service(session),
            localization_verifier=ConstructionOnboardingLocalizationVerifier(session=session),
            tax_verifier=ConstructionOnboardingTaxVerifier(
                session=session,
                tax_service=build_construction_onboarding_tax_service(
                    session=session, now_provider=now_provider)),
            template_verifier=build_construction_onboarding_template_verifier(
                session=session, now_provider=now_provider),
            pricing_verifier=ConstructionOnboardingPricingVerifier(
                session=session, pricing_service=build_construction_pricing_profile_service(
                    session=session, now_provider=now_provider)))
    return ConstructionOnboardingService(
        session=session, repository=ConstructionOnboardingRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session),
        completion_verifier=completion_verifier, now_provider=now_provider)

# onboarding_explicit_tax_profile_v1
