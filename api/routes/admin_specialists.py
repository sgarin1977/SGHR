from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import (
    require_admin_any_permission,
)
from api.dependencies import (
    get_api_admin_specialists_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    AdminSpecialistDetailResponse,
    AdminSpecialistListResponse,
    AdminSpecialistModerationRequest,
    AdminSpecialistModerationResponse,
)
from services.api_admin_specialists import (
    ApiAdminSpecialistNotFoundError,
    ApiAdminSpecialistsService,
)
from services.api_identity import (
    ApiActorContext,
)


from api.auth import require_admin_api_rate_limit


router = APIRouter(
    dependencies=[
        Depends(
            require_admin_api_rate_limit()
        ),
    ],
)

ADMIN_SPECIALISTS_READ_PERMISSIONS = (
    "admin.specialists.moderate",
    "moderation.specialists.view",
)
require_admin_specialists_read = (
    require_admin_any_permission(
        *ADMIN_SPECIALISTS_READ_PERMISSIONS
    )
)


require_admin_specialists_approve = (
    require_admin_any_permission(
        "admin.specialists.moderate",
        "moderation.specialists.approve",
    )
)
require_admin_specialists_reject = (
    require_admin_any_permission(
        "admin.specialists.moderate",
        "moderation.specialists.reject",
    )
)
require_admin_specialists_visibility = (
    require_admin_any_permission(
        "admin.specialists.moderate",
    )
)


@router.get(
    "/admin/specialists",
    response_model=(
        AdminSpecialistListResponse
    ),
)
async def list_admin_specialists(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_specialists_read
        ),
    ],
    service: Annotated[
        ApiAdminSpecialistsService,
        Depends(
            get_api_admin_specialists_service
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=128,
        ),
    ] = None,
):
    page_number = decode_page_cursor(cursor)

    page = await service.list_specialists(
        actor=actor,
        page=page_number,
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "user_id": item.user_id,
                "professional_cabinet_id": (
                    item
                    .professional_cabinet_id
                ),
                "category_id": (
                    item.category_id
                ),
                "profession_id": (
                    item.profession_id
                ),
                "country_id": (
                    item.country_id
                ),
                "city_id": item.city_id,
                "display_name": (
                    item.display_name
                ),
                "status": item.status,
                "moderation_status": (
                    item.moderation_status
                ),
                "is_verified": (
                    item.is_verified
                ),
                "availability_status": (
                    item.availability_status
                ),
                "rating": item.rating,
                "reviews_count": (
                    item.reviews_count
                ),
                "created_at": (
                    item.created_at
                ),
            }
            for item in page.items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(
                    page.page + 1
                )
                if page.has_next
                else None
            ),
            "has_more": page.has_next,
        },
        request_id=(
            request.state.request_id
        ),
    )



@router.get(
    "/admin/specialists/{specialist_id}",
    response_model=(
        AdminSpecialistDetailResponse
    ),
)
async def get_admin_specialist(
    specialist_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_specialists_read
        ),
    ],
    service: Annotated[
        ApiAdminSpecialistsService,
        Depends(
            get_api_admin_specialists_service
        ),
    ],
):
    try:
        item = await service.get_specialist(
            actor=actor,
            specialist_id=specialist_id,
        )
    except ApiAdminSpecialistNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code=(
                "admin_specialist_not_found"
            ),
            message=(
                "Specialist was not found."
            ),
        ) from None

    return success_envelope(
        data={
            "id": item.id,
            "user_id": item.user_id,
            "professional_cabinet_id": (
                item.professional_cabinet_id
            ),
            "category_id": item.category_id,
            "profession_id": (
                item.profession_id
            ),
            "country_id": item.country_id,
            "city_id": item.city_id,
            "display_name": (
                item.display_name
            ),
            "status": item.status,
            "moderation_status": (
                item.moderation_status
            ),
            "is_verified": (
                item.is_verified
            ),
            "availability_status": (
                item.availability_status
            ),
            "rating": item.rating,
            "reviews_count": (
                item.reviews_count
            ),
            "created_at": item.created_at,
        },
        request_id=(
            request.state.request_id
        ),
    )



def _moderation_response(
    *,
    result,
    request: Request,
):
    return success_envelope(
        data={
            "entity_id": result.entity_id,
            "status": result.status,
            "message": result.message,
        },
        request_id=(
            request.state.request_id
        ),
    )


@router.post(
    "/admin/specialists/{specialist_id}/approve",
    response_model=(
        AdminSpecialistModerationResponse
    ),
)
async def approve_admin_specialist(
    specialist_id: UUID,
    payload: AdminSpecialistModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_specialists_approve
        ),
    ],
    service: Annotated[
        ApiAdminSpecialistsService,
        Depends(
            get_api_admin_specialists_service
        ),
    ],
):
    result = await service.approve_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason=payload.reason,
    )
    return _moderation_response(
        result=result,
        request=request,
    )


@router.post(
    "/admin/specialists/{specialist_id}/reject",
    response_model=(
        AdminSpecialistModerationResponse
    ),
)
async def reject_admin_specialist(
    specialist_id: UUID,
    payload: AdminSpecialistModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_specialists_reject
        ),
    ],
    service: Annotated[
        ApiAdminSpecialistsService,
        Depends(
            get_api_admin_specialists_service
        ),
    ],
):
    result = await service.reject_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason=payload.reason,
    )
    return _moderation_response(
        result=result,
        request=request,
    )


@router.post(
    "/admin/specialists/{specialist_id}/hide",
    response_model=(
        AdminSpecialistModerationResponse
    ),
)
async def hide_admin_specialist(
    specialist_id: UUID,
    payload: AdminSpecialistModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_specialists_visibility
        ),
    ],
    service: Annotated[
        ApiAdminSpecialistsService,
        Depends(
            get_api_admin_specialists_service
        ),
    ],
):
    result = await service.hide_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason=payload.reason,
    )
    return _moderation_response(
        result=result,
        request=request,
    )


@router.post(
    "/admin/specialists/{specialist_id}/unhide",
    response_model=(
        AdminSpecialistModerationResponse
    ),
)
async def unhide_admin_specialist(
    specialist_id: UUID,
    payload: AdminSpecialistModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_specialists_visibility
        ),
    ],
    service: Annotated[
        ApiAdminSpecialistsService,
        Depends(
            get_api_admin_specialists_service
        ),
    ],
):
    result = await service.unhide_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason=payload.reason,
    )
    return _moderation_response(
        result=result,
        request=request,
    )
