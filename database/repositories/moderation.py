from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AdminAction,
    Blacklist,
    Complaint,
    EventLog,
    RiskFlag,
    Specialist,
    User,
    UserAccount,
    UserRoleMapping,
)


ADMIN_ROLES = {"super_admin", "admin", "moderator"}
BLOCK_USER_ROLES = {"super_admin", "admin"}
COMPLAINT_OPEN_STATUSES = {"new", "in_review"}


class ModerationAccessError(Exception):
    pass


class ModerationNotFoundError(Exception):
    pass


class ModerationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_admin_roles(self, user_id: UUID) -> set[str]:
        result = await self.session.execute(
            select(UserRoleMapping.role).where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(ADMIN_ROLES),
            )
        )
        return set(result.scalars().all())

    async def require_admin_role(
        self,
        user_id: UUID,
        allowed_roles: set[str] | None = None,
    ) -> set[str]:
        roles = await self.get_admin_roles(user_id)
        allowed = allowed_roles or ADMIN_ROLES

        if not roles.intersection(allowed):
            raise ModerationAccessError("Admin access denied.")

        return roles

    async def list_pending_specialists(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Specialist]:
        await self.require_admin_role(admin_user_id)

        result = await self.session.execute(
            select(Specialist)
            .where(Specialist.status == "pending_moderation")
            .order_by(Specialist.created_at.asc())
            .offset(max(int(offset), 0))
            .limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def approve_specialist(
        self,
        *,
        admin_user_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> Specialist:
        await self.require_admin_role(admin_user_id)

        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist:
            raise ModerationNotFoundError("Specialist not found.")

        before_state = self._specialist_audit_state(specialist)

        specialist.status = "active"
        specialist.moderation_comment = None
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=specialist.tenant_id,
            action_type="approve_specialist",
            target_type="specialist",
            target_id=specialist.id,
            before_state=before_state,
            after_state=self._specialist_audit_state(specialist),
            reason=reason,
        )
        await self.log_event(
            tenant_id=specialist.tenant_id,
            user_id=admin_user_id,
            event_type="specialist_approved",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={"reason": reason},
        )
        await self.session.flush()
        return specialist

    async def reject_specialist(
        self,
        *,
        admin_user_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> Specialist:
        await self.require_admin_role(admin_user_id)

        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist:
            raise ModerationNotFoundError("Specialist not found.")

        before_state = self._specialist_audit_state(specialist)

        specialist.status = "rejected"
        specialist.moderation_comment = reason
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=specialist.tenant_id,
            action_type="reject_specialist",
            target_type="specialist",
            target_id=specialist.id,
            before_state=before_state,
            after_state=self._specialist_audit_state(specialist),
            reason=reason,
        )
        await self.log_event(
            tenant_id=specialist.tenant_id,
            user_id=admin_user_id,
            event_type="specialist_rejected",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={"reason": reason},
        )
        await self.session.flush()
        return specialist

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
        complaint = Complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            comment=comment,
            status="new",
        )
        self.session.add(complaint)
        await self.session.flush()

        risk_flag = RiskFlag(
            tenant_id=tenant_id,
            entity_type=target_type,
            entity_id=target_id,
            flag_code=f"complaint_{reason}",
            severity="medium",
            status="open",
            details={
                "complaint_id": str(complaint.id),
                "reporter_user_id": str(reporter_user_id),
                "comment": comment,
            },
        )
        self.session.add(risk_flag)

        await self.log_event(
            tenant_id=tenant_id,
            user_id=reporter_user_id,
            event_type="complaint_created",
            entity_type=target_type,
            entity_id=target_id,
            payload={
                "complaint_id": str(complaint.id),
                "reason": reason,
            },
        )
        await self.session.flush()
        return complaint

    async def list_open_complaints(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Complaint]:
        await self.require_admin_role(admin_user_id)

        result = await self.session.execute(
            select(Complaint)
            .where(Complaint.status.in_(COMPLAINT_OPEN_STATUSES))
            .order_by(Complaint.created_at.asc())
            .offset(max(int(offset), 0))
            .limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def resolve_complaint(
        self,
        *,
        admin_user_id: UUID,
        complaint_id: UUID,
        status: str,
        reason: str,
    ) -> Complaint:
        await self.require_admin_role(admin_user_id)

        if status not in {"resolved", "rejected"}:
            raise ValueError("Unsupported complaint resolution status.")

        complaint = await self.session.get(Complaint, complaint_id)
        if not complaint:
            raise ModerationNotFoundError("Complaint not found.")

        before_state = self._complaint_audit_state(complaint)

        complaint.status = status
        complaint.reviewed_by = admin_user_id
        complaint.reviewed_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=complaint.tenant_id,
            action_type=f"{status}_complaint",
            target_type="complaint",
            target_id=complaint.id,
            before_state=before_state,
            after_state=self._complaint_audit_state(complaint),
            reason=reason,
        )
        await self.log_event(
            tenant_id=complaint.tenant_id,
            user_id=admin_user_id,
            event_type=f"complaint_{status}",
            entity_type="complaint",
            entity_id=complaint.id,
            payload={"reason": reason},
        )
        await self.session.flush()
        return complaint

    async def block_user(
        self,
        *,
        admin_user_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> User:
        await self.require_admin_role(admin_user_id, BLOCK_USER_ROLES)

        user = await self.session.get(User, user_id)
        if not user:
            raise ModerationNotFoundError("User not found.")

        before_state = self._user_audit_state(user)
        user.status = "blocked"
        user.updated_at = datetime.utcnow()

        account = await self.get_telegram_account(user.id)

        blacklist = Blacklist(
            tenant_id=user.tenant_id,
            user_id=user.id,
            platform=account.platform if account else "telegram",
            platform_user_id=account.platform_user_id if account else None,
            reason=reason,
            comment=comment,
            status="active",
            created_by=admin_user_id,
        )
        self.session.add(blacklist)
        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=user.tenant_id,
            action_type="block_user",
            target_type="user",
            target_id=user.id,
            before_state=before_state,
            after_state=self._user_audit_state(user),
            reason=reason,
        )
        await self.log_event(
            tenant_id=user.tenant_id,
            user_id=admin_user_id,
            event_type="user_blocked",
            entity_type="user",
            entity_id=user.id,
            payload={
                "reason": reason,
                "comment": comment,
                "blacklist_id": str(blacklist.id),
            },
        )
        await self.session.flush()
        return user

    async def get_telegram_account(self, user_id: UUID) -> UserAccount | None:
        result = await self.session.execute(
            select(UserAccount).where(
                UserAccount.user_id == user_id,
                UserAccount.platform == "telegram",
            )
        )
        return result.scalar_one_or_none()

    async def log_admin_action(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        action_type: str,
        target_type: str,
        target_id: UUID,
        before_state: dict,
        after_state: dict,
        reason: str,
    ) -> AdminAction:
        action = AdminAction(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def log_event(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        payload: dict,
    ) -> EventLog:
        event = EventLog(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            platform="telegram",
        )
        self.session.add(event)
        await self.session.flush()
        return event

    def _specialist_audit_state(self, specialist: Specialist) -> dict:
        return {
            "id": str(specialist.id),
            "user_id": str(specialist.user_id),
            "status": specialist.status,
            "moderation_comment": specialist.moderation_comment,
            "is_verified": bool(specialist.is_verified),
            "is_available": bool(specialist.is_available),
        }

    def _complaint_audit_state(self, complaint: Complaint) -> dict:
        return {
            "id": str(complaint.id),
            "reporter_user_id": str(complaint.reporter_user_id),
            "target_type": complaint.target_type,
            "target_id": str(complaint.target_id),
            "reason": complaint.reason,
            "status": complaint.status,
            "reviewed_by": str(complaint.reviewed_by) if complaint.reviewed_by else None,
        }

    def _user_audit_state(self, user: User) -> dict:
        return {
            "id": str(user.id),
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "status": user.status,
            "active_role": user.active_role,
            "risk_score": user.risk_score,
        }