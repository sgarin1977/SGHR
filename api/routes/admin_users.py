from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import (
    require_admin_permission,
)
from api.dependencies import (
    get_api_admin_users_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    AdminUserDetailResponse,
    AdminUserListResponse,
)
from services.api_admin_users import (
    ApiAdminUserNotFoundError,
    ApiAdminUsersService,
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

ADMIN_USERS_READ_PERMISSION = (
    "admin.users.read"
)
require_admin_users_read = (
    require_admin_permission(
        ADMIN_USERS_READ_PERMISSION
    )
)


@router.get(
    "/admin/users",
    response_model=AdminUserListResponse,
)
async def list_admin_users(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_admin_users_read),
    ],
    service: Annotated[
        ApiAdminUsersService,
        Depends(
            get_api_admin_users_service
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

    page = await service.list_users(
        actor=actor,
        page=page_number,
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "active_role": (
                    item.active_role
                ),
                "language_code": (
                    item.language_code
                ),
                "country_id": (
                    item.country_id
                ),
                "city_id": item.city_id,
                "status": item.status,
                "last_seen_at": (
                    item.last_seen_at
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
    "/admin/users/{user_id}",
    response_model=AdminUserDetailResponse,
)
async def get_admin_user(
    user_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_admin_users_read),
    ],
    service: Annotated[
        ApiAdminUsersService,
        Depends(
            get_api_admin_users_service
        ),
    ],
):
    try:
        item = await service.get_user(
            actor=actor,
            user_id=user_id,
        )
    except ApiAdminUserNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="admin_user_not_found",
            message="User was not found.",
        ) from None

    return success_envelope(
        data={
            "id": item.id,
            "active_role": item.active_role,
            "language_code": (
                item.language_code
            ),
            "country_id": item.country_id,
            "city_id": item.city_id,
            "status": item.status,
            "last_seen_at": (
                item.last_seen_at
            ),
            "created_at": item.created_at,
        },
        request_id=request.state.request_id,
    )
