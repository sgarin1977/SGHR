"""Platform HTTP adapters for approved Telegram binding operations."""
import os
import re
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, get_telegram_init_data_verifier, require_authenticated_user_rate_limit
from api.client_ip import resolve_client_ip
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from api.telegram_auth import TelegramAuthenticationError
from services.platform_telegram_binding_consume import PlatformTelegramAccountAlreadyLinkedError
from services.api_identity import ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_telegram_binding_consume import PlatformTelegramBindingResult, PlatformTelegramBindingVersionConflictError
from services.platform_telegram_binding_lifecycle import PlatformTelegramBindingRevocationResult
from services.platform_telegram_binding_lifecycle_workflow import build_platform_telegram_binding_lifecycle_workflow
from services.platform_telegram_binding_presentation import present_platform_telegram_binding, read_platform_telegram_binding_code
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError
from services.platform_telegram_binding_workflow import PlatformTelegramBindingIdempotencyInProgressError
from services.platform_telegram_binding_consume import PlatformTelegramAccountUnlinkedError
from typing import Literal
from services.platform_telegram_account_unlink import PlatformTelegramAccountUnlinkResult
from services.platform_telegram_account_unlink_workflow import build_platform_telegram_account_unlink_workflow


class BindingInitiateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BindingConsumeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: StrictStr = Field(min_length=43, max_length=43, repr=False)
    init_data: StrictStr = Field(min_length=1, max_length=16384, repr=False)

    @field_validator('token')
    @classmethod
    def canonical_code(cls, value): return read_platform_telegram_binding_code(value)


class BindingVersionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_row_version: StrictInt = Field(ge=1, lt=2**63-1)


def _response_time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Binding response timestamp is invalid.')
    return value.astimezone(timezone.utc)


class BindingResponseFields(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('issued_at', 'expires_at', 'consumed_at', 'revoked_at', check_fields=False)
    @classmethod
    def utc_time(cls, value):
        return _response_time(value)


class BindingIssuedResponse(BindingResponseFields):
    binding_id: UUID
    code: StrictStr = Field(repr=False)
    deep_link: StrictStr = Field(repr=False)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    row_version: StrictInt = Field(ge=1)


class BindingResentResponse(BindingIssuedResponse):
    source_binding_id: UUID
    source_row_version: StrictInt = Field(ge=2)


class BindingConsumedResponse(BindingResponseFields):
    binding_id: UUID
    account_id: UUID
    consumed_at: AwareDatetime
    row_version: StrictInt = Field(ge=2)
    account_created: StrictBool


class BindingRevokedResponse(BindingResponseFields):
    binding_id: UUID
    revoked_at: AwareDatetime
    row_version: StrictInt = Field(ge=2)


class BindingIssuedEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: BindingIssuedResponse
    meta: dict[str, Any]
    request_id: str


class BindingResentEnvelope(BindingIssuedEnvelope):
    data: BindingResentResponse


class BindingConsumedEnvelope(BindingIssuedEnvelope):
    data: BindingConsumedResponse


class BindingRevokedEnvelope(BindingIssuedEnvelope):
    data: BindingRevokedResponse


def _key(request, value, *, required):
    count = len(request.headers.getlist('Idempotency-Key'))
    if value is None and not required and count == 0: return None
    if count != 1 or not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ApiHttpError(status_code=422, code='validation_error', message='Idempotency key is invalid.')
    return value


def require_binding_key(request: Request, value: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=255)]):
    return _key(request, value, required=True)


def optional_binding_key(request: Request, value: Annotated[str | None, Header(alias='Idempotency-Key', min_length=1, max_length=255)] = None):
    return _key(request, value, required=False)


async def get_binding_session(session=Depends(get_api_session)):
    # Consume intentionally has no Platform actor dependency to auto-open this.
    if not session.in_transaction(): await session.begin()
    return session


def get_binding_verifier():
    try: return get_telegram_init_data_verifier()
    except (ApiConfigurationError, ValueError):
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Telegram binding is not configured.') from None


def get_binding_workflow(session=Depends(get_binding_session), verifier=Depends(get_binding_verifier)):
    try:
        return build_platform_telegram_binding_lifecycle_workflow(session=session,
            encryption_key=ApiIdempotencySettings.from_env().encryption_key, telegram_verifier=verifier)
    except (ApiConfigurationError, ValueError):
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Telegram binding is not configured.') from None


def get_binding_bot_username():
    load_dotenv()
    username = os.environ.get('BOT_USERNAME', '')
    if not isinstance(username, str) or re.fullmatch(r'[A-Za-z0-9_]{5,32}', username) is None:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Telegram binding target bot is not configured.')
    return username


async def require_binding_consume_rate_limit(request: Request, service=Depends(get_api_request_rate_limit_service)):
    if request.client is None:
        raise ApiHttpError(status_code=400, code='validation_error', message='Request origin is unavailable.')
    try:
        # No untrusted forwarding headers are used as a rate-limit identity.
        address = resolve_client_ip(peer_ip=request.client.host, forwarded_for=None, trusted_proxy_networks=())
        await service.ensure_public_api_allowed(client_ip=address)
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(status_code=429, code='RATE_LIMIT_EXCEEDED', message='Rate limit exceeded.') from None


async def _run(operation):
    try:
        return await operation
    except TelegramAuthenticationError:
        raise ApiHttpError(status_code=401, code='invalid_telegram_auth', message='Telegram authentication data is invalid.') from None
    except (ApiIdentityAccessError, PlatformTelegramAccountUnlinkedError):
        raise ApiHttpError(status_code=403, code='account_unavailable', message='Platform account is not available.') from None
    except PlatformTelegramBindingTokenDeniedError:
        raise ApiHttpError(status_code=400, code='invalid_binding_token', message='Telegram binding token is not available.') from None
    except PlatformTelegramBindingVersionConflictError:
        raise ApiHttpError(status_code=409, code='ROW_VERSION_CONFLICT', message='Telegram binding version conflict.') from None
    except PlatformTelegramAccountAlreadyLinkedError:
        raise ApiHttpError(status_code=409, code='TELEGRAM_ACCOUNT_ALREADY_LINKED', message='Telegram account is already linked.') from None
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code='IDEMPOTENCY_KEY_REUSED', message='Idempotency key was reused with a different request.') from None
    except PlatformTelegramBindingIdempotencyInProgressError:
        raise ApiHttpError(status_code=409, code='idempotency_in_progress', message='Telegram binding operation is processing.') from None
    except ValueError:
        raise ApiHttpError(status_code=422, code='validation_error', message='Telegram binding request is invalid.') from None
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Telegram binding operation failed.') from None


def _trace():
    # Keep caller-controlled request IDs out of durable token/audit metadata.
    return 'telegram-binding:'+uuid4().hex


def _reply(data, request, response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return success_envelope(data=data, request_id=request.state.request_id)


def _issued(result, *, actor, username):
    item = present_platform_telegram_binding(result, bot_username=username,
        expected_user_id=actor.user_id, expected_tenant_id=actor.tenant_id)
    return dict(binding_id=item.binding_id, code=item.code, deep_link=item.deep_link,
        issued_at=item.issued_at, expires_at=item.expires_at, row_version=item.row_version)


def _projection_failed():
    return ApiHttpError(status_code=500, code='internal_error', message='Telegram binding response failed.')


router = APIRouter(prefix='/platform/integrations/telegram')
actor_limit = require_authenticated_user_rate_limit()
_RESPONSES = {401: {'description': 'Authentication required or invalid Telegram proof'},
    403: {'description': 'Platform account unavailable'}, 409: {'description': 'Version, identity or idempotency conflict'},
    422: {'description': 'Invalid request'}, 429: {'description': 'Rate limit exceeded'},
    500: {'description': 'Internal error'}, 503: {'description': 'Binding configuration unavailable'}}


@router.post('/bindings', response_model=BindingIssuedEnvelope, status_code=201,
    dependencies=[Depends(actor_limit)], responses=_RESPONSES)
async def initiate_binding(payload: BindingInitiateRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(require_binding_key),
    username=Depends(get_binding_bot_username), workflow=Depends(get_binding_workflow)):
    result = await _run(workflow.base.initiate_binding(actor=actor, idempotency_key=key, trace_id=_trace()))
    try: data = BindingIssuedResponse.model_validate(_issued(result, actor=actor, username=username)).model_dump(mode='json')
    except Exception: raise _projection_failed() from None
    return _reply(data, request, response)


@router.post('/bindings/consume', response_model=BindingConsumedEnvelope,
    dependencies=[Depends(require_binding_consume_rate_limit)], responses={**_RESPONSES, 400: {'description': 'Binding token unavailable'}})
async def consume_binding(payload: BindingConsumeRequest, request: Request, response: Response,
    key=Depends(require_binding_key), workflow=Depends(get_binding_workflow)):
    result = await _run(workflow.base.consume_binding(token=payload.token, telegram_init_data=payload.init_data,
        idempotency_key=key, trace_id=_trace()))
    try:
        if (not isinstance(result, PlatformTelegramBindingResult)
            or any(not isinstance(getattr(result, field), UUID) for field in ('binding_id', 'account_id', 'user_id', 'tenant_id'))):
            raise ValueError('Binding consume receipt is invalid.')
        data = BindingConsumedResponse.model_validate(dict(binding_id=result.binding_id, account_id=result.account_id,
            consumed_at=_response_time(result.consumed_at), row_version=result.row_version, account_created=result.account_created)).model_dump(mode='json')
    except Exception: raise _projection_failed() from None
    return _reply(data, request, response)


@router.post('/bindings/{id}/resend', response_model=BindingResentEnvelope,
    dependencies=[Depends(actor_limit)], responses={**_RESPONSES, 400: {'description': 'Binding token unavailable'}})
async def resend_binding(id: UUID, payload: BindingVersionRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(require_binding_key),
    username=Depends(get_binding_bot_username), workflow=Depends(get_binding_workflow)):
    result = await _run(workflow.resend_binding(actor=actor, binding_id=id, expected_row_version=payload.expected_row_version,
        idempotency_key=key, trace_id=_trace()))
    try:
        values = _issued(result, actor=actor, username=username)
        if (result.source_binding_id != id or result.source_row_version != payload.expected_row_version+1
            or result.binding_id == id or result.source_revoked_at != result.issued_at):
            raise ValueError('Binding resend receipt is invalid.')
        data = BindingResentResponse.model_validate({**values, 'source_binding_id': result.source_binding_id,
            'source_row_version': result.source_row_version}).model_dump(mode='json')
    except Exception: raise _projection_failed() from None
    return _reply(data, request, response)


@router.post('/bindings/{id}/revoke', response_model=BindingRevokedEnvelope,
    dependencies=[Depends(actor_limit)], responses={**_RESPONSES, 400: {'description': 'Binding token unavailable'}})
async def revoke_binding(id: UUID, payload: BindingVersionRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(optional_binding_key), workflow=Depends(get_binding_workflow)):
    result = await _run(workflow.revoke_binding(actor=actor, binding_id=id, expected_row_version=payload.expected_row_version,
        idempotency_key=key, trace_id=_trace()))
    try:
        if (not isinstance(result, PlatformTelegramBindingRevocationResult) or result.binding_id != id
            or result.user_id != actor.user_id or result.tenant_id != actor.tenant_id
            or result.row_version != payload.expected_row_version+1): raise ValueError('Binding revoke receipt is invalid.')
        data = BindingRevokedResponse.model_validate(dict(binding_id=result.binding_id,
            revoked_at=_response_time(result.revoked_at), row_version=result.row_version)).model_dump(mode='json')
    except Exception: raise _projection_failed() from None
    return _reply(data, request, response)


class AccountUnlinkedResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    status: Literal['unlinked']
    row_version: StrictInt = Field(ge=1, le=2**63-1)
    changed: StrictBool


class AccountUnlinkedEnvelope(BindingIssuedEnvelope):
    data: AccountUnlinkedResponse


def get_account_unlink_workflow(session=Depends(get_binding_session)):
    try:
        return build_platform_telegram_account_unlink_workflow(session=session,
            encryption_key=ApiIdempotencySettings.from_env().encryption_key)
    except (ApiConfigurationError, ValueError):
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Telegram account unlink is not configured.') from None


@router.post('/accounts/{id}/unlink', response_model=AccountUnlinkedEnvelope,
    dependencies=[Depends(actor_limit)], responses=_RESPONSES)
async def unlink_account(id: UUID, payload: BindingVersionRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(require_binding_key), workflow=Depends(get_account_unlink_workflow)):
    result = await _run(workflow.unlink_account(actor=actor, account_id=id,
        expected_row_version=payload.expected_row_version, idempotency_key=key, trace_id=_trace()))
    try:
        if (not isinstance(result, PlatformTelegramAccountUnlinkResult)
            or any(not isinstance(getattr(result, field), UUID) for field in ('account_id', 'user_id', 'tenant_id'))
            or result.account_id != id or result.user_id != actor.user_id or result.tenant_id != actor.tenant_id
            or type(result.changed) is not bool or type(result.row_version) is not int
            or result.row_version != payload.expected_row_version+int(result.changed)):
            raise ValueError('Account unlink receipt is invalid.')
        data = AccountUnlinkedResponse.model_validate(dict(account_id=result.account_id, status=result.status,
            row_version=result.row_version, changed=result.changed)).model_dump(mode='json')
    except Exception: raise _projection_failed() from None
    return _reply(data, request, response)
