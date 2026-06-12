from dataclasses import dataclass
from uuid import UUID

from database.models import AdminAction, Complaint, EventLog, Specialist, User
from database.repositories.moderation import (
    ModerationAccessError,
    ModerationNotFoundError,
    ModerationRepository,
)
from database.repositories.rate_limit import RateLimitRepository
from services.rate_limit import RateLimitError, RateLimitService

class ModerationError(Exception):
    pass


@dataclass(frozen=True)
class ModerationActionResult:
    entity_id: UUID
    status: str
    message: str


class ModerationService:
    def __init__(
        self,
        repository: ModerationRepository,
        rate_limit_service: RateLimitService | None = None,
    ):
        self.repository = repository
        if rate_limit_service is not None:
            self.rate_limit_service = rate_limit_service
        elif hasattr(repository, "session"):
            self.rate_limit_service = RateLimitService(
                RateLimitRepository(repository.session)
            )
        else:
            self.rate_limit_service = None

    async def get_admin_roles(self, user_id: UUID) -> set[str]:
        return await self.repository.get_admin_roles(user_id)

    async def ensure_admin_access(self, user_id: UUID) -> set[str]:
        try:
            return await self.repository.require_admin_role(user_id)
        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

    async def list_recent_event_logs(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID | None = None,
        limit: int = 10,
    ) -> list[EventLog]:
        try:
            return await self.repository.list_recent_event_logs(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                limit=limit,
            )
        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

    async def list_recent_admin_actions(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID | None = None,
        limit: int = 10,
    ) -> list[AdminAction]:
        try:
            return await self.repository.list_recent_admin_actions(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                limit=limit,
            )
        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

    async def grant_admin_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_platform_user_id: int | str,
        role: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            role_mapping = await self.repository.grant_admin_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_platform_user_id=target_platform_user_id,
                role=role,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (ModerationAccessError, ModerationNotFoundError, ValueError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=role_mapping.user_id,
            status=role_mapping.status,
            message=f"Role {role_mapping.role} granted.",
        )

    async def revoke_admin_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_platform_user_id: int | str,
        role: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            role_mapping = await self.repository.revoke_admin_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_platform_user_id=target_platform_user_id,
                role=role,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (ModerationAccessError, ModerationNotFoundError, ValueError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=role_mapping.user_id,
            status=role_mapping.status,
            message=f"Role {role_mapping.role} revoked.",
        )

    async def list_pending_specialists(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Specialist]:
        try:
            return await self.repository.list_pending_specialists(
                admin_user_id=admin_user_id,
                limit=limit,
                offset=offset,
            )
        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

    async def approve_specialist(
        self,
        *,
        admin_user_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            specialist = await self.repository.approve_specialist(
                admin_user_id=admin_user_id,
                specialist_id=specialist_id,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (ModerationAccessError, ModerationNotFoundError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=specialist.id,
            status=specialist.status,
            message="Specialist approved.",
        )

    async def reject_specialist(
        self,
        *,
        admin_user_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            specialist = await self.repository.reject_specialist(
                admin_user_id=admin_user_id,
                specialist_id=specialist_id,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (ModerationAccessError, ModerationNotFoundError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=specialist.id,
            status=specialist.status,
            message="Specialist rejected.",
        )

    async def create_complaint(
        self,
        *,
        tenant_id: UUID,
        reporter_user_id: UUID,
        target_type: str,
        target_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> Complaint:
        normalized_reason = self._require_reason(reason)
        normalized_target_type = self._normalize_target_type(target_type)

        if self.rate_limit_service is not None:
            try:
                await self.rate_limit_service.ensure_complaint_allowed(
                    tenant_id=tenant_id,
                    user_id=reporter_user_id,
                )
            except RateLimitError as exc:
                raise ModerationError(str(exc)) from exc

        try:
            complaint = await self.repository.create_complaint(
                tenant_id=tenant_id,
                reporter_user_id=reporter_user_id,
                target_type=normalized_target_type,
                target_id=target_id,
                reason=normalized_reason,
                comment=(comment or "").strip() or None,
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

        return complaint

    async def list_open_complaints(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Complaint]:
        try:
            return await self.repository.list_open_complaints(
                admin_user_id=admin_user_id,
                limit=limit,
                offset=offset,
            )
        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

    async def resolve_complaint(
        self,
        *,
        admin_user_id: UUID,
        complaint_id: UUID,
        status: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            complaint = await self.repository.resolve_complaint(
                admin_user_id=admin_user_id,
                complaint_id=complaint_id,
                status=status,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (ModerationAccessError, ModerationNotFoundError, ValueError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=complaint.id,
            status=complaint.status,
            message="Complaint updated.",
        )

    async def block_user(
        self,
        *,
        admin_user_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            user = await self.repository.block_user(
                admin_user_id=admin_user_id,
                user_id=user_id,
                reason=normalized_reason,
                comment=(comment or "").strip() or None,
            )
            await self.repository.session.commit()
        except (ModerationAccessError, ModerationNotFoundError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=user.id,
            status=user.status,
            message="User blocked.",
        )

    def _require_reason(self, reason: str | None) -> str:
        normalized = (reason or "").strip()
        if len(normalized) < 3:
            raise ModerationError("Reason is required.")
        return normalized[:500]

    def _normalize_target_type(self, target_type: str) -> str:
        normalized = (target_type or "").strip().lower()
        if normalized not in {
            "specialist",
            "user",
            "message",
            "thread",
            "contact_request",
            "review",
            "portfolio_item",
        }:
            raise ModerationError("Unsupported complaint target type.")
        return normalized