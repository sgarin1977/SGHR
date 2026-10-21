from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from api.auth import get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context, get_construction_scoped_session,
    require_construction_permission,
)
from services.construction_address_matching import ConstructionAddressSuggestion
from services.construction_address_suggestions import (
    ConstructionAddressSuggestionsService, ConstructionAddressSuggestionValidationError,
    build_construction_address_suggestions_service,
)
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


# Transport resource bound only; saved address_raw is not restricted by this.
MAX_ADDRESS_QUERY_LENGTH = 4096


class AddressSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entity_type: Literal['client', 'project']
    entity_id: UUID
    label: StrictStr
    address_raw: StrictStr
    match_kind: Literal['exact', 'prefix_token', 'fuzzy']
    score: StrictInt = Field(ge=0, le=1000)


class AddressSuggestionsEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: list[AddressSuggestionResponse] = Field(max_length=10)
    meta: dict[str, Any]
    request_id: str


def get_address_suggestions_service(session=Depends(get_construction_scoped_session)):
    return build_construction_address_suggestions_service(session=session)


def public_address_suggestions(result, *, actor, access_context, limit):
    if not isinstance(result, tuple) or len(result) > limit or len(result) > 10:
        raise ValueError('Address suggestion result is invalid.')
    allowed = ConstructionAddressSuggestionsService._scope(actor, access_context)
    seen, public = set(), []
    fields = AddressSuggestionResponse.model_fields
    for item in result:
        if not isinstance(item, ConstructionAddressSuggestion):
            raise ValueError('Address suggestion item is invalid.')
        checked = AddressSuggestionResponse.model_validate({field: getattr(item, field) for field in fields})
        identity = checked.entity_type, checked.entity_id
        if (identity in seen or (checked.match_kind == 'exact' and checked.score != 1000)
                or (allowed is not None and (checked.entity_type != 'project' or checked.entity_id not in allowed))):
            raise ValueError('Address suggestion context is invalid.')
        seen.add(identity)
        public.append(checked.model_dump(mode='json'))
    return public


read_permission = require_construction_permission('construction.projects.read')
actor_rate_limit = require_authenticated_user_rate_limit()
router = ConstructionAPIRouter(
    prefix='/construction',
    dependencies=[Depends(get_current_actor),
        Depends(require_suite('construction', error_code='MODULE_DISABLED')),
        Depends(require_module('construction.core', error_code='MODULE_DISABLED'))],
)


@router.get('/address-suggestions', response_model=AddressSuggestionsEnvelope,
    dependencies=[Depends(read_permission), Depends(actor_rate_limit)],
    responses={401: {'description': 'Authentication required or invalid token'},
        403: {'description': 'Module disabled or project read access unavailable'},
        422: {'description': 'Invalid query parameters'}, 429: {'description': 'Rate limit exceeded'},
        500: {'description': 'Address suggestion read failed'}})
async def suggest_addresses(request: Request, response: Response,
    q: str = Query(default='', max_length=MAX_ADDRESS_QUERY_LENGTH),
    limit: int = Query(default=10, ge=1, le=10),
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    service=Depends(get_address_suggestions_service)):
    try:
        # Tenant/user/project scope comes only from trusted server dependencies.
        # Reject unknown or repeated fields instead of accepting misleading
        # tenant_id, user_id or project_ids parameters from the caller.
        if (set(request.query_params) - {'q', 'limit'}
                or any(len(request.query_params.getlist(name)) > 1 for name in ('q', 'limit'))):
            raise ConstructionAddressSuggestionValidationError('Address query parameters are invalid.')
        result = await service.suggest_addresses(actor=actor, access_context=access_context, query=q, limit=limit)
        data = public_address_suggestions(result, actor=actor, access_context=access_context, limit=limit)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code='forbidden', message='Address suggestion access is unavailable.') from None
    except ConstructionAddressSuggestionValidationError:
        raise ApiHttpError(status_code=422, code='validation_error', message='Address query is invalid.') from None
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Address suggestion read failed.') from None
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, request_id=request.state.request_id)
