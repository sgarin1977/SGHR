"""CON-015 collection read for existing organization tax profiles."""
from dataclasses import asdict
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.construction_tax_profile_cursors import (
    MAX_TAX_PROFILE_CURSOR_LENGTH, decode_tax_profile_cursor, encode_tax_profile_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_tax_profiles import TaxProfileReadData
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileCursor, ConstructionTaxProfilePage, ConstructionTaxProfileState,
    ConstructionTaxProfileValidationError, build_construction_tax_profile_service,
)


class TaxProfileListPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: StrictStr | None = Field(max_length=MAX_TAX_PROFILE_CURSOR_LENGTH)


class TaxProfileListMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: TaxProfileListPagination


class TaxProfileListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[TaxProfileReadData]
    meta: TaxProfileListMeta
    request_id: StrictStr = Field(min_length=1)


def get_tax_profile_list_service(session=Depends(get_construction_scoped_session)):
    return build_construction_tax_profile_service(session=session)


def public_page(result, *, actor, limit, after):
    if (not isinstance(result, ConstructionTaxProfilePage) or not isinstance(result.items, tuple)
            or len(result.items) > limit):
        raise ValueError("Tax profile page type differs.")
    previous = None if after is None else after.after_id
    data = []
    for state in result.items:
        if (not isinstance(state, ConstructionTaxProfileState) or not isinstance(state.id, UUID)
                or state.tenant_id != actor.tenant_id):
            raise ValueError("Tax profile page context differs.")
        item = TaxProfileReadData.model_validate(asdict(state))
        if previous is not None and item.id <= previous:
            raise ValueError("Tax profile page order differs.")
        previous = item.id
        data.append(item.model_dump(mode="json"))
    cursor = result.next_cursor
    if cursor is not None:
        if (not isinstance(cursor, ConstructionTaxProfileCursor) or len(data) != limit or not data
                or cursor.after_id != result.items[-1].id):
            raise ValueError("Tax profile continuation differs.")
    try:
        encoded = encode_tax_profile_cursor(cursor, tenant_id=actor.tenant_id, user_id=actor.user_id)
    except Exception:
        raise ValueError("Tax profile continuation context differs.") from None
    return data, {"pagination": {"next_cursor": encoded}}


router = ConstructionAPIRouter(
    prefix="/construction/settings/tax-profiles",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)
read_permission = require_construction_permission("construction.tax.read")


@router.get(
    "", response_model=TaxProfileListEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "Platform authentication required"},
        403: {"description": "Membership, module, tax permission or tenant scope unavailable"},
        422: {"description": "Invalid limit, cursor or unsupported query selectors"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "Sanitized collection read failure"},
    },
)
async def list_tax_profiles(
    request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_TAX_PROFILE_CURSOR_LENGTH),
    actor=Depends(get_current_actor), service=Depends(get_tax_profile_list_service),
):
    try:
        if (set(request.query_params) - {"limit", "cursor"}
                or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)):
            raise ConstructionTaxProfileValidationError("Invalid tax profile list query.")
        after = decode_tax_profile_cursor(cursor, tenant_id=actor.tenant_id, user_id=actor.user_id)
        result = await service.list_profiles(actor=actor, limit=limit, after=after)
        data, meta = public_page(result, actor=actor, limit=limit, after=after)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden",
                           message="Organization tax profiles are unavailable.") from None
    except ConstructionTaxProfileValidationError:
        raise ApiHttpError(status_code=422, code="validation_error",
                           message="Tax profile list query is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error",
                           message="Tax profile list read failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)
