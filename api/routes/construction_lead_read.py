"""Approved CON-C009 lead reads; identity and tenant come from Platform context."""
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr

from api.auth import get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite
from api.construction_lead_cursors import MAX_LEAD_LIST_CURSOR_LENGTH, decode_lead_list_cursor, encode_lead_list_cursor
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context, get_construction_scoped_session,
    require_construction_permission,
)
from api.routes.construction_lead_status import LeadPipelineStatus
from services.construction.construction_lead_list_read import (
    ConstructionLeadListFilters, ConstructionLeadListReadPage, build_construction_lead_list_read_service,
)
from services.construction.construction_lead_read import (
    LEAD_READ_PERMISSION, PUBLIC_LEAD_DETAIL_FIELDS, ConstructionLeadReadNotFoundError,
    ConstructionLeadReadResponse, ConstructionLeadReadService, build_construction_lead_read_service,
)
from services.construction.construction_lead_rules import ConstructionLeadValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class LeadReadEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: ConstructionLeadReadResponse
    meta: dict
    request_id: StrictStr


class LeadListPagination(BaseModel):
    model_config = ConfigDict(extra='forbid')
    next_cursor: StrictStr | None


class LeadListMeta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pagination: LeadListPagination


class LeadListEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: list[ConstructionLeadReadResponse]
    meta: LeadListMeta
    request_id: StrictStr


def get_lead_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_lead_read_service(session=session)


def get_lead_list_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_lead_list_read_service(session=session)


def _query(request, allowed):
    if (set(request.query_params) - allowed
            or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)):
        raise ConstructionLeadValidationError('Lead query parameters are invalid.')


def public_lead(result, *, actor, access_context, lead_id=None):
    if not isinstance(result, dict) or set(result) != PUBLIC_LEAD_DETAIL_FIELDS:
        raise ValueError('Lead response fields are invalid.')
    model = ConstructionLeadReadResponse.model_validate(result)
    if model.tenant_id != actor.tenant_id or (lead_id is not None and model.id != lead_id):
        raise ValueError('Lead response context is invalid.')
    if model.next_action is not None and (
            model.next_action.tenant_id != actor.tenant_id or model.next_action.lead_id != model.id):
        raise ValueError('Lead next-action context is invalid.')
    ConstructionLeadReadService._authorize(actor, access_context)
    ConstructionLeadReadService._project_access(actor, access_context, model, model.next_action)
    return model


def public_lead_page(result, *, actor, access_context, filters, limit, after):
    if (not isinstance(result, ConstructionLeadListReadPage) or not isinstance(result.items, tuple)
            or len(result.items) > limit):
        raise ValueError('Lead page is invalid.')
    data, seen = [], set()
    previous = (after.updated_at, after.lead_id) if after is not None else None
    last = None
    for item in result.items:
        model = public_lead(item, actor=actor, access_context=access_context)
        key = model.updated_at, model.id
        if (model.id in seen or (previous is not None and key >= previous)
                or any(value is not None and getattr(model, name) != value for name, value in filters.values().items())):
            raise ValueError('Lead page order or filters are invalid.')
        seen.add(model.id)
        previous, last = key, model
        data.append(model.model_dump(mode='json'))
    if result.next_cursor is not None:
        if (len(data) != limit or last is None
                or (result.next_cursor.updated_at, result.next_cursor.lead_id) != (last.updated_at, last.id)):
            raise ValueError('Lead page cursor does not match its last item.')
    encoded = encode_lead_list_cursor(result.next_cursor,
        tenant_id=actor.tenant_id, user_id=actor.user_id, filters=filters)
    return data, {'pagination': {'next_cursor': encoded}}


def _failure(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(status_code=403, code='forbidden', message='Construction lead access is not available.')
    if isinstance(error, ConstructionLeadReadNotFoundError):
        return ApiHttpError(status_code=404, code='not_found', message='Construction lead is not available.')
    if isinstance(error, ConstructionLeadValidationError):
        return ApiHttpError(status_code=422, code='validation_error', message='Lead query is invalid.')
    return ApiHttpError(status_code=500, code='internal_error', message='Construction lead read failed.')


read_permission = require_construction_permission(LEAD_READ_PERMISSION)
actor_rate_limit = require_authenticated_user_rate_limit()
router = ConstructionAPIRouter(prefix='/construction/crm/leads', dependencies=[
    Depends(get_current_actor), Depends(require_suite('construction', error_code='MODULE_DISABLED')),
    Depends(require_module('construction.core', error_code='MODULE_DISABLED')),
])
READ_ERRORS = {
    401: {'description': 'Authentication required or invalid token'},
    403: {'description': 'Module disabled or lead read access unavailable'},
    422: {'description': 'Invalid query parameters'}, 429: {'description': 'Rate limit exceeded'},
    500: {'description': 'Lead read failed'},
}


@router.get('', response_model=LeadListEnvelope,
    dependencies=[Depends(read_permission), Depends(actor_rate_limit)], responses=READ_ERRORS)
async def list_leads(request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_LEAD_LIST_CURSOR_LENGTH),
    status: LeadPipelineStatus | None = Query(default=None), source: str | None = Query(default=None),
    responsible_user_id: UUID | None = Query(default=None), converted_client_id: UUID | None = Query(default=None),
    converted_project_id: UUID | None = Query(default=None),
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    service=Depends(get_lead_list_read_service)):
    try:
        _query(request, {'limit', 'cursor', 'status', 'source', 'responsible_user_id', 'converted_client_id', 'converted_project_id'})
        filters = ConstructionLeadListFilters(status, source, responsible_user_id, converted_client_id, converted_project_id)
        after = decode_lead_list_cursor(cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, filters=filters)
        result = await service.list_leads(actor=actor, access_context=access_context, filters=filters, limit=limit, after=after)
    except Exception as error:
        raise _failure(error) from None
    try:
        data, meta = public_lead_page(result, actor=actor, access_context=access_context, filters=filters, limit=limit, after=after)
    except Exception:
        raise _failure(None) from None
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)


@router.get('/{id}', response_model=LeadReadEnvelope,
    dependencies=[Depends(read_permission), Depends(actor_rate_limit)],
    responses={**READ_ERRORS, 404: {'description': 'Lead unavailable in the authorized tenant'}})
async def read_lead(id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    service=Depends(get_lead_read_service)):
    try:
        _query(request, set())
        result = await service.get_lead(actor=actor, access_context=access_context, lead_id=id)
    except Exception as error:
        raise _failure(error) from None
    try:
        data = public_lead(result, actor=actor, access_context=access_context, lead_id=id).model_dump(mode='json')
    except Exception:
        raise _failure(None) from None
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, request_id=request.state.request_id)
