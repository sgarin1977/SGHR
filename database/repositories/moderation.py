from uuid import UUID
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import (
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    union_all,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AdminAction,
    Blacklist,
    RoleScope,
    Complaint,
    EventLog,
    RiskFlag,
    Specialist,
    SupportTicket,
    ConversationThread,
    Message,
    User,
    UserAccount,
    UserRoleMapping,
    Review,
    SpecialistPortfolioItem,
    City,
    Profession,
    Country,
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
SUPER_ADMIN_GRANTABLE_ROLES = {
    "admin",
    "moderator",
    "support",
    "finance_admin",
    "content_manager",
    "super_admin",
}

SUPER_ADMIN_PERMISSION_ROLES = {
    "client",
    "specialist",
    "support",
    "moderator",
    "admin",
    "super_admin",
    "finance_admin",
    "content_manager",
}

LOG_VIEW_ROLES = {"super_admin", "admin"}
FULL_LOG_VIEW_ROLES = {"super_admin", "admin"}
COMPLAINT_OPEN_STATUSES = {"new", "in_review"}

@dataclass(frozen=True)
class AdminUserHistoryRow:
    created_at: datetime
    actor_user_id: UUID | None
    action: str
    reason: str | None
    source: str

@dataclass(frozen=True)
class AdminUserDetailsRow:
    user_id: UUID
    username: str | None
    first_name: str | None
    last_name: str | None
    status: str
    last_seen_at: datetime | None
    roles: tuple[str, ...]
    complaints_count: int
    is_global_blacklisted: bool

@dataclass(frozen=True)
class AdminUserSearchRow:
    user_id: UUID
    platform_user_id: str
    username: str | None
    first_name: str | None
    last_name: str | None
    status: str

@dataclass(frozen=True)
class SuperAdminUserSearchRow:
    user_id: UUID
    platform_user_id: str | None
    username: str | None
    first_name: str | None
    display_name: str | None
    status: str
    roles: str | None

@dataclass(frozen=True)
class SuperAdminUserDetailsRow:
    user_id: UUID
    platform_user_id: str | None
    username: str | None
    first_name: str | None
    display_name: str | None
    status: str
    active_role: str | None
    last_seen_at: datetime | None
    risk_score: int | None
    roles: str | None
    complaints_count: int
    blacklist_count: int

@dataclass(frozen=True)
class SuperAdminUserRoleRow:
    role_id: UUID
    role: str
    status: str
    tenant_id: UUID | None
    granted_by: UUID | None
    granted_at: datetime | None

@dataclass(frozen=True)
class SuperAdminPermissionMatrixRow:
    permission_id: UUID
    role: str
    permission_code: str
    description: str | None
    scope: str
    status: str
    granted_by: str | None
    created_at: datetime | None

@dataclass(frozen=True)
class AdminSpecialistQueueItem:
    specialist_id: UUID
    display_name: str
    profession_name: str
    city_name: str | None
    status: str
    created_at: datetime

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

@dataclass(frozen=True)
class GlobalBlacklistQueueItem:
    blacklist_id: UUID
    user_id: UUID
    reason: str
    comment: str | None
    status: str
    user_status: str
    created_at: datetime
    created_by: UUID

@dataclass(frozen=True)
class AdminThreadMessageRow:
    thread_id: UUID
    thread_status: str
    context_id: UUID
    client_user_id: UUID
    specialist_id: UUID
    message_id: UUID
    sender_user_id: UUID
    receiver_user_id: UUID
    original_text: str
    translated_text: str | None
    is_masked: bool
    is_system: bool
    risk_detected_types: tuple[str, ...]
    risk_severity: str | None
    created_at: datetime

@dataclass(frozen=True)
class AdminThreadContextRow:
    thread_id: UUID
    thread_status: str
    context_id: UUID
    client_user_id: UUID
    specialist_id: UUID
    messages_count: int
    has_complaint: bool
    has_risk_flag: bool
    updated_at: datetime

@dataclass(frozen=True)
class SuperAdminRoleScopeRow:
    scope_id: UUID
    user_id: UUID
    role: str
    scope_type: str
    scope_value: str
    status: str
    reason: str
    created_by: UUID
    created_at: datetime
    revoked_by: UUID | None
    revoked_at: datetime | None

@dataclass(frozen=True)
class AdminAuditQueueItem:
    action_id: UUID
    actor_user_id: UUID | None
    action: str
    target_type: str
    target_id: UUID | None
    reason: str | None
    created_at: datetime
    source: str = "admin_action"

@dataclass(frozen=True)
class SuperAdminAuditEventDetailRow:
    action_id: UUID
    actor_user_id: UUID | None
    action: str
    target_type: str
    target_id: UUID | None
    reason: str | None
    before_state: dict
    after_state: dict
    payload: dict
    created_at: datetime
    source: str
    correlation_id: str | None

@dataclass(frozen=True)
class SuperAdminSystemStatusRow:
    db_status: str
    db_version: str
    migration_version: str
    migrations_table_exists: bool

class ModerationAccessError(Exception):
    pass


class ModerationNotFoundError(Exception):
    pass


class ModerationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def count_specialists_by_status(
        self,
        *,
        status: str,
    ) -> int:
        result = await self.session.execute(
            select(func.count(Specialist.id)).where(
                Specialist.status == status
            )
        )

        return int(result.scalar_one() or 0)

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

    async def list_admin_thread_contexts(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        limit: int = 20,
    ) -> list[AdminThreadContextRow]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin", "admin", "moderator"},
        )

        complaint_exists = select(Complaint.id).where(
            Complaint.tenant_id == tenant_id,
            Complaint.target_type == "thread",
            Complaint.target_id == ConversationThread.id,
            Complaint.status.in_(COMPLAINT_OPEN_STATUSES),
        ).exists()

        thread_risk_exists = select(RiskFlag.id).where(
            RiskFlag.tenant_id == tenant_id,
            RiskFlag.entity_type == "thread",
            RiskFlag.entity_id == ConversationThread.id,
            RiskFlag.status == "open",
        ).exists()

        message_risk_exists = select(Message.id).where(
            Message.tenant_id == tenant_id,
            Message.thread_id == ConversationThread.id,
            select(RiskFlag.id).where(
                RiskFlag.tenant_id == tenant_id,
                RiskFlag.entity_type == "message",
                RiskFlag.entity_id == Message.id,
                RiskFlag.status == "open",
            ).exists(),
        ).exists()

        risk_exists = or_(
            thread_risk_exists,
            message_risk_exists,
        )

        messages_count = (
            select(func.count(Message.id))
            .where(
                Message.tenant_id == tenant_id,
                Message.thread_id == ConversationThread.id,
            )
            .correlate(ConversationThread)
            .scalar_subquery()
        )

        result = await self.session.execute(
            select(
                ConversationThread.id,
                ConversationThread.status,
                ConversationThread.context_id,
                ConversationThread.client_user_id,
                ConversationThread.specialist_id,
                case(
                    (complaint_exists, 1),
                    else_=0,
                ).label("has_complaint"),
                case(
                    (risk_exists, 1),
                    else_=0,
                ).label("has_risk_flag"),
                messages_count.label("messages_count"),
                ConversationThread.updated_at,
            )
            .where(
                ConversationThread.tenant_id == tenant_id,
                or_(
                    complaint_exists,
                    risk_exists,
                ),
            )
            .order_by(
                ConversationThread.updated_at.desc(),
                ConversationThread.id.desc(),
            )
            .limit(max(1, min(int(limit), 50)))
        )

        return [
            AdminThreadContextRow(
                thread_id=row.id,
                thread_status=row.status,
                context_id=row.context_id,
                client_user_id=row.client_user_id,
                specialist_id=row.specialist_id,
                messages_count=int(row.messages_count or 0),
                has_complaint=bool(row.has_complaint),
                has_risk_flag=bool(row.has_risk_flag),
                updated_at=row.updated_at,
            )
            for row in result
        ]

    async def list_admin_thread_messages_for_thread(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        thread_id: UUID,
        limit: int = 50,
    ) -> list[AdminThreadMessageRow]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin", "admin", "moderator"},
        )

        thread = await self.session.get(
            ConversationThread,
            thread_id,
        )

        if not thread or thread.tenant_id != tenant_id:
            raise ModerationNotFoundError(
                "Conversation thread not found."
            )

        complaint_exists = await self.session.scalar(
            select(func.count())
            .select_from(Complaint)
            .where(
                Complaint.tenant_id == tenant_id,
                Complaint.target_type == "thread",
                Complaint.target_id == thread_id,
                Complaint.status.in_(COMPLAINT_OPEN_STATUSES),
            )
        )

        thread_risk_exists = await self.session.scalar(
            select(func.count())
            .select_from(RiskFlag)
            .where(
                RiskFlag.tenant_id == tenant_id,
                RiskFlag.entity_type == "thread",
                RiskFlag.entity_id == thread_id,
                RiskFlag.status == "open",
            )
        )

        message_risk_exists = await self.session.scalar(
            select(func.count())
            .select_from(RiskFlag)
            .join(
                Message,
                Message.id == RiskFlag.entity_id,
            )
            .where(
                RiskFlag.tenant_id == tenant_id,
                RiskFlag.entity_type == "message",
                RiskFlag.status == "open",
                Message.tenant_id == tenant_id,
                Message.thread_id == thread_id,
            )
        )


        if not any(
            (
                complaint_exists,
                thread_risk_exists,
                message_risk_exists,
            )
        ):
            raise ModerationAccessError(
                "Thread can be viewed only with an open complaint or risk flag."
            )
        

        result = await self.session.execute(
            select(
                Message.id,
                Message.sender_user_id,
                Message.receiver_user_id,
                Message.original_text,
                Message.translated_text,
                Message.is_masked,
                Message.is_system,
                Message.created_at,
            )
            .where(
                Message.tenant_id == tenant_id,
                Message.thread_id == thread_id,
            )
            .order_by(Message.created_at.asc())
            .limit(max(1, min(int(limit), 100)))
        )

        message_rows = result.all()
        message_ids = [
            row.id
            for row in message_rows
        ]

        risk_by_message: dict[
            UUID,
            tuple[tuple[str, ...], str | None],
        ] = {}

        if message_ids:
            risk_result = await self.session.execute(
                select(
                    RiskFlag.entity_id,
                    RiskFlag.details,
                    RiskFlag.severity,
                ).where(
                    RiskFlag.tenant_id == tenant_id,
                    RiskFlag.entity_type == "message",
                    RiskFlag.entity_id.in_(message_ids),
                    RiskFlag.flag_code == "off_platform_contact",
                    RiskFlag.status == "open",
                )
            )

            severity_rank = {
                "low": 1,
                "medium": 2,
                "high": 3,
                "critical": 4,
            }

            for entity_id, details, severity in risk_result:
                metadata = details or {}
                detected_types = tuple(
                    sorted(
                        {
                            str(item)
                            for item in (
                                metadata.get(
                                    "detected_types"
                                )
                                or []
                            )
                            if item
                        }
                    )
                )

                previous_types, previous_severity = (
                    risk_by_message.get(
                        entity_id,
                        ((), None),
                    )
                )

                selected_severity = severity

                if (
                    previous_severity
                    and severity_rank.get(
                        previous_severity,
                        0,
                    )
                    > severity_rank.get(
                        severity,
                        0,
                    )
                ):
                    selected_severity = previous_severity

                risk_by_message[entity_id] = (
                    tuple(
                        sorted(
                            set(
                                previous_types
                            ).union(
                                detected_types
                            )
                        )
                    ),
                    selected_severity,
                )

        return [
            AdminThreadMessageRow(
                thread_id=thread.id,
                thread_status=thread.status,
                context_id=thread.context_id,
                client_user_id=thread.client_user_id,
                specialist_id=thread.specialist_id,
                message_id=row.id,
                sender_user_id=row.sender_user_id,
                receiver_user_id=row.receiver_user_id,
                original_text=row.original_text,
                translated_text=row.translated_text,
                is_masked=row.is_masked,
                is_system=row.is_system,
                risk_detected_types=risk_by_message.get(
                    row.id,
                    ((), None),
                )[0],
                risk_severity=risk_by_message.get(
                    row.id,
                    ((), None),
                )[1],
                created_at=row.created_at,
            )
            for row in message_rows
        ]

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

    async def list_admin_audit_actions(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_types: set[str] | None,
        limit: int,
        offset: int,
    ) -> list[AdminAuditQueueItem]:
        await self.require_admin_role(
            admin_user_id,
            FULL_LOG_VIEW_ROLES,
        )

        normalized_limit = max(
            1,
            min(int(limit), 11),
        )
        normalized_offset = max(
            0,
            int(offset),
        )

        admin_actions_query = select(
            AdminAction.id.label("record_id"),
            literal("admin_action").label("source"),
            AdminAction.admin_user_id.label(
                "actor_user_id"
            ),
            AdminAction.action_type.label("action"),
            AdminAction.target_type.label("target_type"),
            AdminAction.target_id.label("target_id"),
            AdminAction.reason.label("reason"),
            AdminAction.created_at.label("created_at"),
        ).where(
            AdminAction.tenant_id == tenant_id,
        )

        event_logs_query = select(
            EventLog.id.label("record_id"),
            literal("event").label("source"),
            EventLog.user_id.label("actor_user_id"),
            EventLog.event_type.label("action"),
            func.coalesce(
                EventLog.entity_type,
                "event",
            ).label("target_type"),
            EventLog.entity_id.label("target_id"),
            EventLog.payload["reason"].astext.label(
                "reason"
            ),
            EventLog.created_at.label("created_at"),
        ).where(
            EventLog.tenant_id == tenant_id,
            EventLog.event_type != "audit_viewed",
        )

        combined = union_all(
            admin_actions_query,
            event_logs_query,
        ).subquery("admin_audit_records")

        normalized_target_types = {
            str(target_type).strip()
            for target_type in (target_types or set())
            if str(target_type).strip()
        }

        query = select(combined)

        if normalized_target_types:
            query = query.where(
                combined.c.target_type.in_(
                    normalized_target_types
                )
            )

        result = await self.session.execute(
            query
            .order_by(
                combined.c.created_at.desc(),
                combined.c.record_id.desc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return [
            AdminAuditQueueItem(
                action_id=row.record_id,
                actor_user_id=row.actor_user_id,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                reason=row.reason,
                created_at=row.created_at,
                source=row.source,
            )
            for row in result.all()
        ]

    async def list_super_admin_audit_actions(
        self,
        *,
        admin_user_id: UUID,
        target_types: set[str] | None,
        limit: int,
        offset: int,
    ) -> list[AdminAuditQueueItem]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_limit = max(
            1,
            min(int(limit), 11),
        )
        normalized_offset = max(
            0,
            int(offset),
        )

        admin_actions_query = select(
            AdminAction.id.label("record_id"),
            literal("admin_action").label("source"),
            AdminAction.admin_user_id.label("actor_user_id"),
            AdminAction.action_type.label("action"),
            AdminAction.target_type.label("target_type"),
            AdminAction.target_id.label("target_id"),
            AdminAction.reason.label("reason"),
            AdminAction.created_at.label("created_at"),
        )

        event_logs_query = select(
            EventLog.id.label("record_id"),
            literal("event").label("source"),
            EventLog.user_id.label("actor_user_id"),
            EventLog.event_type.label("action"),
            func.coalesce(
                EventLog.entity_type,
                "event",
            ).label("target_type"),
            EventLog.entity_id.label("target_id"),
            EventLog.payload["reason"].astext.label("reason"),
            EventLog.created_at.label("created_at"),
        ).where(
            EventLog.event_type != "audit_viewed",
        )

        combined = union_all(
            admin_actions_query,
            event_logs_query,
        ).subquery("super_admin_audit_records")

        normalized_target_types = {
            str(target_type).strip()
            for target_type in (target_types or set())
            if str(target_type).strip()
        }

        query = select(combined)

        if normalized_target_types:
            query = query.where(
                combined.c.target_type.in_(
                    normalized_target_types
                )
            )

        result = await self.session.execute(
            query
            .order_by(
                combined.c.created_at.desc(),
                combined.c.record_id.desc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return [
            AdminAuditQueueItem(
                action_id=row.record_id,
                actor_user_id=row.actor_user_id,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                reason=row.reason,
                created_at=row.created_at,
                source=row.source,
            )
            for row in result.all()
        ]

    async def get_admin_audit_action(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        action_id: UUID,
    ) -> AdminAuditQueueItem:
        await self.require_admin_role(
            admin_user_id,
            FULL_LOG_VIEW_ROLES,
        )

        admin_result = await self.session.execute(
            select(AdminAction).where(
                AdminAction.id == action_id,
                AdminAction.tenant_id == tenant_id,
            )
        )
        admin_action = admin_result.scalar_one_or_none()

        if admin_action:
            return AdminAuditQueueItem(
                action_id=admin_action.id,
                actor_user_id=admin_action.admin_user_id,
                action=admin_action.action_type,
                target_type=admin_action.target_type,
                target_id=admin_action.target_id,
                reason=admin_action.reason,
                created_at=admin_action.created_at,
                source="admin_action",
            )

        event_result = await self.session.execute(
            select(EventLog).where(
                EventLog.id == action_id,
                EventLog.tenant_id == tenant_id,
                EventLog.event_type != "audit_viewed",
            )
        )
        event = event_result.scalar_one_or_none()

        if not event:
            raise ModerationNotFoundError(
                "Audit record not found."
            )

        payload = event.payload or {}

        return AdminAuditQueueItem(
            action_id=event.id,
            actor_user_id=event.user_id,
            action=event.event_type,
            target_type=event.entity_type or "event",
            target_id=event.entity_id,
            reason=(
                str(payload.get("reason")).strip()
                if payload.get("reason")
                else None
            ),
            created_at=event.created_at,
            source="event",
        )

    async def get_super_admin_audit_event_detail(
        self,
        *,
        admin_user_id: UUID,
        action_id: UUID,
    ) -> SuperAdminAuditEventDetailRow:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        admin_result = await self.session.execute(
            select(AdminAction).where(
                AdminAction.id == action_id,
            )
        )
        admin_action = admin_result.scalar_one_or_none()

        if admin_action:
            return SuperAdminAuditEventDetailRow(
                action_id=admin_action.id,
                actor_user_id=admin_action.admin_user_id,
                action=admin_action.action_type,
                target_type=admin_action.target_type,
                target_id=admin_action.target_id,
                reason=admin_action.reason,
                before_state=admin_action.before_state or {},
                after_state=admin_action.after_state or {},
                payload={},
                created_at=admin_action.created_at,
                source="admin_action",
                correlation_id=None,
            )

        event_result = await self.session.execute(
            select(EventLog).where(
                EventLog.id == action_id,
                EventLog.event_type != "audit_viewed",
            )
        )
        event = event_result.scalar_one_or_none()

        if not event:
            raise ModerationNotFoundError("Audit event not found.")

        payload = event.payload or {}

        return SuperAdminAuditEventDetailRow(
            action_id=event.id,
            actor_user_id=event.user_id,
            action=event.event_type,
            target_type=event.entity_type or "event",
            target_id=event.entity_id,
            reason=(
                str(payload.get("reason")).strip()
                if payload.get("reason")
                else None
            ),
            before_state={},
            after_state={},
            payload=payload,
            created_at=event.created_at,
            source="event",
            correlation_id=event.trace_id,
        )

    async def get_super_admin_system_status(
        self,
        *,
        admin_user_id: UUID,
    ) -> SuperAdminSystemStatusRow:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        db_status = "ok"
        db_version = "unknown"

        try:
            version_result = await self.session.execute(
                text("SELECT version() AS db_version")
            )
            db_version = str(
                version_result.scalar_one_or_none() or "unknown"
            )
        except Exception:
            db_status = "error"

        migrations_table_result = await self.session.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'alembic_version'
                )
            """)
        )
        migrations_table_exists = bool(
            migrations_table_result.scalar_one()
        )

        migration_version = "not configured"

        if migrations_table_exists:
            migration_result = await self.session.execute(
                text("""
                    SELECT version_num
                    FROM alembic_version
                    LIMIT 1
                """)
            )
            migration_version = str(
                migration_result.scalar_one_or_none()
                or "empty"
            )

        return SuperAdminSystemStatusRow(
            db_status=db_status,
            db_version=db_version,
            migration_version=migration_version,
            migrations_table_exists=migrations_table_exists,
        )

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

    async def grant_super_admin_user_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        role: str,
        reason: str,
    ) -> UserRoleMapping:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_role = (role or "").strip().lower()

        if normalized_role == "root":
            raise ValueError("Root role is disabled outside recovery flow.")

        if normalized_role not in SUPER_ADMIN_GRANTABLE_ROLES:
            raise ValueError("Unsupported role for Super Admin grant.")

        target_user = await self.session.get(User, target_user_id)

        if not target_user or target_user.tenant_id != tenant_id:
            raise ModerationNotFoundError("Target user not found.")

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
            existing.tenant_id = target_user.tenant_id
            existing.granted_by = admin_user_id
            existing.granted_at = datetime.utcnow()
            role_mapping = existing
        else:
            role_mapping = UserRoleMapping(
                user_id=target_user.id,
                tenant_id=target_user.tenant_id,
                role=normalized_role,
                status="active",
                granted_by=admin_user_id,
            )
            self.session.add(role_mapping)

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="user_role_changed",
            target_type="user",
            target_id=target_user.id,
            before_state=before_state,
            after_state=self._role_audit_state(role_mapping),
            reason=reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="user_role_changed",
            entity_type="user",
            entity_id=target_user.id,
            payload={
                "action": "granted",
                "role": normalized_role,
                "reason": reason,
            },
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="role_change_confirmed",
            entity_type="user",
            entity_id=target_user.id,
            payload={
                "action": "granted",
                "role": normalized_role,
            },
        )

        await self.session.flush()
        return role_mapping

    async def revoke_super_admin_user_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        role: str,
        reason: str,
    ) -> UserRoleMapping:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_role = (role or "").strip().lower()

        if normalized_role == "root":
            raise ValueError("Root role is disabled outside recovery flow.")

        if normalized_role not in SUPER_ADMIN_GRANTABLE_ROLES:
            raise ValueError("Unsupported role for Super Admin revoke.")

        target_user = await self.session.get(User, target_user_id)

        if not target_user or target_user.tenant_id != tenant_id:
            raise ModerationNotFoundError("Target user not found.")

        role_mapping = (
            await self.session.execute(
                select(UserRoleMapping)
                .where(
                    UserRoleMapping.user_id == target_user.id,
                    UserRoleMapping.role == normalized_role,
                    UserRoleMapping.status == "active",
                )
                .order_by(UserRoleMapping.granted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if not role_mapping:
            raise ModerationNotFoundError("Active role not found.")

        if normalized_role == "super_admin":
            active_super_admins = await self.session.scalar(
                select(func.count(UserRoleMapping.id)).where(
                    UserRoleMapping.role == "super_admin",
                    UserRoleMapping.status == "active",
                    UserRoleMapping.tenant_id == tenant_id,
                )
            )

            if int(active_super_admins or 0) <= 1:
                raise ValueError(
                    "Cannot revoke the last Super Admin without Root recovery flow."
                )

        before_state = self._role_audit_state(role_mapping)

        role_mapping.status = "revoked"
        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="user_role_changed",
            target_type="user",
            target_id=target_user.id,
            before_state=before_state,
            after_state=self._role_audit_state(role_mapping),
            reason=reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="user_role_changed",
            entity_type="user",
            entity_id=target_user.id,
            payload={
                "action": "revoked",
                "role": normalized_role,
                "reason": reason,
            },
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="role_change_confirmed",
            entity_type="user",
            entity_id=target_user.id,
            payload={
                "action": "revoked",
                "role": normalized_role,
            },
        )

        await self.session.flush()
        return role_mapping

    async def log_super_admin_impersonation_view(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        target_role: str | None,
        action: str,
        reason: str,
    ) -> None:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        target_user = await self.session.get(User, target_user_id)

        if not target_user or target_user.tenant_id != tenant_id:
            raise ModerationNotFoundError("Target user not found.")

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type=f"impersonation_view_{action}",
            entity_type="user",
            entity_id=target_user_id,
            payload={
                "target_user": f"user-{target_user_id.hex[:8]}",
                "target_role": target_role,
                "read_only": True,
                "reason": reason,
            },
        )

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=f"impersonation_view_{action}",
            target_type="user",
            target_id=target_user_id,
            before_state={
                "read_only": True,
                "target_role": target_role,
            },
            after_state={
                "read_only": True,
                "target_role": target_role,
                "action": action,
            },
            reason=reason,
        )

        await self.session.flush()

    async def get_admin_menu_counts(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, int]:
        await self.require_admin_role(
            admin_user_id,
            {"admin", "super_admin"},
        )

        async def count_rows(model, *conditions) -> int:
            result = await self.session.execute(
                select(func.count())
                .select_from(model)
                .where(*conditions)
            )
            return int(result.scalar_one())

        global_blacklist_result = await self.session.execute(
            select(
                func.count(
                    func.distinct(Blacklist.id)
                )
            )
            .select_from(Blacklist)
            .join(
                User,
                User.id == Blacklist.user_id,
            )
            .join(
                EventLog,
                and_(
                    EventLog.tenant_id == Blacklist.tenant_id,
                    EventLog.entity_type == "user",
                    EventLog.entity_id == Blacklist.user_id,
                    EventLog.event_type
                    == "global_blacklist_changed",
                    EventLog.payload["action"].as_string()
                    == "added",
                    EventLog.payload["scope"].as_string()
                    == "global",
                    EventLog.payload["blacklist_id"].as_string()
                    == cast(Blacklist.id, String),
                ),
            )
            .where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.status == "active",
                User.status == "blocked",
            )
        )

        global_blacklist_count = int(
            global_blacklist_result.scalar_one()
        )

        return {
            "users": await count_rows(
                User,
                User.tenant_id == tenant_id,
                User.status != "deleted",
            ),
            "specialists": await count_rows(
                Specialist,
                Specialist.tenant_id == tenant_id,
                Specialist.status != "deleted",
            ),
            "tickets": await count_rows(
                SupportTicket,
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.status.in_({"open", "in_progress"}),
            ),
            "complaints": await count_rows(
                Complaint,
                Complaint.tenant_id == tenant_id,
                Complaint.status.in_(COMPLAINT_OPEN_STATUSES),
            ),
            "blacklist": global_blacklist_count,
            "audit_alerts": await count_rows(
                RiskFlag,
                RiskFlag.tenant_id == tenant_id,
                RiskFlag.status == "open",
            ),
        }

    async def get_super_admin_menu_counts(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> dict:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        users_count = await self.session.scalar(
            select(func.count(User.id))
            .where(User.tenant_id == tenant_id)
        )

        specialists_count = await self.session.scalar(
            select(func.count(Specialist.id))
            .where(Specialist.tenant_id == tenant_id)
        )

        tickets_count = await self.session.scalar(
            select(func.count(SupportTicket.id))
            .where(SupportTicket.tenant_id == tenant_id)
        )

        complaints_count = await self.session.scalar(
            select(func.count(Complaint.id))
            .where(Complaint.tenant_id == tenant_id)
        )

        global_blacklist_count = await self.session.scalar(
            select(func.count(Blacklist.id))
            .where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.status == "active",
            )
        )

        audit_alerts_count = await self.session.scalar(
            select(func.count(AdminAction.id))
            .where(AdminAction.tenant_id == tenant_id)
        )

        return {
            "users": int(users_count or 0),
            "specialists": int(specialists_count or 0),
            "tickets": int(tickets_count or 0),
            "complaints": int(complaints_count or 0),
            "global_blacklist": int(global_blacklist_count or 0),
            "system_alerts": 0,
            "finance_alerts": 0,
            "audit_alerts": int(audit_alerts_count or 0),
        }

    async def list_admin_user_history(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        limit: int = 10,
    ) -> list[AdminUserHistoryRow]:
        await self.require_admin_role(
            admin_user_id,
            {"admin", "super_admin"},
        )

        target_exists = await self.session.execute(
            select(User.id).where(
                User.id == target_user_id,
                User.tenant_id == tenant_id,
            )
        )

        if target_exists.scalar_one_or_none() is None:
            raise ModerationNotFoundError(
                "User not found."
            )

        normalized_limit = max(1, min(int(limit), 20))

        event_result = await self.session.execute(
            select(EventLog)
            .where(
                EventLog.tenant_id == tenant_id,
                or_(
                    EventLog.entity_id == target_user_id,
                    EventLog.user_id == target_user_id,
                ),
            )
            .order_by(EventLog.created_at.desc())
            .limit(normalized_limit)
        )

        action_result = await self.session.execute(
            select(AdminAction)
            .where(
                AdminAction.tenant_id == tenant_id,
                AdminAction.target_id == target_user_id,
            )
            .order_by(AdminAction.created_at.desc())
            .limit(normalized_limit)
        )

        history: list[AdminUserHistoryRow] = []

        for event in event_result.scalars().all():
            payload = event.payload or {}

            history.append(
                AdminUserHistoryRow(
                    created_at=event.created_at,
                    actor_user_id=event.user_id,
                    action=event.event_type,
                    reason=(
                        str(payload.get("reason")).strip()
                        if payload.get("reason")
                        else None
                    ),
                    source="event",
                )
            )

        for action in action_result.scalars().all():
            history.append(
                AdminUserHistoryRow(
                    created_at=action.created_at,
                    actor_user_id=action.admin_user_id,
                    action=action.action_type,
                    reason=action.reason,
                    source="admin_action",
                )
            )

        history.sort(
            key=lambda item: item.created_at,
            reverse=True,
        )

        return history[:normalized_limit]

    async def get_admin_user_details(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> AdminUserDetailsRow:
        await self.require_admin_role(
            admin_user_id,
            {"admin", "super_admin"},
        )

        user_result = await self.session.execute(
            select(
                User.id,
                User.status,
                User.last_seen_at,
                UserAccount.username,
                UserAccount.first_name,
                UserAccount.last_name,
            )
            .outerjoin(
                UserAccount,
                UserAccount.user_id == User.id,
            )
            .where(
                User.id == target_user_id,
                User.tenant_id == tenant_id,
            )
            .order_by(UserAccount.created_at.asc())
            .limit(1)
        )
        user_row = user_result.one_or_none()

        if not user_row:
            raise ModerationNotFoundError(
                "User not found."
            )

        roles_result = await self.session.execute(
            select(UserRoleMapping.role)
            .where(
                UserRoleMapping.user_id == target_user_id,
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.status == "active",
            )
            .distinct()
            .order_by(UserRoleMapping.role)
        )
        roles = tuple(roles_result.scalars().all())

        specialist_ids = (
            select(Specialist.id)
            .where(
                Specialist.user_id == target_user_id,
                Specialist.tenant_id == tenant_id,
            )
        )

        complaints_result = await self.session.execute(
            select(func.count())
            .select_from(Complaint)
            .where(
                Complaint.tenant_id == tenant_id,
                or_(
                    (
                        (Complaint.target_type == "user")
                        & (Complaint.target_id == target_user_id)
                    ),
                    (
                        Complaint.target_type.in_(
                            {
                                "specialist",
                                "specialist_profile",
                            }
                        )
                        & Complaint.target_id.in_(specialist_ids)
                    ),
                ),
            )
        )

        return AdminUserDetailsRow(
            user_id=user_row.id,
            username=user_row.username,
            first_name=user_row.first_name,
            last_name=user_row.last_name,
            status=user_row.status,
            last_seen_at=user_row.last_seen_at,
            roles=roles,
            complaints_count=int(
                complaints_result.scalar_one()
            ),
            is_global_blacklisted=(
                user_row.status == "blocked"
            ),
        )

    async def search_admin_users(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        query: str,
        limit: int = 10,
    ) -> list[AdminUserSearchRow]:
        await self.require_admin_role(
            admin_user_id,
            {"admin", "super_admin"},
        )

        normalized_query = query.strip().lstrip("@")
        user_id_query = normalized_query.removeprefix("user-")
        contains_query = f"%{normalized_query}%"
        user_id_prefix = f"{user_id_query}%"

        result = await self.session.execute(
            select(
                User.id,
                UserAccount.platform_user_id,
                UserAccount.username,
                UserAccount.first_name,
                UserAccount.last_name,
                User.status,
            )
            .join(
                UserAccount,
                UserAccount.user_id == User.id,
            )
            .where(
                User.tenant_id == tenant_id,
                UserAccount.platform == "telegram",
                or_(
                    UserAccount.platform_user_id == normalized_query,
                    UserAccount.username.ilike(contains_query),
                    cast(User.id, String).ilike(user_id_prefix),
                ),
            )
            .order_by(User.created_at.desc())
            .limit(max(1, min(int(limit), 20)))
        )

        return [
            AdminUserSearchRow(
                user_id=row.id,
                platform_user_id=row.platform_user_id,
                username=row.username,
                first_name=row.first_name,
                last_name=row.last_name,
                status=row.status,
            )
            for row in result.all()
        ]

    async def search_super_admin_users(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        query: str,
        limit: int = 10,
    ) -> list[SuperAdminUserSearchRow]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_query = (query or "").strip()
        normalized_search_query = normalized_query.lstrip("@").lower()
        normalized_like = f"%{normalized_search_query}%"

        roles_subquery = (
            select(
                UserRoleMapping.user_id.label("user_id"),
                func.string_agg(
                    UserRoleMapping.role,
                    ",",
                ).label("roles"),
            )
            .where(UserRoleMapping.status == "active")
            .group_by(UserRoleMapping.user_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                User.id.label("user_id"),
                UserAccount.platform_user_id,
                UserAccount.username,
                UserAccount.first_name,
                UserAccount.display_name,
                User.status,
                roles_subquery.c.roles,
            )
            .join(
                UserAccount,
                UserAccount.user_id == User.id,
            )
            .outerjoin(
                roles_subquery,
                roles_subquery.c.user_id == User.id,
            )
            .where(
                User.tenant_id == tenant_id,
                UserAccount.platform == "telegram",
                or_(
                    func.lower(UserAccount.platform_user_id).like(
                        normalized_like,
                    ),
                    func.lower(UserAccount.username).like(
                        normalized_like,
                    ),
                    func.lower(
                        func.concat(
                            "@",
                            UserAccount.username,
                        )
                    ).like(
                        f"%{normalized_query.lower()}%",
                    ),
                    func.lower(UserAccount.display_name).like(
                        normalized_like,
                    ),
                    func.lower(
                        func.concat(
                            "user-",
                            func.substr(
                                cast(User.id, String),
                                1,
                                8,
                            ),
                        )
                    ).like(normalized_like),
                ),
            )
            .order_by(User.updated_at.desc())
            .limit(limit)
        )

        return [
            SuperAdminUserSearchRow(**row._mapping)
            for row in result.all()
        ]

    async def get_super_admin_user_details(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> SuperAdminUserDetailsRow:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        roles_subquery = (
            select(
                UserRoleMapping.user_id.label("user_id"),
                func.string_agg(
                    UserRoleMapping.role,
                    ",",
                ).label("roles"),
            )
            .where(UserRoleMapping.status == "active")
            .group_by(UserRoleMapping.user_id)
            .subquery()
        )

        complaints_subquery = (
            select(
                Complaint.reporter_user_id.label("user_id"),
                func.count(Complaint.id).label("complaints_count"),
            )
            .where(Complaint.tenant_id == tenant_id)
            .group_by(Complaint.reporter_user_id)
            .subquery()
        )

        blacklist_subquery = (
            select(
                Blacklist.user_id.label("user_id"),
                func.count(Blacklist.id).label("blacklist_count"),
            )
            .where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.status == "active",
            )
            .group_by(Blacklist.user_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                User.id.label("user_id"),
                UserAccount.platform_user_id,
                UserAccount.username,
                UserAccount.first_name,
                UserAccount.display_name,
                User.status,
                User.active_role,
                User.last_seen_at,
                User.risk_score,
                roles_subquery.c.roles,
                func.coalesce(
                    complaints_subquery.c.complaints_count,
                    0,
                ).label("complaints_count"),
                func.coalesce(
                    blacklist_subquery.c.blacklist_count,
                    0,
                ).label("blacklist_count"),
            )
            .join(
                UserAccount,
                UserAccount.user_id == User.id,
            )
            .outerjoin(
                roles_subquery,
                roles_subquery.c.user_id == User.id,
            )
            .outerjoin(
                complaints_subquery,
                complaints_subquery.c.user_id == User.id,
            )
            .outerjoin(
                blacklist_subquery,
                blacklist_subquery.c.user_id == User.id,
            )
            .where(
                User.tenant_id == tenant_id,
                User.id == target_user_id,
                UserAccount.platform == "telegram",
            )
            .order_by(UserAccount.created_at.asc())
            .limit(1)
        )

        row = result.one_or_none()

        if not row:
            raise ModerationNotFoundError("User not found.")

        return SuperAdminUserDetailsRow(**row._mapping)

    async def list_super_admin_user_roles(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> list[SuperAdminUserRoleRow]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        target_user = await self.session.get(User, target_user_id)

        if not target_user or target_user.tenant_id != tenant_id:
            raise ModerationNotFoundError("User not found.")

        result = await self.session.execute(
            select(
                UserRoleMapping.id.label("role_id"),
                UserRoleMapping.role,
                UserRoleMapping.status,
                UserRoleMapping.tenant_id,
                UserRoleMapping.granted_by,
                UserRoleMapping.granted_at,
            )
            .where(UserRoleMapping.user_id == target_user_id)
            .order_by(
                UserRoleMapping.status.asc(),
                UserRoleMapping.role.asc(),
                UserRoleMapping.granted_at.desc(),
            )
        )

        return [
            SuperAdminUserRoleRow(**row._mapping)
            for row in result.all()
        ]

    async def list_super_admin_role_scopes(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        user_id: UUID | None,
        statuses: set[str],
        limit: int,
        offset: int,
    ) -> list[SuperAdminRoleScopeRow]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin", "root"},
        )

        allowed_statuses = {"active", "revoked"}
        normalized_statuses = set(statuses) & allowed_statuses

        if not normalized_statuses:
            normalized_statuses = {"active"}

        normalized_limit = max(1, min(int(limit), 11))
        normalized_offset = max(0, int(offset))

        country_name = func.coalesce(
            Country.name,
            cast(RoleScope.scope_id, String),
        )
        city_name = func.coalesce(
            City.name,
            cast(RoleScope.scope_id, String),
        )

        scope_value = case(
            (
                RoleScope.scope_type == "country",
                country_name,
            ),
            (
                RoleScope.scope_type == "city",
                city_name,
            ),
            else_=cast(RoleScope.scope_id, String),
        )

        conditions = [
            RoleScope.tenant_id == tenant_id,
            RoleScope.status.in_(normalized_statuses),
        ]

        if user_id:
            conditions.append(RoleScope.user_id == user_id)

        result = await self.session.execute(
            select(
                RoleScope.id.label("scope_id"),
                RoleScope.user_id,
                RoleScope.role,
                RoleScope.scope_type,
                scope_value.label("scope_value"),
                RoleScope.status,
                RoleScope.reason,
                RoleScope.created_by,
                RoleScope.created_at,
                RoleScope.revoked_by,
                RoleScope.revoked_at,
            )
            .outerjoin(
                Country,
                and_(
                    RoleScope.scope_type == "country",
                    Country.id == RoleScope.scope_id,
                ),
            )
            .outerjoin(
                City,
                and_(
                    RoleScope.scope_type == "city",
                    City.id == RoleScope.scope_id,
                ),
            )
            .where(*conditions)
            .order_by(
                RoleScope.created_at.desc(),
                RoleScope.id.desc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return [
            SuperAdminRoleScopeRow(**row._mapping)
            for row in result.all()
        ]

    async def add_super_admin_role_scope(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        role: str,
        scope_type: str,
        scope_value: str,
        reason: str,
    ) -> RoleScope:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin", "root"},
        )

        normalized_role = (role or "").strip().lower()
        normalized_scope_type = (scope_type or "").strip().lower()
        normalized_scope_value = (scope_value or "").strip()
        normalized_reason = (reason or "").strip()

        if not normalized_role:
            raise ValueError("Role is required.")

        if normalized_scope_type not in {
            "country",
            "city",
            "region",
            "agency",
            "community",
        }:
            raise ValueError("Unsupported scope type.")

        if not normalized_scope_value:
            raise ValueError("Scope value is required.")

        if len(normalized_reason) < 3:
            raise ValueError("Reason is required.")

        target_user = await self.session.get(User, user_id)

        if not target_user or target_user.tenant_id != tenant_id:
            raise ModerationNotFoundError("User not found.")

        if admin_user_id == user_id:
            raise ModerationAccessError(
                "Regional role cannot change its own scope."
            )

        role_result = await self.session.execute(
            select(UserRoleMapping)
            .where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.role == normalized_role,
                UserRoleMapping.status == "active",
            )
            .order_by(UserRoleMapping.granted_at.desc())
            .limit(1)
        )
        user_role = role_result.scalar_one_or_none()

        if not user_role:
            raise ModerationNotFoundError(
                "Active role not found for this user."
            )

        scope_id = await self._resolve_role_scope_id(
            scope_type=normalized_scope_type,
            scope_value=normalized_scope_value,
        )

        existing_result = await self.session.execute(
            select(RoleScope)
            .where(
                RoleScope.tenant_id == tenant_id,
                RoleScope.user_id == user_id,
                RoleScope.role == normalized_role,
                RoleScope.scope_type == normalized_scope_type,
                RoleScope.scope_id == scope_id,
                RoleScope.status == "active",
            )
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            raise ModerationAccessError(
                "Active scope already exists."
            )

        role_scope = RoleScope(
            tenant_id=tenant_id,
            user_role_id=user_role.id,
            user_id=user_id,
            role=normalized_role,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            status="active",
            reason=normalized_reason,
            created_by=admin_user_id,
        )
        self.session.add(role_scope)
        await self.session.flush()

        after_state = {
            "scope_id": str(role_scope.id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "role": normalized_role,
            "scope_type": normalized_scope_type,
            "scope_value": normalized_scope_value,
            "scope_entity_id": str(scope_id),
            "status": "active",
        }

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="scope_changed",
            target_type="role_scope",
            target_id=role_scope.id,
            before_state={},
            after_state=after_state,
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="scope_changed",
            entity_type="role_scope",
            entity_id=role_scope.id,
            payload={
                "action": "added",
                "user": f"user-{user_id.hex[:8]}",
                "role": normalized_role,
                "scope_type": normalized_scope_type,
                "scope_value": normalized_scope_value,
                "reason": normalized_reason,
            },
        )

        await self.session.flush()
        return role_scope

    async def revoke_super_admin_role_scope(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        scope_id: UUID,
        reason: str,
    ) -> RoleScope:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin", "root"},
        )

        normalized_reason = (reason or "").strip()

        if len(normalized_reason) < 3:
            raise ValueError("Reason is required.")

        role_scope = await self.session.get(RoleScope, scope_id)

        if not role_scope or role_scope.tenant_id != tenant_id:
            raise ModerationNotFoundError("Role scope not found.")

        if role_scope.status != "active":
            raise ModerationAccessError(
                "Role scope is not active."
            )

        if admin_user_id == role_scope.user_id:
            raise ModerationAccessError(
                "Regional role cannot change its own scope."
            )

        before_state = {
            "scope_id": str(role_scope.id),
            "tenant_id": str(role_scope.tenant_id),
            "user_id": str(role_scope.user_id),
            "role": role_scope.role,
            "scope_type": role_scope.scope_type,
            "scope_entity_id": str(role_scope.scope_id),
            "status": role_scope.status,
            "reason": role_scope.reason,
        }

        role_scope.status = "revoked"
        role_scope.revoked_by = admin_user_id
        role_scope.revoked_at = datetime.utcnow()

        after_state = {
            **before_state,
            "status": "revoked",
            "revoked_by": str(admin_user_id),
            "revoked_at": (
                role_scope.revoked_at.isoformat()
                if role_scope.revoked_at
                else None
            ),
            "revoke_reason": normalized_reason,
        }

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="scope_changed",
            target_type="role_scope",
            target_id=role_scope.id,
            before_state=before_state,
            after_state=after_state,
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="scope_changed",
            entity_type="role_scope",
            entity_id=role_scope.id,
            payload={
                "action": "revoked",
                "user": f"user-{role_scope.user_id.hex[:8]}",
                "role": role_scope.role,
                "scope_type": role_scope.scope_type,
                "scope_id": str(role_scope.scope_id),
                "reason": normalized_reason,
            },
        )

        await self.session.flush()
        return role_scope

    async def _resolve_role_scope_id(
        self,
        *,
        scope_type: str,
        scope_value: str,
    ) -> UUID:
        normalized_scope_type = (scope_type or "").strip().lower()
        normalized_scope_value = (scope_value or "").strip()
        normalized_lookup = normalized_scope_value.lower()

        if normalized_scope_type == "country":
            result = await self.session.execute(
                select(Country.id)
                .where(
                    or_(
                        func.lower(Country.code) == normalized_lookup,
                        func.lower(Country.name) == normalized_lookup,
                        func.lower(Country.name_ru) == normalized_lookup,
                        func.lower(Country.name_en) == normalized_lookup,
                        func.lower(Country.name_pt) == normalized_lookup,
                    )
                )
                .limit(1)
            )
            scope_id = result.scalar_one_or_none()

            if not scope_id:
                raise ModerationNotFoundError("Country scope not found.")

            return scope_id

        if normalized_scope_type == "city":
            result = await self.session.execute(
                select(City.id)
                .where(
                    or_(
                        func.lower(City.name) == normalized_lookup,
                        func.lower(City.name_ru) == normalized_lookup,
                        func.lower(City.name_en) == normalized_lookup,
                        func.lower(City.name_pt) == normalized_lookup,
                    )
                )
                .order_by(City.name.asc())
                .limit(1)
            )
            scope_id = result.scalar_one_or_none()

            if not scope_id:
                raise ModerationNotFoundError("City scope not found.")

            return scope_id

        try:
            return UUID(normalized_scope_value)
        except (TypeError, ValueError) as exc:
            raise ModerationNotFoundError(
                "Scope id must be UUID for this scope type."
            ) from exc

    async def list_super_admin_permission_matrix(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        query: str = "",
        limit: int = 10,
    ) -> list[SuperAdminPermissionMatrixRow]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_query = (query or "").strip().lower()
        normalized_limit = max(1, min(int(limit), 25))

        result = await self.session.execute(
            text("""
                SELECT
                    rp.id AS permission_id,
                    rp.role AS role,
                    rp.permission_code AS permission_code,
                    p.description AS description,
                    'global' AS scope,
                    'active' AS status,
                    NULL AS granted_by,
                    rp.created_at AS created_at
                FROM role_permissions rp
                LEFT JOIN permissions p
                    ON p.code = rp.permission_code
                WHERE
                    :query = ''
                    OR lower(rp.role) LIKE :like_query
                    OR lower(rp.permission_code) LIKE :like_query
                    OR lower(COALESCE(p.description, '')) LIKE :like_query
                ORDER BY
                    rp.role ASC,
                    rp.permission_code ASC,
                    rp.created_at DESC
                LIMIT :limit
            """),
            {
                "query": normalized_query,
                "like_query": f"%{normalized_query}%",
                "limit": normalized_limit,
            },
        )

        return [
            SuperAdminPermissionMatrixRow(**row._mapping)
            for row in result.all()
        ]

    def _permission_audit_state(
        self,
        row,
    ) -> dict:
        if not row:
            return {}

        return {
            "id": str(row["id"]),
            "role": row["role"],
            "permission_code": row["permission_code"],
            "created_at": (
                row["created_at"].isoformat()
                if row["created_at"]
                else None
            ),
        }

    async def grant_super_admin_permission(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        role: str,
        permission_code: str,
        reason: str,
    ) -> UUID:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_role = (role or "").strip().lower()
        normalized_permission = (permission_code or "").strip()

        if normalized_role not in SUPER_ADMIN_PERMISSION_ROLES:
            raise ValueError("Unsupported role for permission grant.")

        if not normalized_permission:
            raise ValueError("Permission code is required.")

        permission_result = await self.session.execute(
            text("""
                SELECT id
                FROM permissions
                WHERE code = :permission_code
                LIMIT 1
            """),
            {
                "permission_code": normalized_permission,
            },
        )
        permission_row = permission_result.mappings().one_or_none()

        if not permission_row:
            raise ModerationNotFoundError("Permission not found.")

        existing_result = await self.session.execute(
            text("""
                SELECT id, role, permission_code, created_at
                FROM role_permissions
                WHERE role = :role
                  AND permission_code = :permission_code
                LIMIT 1
            """),
            {
                "role": normalized_role,
                "permission_code": normalized_permission,
            },
        )
        existing = existing_result.mappings().one_or_none()

        before_state = self._permission_audit_state(existing)

        if existing:
            permission_role_id = existing["id"]
            after_state = self._permission_audit_state(existing)
            action = "grant_existing"
        else:
            insert_result = await self.session.execute(
                text("""
                    INSERT INTO role_permissions (
                        id,
                        role,
                        permission_code,
                        created_at
                    )
                    VALUES (
                        gen_random_uuid(),
                        :role,
                        :permission_code,
                        now()
                    )
                    RETURNING id, role, permission_code, created_at
                """),
                {
                    "role": normalized_role,
                    "permission_code": normalized_permission,
                },
            )
            inserted = insert_result.mappings().one()
            permission_role_id = inserted["id"]
            after_state = self._permission_audit_state(inserted)
            action = "granted"

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="permission_changed",
            target_type="permission",
            target_id=permission_role_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="permission_changed",
            entity_type="permission",
            entity_id=permission_role_id,
            payload={
                "action": action,
                "role": normalized_role,
                "permission_code": normalized_permission,
                "reason": reason,
            },
        )

        await self.session.flush()
        return permission_role_id

    async def revoke_super_admin_permission(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        role: str,
        permission_code: str,
        reason: str,
    ) -> UUID:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin"},
        )

        normalized_role = (role or "").strip().lower()
        normalized_permission = (permission_code or "").strip()

        if normalized_role not in SUPER_ADMIN_PERMISSION_ROLES:
            raise ValueError("Unsupported role for permission revoke.")

        if not normalized_permission:
            raise ValueError("Permission code is required.")

        existing_result = await self.session.execute(
            text("""
                SELECT id, role, permission_code, created_at
                FROM role_permissions
                WHERE role = :role
                  AND permission_code = :permission_code
                LIMIT 1
            """),
            {
                "role": normalized_role,
                "permission_code": normalized_permission,
            },
        )
        existing = existing_result.mappings().one_or_none()

        if not existing:
            raise ModerationNotFoundError(
                "Permission is not granted to this role."
            )

        permission_role_id = existing["id"]
        before_state = self._permission_audit_state(existing)
        after_state = {
            "role": normalized_role,
            "permission_code": normalized_permission,
            "status": "revoked",
        }

        await self.session.execute(
            text("""
                DELETE FROM role_permissions
                WHERE id = :permission_role_id
            """),
            {
                "permission_role_id": permission_role_id,
            },
        )

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="permission_changed",
            target_type="permission",
            target_id=permission_role_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="permission_changed",
            entity_type="permission",
            entity_id=permission_role_id,
            payload={
                "action": "revoked",
                "role": normalized_role,
                "permission_code": normalized_permission,
                "reason": reason,
            },
        )

        await self.session.flush()
        return permission_role_id

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

    async def list_admin_specialists(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        statuses: set[str],
        limit: int = 5,
        offset: int = 0,
    ) -> list[AdminSpecialistQueueItem]:
        await self.require_admin_role(
            admin_user_id,
            {"admin", "super_admin"},
        )

        result = await self.session.execute(
            select(
                Specialist.id,
                Specialist.display_name,
                Profession.name,
                City.name,
                Specialist.status,
                Specialist.created_at,
            )
            .outerjoin(
                Profession,
                Profession.id == Specialist.profession_id,
            )
            .outerjoin(
                City,
                City.id == Specialist.city_id,
            )
            .where(
                Specialist.tenant_id == tenant_id,
                Specialist.user_id != admin_user_id,
                Specialist.status.in_(statuses),
            )
            .order_by(
                Specialist.updated_at.desc(),
                Specialist.id.asc(),
            )
            .offset(max(int(offset), 0))
            .limit(max(1, min(int(limit), 20)))
        )

        return [
            AdminSpecialistQueueItem(
                specialist_id=specialist_id,
                display_name=display_name or "-",
                profession_name=profession_name or "-",
                city_name=city_name,
                status=status,
                created_at=created_at,
            )
            for (
                specialist_id,
                display_name,
                profession_name,
                city_name,
                status,
                created_at,
            ) in result.all()
        ]

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

        specialist.status = "approved"
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
                "after_status": "approved",
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

    async def update_specialist_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        expected_status: str,
        target_status: str,
        moderation_comment: str | None,
        reason: str,
        action_type: str,
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
                "You cannot change visibility of your own profile."
            )

        if specialist.status != expected_status:
            raise ModerationAccessError(
                "Specialist profile status has changed."
            )

        before_state = self._specialist_audit_state(specialist)

        specialist.status = target_status
        specialist.moderation_comment = moderation_comment
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=action_type,
            target_type="specialist",
            target_id=specialist.id,
            before_state=before_state,
            after_state=self._specialist_audit_state(specialist),
            reason=reason,
        )

        await self.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="profile_visibility_changed",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "reason": reason,
                "before_status": before_state["status"],
                "after_status": target_status,
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

    async def list_global_blacklist(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        statuses: set[str],
        limit: int,
        offset: int,
    ) -> list[GlobalBlacklistQueueItem]:
        await self.require_admin_role(
            admin_user_id,
            BLOCK_USER_ROLES,
        )

        allowed_statuses = {"active", "revoked"}
        normalized_statuses = set(statuses) & allowed_statuses

        if not normalized_statuses:
            normalized_statuses = {"active"}

        normalized_limit = max(1, min(int(limit), 11))
        normalized_offset = max(0, int(offset))

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
            .join(
                EventLog,
                and_(
                    EventLog.tenant_id == Blacklist.tenant_id,
                    EventLog.entity_type == "user",
                    EventLog.entity_id == Blacklist.user_id,
                    EventLog.event_type == "global_blacklist_changed",
                    EventLog.payload["action"].as_string() == "added",
                    EventLog.payload["scope"].as_string() == "global",
                    EventLog.payload["blacklist_id"].as_string()
                    == cast(Blacklist.id, String),
                ),
            )
            .where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.status.in_(normalized_statuses),
            )
            .order_by(
                Blacklist.created_at.desc(),
                Blacklist.id.desc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return [
            GlobalBlacklistQueueItem(
                blacklist_id=row.id,
                user_id=row.user_id,
                reason=row.reason,
                comment=row.comment,
                status=row.status,
                user_status=row.user_status,
                created_at=row.created_at,
                created_by=row.created_by,
            )
            for row in result.all()
        ]

    async def list_super_admin_global_blacklist(
        self,
        *,
        admin_user_id: UUID,
        statuses: set[str],
        limit: int,
        offset: int,
    ) -> list[GlobalBlacklistQueueItem]:
        await self.require_admin_role(
            admin_user_id,
            {"super_admin", "root"},
        )

        allowed_statuses = {"active", "revoked"}
        normalized_statuses = set(statuses) & allowed_statuses

        if not normalized_statuses:
            normalized_statuses = {"active"}

        normalized_limit = max(1, min(int(limit), 11))
        normalized_offset = max(0, int(offset))

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
            .join(
                EventLog,
                and_(
                    EventLog.entity_type == "user",
                    EventLog.entity_id == Blacklist.user_id,
                    EventLog.event_type == "global_blacklist_changed",
                    EventLog.payload["action"].as_string() == "added",
                    EventLog.payload["scope"].as_string() == "global",
                    EventLog.payload["blacklist_id"].as_string()
                    == cast(Blacklist.id, String),
                ),
            )
            .where(
                Blacklist.status.in_(normalized_statuses),
            )
            .order_by(
                Blacklist.created_at.desc(),
                Blacklist.id.desc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return [
            GlobalBlacklistQueueItem(
                blacklist_id=row.id,
                user_id=row.user_id,
                reason=row.reason,
                comment=row.comment,
                status=row.status,
                user_status=row.user_status,
                created_at=row.created_at,
                created_by=row.created_by,
            )
            for row in result
        ]

    async def unblock_user(
        self,
        *,
        admin_user_id: UUID,
        user_id: UUID,
        reason: str,
    ) -> User:
        await self.require_admin_role(
            admin_user_id,
            BLOCK_USER_ROLES,
        )

        user = await self.session.get(User, user_id)

        if not user:
            raise ModerationNotFoundError(
                "User not found."
            )

        if user.status != "blocked":
            raise ModerationAccessError(
                "User is not globally blocked."
            )

        event_result = await self.session.execute(
            select(EventLog)
            .where(
                EventLog.tenant_id == user.tenant_id,
                EventLog.entity_type == "user",
                EventLog.entity_id == user.id,
                EventLog.event_type
                == "global_blacklist_changed",
            )
            .order_by(EventLog.created_at.desc())
        )

        global_blacklist = None

        for event in event_result.scalars().all():
            payload = event.payload or {}

            if payload.get("action") != "added":
                continue

            blacklist_id = payload.get("blacklist_id")

            if not blacklist_id:
                continue

            try:
                candidate_id = UUID(str(blacklist_id))
            except (TypeError, ValueError):
                continue

            candidate = await self.session.get(
                Blacklist,
                candidate_id,
            )

            if (
                candidate
                and candidate.user_id == user.id
                and candidate.tenant_id == user.tenant_id
                and candidate.status == "active"
            ):
                global_blacklist = candidate
                break

        if not global_blacklist:
            raise ModerationNotFoundError(
                "Active global blacklist record not found."
            )

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ModerationAccessError(
                "Unblock reason is required."
            )

        before_state = self._user_audit_state(user)

        user.status = "active"
        user.updated_at = datetime.utcnow()
        global_blacklist.status = "revoked"

        await self.session.flush()

        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=user.tenant_id,
            action_type="unblock_user",
            target_type="user",
            target_id=user.id,
            before_state={
                **before_state,
                "blacklist_id": str(global_blacklist.id),
                "blacklist_status": "active",
            },
            after_state={
                **self._user_audit_state(user),
                "blacklist_id": str(global_blacklist.id),
                "blacklist_status": "revoked",
            },
            reason=normalized_reason,
        )

        await self.log_event(
            tenant_id=user.tenant_id,
            user_id=admin_user_id,
            event_type="user_unblocked",
            entity_type="user",
            entity_id=user.id,
            payload={
                "reason": normalized_reason,
                "blacklist_id": str(global_blacklist.id),
            },
        )

        await self.log_event(
            tenant_id=user.tenant_id,
            user_id=admin_user_id,
            event_type="global_blacklist_changed",
            entity_type="user",
            entity_id=user.id,
            payload={
                "action": "revoked",
                "scope": "global",
                "reason": normalized_reason,
                "blacklist_id": str(global_blacklist.id),
            },
        )

        await self.session.flush()
        return user

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

        if user.status == "blocked":
            raise ModerationAccessError(
                "User is already globally blocked."
            )

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
        await self.log_event(
            tenant_id=user.tenant_id,
            user_id=admin_user_id,
            event_type="global_blacklist_changed",
            entity_type="user",
            entity_id=user.id,
            payload={
                "action": "added",
                "scope": "global",
                "reason": reason,
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