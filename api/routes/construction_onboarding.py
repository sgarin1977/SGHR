"""Versioned CON-O001/O004 metadata over the shared Construction API."""
from dataclasses import asdict
from datetime import timezone
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr,
    field_validator, model_validator,
)

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session,
    require_construction_permission,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingService as ProgressService,
    ConstructionOnboardingState as ProgressState,
    ConstructionOnboardingResult as ProgressResult,
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingStateConflictError as StateConflict,
    ConstructionOnboardingStepNotReady as NotReady,
    ConstructionOnboardingCompletionUnavailable as Unavailable,
    build_construction_onboarding_service,
    _progress,
)
from services.construction.construction_onboarding_tax import (
    ConstructionOnboardingTaxService as TaxService,
    ConstructionOnboardingTaxSelection as TaxSelection,
    ConstructionOnboardingTaxSelectionResult as TaxResult,
    ConstructionOnboardingTaxProfileUnavailable as ProfileUnavailable,
    build_construction_onboarding_tax_service,
)

OrdinaryStatus = Literal["PENDING", "COMPLETED"]
OptionalStatus = Literal["PENDING", "COMPLETED", "SKIPPED"]
SkippableStep = Literal["WORK_PRICES", "MATERIALS_SUPPLIERS"]


class OnboardingStepStates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    COMPANY: OrdinaryStatus
    LOCALIZATION: OrdinaryStatus
    TAXES: OrdinaryStatus
    PRICING_PROFILES: OrdinaryStatus
    WORK_PRICES: OptionalStatus
    MATERIALS_SUPPLIERS: OptionalStatus
    MEASUREMENT_TEMPLATES: OrdinaryStatus


class OnboardingStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: SettingsVersion | None = Field(
        description="Required. Null explicitly expects no settings row; otherwise use its current version.")


class OnboardingVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: SettingsVersion


class OnboardingTaxSelectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: SettingsVersion
    tax_profile_id: UUID


class OnboardingProgressData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    row_version: SettingsVersion | None
    current_step: Step | None
    step_states: OnboardingStepStates | None
    created_at: AwareDatetime | None
    updated_at: AwareDatetime | None
    started: StrictBool
    is_complete: StrictBool = Field(
        description="Guide progress only; not commercial readiness or additional authority.")

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return None if value is None else value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def coherent_progress(self):
        states = (
            None if self.step_states is None
            else self.step_states.model_dump(mode="json")
        )
        current, normalized = _progress(
            None if self.current_step is None else self.current_step.value,
            states)
        started = current is not None
        complete = started and all(
            value.value in ("COMPLETED", "SKIPPED")
            for _, value in normalized)
        if self.started != started or self.is_complete != complete:
            raise ValueError("Progress flags differ.")
        if self.row_version is None:
            if started or self.created_at is not None or self.updated_at is not None:
                raise ValueError("Missing settings must have no persisted progress or timestamps.")
        elif self.created_at is None or self.updated_at is None:
            raise ValueError("Persisted settings require timestamps.")
        return self


class OnboardingProgressWriteData(OnboardingProgressData):
    row_version: SettingsVersion
    changed: StrictBool
    settings_created: StrictBool


class OnboardingProgressEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OnboardingProgressData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class OnboardingProgressWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OnboardingProgressWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class OnboardingTaxSelectionData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    row_version: SettingsVersion
    onboarding_tax_profile_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class OnboardingTaxSelectionWriteData(OnboardingTaxSelectionData):
    changed: StrictBool


class OnboardingTaxSelectionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OnboardingTaxSelectionData | None
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class OnboardingTaxSelectionWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OnboardingTaxSelectionWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def get_onboarding_service(session=Depends(get_construction_scoped_session)):
    return build_construction_onboarding_service(session=session)


def get_onboarding_tax_service(session=Depends(get_construction_scoped_session)):
    return build_construction_onboarding_tax_service(session=session)


def no_query(request: Request):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Onboarding accepts no query selectors.")


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def operation_error(error):
    if isinstance(error, ConstructionRowVersionConflictError):
        return ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT",
            message="Organization settings changed. Reload before continuing.")
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization onboarding access is unavailable.")
    if isinstance(error, ProfileUnavailable):
        return ApiHttpError(
            status_code=404, code="not_found",
            message="Selected tax profile is unavailable.")
    if isinstance(error, NotReady):
        return ApiHttpError(
            status_code=409, code="ONBOARDING_STEP_NOT_READY",
            message="Save the required step data before completing it.")
    if isinstance(error, Unavailable):
        return ApiHttpError(
            status_code=409, code="ONBOARDING_STEP_UNAVAILABLE",
            message="Canonical verification for this step is unavailable.")
    if isinstance(error, StateConflict):
        return ApiHttpError(
            status_code=409, code="ONBOARDING_STATE_CONFLICT",
            message="Onboarding state does not allow this operation.")
    if isinstance(error, Validation):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Onboarding request is invalid.")
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Onboarding operation failed.")


def public_progress(state, actor):
    if not isinstance(state, ProgressState) or state.tenant_id != actor.tenant_id:
        raise ValueError("Onboarding response context differs.")
    if state.row_version is None:
        expected = ProgressState(actor.tenant_id, None, None, None, None, None)
    else:
        expected = ProgressService._state({
            "tenant_id": state.tenant_id,
            "row_version": state.row_version,
            "onboarding_current_step": (
                None if state.current_step is None else state.current_step.value),
            "onboarding_step_states": state.states_dict(),
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }, actor.tenant_id)
    if state != expected:
        raise ValueError("Onboarding response state differs.")
    return OnboardingProgressData(
        tenant_id=state.tenant_id, row_version=state.row_version,
        current_step=state.current_step, step_states=state.states_dict(),
        created_at=state.created_at, updated_at=state.updated_at,
        started=state.started, is_complete=state.is_complete)


def public_selection(state, actor):
    if not isinstance(state, TaxSelection):
        raise ValueError("Tax selection response type differs.")
    fields = asdict(state)
    TaxService._state(fields, actor.tenant_id)
    return OnboardingTaxSelectionData.model_validate(fields)


router = ConstructionAPIRouter(
    prefix="/construction/settings/onboarding",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
        Depends(no_query),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: selected tax profile is unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT, ONBOARDING_STATE_CONFLICT, ONBOARDING_STEP_NOT_READY or ONBOARDING_STEP_UNAVAILABLE"},
        422: {"description": "validation_error: explicit version, valid step and approved fields required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation failure"},
    },
)
permission = require_construction_permission("construction.settings.manage")


@router.get("", response_model=OnboardingProgressEnvelope,
            dependencies=[Depends(permission)])
async def read_onboarding(
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_onboarding_service),
):
    try:
        data = public_progress(await service.read_progress(actor=actor), actor)
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


async def progress_write(
    *, request, response, actor, service, body, action, step=None,
):
    try:
        arguments = dict(
            actor=actor, expected_row_version=body.expected_row_version,
            trace_id=request.state.request_id)
        if action == "start":
            result = await service.start_progress(**arguments)
        else:
            operation = {
                "open": service.move_to_step,
                "skip": service.skip_step,
                "complete": service.complete_step,
            }[action]
            result = await operation(step=step, **arguments)
        if (
            not isinstance(result, ProgressResult)
            or type(result.changed) is not bool
            or type(result.settings_created) is not bool
        ):
            raise ValueError("Onboarding write response differs.")
        data = public_progress(result.progress, actor)
        if result.settings_created:
            if action != "start" or body.expected_row_version is not None or not result.changed:
                raise ValueError("Onboarding creation context differs.")
            expected = 1
        else:
            if body.expected_row_version is None:
                raise ValueError("Onboarding existing version is missing.")
            expected = body.expected_row_version + int(result.changed)
        if data.row_version != expected:
            raise ValueError("Onboarding write version differs.")
        data = OnboardingProgressWriteData.model_validate(
            data.model_dump() | {
                "changed": result.changed,
                "settings_created": result.settings_created,
            })
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.post("/start", response_model=OnboardingProgressWriteEnvelope,
             dependencies=[Depends(permission)])
async def start_onboarding(
    body: OnboardingStartRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_onboarding_service),
):
    return await progress_write(
        request=request, response=response, actor=actor,
        service=service, body=body, action="start")


@router.post("/steps/{step}/open", response_model=OnboardingProgressWriteEnvelope,
             dependencies=[Depends(permission)])
async def open_onboarding_step(
    step: Step, body: OnboardingVersionRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_onboarding_service),
):
    return await progress_write(
        request=request, response=response, actor=actor,
        service=service, body=body, action="open", step=step)


@router.post("/steps/{step}/skip", response_model=OnboardingProgressWriteEnvelope,
             dependencies=[Depends(permission)])
async def skip_onboarding_step(
    step: SkippableStep, body: OnboardingVersionRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_onboarding_service),
):
    return await progress_write(
        request=request, response=response, actor=actor,
        service=service, body=body, action="skip", step=step)


@router.post("/steps/{step}/complete", response_model=OnboardingProgressWriteEnvelope,
             dependencies=[Depends(permission)])
async def complete_onboarding_step(
    step: Step, body: OnboardingVersionRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_onboarding_service),
):
    return await progress_write(
        request=request, response=response, actor=actor,
        service=service, body=body, action="complete", step=step)


@router.get("/tax-profile", response_model=OnboardingTaxSelectionEnvelope,
            dependencies=[Depends(permission)])
async def read_onboarding_tax_profile(
    request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_onboarding_tax_service),
):
    try:
        state = await service.read_selection(actor=actor)
        data = None if state is None else public_selection(state, actor).model_dump(mode="json")
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(data=data, request_id=request.state.request_id)


@router.patch("/tax-profile", response_model=OnboardingTaxSelectionWriteEnvelope,
              dependencies=[Depends(permission)])
async def select_onboarding_tax_profile(
    body: OnboardingTaxSelectionUpdate,
    request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_onboarding_tax_service),
):
    try:
        result = await service.select_profile(
            actor=actor, tax_profile_id=body.tax_profile_id,
            expected_row_version=body.expected_row_version,
            trace_id=request.state.request_id)
        if not isinstance(result, TaxResult) or type(result.changed) is not bool:
            raise ValueError("Tax selection result differs.")
        data = public_selection(result.selection, actor)
        if (
            data.onboarding_tax_profile_id != body.tax_profile_id
            or result.profile.id != body.tax_profile_id
            or result.profile.tenant_id != actor.tenant_id
            or data.row_version != body.expected_row_version + int(result.changed)
        ):
            raise ValueError("Tax selection response context differs.")
        data = OnboardingTaxSelectionWriteData.model_validate(
            data.model_dump() | {"changed": result.changed})
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)
