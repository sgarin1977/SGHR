from uuid import UUID
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import func, select
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
    Review,
    SpecialistPortfolioItem,
    City,
    Profession,
    SpecialistService,
)


ADMIN_ROLES = {
    "super_admin",
    "admin",
    "moderator",
    "support",
    "finance_admin",
    "content_manager",
}
MODERATION_ROLES = {"super_admin", "admin", "moderator"}
BLOCK_USER_ROLES = {"super_admin", "admin"}
ROLE_MANAGEMENT_ROLES = {"super_admin"}
GRANTABLE_ADMIN_ROLES = {
    "admin",
    "moderator",
    "support",
    "finance_admin",
    "content_manager",
}
LOG_VIEW_ROLES = {"super_admin", "admin", "support"}
FULL_LOG_VIEW_ROLES = {"super_admin", "admin"}
COMPLAINT_OPEN_STATUSES = {"new", "in_review"}

@dataclass(frozen=True)
class PendingSpecialistQueueItem:
    specialist_id: UUID
    display_name: str
    profession_name: str
    city_name: str | None
    created_at: datetime


@dataclass(frozen=True)
class PendingSpecialistDetails:
    specialist_id: UUID
    owner_user_id: UUID
    display_name: str
    profession_name: str
    city_name: str | None
    status: str
    description: str
    contact_text: str | None
    service_titles: tuple[str, ...]
    complaints_count: int
    open_risk_flags_count: int
    created_at: datetime

@dataclass(frozen=True)
class ComplaintQueueItem:
    complaint_id: UUID
    reporter_user_id: UUID
    target_type: str
    target_id: UUID
    reason: str
    status: str
    created_at: datetime
    reviewed_by: UUID | None

@dataclass(frozen=True)
class ComplaintModerationDetails:
    complaint_id: UUID
    reporter_user_id: UUID
    target_type: str
    target_id: UUID
    target_label: str
    reason: str
    comment: str | None
    status: str
    created_at: datetime
    reviewed_by: UUID | None
    requires_admin_escalation: bool
    history: tuple[tuple[str, datetime], ...]

@dataclass(frozen=True)
class ScopedBlacklistQueueItem:
    blacklist_id: UUID
    user_id: UUID
    reason: str
    comment: str | None
    status: str
    user_status: str
    created_at: datetime
    created_by: UUID
    revoke_reason: str | None

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
        allowed = allowed_roles or MODERATION_ROLES

        if not roles.intersection(allowed):
            raise ModerationAccessError("Admin access denied.")

        return roles

    async def list_recent_event_logs(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID | None = None,
        limit: int = 10,
    ) -> list[EventLog]:
        await self.require_admin_role(admin_user_id, LOG_VIEW_ROLES)

        query = select(EventLog).order_by(EventLog.created_at.desc())

        if tenant_id:
            query = query.where(EventLog.tenant_id == tenant_id)

        result = await self.session.execute(
            query.limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def list_recent_admin_actions(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID | None = None,
        limit: int = 10,
    ) -> list[AdminAction]:
        await self.require_admin_role(admin_user_id, FULL_LOG_VIEW_ROLES)

        query = select(AdminAction).order_by(AdminAction.created_at.desc())

        if tenant_id:
            query = query.where(AdminAction.tenant_id == tenant_id)

        result = await self.session.execute(
            query.limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def get_user_by_telegram_id(self, platform_user_id: int | str) -> User | None:
        result = await self.session.execute(
            select(User)
            .join(UserAccount, UserAccount.user_id == User.id)
            .where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == str(platform_user_id),
            )
        )
        return result.scalar_one_or_none()

    async def grant_admin_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_platform_user_id: int | str,
        role: str,
        reason: str,
    ) -> UserRoleMapping:
        await self.require_admin_role(admin_user_id, ROLE_MANAGEMENT_ROLES)

        normalized_role = (role or "").strip().lower()
        if normalized_role not in GRANTABLE_ADMIN_ROLES:
            raise ValueError("Unsupported role for manual grant.")

        target_user = await self.get_user_by_telegram_id(target_platform_user_id)
        if not target_user:
            raise ModerationNotFoundError("Target user not found.")

        action_tenant_id = target_user.tenant_id or tenant_id

        existing = (
            await self.session.execute(
                select(UserRoleMapping)
                .where(
                    UserRoleMapping.user_id == target_user.id,
                    UserRoleMapping.role == normalized_role,
                )
                .order_by(UserRoleMapping.granted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        before_state = self._role_audit_state(existing)

        if existing:
            existing.status = "active"
            existing.tenant_id = action_tenant_id
            existing.granted_by = admin_user_id
            existing.granted_at = datetime.utcnow()
            role_mapping = existing
        else:
            role_mapping = UserRoleMapping(
                user_id=target_user.id,
                tenant_id=action_tenant_id,
                role=normalized_role,
                status="active",
                granted_by=admin_user_id,
            )
            self.session.add(role_mapping)

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=action_tenant_id,
            action_type="grant_admin_role",
            target_type="user",
            target_id=target_user.id,
            before_state=before_state,
            after_state=self._role_audit_state(role_mapping),
            reason=reason,
        )
        await self.log_event(
            tenant_id=action_tenant_id,
            user_id=admin_user_id,
            event_type="admin_role_granted",
            entity_type="user",
            entity_id=target_user.id,
            payload={
                "role": normalized_role,
                "target_platform_user_id": str(target_platform_user_id),
                "reason": reason,
            },
        )
        await self.session.flush()
        return role_mapping

    async def revoke_admin_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_platform_user_id: int | str,
        role: str,
        reason: str,
    ) -> UserRoleMapping:
        await self.require_admin_role(admin_user_id, ROLE_MANAGEMENT_ROLES)

        normalized_role = (role or "").strip().lower()
        if normalized_role not in GRANTABLE_ADMIN_ROLES:
            raise ValueError("Unsupported role for manual revoke.")

        target_user = await self.get_user_by_telegram_id(target_platform_user_id)
        if not target_user:
            raise ModerationNotFoundError("Target user not found.")

        role_mapping = (
            await self.session.execute(
                select(UserRoleMapping).where(
                    UserRoleMapping.user_id == target_user.id,
                    UserRoleMapping.role == normalized_role,
                    UserRoleMapping.status == "active",
                )
            )
        ).scalar_one_or_none()

        if not role_mapping:
            raise ModerationNotFoundError("Active role not found.")

        action_tenant_id = target_user.tenant_id or tenant_id
        before_state = self._role_audit_state(role_mapping)

        role_mapping.status = "revoked"

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=action_tenant_id,
            action_type="revoke_admin_role",
            target_type="user",
            target_id=target_user.id,
            before_state=before_state,
            after_state=self._role_audit_state(role_mapping),
            reason=reason,
        )
        await self.log_event(
            tenant_id=action_tenant_id,
            user_id=admin_user_id,
            event_type="admin_role_revoked",
            entity_type="user",
            entity_id=target_user.id,
            payload={
                "role": normalized_role,
                "target_platform_user_id": str(target_platform_user_id),
                "reason": reason,
            },
        )
        await self.session.flush()
        return role_mapping

    async def get_moderator_menu_counts(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, int]:
        await self.require_admin_role(
            admin_user_id,
            {"moderator", "admin", "super_admin"},
        )

        async def count_rows(model, *conditions) -> int:
            result = await self.session.execute(
                select(func.count())
                .select_from(model)
                .where(*conditions)
            )
            return int(result.scalar_one())

        return {
            "profiles": await count_rows(
                Specialist,
                Specialist.tenant_id == tenant_id,
                Specialist.status == "pending_moderation",
            ),
            "portfolio": await count_rows(
                SpecialistPortfolioItem,
                SpecialistPortfolioItem.tenant_id == tenant_id,
                SpecialistPortfolioItem.status == "pending_moderation",
            ),
            "reviews": await count_rows(
                Review,
                Review.tenant_id == tenant_id,
                Review.status == "pending_moderation",
            ),
            "complaints": await count_rows(
                Complaint,
                Complaint.tenant_id == tenant_id,
                Complaint.status.in_(COMPLAINT_OPEN_STATUSES),
            ),
            "blacklist": await count_rows(
                Blacklist,
                Blacklist.tenant_id == tenant_id,
                Blacklist.status == "active",
            ),
        }

    async def list_pending_specialists(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        limit: int = 5,
        offset: int = 0,
    ) -> list[PendingSpecialistQueueItem]:
        await self.require_admin_role(
            admin_user_id,
            {"moderator", "admin", "super_admin"},
        )

        result = await self.session.execute(
            select(
                Specialist.id,
                Specialist.display_name,
                Profession.name,
                City.name,
                Specialist.created_at,
            )
            .join(
                Profession,
                Profession.id == Specialist.profession_id,
            )
            .outerjoin(
                City,
                City.id == Specialist.city_id,
            )
            .where(
                Specialist.tenant_id == tenant_id,
                Specialist.status == "pending_moderation",
                Specialist.user_id != admin_user_id,
            )
            .order_by(
                Specialist.created_at.asc(),
                Specialist.id.asc(),
            )
            .offset(max(int(offset), 0))
            .limit(max(1, min(int(limit), 20)))
        )

        return [
            PendingSpecialistQueueItem(
                specialist_id=specialist_id,
                display_name=display_name,
                profession_name=profession_name,
                city_name=city_name,
                created_at=created_at,
            )
            for (
                specialist_id,
                display_name,
                profession_name,
                city_name,
                created_at,
            ) in result.all()
        ]

    async def get_pending_specialist_details(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
    ) -> PendingSpecialistDetails:
        await self.require_admin_role(
            admin_user_id,
            {"moderator", "admin", "super_admin"},
        )

        result = await self.session.execute(
            select(
                Specialist,
                Profession.name,
                City.name,
            )
            .join(
                Profession,
                Profession.id == Specialist.profession_id,
            )
            .outerjoin(
                City,
                City.id == Specialist.city_id,
            )
            .where(
                Specialist.id == specialist_id,
                Specialist.tenant_id == tenant_id,
            )
        )
        row = result.first()

        if not row:
            raise ModerationNotFoundError("Specialist not found.")

        specialist, profession_name, city_name = row

        if specialist.user_id == admin_user_id:
            raise ModerationAccessError(
                "You cannot moderate your own profile."
            )

        services_result = await self.session.execute(
            select(SpecialistService.title)
            .where(
                SpecialistService.tenant_id == tenant_id,
                SpecialistService.specialist_id == specialist.id,
                SpecialistService.status != "deleted",
            )
            .order_by(SpecialistService.created_at.asc())
            .limit(10)
        )
        service_titles = tuple(services_result.scalars().all())

        complaints_result = await self.session.execute(
            select(func.count())
            .select_from(Complaint)
            .where(
                Complaint.tenant_id == tenant_id,
                Complaint.target_type == "specialist",
                Complaint.target_id == specialist.id,
            )
        )
        complaints_count = int(complaints_result.scalar_one())

        risk_result = await self.session.execute(
            select(func.count())
            .select_from(RiskFlag)
            .where(
                RiskFlag.tenant_id == tenant_id,
                RiskFlag.entity_type == "specialist",
                RiskFlag.entity_id == specialist.id,
                RiskFlag.status == "open",
            )
        )
        open_risk_flags_count = int(
            risk_result.scalar_one()
        )

        metadata = specialist.extra_metadata or {}
        contact_text = (
            str(metadata.get("contact_text") or "").strip()
            or None
        )

        return PendingSpecialistDetails(
            specialist_id=specialist.id,
            owner_user_id=specialist.user_id,
            display_name=specialist.display_name,
            profession_name=profession_name,
            city_name=city_name,
            status=specialist.status,
            description=specialist.short_description,
            contact_text=contact_text,
            service_titles=service_titles,
            complaints_count=complaints_count,
            open_risk_flags_count=open_risk_flags_count,
            created_at=specialist.created_at,
        )

    async def approve_specialist(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> Specialist:
        await self.require_admin_role(
            admin_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Specialist).where(
                Specialist.id == specialist_id,
                Specialist.tenant_id == tenant_id,
            )
        )
        specialist = result.scalar_one_or_none()

        if not specialist:
            raise ModerationNotFoundError("Specialist not found.")

        if specialist.user_id == admin_user_id:
            raise ModerationAccessError(
                "You cannot moderate your own profile."
            )

        if specialist.status != "pending_moderation":
            raise ModerationAccessError(
                "Specialist profile is no longer pending moderation."
            )

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ModerationAccessError(
                "Moderation reason is required."
            )

        before_state = self._specialist_audit_state(specialist)

        specialist.status = "active"
        specialist.moderation_comment = None
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="approve_specialist",
            target_type="specialist",
            target_id=specialist.id,
            before_state=before_state,
            after_state=self._specialist_audit_state(specialist),
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="profile_moderated",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "decision": "approved",
                "reason": normalized_reason,
                "before_status": before_state["status"],
                "after_status": "active",
            },
        )

        await self.session.flush()
        return specialist
    async def reject_specialist(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> Specialist:
        await self.require_admin_role(
            admin_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Specialist).where(
                Specialist.id == specialist_id,
                Specialist.tenant_id == tenant_id,
            )
        )
        specialist = result.scalar_one_or_none()

        if not specialist:
            raise ModerationNotFoundError("Specialist not found.")

        if specialist.user_id == admin_user_id:
            raise ModerationAccessError(
                "You cannot moderate your own profile."
            )

        if specialist.status != "pending_moderation":
            raise ModerationAccessError(
                "Specialist profile is no longer pending moderation."
            )

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ModerationAccessError(
                "Moderation reason is required."
            )

        before_state = self._specialist_audit_state(specialist)

        specialist.status = "rejected"
        specialist.moderation_comment = normalized_reason
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="reject_specialist",
            target_type="specialist",
            target_id=specialist.id,
            before_state=before_state,
            after_state=self._specialist_audit_state(specialist),
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="profile_moderated",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "decision": "rejected",
                "reason": normalized_reason,
                "before_status": before_state["status"],
                "after_status": "rejected",
            },
        )

        await self.session.flush()
        return specialist

    async def request_specialist_changes(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> Specialist:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Specialist).where(
                Specialist.id == specialist_id,
                Specialist.tenant_id == tenant_id,
            )
        )
        specialist = result.scalar_one_or_none()

        if not specialist:
            raise ModerationNotFoundError("Specialist not found.")

        if specialist.user_id == moderator_user_id:
            raise ModerationAccessError(
                "You cannot moderate your own profile."
            )

        if specialist.status != "pending_moderation":
            raise ModerationAccessError(
                "Specialist profile is no longer pending moderation."
            )

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ModerationAccessError(
                "Moderation reason is required."
            )

        before_state = self._specialist_audit_state(specialist)

        specialist.status = "draft"
        specialist.moderation_comment = normalized_reason
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=moderator_user_id,
            tenant_id=tenant_id,
            action_type="request_specialist_changes",
            target_type="specialist",
            target_id=specialist.id,
            before_state=before_state,
            after_state=self._specialist_audit_state(specialist),
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
            event_type="profile_moderated",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "decision": "changes_requested",
                "reason": normalized_reason,
                "before_status": before_state["status"],
                "after_status": "draft",
            },
        )

        await self.session.flush()
        return specialist

    async def has_active_complaint(
        self,
        *,
        tenant_id: UUID,
        reporter_user_id: UUID,
        target_type: str,
        target_id: UUID,
        reason: str,
    ) -> bool:
        result = await self.session.execute(
            select(Complaint.id)
            .where(
                Complaint.tenant_id == tenant_id,
                Complaint.reporter_user_id == reporter_user_id,
                Complaint.target_type == target_type,
                Complaint.target_id == target_id,
                Complaint.reason == reason,
                Complaint.status.in_(COMPLAINT_OPEN_STATUSES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


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

    async def confirm_complaint(
        self,
        *,
        reporter_user_id: UUID,
        complaint_id: UUID,
    ) -> Complaint:
        complaint = await self.session.get(Complaint, complaint_id)

        if not complaint or complaint.reporter_user_id != reporter_user_id:
            raise ModerationNotFoundError("Complaint not found.")

        await self.log_event(
            tenant_id=complaint.tenant_id,
            user_id=reporter_user_id,
            event_type="complaint_confirmed",
            entity_type="complaint",
            entity_id=complaint.id,
            payload={
                "complaint_number": str(complaint.id).split("-", 1)[0],
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

    async def list_complaints_queue(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        statuses: set[str],
        limit: int = 6,
        offset: int = 0,
    ) -> list[ComplaintQueueItem]:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        allowed_statuses = {
            "new",
            "in_review",
            "resolved",
            "rejected",
        }
        normalized_statuses = set(statuses).intersection(
            allowed_statuses
        )

        if not normalized_statuses:
            normalized_statuses = {
                "new",
                "in_review",
            }

        normalized_limit = max(
            1,
            min(int(limit), 20),
        )
        normalized_offset = max(
            0,
            int(offset),
        )

        result = await self.session.execute(
            select(
                Complaint.id,
                Complaint.reporter_user_id,
                Complaint.target_type,
                Complaint.target_id,
                Complaint.reason,
                Complaint.status,
                Complaint.created_at,
                Complaint.reviewed_by,
            )
            .where(
                Complaint.tenant_id == tenant_id,
                Complaint.status.in_(normalized_statuses),
            )
            .order_by(
                Complaint.created_at.asc(),
                Complaint.id.asc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return [
            ComplaintQueueItem(
                complaint_id=row.id,
                reporter_user_id=row.reporter_user_id,
                target_type=row.target_type,
                target_id=row.target_id,
                reason=row.reason,
                status=row.status,
                created_at=row.created_at,
                reviewed_by=row.reviewed_by,
            )
            for row in result.all()
        ]

    async def get_complaint_target_context(
        self,
        *,
        tenant_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> tuple[str, bool]:
        owner_user_id = None
        target_label = target_type.replace("_", " ").title()

        if target_type == "specialist":
            result = await self.session.execute(
                select(
                    Specialist.display_name,
                    Specialist.user_id,
                ).where(
                    Specialist.id == target_id,
                    Specialist.tenant_id == tenant_id,
                )
            )
            row = result.one_or_none()

            if row:
                target_label = row.display_name
                owner_user_id = row.user_id

        elif target_type == "user":
            result = await self.session.execute(
                select(User.id).where(
                    User.id == target_id,
                    User.tenant_id == tenant_id,
                )
            )
            owner_user_id = result.scalar_one_or_none()

            if owner_user_id:
                token = str(owner_user_id).replace("-", "")[:8]
                target_label = f"user-{token}"

        elif target_type == "portfolio_item":
            result = await self.session.execute(
                select(
                    SpecialistPortfolioItem.title,
                    Specialist.user_id,
                )
                .join(
                    Specialist,
                    Specialist.id
                    == SpecialistPortfolioItem.specialist_id,
                )
                .where(
                    SpecialistPortfolioItem.id == target_id,
                    SpecialistPortfolioItem.tenant_id == tenant_id,
                )
            )
            row = result.one_or_none()

            if row:
                target_label = (
                    row.title
                    or "Portfolio item"
                )
                owner_user_id = row.user_id

        elif target_type == "review":
            result = await self.session.execute(
                select(
                    Review.target_type,
                    Review.target_id,
                ).where(
                    Review.id == target_id,
                    Review.tenant_id == tenant_id,
                )
            )
            review_row = result.one_or_none()

            if (
                review_row
                and review_row.target_type == "specialist"
            ):
                specialist_result = await self.session.execute(
                    select(
                        Specialist.display_name,
                        Specialist.user_id,
                    ).where(
                        Specialist.id == review_row.target_id,
                        Specialist.tenant_id == tenant_id,
                    )
                )
                specialist_row = specialist_result.one_or_none()

                if specialist_row:
                    target_label = (
                        f"Review: {specialist_row.display_name}"
                    )
                    owner_user_id = specialist_row.user_id

        requires_admin_escalation = False

        if owner_user_id:
            role_result = await self.session.execute(
                select(func.count())
                .select_from(UserRoleMapping)
                .where(
                    UserRoleMapping.user_id == owner_user_id,
                    UserRoleMapping.status == "active",
                    UserRoleMapping.role.in_(
                        {
                            "admin",
                            "super_admin",
                        }
                    ),
                )
            )
            requires_admin_escalation = (
                int(role_result.scalar_one()) > 0
            )

        return (
            target_label,
            requires_admin_escalation,
        )

    async def get_complaint_moderation_details(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
    ) -> ComplaintModerationDetails:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Complaint).where(
                Complaint.id == complaint_id,
                Complaint.tenant_id == tenant_id,
            )
        )
        complaint = result.scalar_one_or_none()

        if not complaint:
            raise ModerationNotFoundError(
                "Complaint not found."
            )

        (
            target_label,
            requires_admin_escalation,
        ) = await self.get_complaint_target_context(
            tenant_id=tenant_id,
            target_type=complaint.target_type,
            target_id=complaint.target_id,
        )

        history_result = await self.session.execute(
            select(
                EventLog.event_type,
                EventLog.created_at,
            )
            .where(
                EventLog.tenant_id == tenant_id,
                EventLog.entity_type == "complaint",
                EventLog.entity_id == complaint.id,
            )
            .order_by(EventLog.created_at.desc())
            .limit(10)
        )

        history = tuple(
            (event_type, created_at)
            for event_type, created_at in history_result.all()
        )

        return ComplaintModerationDetails(
            complaint_id=complaint.id,
            reporter_user_id=complaint.reporter_user_id,
            target_type=complaint.target_type,
            target_id=complaint.target_id,
            target_label=target_label,
            reason=complaint.reason,
            comment=complaint.comment,
            status=complaint.status,
            created_at=complaint.created_at,
            reviewed_by=complaint.reviewed_by,
            requires_admin_escalation=requires_admin_escalation,
            history=history,
        )

    async def get_complaint_target_user_id(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
    ) -> UUID:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Complaint).where(
                Complaint.id == complaint_id,
                Complaint.tenant_id == tenant_id,
            )
        )
        complaint = result.scalar_one_or_none()

        if not complaint:
            raise ModerationNotFoundError(
                "Complaint not found."
            )

        target_user_id = None

        if complaint.target_type == "user":
            result = await self.session.execute(
                select(User.id).where(
                    User.id == complaint.target_id,
                    User.tenant_id == tenant_id,
                )
            )
            target_user_id = result.scalar_one_or_none()

        elif complaint.target_type == "specialist":
            result = await self.session.execute(
                select(Specialist.user_id).where(
                    Specialist.id == complaint.target_id,
                    Specialist.tenant_id == tenant_id,
                )
            )
            target_user_id = result.scalar_one_or_none()

        elif complaint.target_type == "portfolio_item":
            result = await self.session.execute(
                select(Specialist.user_id)
                .join(
                    SpecialistPortfolioItem,
                    SpecialistPortfolioItem.specialist_id
                    == Specialist.id,
                )
                .where(
                    SpecialistPortfolioItem.id
                    == complaint.target_id,
                    SpecialistPortfolioItem.tenant_id
                    == tenant_id,
                    Specialist.tenant_id == tenant_id,
                )
            )
            target_user_id = result.scalar_one_or_none()

        elif complaint.target_type == "review":
            result = await self.session.execute(
                select(Review.reviewer_user_id).where(
                    Review.id == complaint.target_id,
                    Review.tenant_id == tenant_id,
                )
            )
            target_user_id = result.scalar_one_or_none()

        if not target_user_id:
            raise ModerationNotFoundError(
                "Complaint target user not found."
            )

        roles_result = await self.session.execute(
            select(func.count())
            .select_from(UserRoleMapping)
            .where(
                UserRoleMapping.user_id == target_user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(
                    {
                        "admin",
                        "super_admin",
                    }
                ),
            )
        )

        if int(roles_result.scalar_one()) > 0:
            raise ModerationAccessError(
                "Admin target requires escalation."
            )

        return target_user_id

    async def take_complaint(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
    ) -> Complaint:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Complaint)
            .where(
                Complaint.id == complaint_id,
                Complaint.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        complaint = result.scalar_one_or_none()

        if not complaint:
            raise ModerationNotFoundError(
                "Complaint not found."
            )

        if complaint.status != "new":
            raise ValueError(
                "Complaint is no longer available."
            )

        _, requires_admin_escalation = (
            await self.get_complaint_target_context(
                tenant_id=tenant_id,
                target_type=complaint.target_type,
                target_id=complaint.target_id,
            )
        )

        if requires_admin_escalation:
            raise ModerationAccessError(
                "Admin target requires escalation."
            )

        before_state = self._complaint_audit_state(
            complaint
        )

        complaint.status = "in_review"
        complaint.reviewed_by = moderator_user_id
        complaint.reviewed_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=moderator_user_id,
            tenant_id=tenant_id,
            action_type="take_complaint",
            target_type="complaint",
            target_id=complaint.id,
            before_state=before_state,
            after_state=self._complaint_audit_state(
                complaint
            ),
            reason="Taken by moderator",
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
            event_type="complaint_in_review",
            entity_type="complaint",
            entity_id=complaint.id,
            payload={
                "action": "taken",
                "before_status": before_state["status"],
                "after_status": complaint.status,
            },
        )

        await self.session.flush()
        return complaint

    async def escalate_complaint_to_admin(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
        reason: str,
    ) -> Complaint:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Complaint)
            .where(
                Complaint.id == complaint_id,
                Complaint.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        complaint = result.scalar_one_or_none()

        if not complaint:
            raise ModerationNotFoundError(
                "Complaint not found."
            )

        if complaint.status not in {"new", "in_review"}:
            raise ModerationAccessError(
                "Only an active complaint can be escalated."
            )

        existing_result = await self.session.execute(
            select(EventLog.id).where(
                EventLog.tenant_id == tenant_id,
                EventLog.entity_type == "complaint",
                EventLog.entity_id == complaint.id,
                EventLog.event_type == "complaint_escalated",
            )
        )

        if existing_result.scalar_one_or_none():
            raise ModerationAccessError(
                "Complaint has already been escalated."
            )

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ModerationAccessError(
                "Escalation reason is required."
            )

        audit_state = self._complaint_audit_state(
            complaint
        )

        await self.log_admin_action(
            admin_user_id=moderator_user_id,
            tenant_id=tenant_id,
            action_type="escalate_complaint",
            target_type="complaint",
            target_id=complaint.id,
            before_state={
                **audit_state,
                "escalated": False,
            },
            after_state={
                **audit_state,
                "escalated": True,
            },
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
            event_type="complaint_escalated",
            entity_type="complaint",
            entity_id=complaint.id,
            payload={
                "destination": "admin",
                "reason": normalized_reason,
                "status": complaint.status,
            },
        )

        await self.session.flush()
        return complaint
    
    async def resolve_complaint(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
        status: str,
        reason: str,
    ) -> Complaint:
        await self.require_admin_role(admin_user_id)
        result = await self.session.execute(
            select(Complaint)
            .where(
                Complaint.id == complaint_id,
                Complaint.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        complaint = result.scalar_one_or_none()
        if not complaint:
            raise ModerationNotFoundError("Complaint not found.")

        allowed_transitions = {
            "new": {"in_review", "rejected"},
            "in_review": {"resolved", "rejected"},
            "resolved": set(),
            "rejected": set(),
        }

        allowed_statuses = allowed_transitions.get(complaint.status)
        if allowed_statuses is None:
            raise ValueError(
                f"Unsupported complaint status: {complaint.status}."
            )

        if status not in allowed_statuses:
            raise ValueError(
                f"Complaint transition from {complaint.status} "
                f"to {status} is not allowed."
            )

        before_state = self._complaint_audit_state(complaint)

        complaint.status = status
        complaint.reviewed_by = admin_user_id
        complaint.reviewed_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=f"{status}_complaint",
            target_type="complaint",
            target_id=complaint.id,
            before_state=before_state,
            after_state=self._complaint_audit_state(complaint),
            reason=reason,
        )
        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type=f"complaint_{status}",
            entity_type="complaint",
            entity_id=complaint.id,
            payload={"reason": reason},
        )
        await self.session.flush()
        return complaint

    async def create_portfolio_risk_flag(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        item_id: UUID,
        reason: str,
    ) -> RiskFlag:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        item_result = await self.session.execute(
            select(SpecialistPortfolioItem.id).where(
                SpecialistPortfolioItem.id == item_id,
                SpecialistPortfolioItem.tenant_id
                == tenant_id,
            )
        )

        if not item_result.scalar_one_or_none():
            raise ModerationNotFoundError(
                "Portfolio item not found."
            )

        existing_result = await self.session.execute(
            select(RiskFlag).where(
                RiskFlag.tenant_id == tenant_id,
                RiskFlag.entity_type == "portfolio_item",
                RiskFlag.entity_id == item_id,
                RiskFlag.flag_code
                == "forbidden_portfolio_content",
                RiskFlag.status == "open",
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            return existing

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ModerationAccessError(
                "Risk reason is required."
            )

        risk_flag = RiskFlag(
            tenant_id=tenant_id,
            entity_type="portfolio_item",
            entity_id=item_id,
            flag_code="forbidden_portfolio_content",
            severity="high",
            status="open",
            details={
                "reason": normalized_reason,
                "created_by": str(moderator_user_id),
            },
        )
        self.session.add(risk_flag)
        await self.session.flush()

        await self.log_event(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
            event_type="portfolio_risk_flagged",
            entity_type="portfolio_item",
            entity_id=item_id,
            payload={
                "flag_code": "forbidden_portfolio_content",
                "severity": "high",
                "reason": normalized_reason,
            },
        )

        await self.session.flush()
        return risk_flag

    async def add_specialist_owner_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> Blacklist:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Specialist.user_id).where(
                Specialist.id == specialist_id,
                Specialist.tenant_id == tenant_id,
            )
        )
        owner_user_id = result.scalar_one_or_none()

        if not owner_user_id:
            raise ModerationNotFoundError(
                "Specialist profile not found."
            )

        return await self.add_scoped_blacklist(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            user_id=owner_user_id,
            reason=reason,
            comment=comment,
        )

    async def get_scoped_blacklist_revoke_reason(
        self,
        *,
        tenant_id: UUID,
        blacklist_id: UUID,
    ) -> str | None:
        result = await self.session.execute(
            select(EventLog.payload)
            .where(
                EventLog.tenant_id == tenant_id,
                EventLog.event_type
                == "scoped_blacklist_changed",
                EventLog.entity_type == "user",
                EventLog.payload["blacklist_id"].astext
                == str(blacklist_id),
                EventLog.payload["action"].astext
                == "revoked",
            )
            .order_by(EventLog.created_at.desc())
            .limit(1)
        )

        payload = result.scalar_one_or_none()

        if not payload:
            return None

        return (
            str(payload.get("reason") or "").strip()
            or None
        )

    async def list_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        statuses: set[str],
        limit: int,
        offset: int,
    ) -> list[ScopedBlacklistQueueItem]:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        allowed_statuses = {
            "active",
            "revoked",
        }
        normalized_statuses = (
            set(statuses) & allowed_statuses
        )

        if not normalized_statuses:
            normalized_statuses = {"active"}

        normalized_limit = max(
            1,
            min(int(limit), 11),
        )
        normalized_offset = max(
            0,
            int(offset),
        )

        result = await self.session.execute(
            select(
                Blacklist.id,
                Blacklist.user_id,
                Blacklist.reason,
                Blacklist.comment,
                Blacklist.status,
                User.status.label("user_status"),
                Blacklist.created_at,
                Blacklist.created_by,
            )
            .join(
                User,
                User.id == Blacklist.user_id,
            )
            .where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.status.in_(
                    normalized_statuses
                ),
            )
            .order_by(
                Blacklist.created_at.desc(),
                Blacklist.id.desc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        items = []

        for row in result.all():
            revoke_reason = None

            if row.status == "revoked":
                revoke_reason = (
                    await self
                    .get_scoped_blacklist_revoke_reason(
                        tenant_id=tenant_id,
                        blacklist_id=row.id,
                    )
                )

            items.append(
                ScopedBlacklistQueueItem(
                    blacklist_id=row.id,
                    user_id=row.user_id,
                    reason=row.reason,
                    comment=row.comment,
                    status=row.status,
                    user_status=row.user_status,
                    created_at=row.created_at,
                    created_by=row.created_by,
                    revoke_reason=revoke_reason,
                )
            )

        return items

    async def add_scoped_blacklist_by_telegram_id(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        telegram_id: str,
        reason: str,
    ) -> Blacklist:
        normalized_telegram_id = telegram_id.strip()

        if not normalized_telegram_id.isdigit():
            raise ModerationAccessError(
                "Telegram ID must contain only digits."
            )

        user = await self.get_user_by_telegram_id(
            normalized_telegram_id
        )

        if not user or user.tenant_id != tenant_id:
            raise ModerationNotFoundError(
                "User not found in this tenant."
            )

        return await self.add_scoped_blacklist(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            user_id=user.id,
            reason=reason,
            comment=(
                "Added manually by moderator "
                f"using Telegram ID {normalized_telegram_id}"
            ),
        )

    async def add_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> Blacklist:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise ModerationNotFoundError(
                "User not found in this tenant."
            )

        if user.id == moderator_user_id:
            raise ModerationAccessError(
                "You cannot blacklist yourself."
            )

        existing_result = await self.session.execute(
            select(Blacklist).where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.user_id == user_id,
                Blacklist.status == "active",
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            raise ModerationAccessError(
                "User is already blacklisted in this tenant."
            )

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ModerationAccessError(
                "Blacklist reason is required."
            )

        account = await self.get_telegram_account(user.id)

        blacklist = Blacklist(
            tenant_id=tenant_id,
            user_id=user.id,
            platform=account.platform if account else "telegram",
            platform_user_id=(
                account.platform_user_id
                if account
                else None
            ),
            reason=normalized_reason,
            comment=(comment or "").strip() or None,
            status="active",
            created_by=moderator_user_id,
        )
        self.session.add(blacklist)
        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=moderator_user_id,
            tenant_id=tenant_id,
            action_type="add_scoped_blacklist",
            target_type="user",
            target_id=user.id,
            before_state={
                "tenant_blacklisted": False,
                "user_status": user.status,
            },
            after_state={
                "tenant_blacklisted": True,
                "user_status": user.status,
                "blacklist_id": str(blacklist.id),
            },
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
            event_type="scoped_blacklist_changed",
            entity_type="user",
            entity_id=user.id,
            payload={
                "action": "added",
                "scope": "tenant",
                "reason": normalized_reason,
                "blacklist_id": str(blacklist.id),
            },
        )

        await self.session.flush()
        return blacklist

    async def revoke_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        blacklist_id: UUID,
        reason: str,
    ) -> Blacklist:
        await self.require_admin_role(
            moderator_user_id,
            MODERATION_ROLES,
        )

        result = await self.session.execute(
            select(Blacklist)
            .where(
                Blacklist.id == blacklist_id,
                Blacklist.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        blacklist = result.scalar_one_or_none()

        if not blacklist:
            raise ModerationNotFoundError(
                "Scoped blacklist record not found."
            )

        if blacklist.status != "active":
            raise ModerationAccessError(
                "Scoped blacklist record is no longer active."
            )

        user_result = await self.session.execute(
            select(User).where(
                User.id == blacklist.user_id,
                User.tenant_id == tenant_id,
            )
        )
        user = user_result.scalar_one_or_none()

        if not user:
            raise ModerationNotFoundError(
                "Blacklisted user not found."
            )

        if user.status == "blocked":
            raise ModerationAccessError(
                "Moderator cannot remove a global block."
            )

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ModerationAccessError(
                "Revoke reason is required."
            )

        before_status = blacklist.status
        blacklist.status = "revoked"

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=moderator_user_id,
            tenant_id=tenant_id,
            action_type="revoke_scoped_blacklist",
            target_type="user",
            target_id=blacklist.user_id,
            before_state={
                "blacklist_id": str(blacklist.id),
                "blacklist_status": before_status,
                "user_status": user.status,
                "scope": "tenant",
            },
            after_state={
                "blacklist_id": str(blacklist.id),
                "blacklist_status": blacklist.status,
                "user_status": user.status,
                "scope": "tenant",
            },
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
            event_type="scoped_blacklist_changed",
            entity_type="user",
            entity_id=blacklist.user_id,
            payload={
                "action": "revoked",
                "scope": "tenant",
                "reason": normalized_reason,
                "blacklist_id": str(blacklist.id),
            },
        )

        await self.session.flush()
        return blacklist

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
    
    def _role_audit_state(self, role_mapping: UserRoleMapping | None) -> dict:
        if not role_mapping:
            return {"status": None}

        return {
            "id": str(role_mapping.id),
            "user_id": str(role_mapping.user_id),
            "tenant_id": str(role_mapping.tenant_id) if role_mapping.tenant_id else None,
            "role": role_mapping.role,
            "status": role_mapping.status,
            "granted_by": str(role_mapping.granted_by) if role_mapping.granted_by else None,
        }