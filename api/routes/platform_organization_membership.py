"""Organization membership is a Platform operation, callable from Construction Team."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter,Depends,Header,Request,Response
from pydantic import BaseModel,ConfigDict,StrictBool

from api.auth import get_current_actor,require_authenticated_user_rate_limit
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.settings import ApiConfigurationError,ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.platform_membership import PlatformMembershipRemovalDeniedError,PlatformMembershipRemovalError
from services.platform_membership_permissions import PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE
from services.platform_organization_removal_command import PlatformRemovalInProgressError,build_platform_organization_removal_command


class RemoveOrganizationMemberRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')


class RemoveOrganizationMemberResponse(BaseModel):
    model_config=ConfigDict(extra='forbid')
    user_id:UUID
    changed:StrictBool


class RemoveOrganizationMemberEnvelope(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data:RemoveOrganizationMemberResponse
    meta:dict
    request_id:str


def optional_removal_key(request:Request,value:Annotated[str|None,Header(alias='Idempotency-Key',min_length=1,max_length=255,
        description='Optional encrypted replay; the ordinary domain operation is an authorized idempotent no-op when already removed.')]=None):
    if len(request.headers.getlist('Idempotency-Key'))>1 or (value is not None and not value.strip()):
        raise ApiHttpError(status_code=422,code='validation_error',message='Idempotency key is invalid.')
    return value


def get_organization_removal_command(session=Depends(get_api_session),key=Depends(optional_removal_key)):
    try:
        encryption=ApiIdempotencySettings.from_env().encryption_key if key is not None else None
        return build_platform_organization_removal_command(session=session,idempotency_encryption_key=encryption)
    except (ApiConfigurationError,ValueError):
        raise ApiHttpError(status_code=503,code='service_unavailable',message='Organization removal is unavailable.') from None


router=APIRouter(prefix='/platform/organizations/current/members',dependencies=[
    Depends(get_current_actor),Depends(require_authenticated_user_rate_limit()),
])


@router.post('/{user_id}/remove',response_model=RemoveOrganizationMemberEnvelope,openapi_extra={
    'x-required-platform-permission':PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE,
    'x-idempotency-key-required':False,
},responses={200:{'description':'Removed or already absent; no new audit for a domain no-op.'},
    401:{'description':'Platform authentication required.'},403:{'description':'Platform authority or ownership/delegation boundary denied.'},
    409:{'description':'IDEMPOTENCY_KEY_REUSED or idempotency_in_progress.'},422:{'description':'Invalid request.'},503:{'description':'Unavailable.'}})
async def remove_organization_member(user_id:UUID,request:Request,response:Response,
        body:RemoveOrganizationMemberRequest|None=None,actor=Depends(get_current_actor),
        command=Depends(get_organization_removal_command),key=Depends(optional_removal_key)):
    response.headers['Cache-Control']='private, no-store'
    if request.query_params:
        raise ApiHttpError(status_code=422,code='validation_error',message='Organization context cannot be supplied in the query.')
    try:
        data=await command.remove_from_organization(actor=actor,user_id=user_id,idempotency_key=key,
            trace_id=request.state.request_id)
        data=RemoveOrganizationMemberResponse.model_validate(data)
        if data.user_id!=user_id:
            raise PlatformMembershipRemovalError('Removal response identity differs.')
    except PlatformMembershipRemovalDeniedError:
        raise ApiHttpError(status_code=403,code='forbidden',message='Organization removal is not permitted.') from None
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409,code='IDEMPOTENCY_KEY_REUSED',message='Idempotency key was reused with a different request.') from None
    except PlatformRemovalInProgressError:
        raise ApiHttpError(status_code=409,code='idempotency_in_progress',message='Organization removal is processing.') from None
    except PlatformMembershipRemovalError:
        raise ApiHttpError(status_code=503,code='service_unavailable',message='Organization removal is unavailable.') from None
    except Exception:
        raise ApiHttpError(status_code=500,code='internal_error',message='Organization removal failed.') from None
    return success_envelope(data=data.model_dump(mode='json'),request_id=request.state.request_id)
