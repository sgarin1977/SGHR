"""CON-013 neutral unit catalog; no unit settings mutation or conversion."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from api.auth import get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from services.construction.construction_unit_catalog import (
    ConstructionUnitCatalogContext, ConstructionUnitCatalogDenied,
    ConstructionUnitCatalogOperationError, ConstructionUnitCatalogLanguageError,
    ConstructionUnitCatalogTranslationError, build_construction_unit_catalog_service,
)
from services.construction.construction_units import UnitCode, UnitDimension


class UnitOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: UnitCode
    dimension: UnitDimension
    symbol: StrictStr | None
    label_key: StrictStr
    label: StrictStr = Field(min_length=1, max_length=120)
    label_status: Literal["provided", "cached", "translated"]


class UnitCatalogData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    catalog_version: Literal[1] = 1
    language_code: StrictStr = Field(min_length=1, max_length=10)
    label_source_version: StrictInt = Field(ge=1)
    units: list[UnitOptionResponse]


class UnitCatalogEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: UnitCatalogData
    meta: dict
    request_id: StrictStr


def get_unit_catalog_service(platform_session=Depends(get_api_session)):
    return build_construction_unit_catalog_service(platform_session=platform_session)


router = APIRouter(
    prefix="/construction/settings",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_authenticated_user_rate_limit()),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "Platform authentication required"},
        403: {"description": "Active membership and enabled Suite/Core required"},
        409: {"description": "LANGUAGE_CONFIGURATION_REQUIRED or LANGUAGE_CONTEXT_CHANGED"},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
        422: {"description": "Query selectors are not accepted"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "Sanitized read failure"},
    },
)


@router.get("/units", response_model=UnitCatalogEnvelope)
async def read_unit_catalog(
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_unit_catalog_service),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Unit catalog accepts no query selectors.",
        )
    try:
        result = await service.read_for_actor(actor=actor)
        if (
            not isinstance(result, ConstructionUnitCatalogContext)
            or result.tenant_id != actor.tenant_id
        ):
            raise ConstructionUnitCatalogOperationError("Unit catalog response context differs.")
        labels = {label.key: label for label in result.labels}
        if len(labels) != len(result.units) or set(labels) != {unit.code.value for unit in result.units}:
            raise ConstructionUnitCatalogOperationError("Unit labels are incomplete.")
        data = UnitCatalogData(
            tenant_id=result.tenant_id, language_code=result.language_code,
            label_source_version=result.source_version,
            units=[
                UnitOptionResponse(
                    code=unit.code, dimension=unit.dimension,
                    symbol=unit.symbol, label_key=unit.label_key,
                    label=labels[unit.code.value].text,
                    label_status=labels[unit.code.value].status,
                )
                for unit in result.units
            ],
        )
    except ConstructionUnitCatalogDenied:
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Unit catalog is unavailable.",
        ) from None
    except ConstructionUnitCatalogLanguageError as error:
        raise ApiHttpError(
            status_code=409, code=error.code, message="Unit catalog language must be refreshed.",
        ) from None
    except ConstructionUnitCatalogTranslationError:
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Unit catalog translation is temporarily unavailable.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Unit catalog read failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id,
    )
