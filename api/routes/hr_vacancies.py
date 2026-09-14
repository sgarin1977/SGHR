from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import require_permission
from api.dependencies import (
    get_hr_vacancy_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    HrVacancyListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.hr_vacancies import (
    HrVacancyAccessError,
    HrVacancyService,
)


router = APIRouter()

HR_VACANCIES_READ_PERMISSION = (
    "hr.vacancies.read"
)
require_hr_vacancies_read = (
    require_permission(
        HR_VACANCIES_READ_PERMISSION
    )
)


@router.get(
    "/hr/vacancies",
    response_model=HrVacancyListResponse,
)
async def list_hr_vacancies(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_hr_vacancies_read
        ),
    ],
    service: Annotated[
        HrVacancyService,
        Depends(get_hr_vacancy_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = decode_page_cursor(cursor)

    try:
        rows = await (
            service.list_vacancies_for_user(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                limit=limit + 1,
                offset=page * limit,
            )
        )
    except HrVacancyAccessError:
        raise ApiHttpError(
            status_code=404,
            code="employer_not_found",
            message=(
                "Active employer "
                "was not found."
            ),
        ) from None

    items = list(rows)
    has_more = len(items) > limit
    items = items[:limit]

    return success_envelope(
        data=[
            {
                "id": item.id,
                "profession_id": (
                    item.profession_id
                ),
                "country_id": item.country_id,
                "city_id": item.city_id,
                "title": item.title,
                "description": (
                    item.description
                ),
                "salary_min": (
                    item.salary_min
                ),
                "salary_max": (
                    item.salary_max
                ),
                "currency": item.currency,
                "employment_type": (
                    item.employment_type
                ),
                "work_format": (
                    item.work_format
                ),
                "recruitment_mode": (
                    item.recruitment_mode
                ),
                "status": item.status,
                "published_at": (
                    item.published_at
                ),
                "expires_at": (
                    item.expires_at
                ),
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
