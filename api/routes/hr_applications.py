from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import require_permission
from api.dependencies import (
    get_hr_application_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    HrApplicationListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.hr_applications import (
    HrApplicationAccessError,
    HrApplicationService,
)


router = APIRouter()

HR_APPLICATIONS_READ_PERMISSION = (
    "hr.applications.read"
)
require_hr_applications_read = (
    require_permission(
        HR_APPLICATIONS_READ_PERMISSION
    )
)


@router.get(
    "/hr/applications",
    response_model=HrApplicationListResponse,
)
async def list_hr_applications(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_hr_applications_read
        ),
    ],
    service: Annotated[
        HrApplicationService,
        Depends(
            get_hr_application_service
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
    page = decode_page_cursor(cursor)

    try:
        rows = await (
            service.list_applications_for_user(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                limit=limit + 1,
                offset=page * limit,
            )
        )
    except HrApplicationAccessError:
        raise ApiHttpError(
            status_code=404,
            code="seeker_not_found",
            message=(
                "Active seeker was not found."
            ),
        ) from None

    items = list(rows)
    has_more = len(items) > limit
    items = items[:limit]

    return success_envelope(
        data=[
            {
                "id": item.id,
                "vacancy_id": (
                    item.vacancy_id
                ),
                "message": item.message,
                "status": item.status,
                "created_at": (
                    item.created_at
                ),
                "updated_at": (
                    item.updated_at
                ),
            }
            for item in items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(page + 1)
                if has_more
                else None
            ),
            "has_more": has_more,
        },
        request_id=request.state.request_id,
    )
