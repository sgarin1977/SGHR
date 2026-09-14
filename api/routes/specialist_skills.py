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
    get_specialist_skills_service,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    SpecialistSkillListResponse,
    SpecialistSkillsUpdateRequest,
    SpecialistSkillsUpdateResponse,
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
)
from services.specialist_skills import (
    SpecialistSkillsService,
)


router = APIRouter()

SPECIALIST_SKILLS_READ_PERMISSION = (
    "specialist.skills.read"
)
require_specialist_skills_read = (
    require_permission(
        SPECIALIST_SKILLS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


SPECIALIST_SKILLS_WRITE_PERMISSION = (
    "specialist.skills.write"
)
require_specialist_skills_write = (
    require_permission(
        SPECIALIST_SKILLS_WRITE_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/skills"
    ),
    response_model=(
        SpecialistSkillListResponse
    ),
)
async def list_specialist_skills(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_skills_read
        ),
    ],
    service: Annotated[
        SpecialistSkillsService,
        Depends(
            get_specialist_skills_service
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 30,
):
    try:
        action = await (
            service.list_skills_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                limit=limit,
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

    result = action.result
    selected_ids = set(
        result.selected_ids
    )

    return success_envelope(
        data={
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "is_selected": (
                        item.id in selected_ids
                    ),
                }
                for item in result.skills
            ],
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.put(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/skills"
    ),
    response_model=(
        SpecialistSkillsUpdateResponse
    ),
)
async def update_specialist_skills(
    cabinet_id: UUID,
    payload: SpecialistSkillsUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_skills_write
        ),
    ],
    service: Annotated[
        SpecialistSkillsService,
        Depends(
            get_specialist_skills_service
        ),
    ],
):
    try:
        action = await (
            service.update_skills_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                skill_ids=list(
                    payload.skill_ids
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

    _before, selected, changed = (
        action.result
    )

    return success_envelope(
        data={
            "selected_ids": selected,
            "changed": changed,
        },
        meta={},
        request_id=request.state.request_id,
    )

