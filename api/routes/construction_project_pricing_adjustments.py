"""CON-018 project-scoped pricing adjustment reads on the shared API."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_project_pricing_adjustment_cursors import (
    MAX_ADJUSTMENT_CURSOR_LENGTH, decode_adjustment_cursor, encode_adjustment_cursor)
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentState as State,
    ConstructionProjectPricingAdjustmentCursor as Cursor,
    ConstructionProjectPricingAdjustmentPage as Page,
    ConstructionProjectPricingAdjustmentValidationError as ValidationError,
    ConstructionProjectPricingAdjustmentNotFoundError as NotFoundError,
    build_construction_project_pricing_adjustment_service)


class ProjectPricingAdjustmentReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    project_id: UUID
    name: StrictStr = Field(min_length=1, max_length=255)
    coefficient: Decimal = Field(
        gt=0, allow_inf_nan=False,
        description="Exact positive commercial coefficient, serialized as a decimal string.")
    enabled: StrictBool
    row_version: SettingsVersion
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value):
        if value != value.strip() or "\x00" in value:
            raise ValueError("Canonical adjustment name required.")
        return value

    @field_validator("coefficient", mode="before")
    @classmethod
    def exact_decimal(cls, value):
        if not isinstance(value, (Decimal, str)):
            raise ValueError("An exact Decimal or decimal string is required.")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class ProjectPricingAdjustmentReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ProjectPricingAdjustmentReadData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class ProjectPricingAdjustmentPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: StrictStr | None = Field(max_length=MAX_ADJUSTMENT_CURSOR_LENGTH)


class ProjectPricingAdjustmentListMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: ProjectPricingAdjustmentPagination


class ProjectPricingAdjustmentListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[ProjectPricingAdjustmentReadData]
    meta: ProjectPricingAdjustmentListMeta
    request_id: StrictStr = Field(min_length=1)


def get_adjustment_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_project_pricing_adjustment_service(session=session)


def public_adjustment(state, *, actor, project_id, adjustment_id=None):
    if (not isinstance(state, State)
            or not all(isinstance(getattr(state, name), UUID) for name in ("id", "tenant_id", "project_id", "created_by"))
            or state.tenant_id != actor.tenant_id or state.project_id != project_id
            or (adjustment_id is not None and state.id != adjustment_id)
            or not isinstance(state.coefficient, Decimal)
            or not all(isinstance(getattr(state, name), datetime) for name in ("created_at", "updated_at"))):
        raise ValueError("Adjustment response context differs.")
    return ProjectPricingAdjustmentReadData.model_validate(asdict(state)).model_dump(mode="json")


def public_page(result, *, actor, project_id, limit, after):
    if not isinstance(result, Page) or not isinstance(result.items, tuple) or len(result.items) > limit:
        raise ValueError("Adjustment page type differs.")
    previous = None if after is None else after.after_id
    data = []
    for state in result.items:
        projected = public_adjustment(state, actor=actor, project_id=project_id)
        if previous is not None and state.id <= previous:
            raise ValueError("Adjustment page order differs.")
        data.append(projected)
        previous = state.id
    cursor = result.next_cursor
    if cursor is not None and (
            not isinstance(cursor, Cursor) or len(data) != limit or not data or cursor.after_id != result.items[-1].id):
        raise ValueError("Adjustment continuation differs.")
    try:
        encoded = encode_adjustment_cursor(cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, project_id=project_id)
    except Exception:
        raise ValueError("Adjustment continuation context differs.") from None
    return data, {"pagination": {"next_cursor": encoded}}


def read_error(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(status_code=403, code="forbidden", message="Project pricing access is unavailable.")
    if isinstance(error, NotFoundError):
        return ApiHttpError(status_code=404, code="not_found", message="Project or pricing adjustment is unavailable.")
    if isinstance(error, ValidationError):
        return ApiHttpError(status_code=422, code="validation_error", message="Adjustment read request is invalid.")
    return ApiHttpError(status_code=500, code="internal_error", message="Pricing adjustment read failed.")


def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


router = ConstructionAPIRouter(
    prefix="/construction/projects/{project_id}/pricing-adjustments",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: project or adjustment unavailable in the current scope"},
        422: {"description": "validation_error: invalid UUID, query or cursor context"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation or response failure"},
    })
read_permission = require_construction_permission("construction.pricing.read")


@router.get("", response_model=ProjectPricingAdjustmentListEnvelope, dependencies=[Depends(read_permission)])
async def list_project_pricing_adjustments(
    project_id: UUID, request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_ADJUSTMENT_CURSOR_LENGTH),
    actor=Depends(get_current_actor), service=Depends(get_adjustment_read_service),
):
    try:
        if (set(request.query_params) - {"limit", "cursor"}
                or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)):
            raise ValidationError("Invalid adjustment query.")
        after = decode_adjustment_cursor(cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, project_id=project_id)
        result = await service.list_adjustments(actor=actor, project_id=project_id, limit=limit, after=after)
        data, meta = public_page(result, actor=actor, project_id=project_id, limit=limit, after=after)
    except Exception as error:
        raise read_error(error) from None
    private_response(response)
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)


@router.get("/{adjustment_id}", response_model=ProjectPricingAdjustmentReadEnvelope, dependencies=[Depends(read_permission)])
async def read_project_pricing_adjustment(
    project_id: UUID, adjustment_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_adjustment_read_service),
):
    if request.query_params:
        raise ApiHttpError(status_code=422, code="validation_error", message="Adjustment detail accepts no query selectors.")
    try:
        state = await service.get_adjustment(actor=actor, project_id=project_id, adjustment_id=adjustment_id)
        data = public_adjustment(state, actor=actor, project_id=project_id, adjustment_id=adjustment_id)
    except Exception as error:
        raise read_error(error) from None
    private_response(response)
    return success_envelope(data=data, request_id=request.state.request_id)
