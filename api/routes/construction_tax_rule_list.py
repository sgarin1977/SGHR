"""CON-016 bounded profile-rule collection on the shared Construction API."""
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.construction_tax_rule_cursors import (
    MAX_TAX_RULE_CURSOR_LENGTH, decode_tax_rule_cursor, encode_tax_rule_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_tax_rules import TaxRuleReadData
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleCursor, ConstructionTaxRulePage, ConstructionTaxRuleState,
    ConstructionTaxRuleValidationError, ConstructionTaxRuleNotFoundError,
    build_construction_tax_rule_service,
)


class TaxRuleListPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: StrictStr | None = Field(max_length=MAX_TAX_RULE_CURSOR_LENGTH)


class TaxRuleListMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: TaxRuleListPagination


class TaxRuleListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[TaxRuleReadData]
    meta: TaxRuleListMeta
    request_id: StrictStr = Field(min_length=1)


def get_tax_rule_list_service(session=Depends(get_construction_scoped_session)):
    return build_construction_tax_rule_service(session=session)


def public_page(result, *, actor, profile_id, limit, after):
    if (not isinstance(result, ConstructionTaxRulePage) or not isinstance(result.items, tuple)
            or len(result.items) > limit):
        raise ValueError("Tax rule page type differs.")
    previous = None if after is None else after.after_id
    data = []
    for state in result.items:
        if (not isinstance(state, ConstructionTaxRuleState) or not isinstance(state.id, UUID)
                or state.tenant_id != actor.tenant_id or state.tax_profile_id != profile_id
                or not isinstance(state.rate_percent, Decimal)
                or (previous is not None and state.id <= previous)):
            raise ValueError("Tax rule page context or order differs.")
        data.append(TaxRuleReadData.model_validate(asdict(state)).model_dump(mode="json"))
        previous = state.id
    cursor = result.next_cursor
    if cursor is not None:
        if (not isinstance(cursor, ConstructionTaxRuleCursor) or len(data) != limit
                or not data or cursor.after_id != result.items[-1].id):
            raise ValueError("Tax rule continuation differs.")
    try:
        encoded = encode_tax_rule_cursor(
            cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, profile_id=profile_id,
        )
    except Exception:
        raise ValueError("Tax rule continuation context differs.") from None
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
    "/{profile_id}/rules", response_model=TaxRuleListEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: profile unavailable in the authenticated organization"},
        422: {"description": "validation_error: invalid query, profile or cursor context"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation or response failure"},
    },
)
async def list_tax_rules(
    profile_id: UUID, request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_TAX_RULE_CURSOR_LENGTH),
    actor=Depends(get_current_actor), service=Depends(get_tax_rule_list_service),
):
    try:
        if (set(request.query_params) - {"limit", "cursor"}
                or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)):
            raise ConstructionTaxRuleValidationError("Invalid tax rule list query.")
        after = decode_tax_rule_cursor(
            cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, profile_id=profile_id,
        )
        result = await service.list_rules(actor=actor, profile_id=profile_id, limit=limit, after=after)
        data, meta = public_page(result, actor=actor, profile_id=profile_id, limit=limit, after=after)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden",
                           message="Organization tax rule access is unavailable.") from None
    except ConstructionTaxRuleNotFoundError:
        raise ApiHttpError(status_code=404, code="not_found", message="Tax profile is unavailable.") from None
    except ConstructionTaxRuleValidationError:
        raise ApiHttpError(status_code=422, code="validation_error", message="Tax rule list query is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error", message="Tax rule list read failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)
