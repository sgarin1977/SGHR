from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import get_current_actor
from api.dependencies import (
    get_user_search_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistSearchResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.user_search import (
    UserSearchAccessError,
    UserSearchQueryError,
    UserSearchSelectionError,
    UserSearchService,
)


router = APIRouter()


@router.get(
    "/search/specialists",
    response_model=SpecialistSearchResponse,
)
async def search_specialists(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserSearchService,
        Depends(get_user_search_service),
    ],
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=200,
        ),
    ] = None,
    category_id: Annotated[
        UUID | None,
        Query(),
    ] = None,
    profession_id: Annotated[
        UUID | None,
        Query(),
    ] = None,
    profession_ids: Annotated[
        list[UUID] | None,
        Query(),
    ] = None,
    country_id: Annotated[
        UUID | None,
        Query(),
    ] = None,
    city_id: Annotated[
        UUID | None,
        Query(),
    ] = None,
    latitude: Annotated[
        float | None,
        Query(ge=-90, le=90),
    ] = None,
    longitude: Annotated[
        float | None,
        Query(ge=-180, le=180),
    ] = None,
    radius_km: Annotated[
        float | None,
        Query(gt=0, le=100),
    ] = None,
    country_wide: Annotated[
        bool,
        Query(),
    ] = False,
    language_code: Annotated[
        str | None,
        Query(min_length=2, max_length=10),
    ] = None,
    verified_only: Annotated[
        bool,
        Query(),
    ] = False,
    available_only: Annotated[
        bool,
        Query(),
    ] = False,
    premium_only: Annotated[
        bool,
        Query(),
    ] = False,
    work_format: Annotated[
        Literal[
            "at_client",
            "at_specialist",
            "remote",
            "mixed",
        ] | None,
        Query(),
    ] = None,
    rating_min: Annotated[
        float | None,
        Query(ge=0, le=5),
    ] = None,
    sort_by: Annotated[
        Literal[
            "distance",
            "relevance",
        ],
        Query(),
    ] = "distance",
    limit: Annotated[
        int,
        Query(ge=1, le=50),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=128,
        ),
    ] = None,
):
    page_number = decode_page_cursor(
        cursor
    )

    if (
        (latitude is None)
        != (longitude is None)
    ):
        raise ApiHttpError(
            status_code=422,
            code="search_validation_error",
            message=(
                "Search filters are not valid."
            ),
        )

    search_data = {}

    if q is not None:
        search_data[
            "search_text_query"
        ] = q.strip()

    if category_id is not None:
        search_data["category_id"] = str(
            category_id
        )

    if profession_id is not None:
        search_data["profession_id"] = str(
            profession_id
        )

    if profession_ids:
        search_data[
            "selected_profession_ids"
        ] = [
            str(value)
            for value in profession_ids
        ]

    if country_id is not None:
        search_data["country_id"] = str(
            country_id
        )

    if city_id is not None:
        search_data["city_id"] = str(
            city_id
        )

    if latitude is not None:
        search_data["latitude"] = latitude

    if longitude is not None:
        search_data["longitude"] = longitude

    if radius_km is not None:
        search_data["radius_km"] = (
            radius_km
        )

    if country_wide:
        search_data["country_wide"] = True

    if language_code is not None:
        search_data["language_code"] = (
            language_code
        )

    if verified_only:
        search_data["verified_only"] = True

    if available_only:
        search_data["available_only"] = True

    if premium_only:
        search_data["premium_only"] = True

    if work_format is not None:
        search_data["work_format"] = (
            work_format
        )

    if rating_min is not None:
        search_data["rating_min"] = (
            rating_min
        )

    if sort_by != "distance":
        search_data["sort_by"] = sort_by

    try:
        page = await (
            service.search_specialists_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                data=search_data,
                page=page_number,
                page_size=limit,
                default_radius_km=25,
            )
        )
    except (
        UserSearchSelectionError,
        UserSearchQueryError,
    ):
        raise ApiHttpError(
            status_code=422,
            code="search_validation_error",
            message=(
                "Search filters are not valid."
            ),
        ) from None
    except UserSearchAccessError:
        raise ApiHttpError(
            status_code=403,
            code="search_access_denied",
            message="Search access denied.",
        ) from None

    items = []

    for result in page.visible_results:
        cabinet = (
            result.professional_cabinet
        )

        if cabinet is None:
            continue

        items.append(
            {
                "specialist_id": (
                    result.specialist.id
                ),
                "professional_cabinet_id": (
                    cabinet.id
                ),
                "display_name": (
                    result.specialist.display_name
                ),
                "title": cabinet.title,
                "short_description": (
                    cabinet.description or ""
                ),
                "experience_years": (
                    result.specialist
                    .experience_years
                ),
                "category_id": (
                    cabinet.category_id
                ),
                "category_name": (
                    result.category_name
                ),
                "profession_id": (
                    cabinet.profession_id
                ),
                "profession_name": (
                    result.profession_name
                ),
                "country_id": (
                    cabinet.country_id
                ),
                "city_id": cabinet.city_id,
                "city_name": (
                    result.city_name
                ),
                "work_format": (
                    cabinet.work_format
                ),
                "languages": tuple(
                    result.languages
                ),
                "rating": float(
                    result.rating
                ),
                "reviews_count": int(
                    result.reviews_count
                ),
                "is_verified": bool(
                    result.specialist
                    .is_verified
                ),
                "is_available": (
                    cabinet.availability_status
                    == "available"
                ),
                "is_premium": bool(
                    result.is_premium
                ),
                "distance_km": (
                    float(result.distance_km)
                    if result.distance_km
                    is not None
                    else None
                ),
                "is_favorite": (
                    cabinet.id
                    in page
                    .saved_professional_cabinet_ids
                ),
            }
        )

    return success_envelope(
        data=items,
        meta={
            "next_cursor": (
                encode_page_cursor(
                    page_number + 1
                )
                if page.has_next
                else None
            ),
            "has_more": page.has_next,
            "total_count": (
                page.total_count
            ),
        },
        request_id=request.state.request_id,
    )
