"""Current own-link version without exposing client card or tenant data."""
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from api.auth import invalid_token_error
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from database.construction_client_unlink_context import construction_client_unlink_lookup_transaction
from services.api_identity import ApiIdentityAccessError
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction_client_unlink import ConstructionClientUnlinkDeniedError
from services.construction_client_own_link_state import build_construction_client_own_link_state_service

class ClientOwnLinkStateResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: UUID
    linked_user_id: UUID
    row_version: StrictInt = Field(ge=1, le=2**63-1)

class ClientOwnLinkStateEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: ClientOwnLinkStateResponse
    meta: dict
    request_id: str

async def get_own_link_state_session(id: UUID, principal=Depends(get_authenticated_platform_principal)):
    async with construction_client_unlink_lookup_transaction(user_id=principal.user_id, client_id=id) as session:
        yield session

def get_own_link_state_service(session=Depends(get_own_link_state_session), platform_session=Depends(get_api_session)):
    return build_construction_client_own_link_state_service(session=session, platform_session=platform_session)

router = APIRouter(prefix='/construction/clients')

@router.get('/{id}/user-link/self', response_model=ClientOwnLinkStateEnvelope)
async def read_own_client_link(id: UUID, request: Request, response: Response,
    principal=Depends(get_authenticated_platform_principal), service=Depends(get_own_link_state_service),
    rate=Depends(get_api_request_rate_limit_service)):
    if request.query_params:
        raise ApiHttpError(status_code=422, code='validation_error', message='Own-link query is invalid.')
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        await rate.ensure_platform_user_allowed(user_id=principal.user_id)
        state = await service.get_state(principal=principal, client_id=id)
        if state.client_id != id or state.linked_user_id != principal.user_id:
            raise ValueError('Own-link response context is invalid.')
        data = ClientOwnLinkStateResponse.model_validate({name:getattr(state,name)
            for name in ClientOwnLinkStateResponse.model_fields})
    except ApiIdentityAccessError:
        raise invalid_token_error() from None
    except ConstructionClientUnlinkDeniedError:
        raise ApiHttpError(status_code=404, code='not_found', message='Client link is not available.') from None
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(status_code=429, code='RATE_LIMIT_EXCEEDED', message='Rate limit exceeded.') from None
    except Exception:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Client link is not available.') from None
    return success_envelope(data=data.model_dump(mode='json'), request_id=request.state.request_id)
