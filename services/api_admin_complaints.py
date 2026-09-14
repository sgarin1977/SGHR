from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.api_identity import (
    ApiActorContext,
)
from services.moderation import (
    ModerationError,
    ModerationService,
)


class ApiAdminComplaintOperationError(
    Exception
):
    pass


@dataclass(frozen=True)
class ApiAdminComplaintView:
    id: object
    reporter_label: str
    target_label: str
    reason: str
    status: str
    created_at: datetime
    is_assigned: bool
    has_conversation_context: bool
    requires_admin_escalation: bool


@dataclass(frozen=True)
class ApiAdminComplaintDetailView:
    id: UUID
    reporter_label: str
    target_type: str
    target_label: str
    reason: str
    comment: str | None
    status: str
    created_at: datetime
    has_conversation_context: bool
    requires_admin_escalation: bool
    history: tuple[str, ...]


@dataclass(frozen=True)
class ApiAdminComplaintActionResult:
    complaint_id: UUID
    status: str
    message: str


@dataclass(frozen=True)
class ApiAdminComplaintPage:
    items: tuple[
        ApiAdminComplaintView,
        ...,
    ]
    page: int
    has_next: bool


class ApiAdminComplaintsService:
    ALLOWED_STATUSES = frozenset(
        {
            "new",
            "in_review",
            "resolved",
            "rejected",
        }
    )

    def __init__(
        self,
        *,
        moderation: ModerationService,
    ) -> None:
        self.moderation = moderation

    @staticmethod
    def _to_view(
        card,
    ) -> ApiAdminComplaintView:
        return ApiAdminComplaintView(
            id=card.complaint_id,
            reporter_label=(
                card.reporter_label
            ),
            target_label=card.target_label,
            reason=card.reason,
            status=card.status,
            created_at=card.created_at,
            is_assigned=card.is_assigned,
            has_conversation_context=(
                card.has_conversation_context
            ),
            requires_admin_escalation=(
                card.requires_admin_escalation
            ),
        )

    async def list_complaints(
        self,
        *,
        actor: ApiActorContext,
        statuses: tuple[str, ...],
        page: int = 0,
        page_size: int = 20,
    ) -> ApiAdminComplaintPage:
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 10),
        )
        normalized_statuses = {
            status.strip().lower()
            for status in statuses
            if (
                isinstance(status, str)
                and status.strip().lower()
                in self.ALLOWED_STATUSES
            )
        }

        cards = await (
            self.moderation
            .open_complaints_queue(
                moderator_user_id=(
                    actor.user_id
                ),
                tenant_id=actor.tenant_id,
                statuses=normalized_statuses,
                page=normalized_page,
                page_size=normalized_size,
            )
        )

        return ApiAdminComplaintPage(
            items=tuple(
                self._to_view(card)
                for card in cards[
                    :normalized_size
                ]
            ),
            page=normalized_page,
            has_next=(
                len(cards) > normalized_size
            ),
        )


    async def get_complaint(
        self,
        *,
        actor: ApiActorContext,
        complaint_id: UUID,
    ) -> ApiAdminComplaintDetailView:
        card = await (
            self.moderation
            .get_moderator_complaint_card(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
            )
        )

        return ApiAdminComplaintDetailView(
            id=card.complaint_id,
            reporter_label=card.reporter_label,
            target_type=card.target_type,
            target_label=card.target_label,
            reason=card.reason,
            comment=card.comment,
            status=card.status,
            created_at=card.created_at,
            has_conversation_context=(
                card.has_conversation_context
            ),
            requires_admin_escalation=(
                card.requires_admin_escalation
            ),
            history=tuple(card.history),
        )


    async def take_complaint(
        self,
        *,
        actor: ApiActorContext,
        complaint_id: UUID,
    ) -> ApiAdminComplaintActionResult:
        try:
            result = await (
                self.moderation.take_complaint(
                    moderator_user_id=actor.user_id,
                    tenant_id=actor.tenant_id,
                    complaint_id=complaint_id,
                )
            )
        except ModerationError:
            raise (
                ApiAdminComplaintOperationError(
                    "Complaint operation failed."
                )
            ) from None

        return ApiAdminComplaintActionResult(
            complaint_id=result.entity_id,
            status=result.status,
            message=result.message,
        )


    async def resolve_complaint(
        self,
        *,
        actor: ApiActorContext,
        complaint_id: UUID,
        status: str,
        reason: str,
    ) -> ApiAdminComplaintActionResult:
        normalized_status = (
            status.strip().lower()
        )
        if normalized_status not in {
            "resolved",
            "rejected",
        }:
            raise ValueError(
                "Unsupported complaint status."
            )

        result = await (
            self.moderation.resolve_complaint(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
                status=normalized_status,
                reason=reason,
            )
        )

        return ApiAdminComplaintActionResult(
            complaint_id=result.entity_id,
            status=result.status,
            message=result.message,
        )


    async def escalate_complaint(
        self,
        *,
        actor: ApiActorContext,
        complaint_id: UUID,
        reason: str,
    ) -> ApiAdminComplaintActionResult:
        result = await (
            self.moderation
            .escalate_complaint_to_admin(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
                reason=reason,
            )
        )

        return ApiAdminComplaintActionResult(
            complaint_id=result.entity_id,
            status=result.status,
            message=result.message,
        )


def build_api_admin_complaints_service(
    session: AsyncSession,
) -> ApiAdminComplaintsService:
    return ApiAdminComplaintsService(
        moderation=ModerationService(
            ModerationRepository(session)
        ),
    )
