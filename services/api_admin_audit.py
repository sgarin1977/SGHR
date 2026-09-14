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
    ModerationService,
)


@dataclass(frozen=True)
class ApiAdminAuditView:
    id: UUID
    date: str
    actor: str
    action: str
    target: str
    target_type: str
    reason: str
    source: str


@dataclass(frozen=True)
class ApiAdminAuditPage:
    items: tuple[ApiAdminAuditView, ...]
    page: int
    has_next: bool


class ApiAdminAuditService:
    def __init__(
        self,
        *,
        moderation: ModerationService,
    ) -> None:
        self.moderation = moderation

    async def list_audit(
        self,
        *,
        actor: ApiActorContext,
        actor_user_id: UUID | None = None,
        actions: tuple[str, ...] = (),
        target_types: tuple[str, ...] = (),
        target_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 0,
        page_size: int = 20,
    ) -> ApiAdminAuditPage:
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 100),
        )
        normalized_actions = {
            value.strip()
            for value in actions
            if (
                isinstance(value, str)
                and value.strip()
            )
        }
        normalized_target_types = {
            value.strip()
            for value in target_types
            if (
                isinstance(value, str)
                and value.strip()
            )
        }

        result = await (
            self.moderation.open_admin_audit(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                actor_user_id=actor_user_id,
                actions=normalized_actions,
                target_types=(
                    normalized_target_types
                ),
                target_id=target_id,
                date_from=date_from,
                date_to=date_to,
                page=normalized_page,
                page_size=normalized_size,
            )
        )

        return ApiAdminAuditPage(
            items=tuple(
                ApiAdminAuditView(
                    id=item.action_id,
                    date=item.date,
                    actor=item.actor,
                    action=item.action,
                    target=item.target,
                    target_type=(
                        item.target_type
                    ),
                    reason=item.reason,
                    source=item.source,
                )
                for item in result.items
            ),
            page=normalized_page,
            has_next=result.has_next,
        )


def build_api_admin_audit_service(
    session: AsyncSession,
) -> ApiAdminAuditService:
    return ApiAdminAuditService(
        moderation=ModerationService(
            ModerationRepository(session)
        ),
    )
