from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import (
    require_permission,
    require_specialist_actor,
)
from api.dependencies import (
    get_specialist_services_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistServiceListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist import (
    SpecialistRegistrationError,
)
from services.specialist_services import (
    SpecialistServicesAccessError,
    SpecialistServicesService,
)


router = APIRouter()

SPECIALIST_SERVICES_READ_PERMISSION = (
    "specialist.services.read"
)
require_specialist_services_read = (
    require_permission(
        SPECIALIST_SERVICES_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/services"
    ),
    response_model=(
        SpecialistServiceListResponse
    ),
)
async def list_specialist_services(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_services_read
        ),
    ],
    service: Annotated[
        SpecialistServicesService,
        Depends(
            get_specialist_services_service
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 25,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = decode_page_cursor(cursor)

    try:
        result = await (
            service.list_services_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                page=page,
                page_size=limit,
                platform="api",
            )
        )
    except (
        SpecialistServicesAccessError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="cabinet_not_found",
            message=(
                "Professional cabinet "
                "was not found."
            ),
        ) from None

    has_more = (
        result.total
        > (page + 1) * limit
    )
    next_cursor = (
        encode_page_cursor(page + 1)
        if has_more
        else None
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "price_from": item.price_from,
                "price_to": item.price_to,
                "currency": item.currency,
                "price_unit": item.price_unit,
                "status": item.status,
            }
            for item in result.items
        ],
        meta={
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        request_id=request.state.request_id,
    )
