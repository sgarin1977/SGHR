from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Request,
    status,
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
    SpecialistCabinetCreateRequest,
    SpecialistCabinetListResponse,
    SpecialistCabinetResponse,
    SpecialistCabinetSelectionResponse,
    SpecialistCabinetUpdateRequest,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsConflictError,
    SpecialistCabinetsSelectionError,
    SpecialistCabinetsService,
    SpecialistCabinetsValidationError,
)


router = APIRouter()

SPECIALIST_CABINETS_READ_PERMISSION = (
    "specialist.cabinets.read"
)
require_specialist_cabinets_read = (
    require_permission(
        SPECIALIST_CABINETS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


SPECIALIST_CABINETS_WRITE_PERMISSION = (
    "specialist.cabinets.write"
)
require_specialist_cabinets_write = (
    require_permission(
        SPECIALIST_CABINETS_WRITE_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)



@router.get(
    "/specialist/cabinets",
    response_model=(
        SpecialistCabinetListResponse
    ),
)
async def list_specialist_cabinets(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_specialist_cabinets_read),
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
            service.list_cabinets_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
            )
        )
    except SpecialistCabinetsAccessError:
        raise ApiHttpError(
            status_code=404,
            code="specialist_profile_not_found",
            message=(
                "Specialist profile was not found."
            ),
        ) from None

    return success_envelope(
        data={
            "items": [
                {
                    "id": item.id,
                    "profession_name": (
                        item.profession_name
                    ),
                    "moderation_status": (
                        item.moderation_status
                    ),
                    "availability_status": (
                        item.availability_status
                    ),
                    "is_selected": (
                        item.is_selected
                    ),
                }
                for item in action.result
            ],
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/specialist/cabinets",
    response_model=SpecialistCabinetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_specialist_cabinet(
    payload: SpecialistCabinetCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_specialist_cabinets_write),
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
            service.create_cabinet_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                category_id=payload.category_id,
                profession_id=(
                    payload.profession_id
                ),
                platform="api",
            )
        )
    except SpecialistCabinetsAccessError:
        raise ApiHttpError(
            status_code=404,
            code="specialist_profile_not_found",
            message=(
                "Specialist profile was not found."
            ),
        ) from None
    except SpecialistCabinetsConflictError:
        raise ApiHttpError(
            status_code=status.HTTP_409_CONFLICT,
            code="cabinet_already_exists",
            message=(
                "Professional cabinet "
                "already exists."
            ),
        ) from None
    except (
        SpecialistCabinetsSelectionError,
        SpecialistCabinetsValidationError,
    ):
        raise ApiHttpError(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            code="cabinet_validation_error",
            message=(
                "Professional cabinet data "
                "is not valid."
            ),
        ) from None

    item = action.result

    return success_envelope(
        data={
            "item": {
                "id": item.id,
                "profession_name": (
                    item.profession_name
                ),
                "moderation_status": (
                    item.moderation_status
                ),
                "availability_status": (
                    item.availability_status
                ),
                "is_selected": (
                    item.is_selected
                ),
            },
        },
        request_id=request.state.request_id,
    )


@router.patch(
    "/specialist/cabinets/{cabinet_id}",
    response_model=(
        SpecialistCabinetSelectionResponse
    ),
)
async def select_specialist_cabinet(
    cabinet_id: UUID,
    payload: SpecialistCabinetUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_specialist_cabinets_write),
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
            service.switch_cabinet_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                platform="api",
            )
        )
    except SpecialistCabinetsAccessError:
        raise ApiHttpError(
            status_code=404,
            code="specialist_profile_not_found",
            message=(
                "Specialist profile was not found."
            ),
        ) from None
    except (
        SpecialistCabinetsSelectionError,
        SpecialistCabinetsValidationError,
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
            "id": cabinet_id,
            "is_selected": (
                payload.is_selected
            ),
            "changed": bool(action.result),
        },
        request_id=request.state.request_id,
    )
