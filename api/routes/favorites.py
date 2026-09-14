from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import get_current_actor
from api.errors import ApiHttpError
from api.dependencies import (
    get_user_favorites_service,
)
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    FavoriteListResponse,
    FavoriteSaveRequest,
    FavoriteSaveResponse,
)
from services.api_identity import ApiActorContext
from services.user_favorites import (
    UserFavoritesNotFoundError,
    UserFavoritesService,
)


router = APIRouter()


@router.get(
    "/me/favorites",
    response_model=FavoriteListResponse,
)
async def list_my_favorites(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserFavoritesService,
        Depends(get_user_favorites_service),
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

    page = await service.list_favorites_for_user(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        language=actor.language_code,
        page=page_number,
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "specialist_id": (
                    card.specialist_id
                ),
                "professional_cabinet_id": (
                    card.professional_cabinet_id
                ),
                "display_name": (
                    card.display_name
                ),
                "short_description": (
                    card.short_description
                ),
                "city_id": card.city_id,
                "city_name": card.city_name,
                "category_name": (
                    card.category_name
                ),
                "profession_name": (
                    card.profession_name
                ),
                "work_format": card.work_format,
                "services": (
                    card.service_titles
                ),
                "skills": card.skill_names,
                "languages": card.languages,
                "rating": card.rating,
                "reviews_count": (
                    card.reviews_count
                ),
                "is_verified": (
                    card.is_verified
                ),
                "is_available": (
                    card.is_available
                ),
                "is_premium": (
                    card.is_premium
                ),
            }
            for card in page.cards
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
        request_id=request.state.request_id,
    )


@router.post(
    "/me/favorites",
    response_model=FavoriteSaveResponse,
)
async def save_my_favorite(
    payload: FavoriteSaveRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserFavoritesService,
        Depends(get_user_favorites_service),
    ],
):
    try:
        action = await service.save_favorite_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            professional_cabinet_id=(
                payload.professional_cabinet_id
            ),
        )
    except UserFavoritesNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="favorite_not_found",
            message=(
                "Favorite target is not available."
            ),
        ) from exc

    return success_envelope(
        data={
            "professional_cabinet_id": (
                payload.professional_cabinet_id
            ),
            "is_favorite": True,
            "created": bool(action.result),
        },
        meta={},
        request_id=request.state.request_id,
    )

