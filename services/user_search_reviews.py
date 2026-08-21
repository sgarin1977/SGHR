from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.contact import (
    ContactChatRepository,
)
from database.repositories.reviews import (
    ReviewRepository,
)
from services.contact_chat import (
    ContactChatService,
)
from services.reviews import ReviewService
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserSearchReviewsAccessError(
    PermissionError
):
    pass


class UserSearchReviewsSelectionError(
    ValueError
):
    pass


@dataclass(frozen=True)
class UserSearchReviewsActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserSearchReviewsResult:
    actor: UserSearchReviewsActor
    review_page: Any


@dataclass(frozen=True)
class UserSearchReviewAction:
    actor: UserSearchReviewsActor
    review: Any
    thread_archived: bool


class UserSearchReviewsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        reviews: ReviewService | None = None,
        chats: ContactChatService | None = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.reviews = (
            reviews
            or ReviewService(
                ReviewRepository(session)
            )
        )
        self.chats = (
            chats
            or ContactChatService(
                ContactChatRepository(session)
            )
        )

    @staticmethod
    def parse_id(
        value: UUID | str,
        *,
        field: str,
    ) -> UUID:
        try:
            return (
                value
                if isinstance(value, UUID)
                else UUID(str(value))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                UserSearchReviewsSelectionError(
                    f"Invalid {field}."
                )
            ) from exc

    @classmethod
    def parse_optional_id(
        cls,
        value: UUID | str | None,
        *,
        field: str,
    ) -> UUID | None:
        if value is None:
            return None

        return cls.parse_id(
            value,
            field=field,
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserSearchReviewsActor:
        try:
            context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError as exc:
            raise (
                UserSearchReviewsAccessError(
                    "Reviews viewer not found."
                )
            ) from exc

        return UserSearchReviewsActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=(
                context.interface_language
            ),
        )

    async def open_reviews(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID | str,
        professional_cabinet_id: (
            UUID | str | None
        ) = None,
        page: int | str = 0,
        page_size: int | str = 5,
    ) -> UserSearchReviewsResult:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_specialist_id = self.parse_id(
            specialist_id,
            field="specialist",
        )
        parsed_cabinet_id = (
            self.parse_optional_id(
                professional_cabinet_id,
                field="professional cabinet",
            )
        )

        try:
            normalized_page = max(
                0,
                int(page),
            )
            normalized_page_size = max(
                1,
                min(
                    int(page_size),
                    10,
                ),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise (
                UserSearchReviewsSelectionError(
                    "Invalid reviews page."
                )
            ) from exc

        review_page = await (
            self.reviews
            .list_public_reviews_for_viewer(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    parsed_specialist_id
                ),
                professional_cabinet_id=(
                    parsed_cabinet_id
                ),
                viewer_user_id=actor.user_id,
                page=normalized_page,
                page_size=(
                    normalized_page_size
                ),
            )
        )

        return UserSearchReviewsResult(
            actor=actor,
            review_page=review_page,
        )

    async def create_contact_review(
        self,
        *,
        platform_user_id: int | str,
        contact_request_id: UUID | str,
        rating: int | str,
        text: str | None = None,
        thread_id: UUID | str | None = None,
    ) -> UserSearchReviewAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_contact_request_id = (
            self.parse_id(
                contact_request_id,
                field="contact request",
            )
        )
        parsed_thread_id = (
            self.parse_optional_id(
                thread_id,
                field="review thread",
            )
        )

        try:
            normalized_rating = int(rating)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise (
                UserSearchReviewsSelectionError(
                    "Invalid review rating."
                )
            ) from exc

        review = await (
            self.reviews.create_contact_review(
                tenant_id=actor.tenant_id,
                reviewer_user_id=(
                    actor.user_id
                ),
                contact_request_id=(
                    parsed_contact_request_id
                ),
                rating=normalized_rating,
                text=text,
            )
        )

        if parsed_thread_id is not None:
            await (
                self.chats
                .archive_thread_after_review(
                    thread_id=parsed_thread_id,
                    user_id=actor.user_id,
                )
            )

        return UserSearchReviewAction(
            actor=actor,
            review=review,
            thread_archived=(
                parsed_thread_id is not None
            ),
        )
