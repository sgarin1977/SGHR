from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Request,
)

from api.auth import (
    require_permission,
    require_specialist_actor,
)
from api.dependencies import (
    get_specialist_cabinets_service,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    SpecialistAvailabilityResponse,
    SpecialistAvailabilityUpdateRequest,
    SpecialistAvailabilityUpdateResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist import (
    SpecialistRegistrationError,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
    SpecialistCabinetsService,
)


router = APIRouter()

SPECIALIST_AVAILABILITY_READ_PERMISSION = (
    "specialist.availability.read"
)
require_specialist_availability_read = (
    require_permission(
        SPECIALIST_AVAILABILITY_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


SPECIALIST_AVAILABILITY_WRITE_PERMISSION = (
    "specialist.availability.write"
)
require_specialist_availability_write = (
    require_permission(
        SPECIALIST_AVAILABILITY_WRITE_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/availability"
    ),
    response_model=(
        SpecialistAvailabilityResponse
    ),
)
async def get_specialist_availability(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_availability_read
        ),
    ],
    service: Annotated[
        SpecialistCabinetsService,
        Depends(
            get_specialist_cabinets_service
        ),
    ],
):
    try:
        action = await (
            service.get_availability_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
            )
        )
    except (
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
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

    return success_envelope(
        data={
            "status": action.result,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.put(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/availability"
    ),
    response_model=(
        SpecialistAvailabilityUpdateResponse
    ),
)
async def update_specialist_availability(
    cabinet_id: UUID,
    payload: SpecialistAvailabilityUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_availability_write
        ),
    ],
    service: Annotated[
        SpecialistCabinetsService,
        Depends(
            get_specialist_cabinets_service
        ),
    ],
):
    try:
        action = await (
            service.set_availability_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                availability_status=(
                    payload.status
                ),
                platform="api",
            )
        )
    except (
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
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

    _before, status, changed = (
        action.result
    )

    return success_envelope(
        data={
            "status": status,
            "changed": changed,
        },
        meta={},
        request_id=request.state.request_id,
    )

