"""Versioned read state for own Telegram accounts; no tenant or account selector from caller."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from api.auth import invalid_token_error
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from services.api_identity import ApiIdentityAccessError
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_telegram_account_state import build_platform_telegram_account_state_service

class TelegramAccountStateResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    telegram_user_id: StrictStr = Field(pattern=r'^[1-9][0-9]*$')
    status: Literal['active', 'unlinked']
    row_version: StrictInt = Field(ge=1, le=2**63-1)

class TelegramAccountStatePagination(BaseModel):
    model_config = ConfigDict(extra='forbid')
    next_cursor: UUID | None

class TelegramAccountStateMeta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pagination: TelegramAccountStatePagination

class TelegramAccountStateEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: list[TelegramAccountStateResponse]
    meta: TelegramAccountStateMeta
    request_id: str

def get_telegram_account_state_service(session=Depends(get_api_session)):
    return build_platform_telegram_account_state_service(session=session)

router = APIRouter(prefix='/platform/integrations/telegram')

@router.get('/accounts', response_model=TelegramAccountStateEnvelope)
async def list_own_telegram_accounts(request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100), cursor: UUID | None = Query(default=None),
    principal=Depends(get_authenticated_platform_principal),
    service=Depends(get_telegram_account_state_service), rate=Depends(get_api_request_rate_limit_service)):
    response.headers['Cache-Control'] = 'private, no-store'
    if (set(request.query_params) - {'limit', 'cursor'}
            or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
        raise ApiHttpError(status_code=422, code='validation_error', message='Account query is invalid.')
    try:
        await rate.ensure_platform_user_allowed(user_id=principal.user_id)
        page = await service.list_accounts(principal=principal, limit=limit, after=cursor)
        data = [TelegramAccountStateResponse.model_validate({name: getattr(row, name)
            for name in TelegramAccountStateResponse.model_fields}).model_dump(mode='json') for row in page.items]
        meta = TelegramAccountStateMeta(pagination=TelegramAccountStatePagination(next_cursor=page.next_cursor))
    except ApiIdentityAccessError:
        raise invalid_token_error() from None
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(status_code=429, code='RATE_LIMIT_EXCEEDED', message='Rate limit exceeded.') from None
    except Exception:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Telegram accounts are not available.') from None
    return success_envelope(data=data, meta=meta.model_dump(mode='json'), request_id=request.state.request_id)
