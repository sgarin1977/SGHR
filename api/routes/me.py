from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
)

from api.auth import (
    get_api_identity_service,
    get_current_actor,
)
from api.responses import success_envelope
from api.errors import ApiHttpError
from api.schemas import (
    MeResponse,
    RolesResponse,
    RoleSwitchRequest,
    RoleSwitchResponse,
)
from services.api_identity import (
    ApiActorContext,
    ApiIdentityRoleError,
    ApiIdentityService,
)


from api.schemas import MeUpdateRequest
from services.api_identity import (
    ApiIdentityProfileError,
    ApiIdentityProfileValidationError,
)

from fastapi import status


router = APIRouter()


@router.get(
    "/me",
    response_model=MeResponse,
)
async def get_me(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
):
    return success_envelope(
        data={
            "id": actor.user_id,
            "tenant_id": actor.tenant_id,
            "active_role": actor.active_role,
            "roles": actor.roles,
            "language_code": (
                actor.language_code
            ),
            "timezone": actor.timezone,
            "status": actor.status,
        },
        request_id=request.state.request_id,
    )


@router.get(
    "/me/roles",
    response_model=RolesResponse,
)
async def get_me_roles(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
):
    return success_envelope(
        data={
            "active_role": actor.active_role,
            "roles": actor.roles,
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/me/roles/switch",
    response_model=RoleSwitchResponse,
)
async def switch_me_role(
    payload: RoleSwitchRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    identity_service: Annotated[
        ApiIdentityService,
        Depends(get_api_identity_service),
    ],
):
    try:
        updated_actor = await (
            identity_service.switch_active_role(
                actor=actor,
                role=payload.role,
            )
        )
    except ApiIdentityRoleError:
        raise ApiHttpError(
            status_code=403,
            code="role_not_available",
            message=(
                "Requested role is not "
                "available."
            ),
        ) from None

    return success_envelope(
        data={
            "active_role": (
                updated_actor.active_role
            ),
            "roles": updated_actor.roles,
        },
        request_id=request.state.request_id,
    )


@router.patch(
    "/me",
    response_model=MeResponse,
)
async def update_me(
    payload: MeUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    identity_service: Annotated[
        ApiIdentityService,
        Depends(get_api_identity_service),
    ],
):
    try:
        updated_actor = (
            await identity_service.update_profile(
                actor=actor,
                language_code=(
                    payload.language_code
                ),
            )
        )
    except ApiIdentityProfileValidationError:
        raise ApiHttpError(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            code="profile_validation_error",
            message=(
                "Profile update is not valid."
            ),
        ) from None
    except ApiIdentityProfileError:
        raise ApiHttpError(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            code="profile_update_unavailable",
            message=(
                "Profile update is temporarily "
                "unavailable."
            ),
        ) from None

    return success_envelope(
        data={
            "id": str(
                updated_actor.user_id
            ),
            "tenant_id": (
                str(updated_actor.tenant_id)
                if updated_actor.tenant_id
                else None
            ),
            "active_role": (
                updated_actor.active_role
            ),
            "roles": list(
                updated_actor.roles
            ),
            "language_code": (
                updated_actor.language_code
            ),
            "timezone": (
                updated_actor.timezone
            ),
            "status": updated_actor.status,
        },
        request_id=request.state.request_id,
    )
