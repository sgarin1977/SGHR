from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import text
from database.models import AdminAction, Complaint, EventLog, Specialist, User
from database.repositories.moderation import (
    SuperAdminUserDetailsRow,
    AdminSpecialistQueueItem,
    ModerationAccessError,
    AdminUserSearchRow,
    ModerationNotFoundError,
    ModerationRepository,
    PendingSpecialistQueueItem,
    SuperAdminRoleScopeRow,
    PendingSpecialistDetails,
    ComplaintQueueItem,
    ComplaintModerationDetails,
    ScopedBlacklistQueueItem,
    GlobalBlacklistQueueItem,
    AdminUserDetailsRow,
    AdminUserHistoryRow,
    SuperAdminPermissionMatrixRow,
    AdminAuditQueueItem,
    SuperAdminUserRoleRow,
    SuperAdminAuditEventDetailRow,
    SuperAdminSystemStatusRow,
    SuperAdminUserSearchRow,
    AdminThreadMessageRow,
    AdminThreadContextRow,
)
from database.repositories.rate_limit import RateLimitRepository
from database.repositories.contact import ContactChatRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.support import SupportRepository
from services.support import SupportService, SupportServiceError
from services.user import UserService
from services.rate_limit import RateLimitError, RateLimitService

class ModerationError(Exception):
    pass

class ImpersonationRoleUnavailableError(ModerationError):
    pass

@dataclass(frozen=True)
class ModerationActionResult:
    entity_id: UUID
    status: str
    message: str

@dataclass(frozen=True)
class AdminUserHistoryCard:
    date: str
    actor: str
    action: str
    reason: str
    source: str

@dataclass(frozen=True)
class AdminUserDetailsCard:
    user_id: UUID
    user_number: str
    display_name: str
    username: str
    roles: tuple[str, ...]
    status: str
    last_seen: str
    complaints_count: int
    is_global_blacklisted: bool

@dataclass(frozen=True)
class AdminUserSearchCard:
    user_id: UUID
    user_number: str
    telegram_id: str
    username: str
    display_name: str
    status: str

@dataclass(frozen=True)
class SuperAdminUserSearchCard:
    user_id: UUID
    user_number: str
    display_name: str
    username: str
    telegram_id: str
    status: str
    roles: tuple[str, ...]

@dataclass(frozen=True)
class SuperAdminUserDetailsCard:
    user_id: UUID
    user_number: str
    display_name: str
    username: str
    telegram_id: str
    status: str
    active_role: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    last_seen: str
    risk_flags: str
    complaints_count: int
    blacklist_count: int

@dataclass(frozen=True)
class SuperAdminUserRoleCard:
    role_number: str
    role: str
    status: str
    scope: str
    granted_by: str
    granted_at: str

@dataclass(frozen=True)
class SuperAdminRoleScopeCard:
    scope_id: UUID
    user_id: UUID
    user_number: str
    role: str
    scope_type: str
    scope_value: str
    status: str
    reason: str
    created_by: str
    created_at: str
    revoked_by: str
    revoked_at: str


@dataclass(frozen=True)
class SuperAdminRoleScopePage:
    items: tuple[SuperAdminRoleScopeCard, ...]
    page: int
    view: str
    has_next: bool

@dataclass(frozen=True)
class SuperAdminPermissionMatrixCard:
    permission_number: str
    role: str
    permission_code: str
    description: str
    scope: str
    status: str
    granted_by: str
    created_at: str

@dataclass(frozen=True)
class SuperAdminImpersonationPreview:
    target_user_number: str
    target_role: str
    read_only: bool
    status: str

@dataclass(frozen=True)
class ClientReadOnlyCabinet:
    user_number: str
    display_name: str | None
    city_name: str | None
    dialogs_unread: int
    requests_new: int
    requests_accepted: int

@dataclass(frozen=True)
class SpecialistReadOnlyCabinet:
    specialist_id: UUID
    user_number: str
    display_name: str
    professions: tuple[str, ...]
    status: str
    dialogs_unread: int
    new_requests: int
    is_available: bool

@dataclass(frozen=True)
class SupportReadOnlyCabinet:
    user_number: str
    open_tickets: int
    in_progress_tickets: int
    resolved_tickets: int

@dataclass(frozen=True)
class AdminMenuSummary:
    users: int
    specialists: int
    tickets: int
    complaints: int
    blacklist: int
    audit_alerts: int

@dataclass(frozen=True)
class SuperAdminMenuSummary:
    users: int
    specialists: int
    tickets: int
    complaints: int
    global_blacklist: int
    system_alerts: int
    finance_alerts: int
    audit_alerts: int

@dataclass(frozen=True)
class ModeratorMenuSummary:
    profiles: int
    portfolio: int
    reviews: int
    complaints: int
    blacklist: int

@dataclass(frozen=True)
class AdminSpecialistPage:
    items: tuple[AdminSpecialistQueueItem, ...]
    page: int
    status: str
    has_next: bool

@dataclass(frozen=True)
class ModeratorSpecialistCard:
    specialist_id: UUID
    display_name: str
    profession_name: str
    city_name: str | None
    status: str
    description: str
    masked_contact: str
    service_titles: tuple[str, ...]
    complaints_count: int
    open_risk_flags_count: int

@dataclass(frozen=True)
class ModeratorComplaintQueueCard:
    complaint_id: UUID
    reporter_label: str
    target_label: str
    reason: str
    status: str
    created_at: object
    is_assigned: bool
    requires_admin_escalation: bool

@dataclass(frozen=True)
class ModeratorScopedBlacklistCard:
    blacklist_id: UUID
    user_id: UUID
    user_label: str
    reason: str
    comment: str | None
    status: str
    scope_label: str
    can_revoke: bool
    created_at: object
    revoke_reason: str | None

@dataclass(frozen=True)
class AdminGlobalBlacklistCard:
    blacklist_id: UUID
    user_id: UUID
    user_label: str
    actor_label: str
    reason: str
    comment: str | None
    status: str
    user_status: str
    created_at: object
    can_revoke: bool


@dataclass(frozen=True)
class AdminGlobalBlacklistPage:
    items: tuple[AdminGlobalBlacklistCard, ...]
    page: int
    view: str
    has_next: bool

@dataclass(frozen=True)
class AdminAuditCard:
    action_id: UUID
    date: str
    actor: str
    action: str
    target: str
    target_type: str
    reason: str
    source: str


@dataclass(frozen=True)
class AdminAuditPage:
    items: tuple[AdminAuditCard, ...]
    page: int
    target_type: str
    has_next: bool

@dataclass(frozen=True)
class SuperAdminAuditEventDetailCard:
    action_id: UUID
    timestamp: str
    actor: str
    action: str
    target: str
    target_type: str
    reason: str
    before_summary: str
    after_summary: str
    payload_summary: str
    correlation_id: str
    source: str

@dataclass(frozen=True)
class SuperAdminSystemStatusCard:
    app_version: str
    db_status: str
    db_version: str
    telegram_status: str
    migration_version: str
    migrations_status: str
    maintenance_mode: str
    feature_flags_status: str
    env_status: str

@dataclass(frozen=True)
class SuperAdminSmokeTestResultCard:
    code: str
    title: str
    status: str
    detail: str


@dataclass(frozen=True)
class SuperAdminSmokeTestRunCard:
    results: tuple[SuperAdminSmokeTestResultCard, ...]
    total: int
    passed: int
    failed: int

@dataclass(frozen=True)
class SuperAdminSmokeHistoryCard:
    date: str
    selected_code: str
    total: int
    passed: int
    failed: int
    destructive: bool

@dataclass(frozen=True)
class ModeratorComplaintCard:
    complaint_id: UUID
    reporter_label: str
    target_type: str
    target_label: str
    reason: str
    comment: str | None
    status: str
    created_at: object
    requires_admin_escalation: bool
    history: tuple[str, ...]

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

    async def open_admin_thread_contexts(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> list[AdminThreadContextRow]:
        try:
            return await self.repository.list_admin_thread_contexts(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
            )
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

    async def open_admin_thread_messages(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        thread_id: UUID,
    ) -> list[AdminThreadMessageRow]:
        try:
            messages = await self.repository.list_admin_thread_messages_for_thread(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
            )

            await self.repository.log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_type="admin_thread_viewed",
                target_type="thread",
                target_id=thread_id,
                before_state={},
                after_state={
                    "messages_count": len(messages),
                    "read_only": True,
                },
                reason="Thread viewed from complaint or risk context",
            )

            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return messages

    async def start_super_admin_impersonation_view(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        target_role: str,
        reason: str,
    ) -> SuperAdminImpersonationPreview:
        normalized_reason = self._require_reason(reason)
        normalized_role = (target_role or "").strip().lower()

        allowed_roles = {
            "client",
            "specialist",
            "support",
            "moderator",
            "admin",
        }

        if normalized_role not in allowed_roles:
            raise ModerationError("Unsupported role for read-only preview.")

        try:
            target_roles = (
                await self.repository.list_super_admin_user_roles(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    target_user_id=target_user_id,
                )
            )

            has_selected_role = any(
                item.role == normalized_role
                and item.status == "active"
                for item in target_roles
            )

            if not has_selected_role:
                raise ImpersonationRoleUnavailableError(
                    "Selected user does not have this active role."
                )

            await self.repository.log_super_admin_impersonation_view(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                target_role=normalized_role,
                action="started",
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except ImpersonationRoleUnavailableError:
            await self.repository.session.rollback()
            raise

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return SuperAdminImpersonationPreview(
            target_user_number=f"user-{target_user_id.hex[:8]}",
            target_role=normalized_role,
            read_only=True,
            status="started",
        )

    async def get_client_read_only_cabinet(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        language: str,
    ) -> ClientReadOnlyCabinet:
        try:
            await self.repository.require_admin_role(
                admin_user_id,
                {"super_admin"},
            )

            user_service = UserService(
                self.repository.session
            )

            profile = (
                await user_service.get_client_profile_by_user_id(
                    user_id=target_user_id,
                    language=language,
                )
            )

            if not profile:
                raise ModerationNotFoundError(
                    "Target user not found."
                )

            if "client" not in profile.available_roles:
                raise ImpersonationRoleUnavailableError(
                    "Selected user does not have this active role."
                )

            unread_counts = (
                await user_service.repository.get_role_unread_counts(
                    target_user_id
                )
            )

            request_counts = (
                await ContactChatRepository(
                    self.repository.session
                ).count_client_active_requests_by_status(
                    user_id=target_user_id,
                )
            )

            requests_new = int(
                request_counts.get("new", 0)
            )
            requests_accepted = int(
                request_counts.get("accepted", 0)
            )

            return ClientReadOnlyCabinet(
                user_number=profile.user_number,
                display_name=profile.name,
                city_name=profile.city_name,
                dialogs_unread=int(
                    unread_counts.get("client", 0)
                ),
                requests_new=requests_new,
                requests_accepted=requests_accepted,
            )

        except ImpersonationRoleUnavailableError:
            raise

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

    async def get_specialist_read_only_cabinet(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        language: str,
    ) -> SpecialistReadOnlyCabinet:
        try:
            await self.repository.require_admin_role(
                admin_user_id,
                {"super_admin"},
            )

            user_service = UserService(
                self.repository.session
            )
            active_roles = (
                await user_service.repository.list_active_roles(
                    target_user_id
                )
            )

            if "specialist" not in active_roles:
                raise ImpersonationRoleUnavailableError(
                    "Selected user does not have this active role."
                )

            specialist_repository = SpecialistRepository(
                self.repository.session
            )
            specialist = (
                await specialist_repository.get_by_user_id(
                    target_user_id
                )
            )

            if (
                not specialist
                or specialist.tenant_id != tenant_id
            ):
                raise ImpersonationRoleUnavailableError(
                    "Selected user does not have a specialist cabinet."
                )

            profession_links = (
                await specialist_repository
                .list_active_specialist_professions(
                    specialist.id
                )
            )

            localized_field = {
                "ru": "name_ru",
                "en": "name_en",
                "pt": "name_pt",
            }.get(language, "name_ru")

            professions = tuple(
                str(
                    getattr(
                        item.Profession,
                        localized_field,
                        None,
                    )
                    or item.Profession.name_ru
                    or item.Profession.name_en
                    or item.Profession.name_pt
                    or item.Profession.name
                )
                for item in profession_links
            )

            unread_counts = (
                await user_service.repository.get_role_unread_counts(
                    target_user_id
                )
            )

            new_requests = (
                await ContactChatRepository(
                    self.repository.session
                ).count_new_requests_for_specialist(
                    specialist_id=specialist.id,
                )
            )

            return SpecialistReadOnlyCabinet(
                specialist_id=specialist.id,
                user_number=f"user-{target_user_id.hex[:8]}",
                display_name=(
                    specialist.display_name
                    or f"user-{target_user_id.hex[:8]}"
                ),
                professions=professions,
                status=specialist.status,
                dialogs_unread=int(
                    unread_counts.get("specialist", 0)
                ),
                new_requests=int(new_requests),
                is_available=bool(specialist.is_available),
            )

        except ImpersonationRoleUnavailableError:
            raise

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

    async def get_support_read_only_cabinet(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> SupportReadOnlyCabinet:
        try:
            await self.repository.require_admin_role(
                admin_user_id,
                {"super_admin"},
            )

            user_service = UserService(
                self.repository.session
            )
            active_roles = (
                await user_service.repository.list_active_roles(
                    target_user_id
                )
            )

            if "support" not in active_roles:
                raise ImpersonationRoleUnavailableError(
                    "Selected user does not have this active role."
                )

            counts = await SupportService(
                SupportRepository(self.repository.session)
            ).get_staff_ticket_counts(
                tenant_id=tenant_id,
                staff_user_id=target_user_id,
                statuses={
                    "open",
                    "in_progress",
                    "resolved",
                },
            )

            return SupportReadOnlyCabinet(
                user_number=f"user-{target_user_id.hex[:8]}",
                open_tickets=int(counts.get("open", 0)),
                in_progress_tickets=int(
                    counts.get("in_progress", 0)
                ),
                resolved_tickets=int(
                    counts.get("resolved", 0)
                ),
            )

        except ImpersonationRoleUnavailableError:
            raise

        except SupportServiceError as exc:
            raise ModerationError(str(exc)) from exc

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

    async def stop_super_admin_impersonation_view(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        reason: str,
    ) -> SuperAdminImpersonationPreview:
        normalized_reason = self._require_reason(reason)

        try:
            await self.repository.log_super_admin_impersonation_view(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                target_role=None,
                action="stopped",
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (ModerationAccessError, ModerationNotFoundError) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return SuperAdminImpersonationPreview(
            target_user_number=f"user-{target_user_id.hex[:8]}",
            target_role="-",
            read_only=True,
            status="stopped",
        )

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

    async def grant_super_admin_user_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        role: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            role_mapping = await self.repository.grant_super_admin_user_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                role=role,
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=role_mapping.id,
            status=role_mapping.status,
            message=f"Role {role_mapping.role} granted.",
        )

    async def revoke_super_admin_user_role(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        role: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            role_mapping = await self.repository.revoke_super_admin_user_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                role=role,
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=role_mapping.id,
            status=role_mapping.status,
            message=f"Role {role_mapping.role} revoked.",
        )

    async def list_admin_user_history(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        limit: int = 10,
    ) -> list[AdminUserHistoryCard]:
        try:
            rows = await self.repository.list_admin_user_history(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                limit=limit,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="admin_user_view",
                entity_type="user",
                entity_id=target_user_id,
                payload={
                    "section": "history",
                    "visible_count": len(rows),
                },
            )

            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return [
            self._build_admin_user_history_card(row)
            for row in rows
        ]

    @staticmethod
    def _build_admin_user_history_card(
        row: AdminUserHistoryRow,
    ) -> AdminUserHistoryCard:
        actor = (
            f"user-{row.actor_user_id.hex[:8]}"
            if row.actor_user_id
            else "system"
        )

        return AdminUserHistoryCard(
            date=(
                row.created_at.strftime("%Y-%m-%d %H:%M")
                if row.created_at
                else "-"
            ),
            actor=actor,
            action=row.action,
            reason=row.reason or "-",
            source=row.source,
        )

    async def get_admin_user_details(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> AdminUserDetailsCard:
        try:
            row = await self.repository.get_admin_user_details(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="admin_user_view",
                entity_type="user",
                entity_id=target_user_id,
                payload={
                    "user_number": (
                        f"user-{target_user_id.hex[:8]}"
                    ),
                },
            )

            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return self._build_admin_user_details_card(row)

    @staticmethod
    def _build_admin_user_details_card(
        row: AdminUserDetailsRow,
    ) -> AdminUserDetailsCard:
        username = (row.username or "").strip()
        masked_username = (
            f"@{username[:3]}***"
            if username
            else "-"
        )

        display_name = " ".join(
            part
            for part in (
                (row.first_name or "").strip(),
                (row.last_name or "").strip(),
            )
            if part
        ) or "-"

        last_seen = (
            row.last_seen_at.strftime("%Y-%m-%d %H:%M")
            if row.last_seen_at
            else "-"
        )

        return AdminUserDetailsCard(
            user_id=row.user_id,
            user_number=f"user-{row.user_id.hex[:8]}",
            display_name=display_name,
            username=masked_username,
            roles=row.roles,
            status=row.status,
            last_seen=last_seen,
            complaints_count=row.complaints_count,
            is_global_blacklisted=(
                row.is_global_blacklisted
            ),
        )

    async def search_admin_users(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        query: str,
    ) -> list[AdminUserSearchCard]:
        normalized_query = (query or "").strip()

        if len(normalized_query) < 2:
            raise ModerationError(
                "Search query must contain at least 2 characters."
            )

        if len(normalized_query) > 100:
            raise ModerationError(
                "Search query is too long."
            )

        try:
            rows = await self.repository.search_admin_users(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=normalized_query,
                limit=10,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="user_search",
                entity_type="user",
                entity_id=None,
                payload={
                    "results_count": len(rows),
                    "query_length": len(normalized_query),
                },
            )

            await self.repository.session.commit()
        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return [
            self._build_admin_user_search_card(row)
            for row in rows
        ]

    async def search_super_admin_users(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        query: str,
    ) -> list[SuperAdminUserSearchCard]:
        normalized_query = (query or "").strip()

        if len(normalized_query) < 2:
            raise ModerationError("Search query must be at least 2 characters.")

        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            rows = await self.repository.search_super_admin_users(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=normalized_query,
                limit=10,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="super_admin_user_search",
                entity_type="user",
                entity_id=None,
                payload={
                    "query_length": len(normalized_query),
                    "result_count": len(rows),
                    "search_type": "basic",
                },
            )

        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

        return [
            self._build_super_admin_user_search_card(row)
            for row in rows
        ]

    @staticmethod
    def _build_super_admin_user_search_card(
        row,
    ) -> SuperAdminUserSearchCard:
        telegram_id = row.platform_user_id or ""
        masked_telegram_id = (
            f"***{telegram_id[-4:]}"
            if telegram_id
            else "-"
        )

        username = (row.username or "").strip()
        masked_username = (
            f"@{username[:3]}***"
            if username
            else "-"
        )

        display_name = (
            row.display_name
            or row.first_name
            or masked_username
            or f"user-{row.user_id.hex[:8]}"
        )

        roles = tuple(
            role.strip()
            for role in (row.roles or "").split(",")
            if role.strip()
        )

        return SuperAdminUserSearchCard(
            user_id=row.user_id,
            user_number=f"user-{row.user_id.hex[:8]}",
            display_name=display_name,
            username=masked_username,
            telegram_id=masked_telegram_id,
            status=row.status or "-",
            roles=roles,
        )

    async def get_super_admin_user_details(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> SuperAdminUserDetailsCard:
        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            row = await self.repository.get_super_admin_user_details(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="super_admin_user_viewed",
                entity_type="user",
                entity_id=target_user_id,
                payload={
                    "user_number": f"user-{target_user_id.hex[:8]}",
                    "source": "super_admin",
                },
            )

        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc
        except ModerationNotFoundError as exc:
            raise ModerationError(str(exc)) from exc

        return self._build_super_admin_user_details_card(row)

    async def list_super_admin_user_roles(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> list[SuperAdminUserRoleCard]:
        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            rows = await self.repository.list_super_admin_user_roles(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="super_admin_user_roles_viewed",
                entity_type="user",
                entity_id=target_user_id,
                payload={
                    "user_number": f"user-{target_user_id.hex[:8]}",
                    "count": len(rows),
                },
            )

        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc
        except ModerationNotFoundError as exc:
            raise ModerationError(str(exc)) from exc

        return [
            self._build_super_admin_user_role_card(row)
            for row in rows
        ]

    async def open_super_admin_role_scopes(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        user_id: UUID | None,
        view: str,
        page: int,
        page_size: int = 5,
    ) -> SuperAdminRoleScopePage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        normalized_view = (
            "history"
            if view == "history"
            else "active"
        )

        statuses = (
            {"revoked"}
            if normalized_view == "history"
            else {"active"}
        )

        try:
            rows = await self.repository.list_super_admin_role_scopes(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                user_id=user_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=normalized_page * normalized_page_size,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="scope_queue_viewed",
                entity_type="role_scope",
                entity_id=user_id,
                payload={
                    "source": "super_admin_scopes",
                    "view": normalized_view,
                    "page": normalized_page,
                    "count": min(len(rows), normalized_page_size),
                    "has_next": len(rows) > normalized_page_size,
                    "filtered_user": (
                        f"user-{user_id.hex[:8]}"
                        if user_id
                        else None
                    ),
                },
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        has_next = len(rows) > normalized_page_size
        visible_rows = rows[:normalized_page_size]

        return SuperAdminRoleScopePage(
            items=tuple(
                self._build_super_admin_role_scope_card(row)
                for row in visible_rows
            ),
            page=normalized_page,
            view=normalized_view,
            has_next=has_next,
        )

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
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            role_scope = await self.repository.add_super_admin_role_scope(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                user_id=user_id,
                role=role,
                scope_type=scope_type,
                scope_value=scope_value,
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=role_scope.id,
            status=role_scope.status,
            message="Scope added.",
        )

    async def revoke_super_admin_role_scope(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        scope_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            role_scope = await self.repository.revoke_super_admin_role_scope(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                scope_id=scope_id,
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=role_scope.id,
            status=role_scope.status,
            message="Scope revoked.",
        )

    @staticmethod
    def _build_super_admin_role_scope_card(
        row: SuperAdminRoleScopeRow,
    ) -> SuperAdminRoleScopeCard:
        created_by = (
            f"user-{row.created_by.hex[:8]}"
            if row.created_by
            else "-"
        )

        revoked_by = (
            f"user-{row.revoked_by.hex[:8]}"
            if row.revoked_by
            else "-"
        )

        created_at = (
            row.created_at.strftime("%Y-%m-%d %H:%M")
            if row.created_at
            else "-"
        )

        revoked_at = (
            row.revoked_at.strftime("%Y-%m-%d %H:%M")
            if row.revoked_at
            else "-"
        )

        return SuperAdminRoleScopeCard(
            scope_id=row.scope_id,
            user_id=row.user_id,
            user_number=f"user-{row.user_id.hex[:8]}",
            role=row.role,
            scope_type=row.scope_type,
            scope_value=row.scope_value,
            status=row.status,
            reason=row.reason,
            created_by=created_by,
            created_at=created_at,
            revoked_by=revoked_by,
            revoked_at=revoked_at,
        )

    async def list_super_admin_permission_matrix(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        query: str = "",
        limit: int = 10,
    ) -> list[SuperAdminPermissionMatrixCard]:
        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            rows = await self.repository.list_super_admin_permission_matrix(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=query,
                limit=limit,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="permission_matrix_viewed",
                entity_type="permission_matrix",
                entity_id=None,
                payload={
                    "query": query,
                    "count": len(rows),
                    "source": "super_admin",
                },
            )

        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

        return [
            self._build_super_admin_permission_matrix_card(row)
            for row in rows
        ]

    async def grant_super_admin_permission(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        role: str,
        permission_code: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            permission_role_id = await self.repository.grant_super_admin_permission(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                role=role,
                permission_code=permission_code,
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=permission_role_id,
            status="active",
            message="Permission granted.",
        )

    async def revoke_super_admin_permission(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        role: str,
        permission_code: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            permission_role_id = await self.repository.revoke_super_admin_permission(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                role=role,
                permission_code=permission_code,
                reason=normalized_reason,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=permission_role_id,
            status="revoked",
            message="Permission revoked.",
        )

    @staticmethod
    def _build_super_admin_permission_matrix_card(
        row: SuperAdminPermissionMatrixRow,
    ) -> SuperAdminPermissionMatrixCard:
        created_at = (
            row.created_at.strftime("%Y-%m-%d %H:%M")
            if row.created_at
            else "-"
        )

        return SuperAdminPermissionMatrixCard(
            permission_number=f"permission-{row.permission_id.hex[:8]}",
            role=row.role,
            permission_code=row.permission_code,
            description=row.description or "-",
            scope=row.scope,
            status=row.status,
            granted_by=row.granted_by or "-",
            created_at=created_at,
        )

    @staticmethod
    def _build_super_admin_user_role_card(
        row: SuperAdminUserRoleRow,
    ) -> SuperAdminUserRoleCard:
        scope = (
            "tenant"
            if row.tenant_id
            else "global"
        )

        granted_by = (
            f"user-{row.granted_by.hex[:8]}"
            if row.granted_by
            else "-"
        )

        granted_at = (
            row.granted_at.strftime("%Y-%m-%d %H:%M")
            if row.granted_at
            else "-"
        )

        return SuperAdminUserRoleCard(
            role_number=f"role-{row.role_id.hex[:8]}",
            role=row.role,
            status=row.status,
            scope=scope,
            granted_by=granted_by,
            granted_at=granted_at,
        )

    @staticmethod
    def _build_super_admin_user_details_card(
        row: SuperAdminUserDetailsRow,
    ) -> SuperAdminUserDetailsCard:
        telegram_id = row.platform_user_id or ""
        masked_telegram_id = (
            f"***{telegram_id[-4:]}"
            if telegram_id
            else "-"
        )

        username = (row.username or "").strip()
        masked_username = (
            f"@{username[:3]}***"
            if username
            else "-"
        )

        display_name = (
            row.display_name
            or row.first_name
            or masked_username
            or f"user-{row.user_id.hex[:8]}"
        )

        roles = tuple(
            role.strip()
            for role in (row.roles or "").split(",")
            if role.strip()
        )

        last_seen = (
            row.last_seen_at.strftime("%Y-%m-%d %H:%M")
            if row.last_seen_at
            else "-"
        )

        risk_score = int(row.risk_score or 0)
        risk_flags = "none" if risk_score <= 0 else f"risk:{risk_score}"

        return SuperAdminUserDetailsCard(
            user_id=row.user_id,
            user_number=f"user-{row.user_id.hex[:8]}",
            display_name=display_name,
            username=masked_username,
            telegram_id=masked_telegram_id,
            status=row.status or "-",
            active_role=row.active_role or "-",
            roles=roles,
            scopes=tuple(),
            last_seen=last_seen,
            risk_flags=risk_flags,
            complaints_count=int(row.complaints_count or 0),
            blacklist_count=int(row.blacklist_count or 0),
        )

    @staticmethod
    def _build_admin_user_search_card(
        row: AdminUserSearchRow,
    ) -> AdminUserSearchCard:
        telegram_id = row.platform_user_id or ""
        masked_telegram_id = (
            f"***{telegram_id[-4:]}"
            if len(telegram_id) > 4
            else "***"
        )

        username = (row.username or "").strip()
        masked_username = (
            f"@{username[:3]}***"
            if username
            else "-"
        )

        display_name = " ".join(
            part
            for part in (
                (row.first_name or "").strip(),
                (row.last_name or "").strip(),
            )
            if part
        ) or "-"

        return AdminUserSearchCard(
            user_id=row.user_id,
            user_number=f"user-{row.user_id.hex[:8]}",
            telegram_id=masked_telegram_id,
            username=masked_username,
            display_name=display_name,
            status=row.status,
        )

    async def open_admin_menu(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> AdminMenuSummary:
        try:
            counts = await self.repository.get_admin_menu_counts(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="admin_menu",
                entity_type="admin_dashboard",
                entity_id=admin_user_id,
                payload={"counts": counts},
            )

            await self.repository.session.commit()
        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return AdminMenuSummary(**counts)

    async def open_super_admin_menu(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> SuperAdminMenuSummary:
        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            counts = await self.repository.get_super_admin_menu_counts(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="super_admin_menu_opened",
                entity_type="super_admin_dashboard",
                entity_id=admin_user_id,
                payload={"counts": counts},
            )

        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

        return SuperAdminMenuSummary(**counts)

    async def open_moderator_menu(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
    ) -> ModeratorMenuSummary:
        try:
            counts = await self.repository.get_moderator_menu_counts(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="moderator_menu",
                entity_type="moderator_dashboard",
                entity_id=moderator_user_id,
                payload={"counts": counts},
            )

            await self.repository.session.commit()
        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModeratorMenuSummary(**counts)

    async def open_admin_specialists(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        status: str = "active",
        page: int = 0,
        page_size: int = 5,
    ) -> AdminSpecialistPage:
        allowed_statuses = {
            "all",
            "draft",
            "pending_moderation",
            "active",
            "paused",
            "rejected",
            "blocked",
            "deleted",
        }

        normalized_status = (status or "active").strip().lower()

        if normalized_status not in allowed_statuses:
            raise ModerationError(
                "Unsupported specialist status."
            )

        normalized_page = max(int(page), 0)
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        statuses = (
            allowed_statuses - {"all"}
            if normalized_status == "all"
            else {normalized_status}
        )

        try:
            rows = await self.repository.list_admin_specialists(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )

            visible_rows = rows[:normalized_page_size]
            has_next = len(rows) > normalized_page_size

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="admin_specialists",
                entity_type="specialist",
                entity_id=admin_user_id,
                payload={
                    "status": normalized_status,
                    "page": normalized_page,
                    "visible_count": len(visible_rows),
                    "has_next": has_next,
                },
            )

            await self.repository.session.commit()
        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return AdminSpecialistPage(
            items=tuple(visible_rows),
            page=normalized_page,
            status=normalized_status,
            has_next=has_next,
        )

    async def open_pending_specialists_queue(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ) -> list[PendingSpecialistQueueItem]:
        normalized_page = max(int(page), 0)
        normalized_page_size = max(1, min(int(page_size), 10))

        try:
            items = await self.repository.list_pending_specialists(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                limit=normalized_page_size + 1,
                offset=normalized_page * normalized_page_size,
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="queue_opened",
                entity_type="specialist",
                entity_id=moderator_user_id,
                payload={
                    "queue": "pending_specialists",
                    "page": normalized_page,
                    "visible_count": min(
                        len(items),
                        normalized_page_size,
                    ),
                    "has_next": len(items) > normalized_page_size,
                },
            )

            await self.repository.session.commit()
            return items
        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

    async def get_moderator_specialist_card(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
    ) -> ModeratorSpecialistCard:
        try:
            details = await self.repository.get_pending_specialist_details(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

        masked_contact = (
            "***"
            if details.contact_text
            else "-"
        )

        return ModeratorSpecialistCard(
            specialist_id=details.specialist_id,
            display_name=details.display_name,
            profession_name=details.profession_name,
            city_name=details.city_name,
            status=details.status,
            description=details.description,
            masked_contact=masked_contact,
            service_titles=details.service_titles,
            complaints_count=details.complaints_count,
            open_risk_flags_count=(
                details.open_risk_flags_count
            ),
        )

    async def approve_specialist(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            specialist = await self.repository.approve_specialist(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
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
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            specialist = await self.repository.reject_specialist(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=specialist.id,
            status=specialist.status,
            message="Specialist rejected.",
        )
    async def request_specialist_changes(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            specialist = await self.repository.request_specialist_changes(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
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
            message="Specialist profile returned for changes.",
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
        normalized_comment = (comment or "").strip() or None

        if normalized_reason == "other" and not normalized_comment:
            raise ModerationError(
                "Comment is required for the other complaint reason."
            )

        if self.rate_limit_service is not None:
            try:
                await self.rate_limit_service.ensure_complaint_allowed(
                    tenant_id=tenant_id,
                    user_id=reporter_user_id,
                )
            except RateLimitError as exc:
                raise ModerationError(str(exc)) from exc

        has_duplicate = await self.repository.has_active_complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type=normalized_target_type,
            target_id=target_id,
            reason=normalized_reason,
        )
        if has_duplicate:
            raise ModerationError(
                "An active complaint with this reason already exists."
            )

        try:
            complaint = await self.repository.create_complaint(
                tenant_id=tenant_id,
                reporter_user_id=reporter_user_id,
                target_type=normalized_target_type,
                target_id=target_id,
                reason=normalized_reason,
                comment=normalized_comment,
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

        return complaint

    async def confirm_complaint(
        self,
        *,
        reporter_user_id: UUID,
        complaint_id: UUID,
    ) -> Complaint:
        try:
            complaint = await self.repository.confirm_complaint(
                reporter_user_id=reporter_user_id,
                complaint_id=complaint_id,
            )
            await self.repository.session.commit()
        except ModerationNotFoundError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return complaint

    async def open_complaints_queue(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        statuses: set[str],
        page: int = 0,
        page_size: int = 5,
    ) -> list[ModeratorComplaintQueueCard]:
        normalized_page = max(
            int(page),
            0,
        )
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        try:
            items = await self.repository.list_complaints_queue(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )

            cards = []

            for item in items:
                target_label, requires_admin_escalation = (
                    await self.repository.get_complaint_target_context(
                        tenant_id=tenant_id,
                        target_type=item.target_type,
                        target_id=item.target_id,
                    )
                )

                reporter_token = str(
                    item.reporter_user_id
                ).replace("-", "")[:8]

                cards.append(
                    ModeratorComplaintQueueCard(
                        complaint_id=item.complaint_id,
                        reporter_label=(
                            f"user-{reporter_token}"
                        ),
                        target_label=target_label,
                        reason=item.reason,
                        status=item.status,
                        created_at=item.created_at,
                        is_assigned=(
                            item.reviewed_by is not None
                        ),
                        requires_admin_escalation=(
                            requires_admin_escalation
                        ),
                    )
                )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="complaint_queue",
                entity_type="complaint",
                entity_id=moderator_user_id,
                payload={
                    "page": normalized_page,
                    "statuses": sorted(statuses),
                    "visible_count": min(
                        len(cards),
                        normalized_page_size,
                    ),
                    "has_next": (
                        len(cards)
                        > normalized_page_size
                    ),
                },
            )

            await self.repository.session.commit()
            return cards

        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

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

    async def get_moderator_complaint_card(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
    ) -> ModeratorComplaintCard:
        try:
            details = (
                await self.repository
                .get_complaint_moderation_details(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                )
            )

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        reporter_token = (
            str(details.reporter_user_id)
            .replace("-", "")[:8]
        )

        history = tuple(
            (
                f"{created_at:%Y-%m-%d %H:%M} | "
                f"{event_type}"
            )
            for event_type, created_at in details.history
        )

        return ModeratorComplaintCard(
            complaint_id=details.complaint_id,
            reporter_label=f"user-{reporter_token}",
            target_type=details.target_type,
            target_label=details.target_label,
            reason=details.reason,
            comment=details.comment,
            status=details.status,
            created_at=details.created_at,
            requires_admin_escalation=(
                details.requires_admin_escalation
            ),
            history=history,
        )

    async def take_complaint(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
    ) -> ModerationActionResult:
        try:
            complaint = await self.repository.take_complaint(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
            ValueError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=complaint.id,
            status=complaint.status,
            message="Complaint taken.",
        )

    async def escalate_complaint_to_admin(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            complaint = (
                await self.repository
                .escalate_complaint_to_admin(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    reason=normalized_reason,
                )
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=complaint.id,
            status=complaint.status,
            message="Complaint escalated to Admin.",
        )

    async def resolve_complaint(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
        status: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            complaint = await self.repository.resolve_complaint(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
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

    async def open_scoped_blacklist_queue(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        view: str,
        page: int,
        page_size: int = 5,
    ) -> list[ModeratorScopedBlacklistCard]:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        statuses = (
            {"revoked"}
            if view == "revoked"
            else {"active"}
        )

        try:
            items = await self.repository.list_scoped_blacklist(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )

            cards = []

            for item in items:
                user_token = (
                    str(item.user_id)
                    .replace("-", "")[:8]
                )

                is_global_blocked = (
                    item.user_status == "blocked"
                )

                cards.append(
                    ModeratorScopedBlacklistCard(
                        blacklist_id=item.blacklist_id,
                        user_id=item.user_id,
                        user_label=f"user-{user_token}",
                        reason=item.reason,
                        comment=item.comment,
                        status=item.status,
                        scope_label=(
                            "Global + tenant"
                            if is_global_blocked
                            else "Tenant"
                        ),
                        can_revoke=(
                            item.status == "active"
                            and not is_global_blocked
                        ),
                        created_at=item.created_at,
                        revoke_reason=item.revoke_reason,
                    )
                )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="scoped_blacklist_opened",
                entity_type="blacklist",
                entity_id=moderator_user_id,
                payload={
                    "view": view,
                    "page": normalized_page,
                    "visible_count": min(
                        len(cards),
                        normalized_page_size,
                    ),
                    "has_next": (
                        len(cards)
                        > normalized_page_size
                    ),
                },
            )

            await self.repository.session.commit()
            return cards

        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

    async def add_complaint_target_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        complaint_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            target_user_id = (
                await self.repository
                .get_complaint_target_user_id(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                )
            )

            blacklist = (
                await self.repository.add_scoped_blacklist(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    user_id=target_user_id,
                    reason=normalized_reason,
                    comment=(
                        "Created from complaint "
                        f"{complaint_id}"
                    ),
                )
            )

            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=blacklist.id,
            status=blacklist.status,
            message=(
                "Complaint target blacklisted "
                "inside tenant."
            ),
        )

    async def add_specialist_owner_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        specialist_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)
        normalized_comment = (comment or "").strip() or None

        try:
            blacklist = (
                await self.repository
                .add_specialist_owner_scoped_blacklist(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    specialist_id=specialist_id,
                    reason=normalized_reason,
                    comment=normalized_comment,
                )
            )
            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=blacklist.id,
            status=blacklist.status,
            message="Specialist owner blacklisted in tenant.",
        )

    async def add_scoped_blacklist_by_telegram_id(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        telegram_id: str,
        reason: str,
    ) -> ModerationActionResult:
        normalized_telegram_id = (
            telegram_id or ""
        ).strip()
        normalized_reason = self._require_reason(
            reason
        )

        if not normalized_telegram_id.isdigit():
            raise ModerationError(
                "Telegram ID must contain only digits."
            )

        try:
            blacklist = (
                await self.repository
                .add_scoped_blacklist_by_telegram_id(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    telegram_id=normalized_telegram_id,
                    reason=normalized_reason,
                )
            )

            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=blacklist.id,
            status=blacklist.status,
            message="User added to tenant blacklist.",
        )

    async def add_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)
        normalized_comment = (comment or "").strip() or None

        try:
            blacklist = await self.repository.add_scoped_blacklist(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                user_id=user_id,
                reason=normalized_reason,
                comment=normalized_comment,
            )
            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=blacklist.id,
            status=blacklist.status,
            message="User blacklisted in tenant.",
        )

    async def revoke_scoped_blacklist(
        self,
        *,
        moderator_user_id: UUID,
        tenant_id: UUID,
        blacklist_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(
            reason
        )

        try:
            blacklist = (
                await self.repository
                .revoke_scoped_blacklist(
                    moderator_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    blacklist_id=blacklist_id,
                    reason=normalized_reason,
                )
            )

            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=blacklist.id,
            status=blacklist.status,
            message="Scoped blacklist revoked.",
        )

    async def get_admin_audit_card(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        action_id: UUID,
    ) -> AdminAuditCard:
        try:
            row = await self.repository.get_admin_audit_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_id=action_id,
            )
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

        actor = (
            f"user-{row.actor_user_id.hex[:8]}"
            if row.actor_user_id
            else "system"
        )

        target = (
            f"{row.target_type}-{row.target_id.hex[:8]}"
            if row.target_id
            else row.target_type
        )

        return AdminAuditCard(
            action_id=row.action_id,
            date=row.created_at.strftime("%Y-%m-%d %H:%M"),
            actor=actor,
            action=row.action,
            target=target,
            target_type=row.target_type,
            reason=row.reason or "-",
            source=row.source,
        )

    async def open_admin_audit(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        target_type: str,
        page: int,
        page_size: int = 5,
    ) -> AdminAuditPage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        normalized_target_type = (
            str(target_type or "all").strip().lower()
        )

        allowed_target_types = {
            "all",
            "user",
            "specialist",
            "support_ticket",
            "complaint",
            "review",
            "specialist_portfolio_item",
            "blacklist",
        }

        if normalized_target_type not in allowed_target_types:
            normalized_target_type = "all"

        target_types = (
            None
            if normalized_target_type == "all"
            else {normalized_target_type}
        )

        try:
            rows = await self.repository.list_admin_audit_actions(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_types=target_types,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )

            has_next = len(rows) > normalized_page_size
            visible_rows = rows[:normalized_page_size]

            cards = tuple(
                AdminAuditCard(
                    action_id=row.action_id,
                    date=row.created_at.strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    actor=(
                        f"user-{row.actor_user_id.hex[:8]}"
                        if row.actor_user_id
                        else "system"
                    ),
                    action=row.action,
                    target=(
                        f"{row.target_type}-"
                        f"{row.target_id.hex[:8]}"
                        if row.target_id
                        else row.target_type
                    ),
                    target_type=row.target_type,
                    reason=row.reason or "-",
                    source=row.source,
                )
                for row in visible_rows
            )

            await self.repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="audit_viewed",
                entity_type="audit",
                entity_id=admin_user_id,
                payload={
                    "page": normalized_page,
                    "target_type": normalized_target_type,
                    "count": len(cards),
                    "has_next": has_next,
                },
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return AdminAuditPage(
            items=cards,
            page=normalized_page,
            target_type=normalized_target_type,
            has_next=has_next,
        )

    async def open_super_admin_audit(
        self,
        *,
        admin_user_id: UUID,
        target_type: str,
        page: int,
        page_size: int = 5,
    ) -> AdminAuditPage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        normalized_target_type = (
            str(target_type or "all").strip().lower()
        )

        allowed_target_types = {
            "all",
            "user",
            "specialist",
            "support_ticket",
            "complaint",
            "review",
            "specialist_portfolio_item",
            "blacklist",
            "permission",
            "role_scope",
            "permission_matrix",
            "audit",
        }

        if normalized_target_type not in allowed_target_types:
            normalized_target_type = "all"

        target_types = (
            None
            if normalized_target_type == "all"
            else {normalized_target_type}
        )

        try:
            rows = await self.repository.list_super_admin_audit_actions(
                admin_user_id=admin_user_id,
                target_types=target_types,
                limit=normalized_page_size + 1,
                offset=normalized_page * normalized_page_size,
            )

            has_next = len(rows) > normalized_page_size
            visible_rows = rows[:normalized_page_size]

            cards = tuple(
                AdminAuditCard(
                    action_id=row.action_id,
                    date=row.created_at.strftime("%Y-%m-%d %H:%M"),
                    actor=(
                        f"user-{row.actor_user_id.hex[:8]}"
                        if row.actor_user_id
                        else "system"
                    ),
                    action=row.action,
                    target=(
                        f"{row.target_type}-{row.target_id.hex[:8]}"
                        if row.target_id
                        else row.target_type
                    ),
                    target_type=row.target_type,
                    reason=row.reason or "-",
                    source=row.source,
                )
                for row in visible_rows
            )

            await self.repository.log_event(
                tenant_id=None,
                user_id=admin_user_id,
                event_type="audit_viewed",
                entity_type="audit",
                entity_id=admin_user_id,
                payload={
                    "page": normalized_page,
                    "target_type": normalized_target_type,
                    "count": len(cards),
                    "has_next": has_next,
                    "source": "super_admin_global_audit",
                },
            )
            await self.repository.session.commit()

        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return AdminAuditPage(
            items=cards,
            page=normalized_page,
            target_type=normalized_target_type,
            has_next=has_next,
        )

    async def get_super_admin_audit_event_detail(
        self,
        *,
        admin_user_id: UUID,
        action_id: UUID,
    ) -> SuperAdminAuditEventDetailCard:
        try:
            row = await self.repository.get_super_admin_audit_event_detail(
                admin_user_id=admin_user_id,
                action_id=action_id,
            )

            await self.repository.log_event(
                tenant_id=None,
                user_id=admin_user_id,
                event_type="audit_event_viewed",
                entity_type="audit",
                entity_id=action_id,
                payload={
                    "source": "super_admin_global_audit",
                    "audit_source": row.source,
                    "action": row.action,
                },
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return self._build_super_admin_audit_event_detail_card(row)

    async def open_super_admin_system_status(
        self,
        *,
        admin_user_id: UUID,
    ) -> SuperAdminSystemStatusCard:
        try:
            row = await self.repository.get_super_admin_system_status(
                admin_user_id=admin_user_id,
            )

            await self.repository.log_event(
                tenant_id=None,
                user_id=admin_user_id,
                event_type="system_settings_viewed",
                entity_type="system",
                entity_id=admin_user_id,
                payload={
                    "source": "super_admin_system",
                    "db_status": row.db_status,
                    "migrations_table_exists": row.migrations_table_exists,
                },
            )
            await self.repository.session.commit()

        except ModerationAccessError as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return self._build_super_admin_system_status_card(row)

    def list_super_admin_smoke_definitions(
        self,
    ) -> tuple[SuperAdminSmokeTestResultCard, ...]:
        return (
            SuperAdminSmokeTestResultCard(
                code="start",
                title="Start",
                status="ready",
                detail="Start command and role switcher smoke.",
            ),
            SuperAdminSmokeTestResultCard(
                code="registration",
                title="Registration",
                status="ready",
                detail="Legal gate and specialist registration smoke.",
            ),
            SuperAdminSmokeTestResultCard(
                code="search",
                title="Search",
                status="ready",
                detail="Public specialist search and card smoke.",
            ),
            SuperAdminSmokeTestResultCard(
                code="request",
                title="Request",
                status="ready",
                detail="Contact request lifecycle smoke.",
            ),
            SuperAdminSmokeTestResultCard(
                code="dialogs",
                title="Dialogs",
                status="ready",
                detail="Dialog list, unread and read-only checks.",
            ),
            SuperAdminSmokeTestResultCard(
                code="support",
                title="Support",
                status="ready",
                detail="Support ticket access smoke.",
            ),
            SuperAdminSmokeTestResultCard(
                code="moderation",
                title="Moderation",
                status="ready",
                detail="Moderator queues and decisions smoke.",
            ),
            SuperAdminSmokeTestResultCard(
                code="admin_access",
                title="Admin access",
                status="ready",
                detail="Admin/Super Admin access smoke.",
            ),
        )

    async def run_super_admin_smoke_tests(
        self,
        *,
        admin_user_id: UUID,
        selected_code: str | None = None,
    ) -> SuperAdminSmokeTestRunCard:
        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            definitions = self.list_super_admin_smoke_definitions()

            if selected_code:
                normalized_code = selected_code.strip().lower()
                definitions = tuple(
                    item
                    for item in definitions
                    if item.code == normalized_code
                )

            if not definitions:
                raise ModerationNotFoundError("Smoke test not found.")

            results = []

            for definition in definitions:
                results.append(
                    await self._run_super_admin_smoke_check(
                        definition,
                        admin_user_id=admin_user_id,
                    )
                )

            passed = sum(
                1
                for item in results
                if item.status == "passed"
            )
            failed = len(results) - passed

            await self.repository.log_event(
                tenant_id=None,
                user_id=admin_user_id,
                event_type="smoke_test_run",
                entity_type="smoke_test",
                entity_id=admin_user_id,
                payload={
                    "selected_code": selected_code or "all",
                    "total": len(results),
                    "passed": passed,
                    "failed": failed,
                    "destructive": False,
                    "source": "super_admin_smoke_tests",
                    "checks": [
                        {
                            "code": item.code,
                            "status": item.status,
                        }
                        for item in results
                    ],
                },
            )
            await self.repository.session.commit()

        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return SuperAdminSmokeTestRunCard(
            results=tuple(results),
            total=len(results),
            passed=passed,
            failed=failed,
        )

    async def list_super_admin_smoke_history(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 5,
    ) -> tuple[SuperAdminSmokeHistoryCard, ...]:
        try:
            roles = await self.repository.get_admin_roles(admin_user_id)

            if "super_admin" not in roles:
                raise ModerationAccessError("Super Admin access required.")

            result = await self.repository.session.execute(
                text("""
                    SELECT payload, created_at
                    FROM event_logs
                    WHERE event_type = 'smoke_test_run'
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                {
                    "limit": max(1, min(int(limit), 10)),
                },
            )

        except ModerationAccessError as exc:
            raise ModerationError(str(exc)) from exc

        items = []

        for row in result.mappings():
            payload = row["payload"] or {}

            items.append(
                SuperAdminSmokeHistoryCard(
                    date=row["created_at"].strftime("%Y-%m-%d %H:%M"),
                    selected_code=str(
                        payload.get("selected_code") or "-"
                    ),
                    total=int(payload.get("total") or 0),
                    passed=int(payload.get("passed") or 0),
                    failed=int(payload.get("failed") or 0),
                    destructive=bool(payload.get("destructive")),
                )
            )

        return tuple(items)

    async def _run_super_admin_smoke_check(
        self,
        definition: SuperAdminSmokeTestResultCard,
        *,
        admin_user_id: UUID,
    ) -> SuperAdminSmokeTestResultCard:
        checks = {
            "start": self._smoke_check_start,
            "registration": self._smoke_check_registration,
            "search": self._smoke_check_search,
            "request": self._smoke_check_request,
            "dialogs": self._smoke_check_dialogs,
            "support": self._smoke_check_support,
            "moderation": self._smoke_check_moderation,
            "admin_access": self._smoke_check_admin_access,
        }

        checker = checks.get(definition.code)

        if not checker:
            return SuperAdminSmokeTestResultCard(
                code=definition.code,
                title=definition.title,
                status="failed",
                detail="Smoke check is not implemented.",
            )

        try:
            detail = await checker(admin_user_id=admin_user_id)
        except Exception as exc:
            return SuperAdminSmokeTestResultCard(
                code=definition.code,
                title=definition.title,
                status="failed",
                detail=str(exc),
            )

        return SuperAdminSmokeTestResultCard(
            code=definition.code,
            title=definition.title,
            status="passed",
            detail=detail,
        )

    async def _table_exists(
        self,
        table_name: str,
    ) -> bool:
        result = await self.repository.session.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                )
            """),
            {
                "table_name": table_name,
            },
        )

        return bool(result.scalar_one())

    async def _count_table_rows(
        self,
        table_name: str,
    ) -> int:
        result = await self.repository.session.execute(
            text(f"SELECT count(*) FROM {table_name}")
        )

        return int(result.scalar_one() or 0)

    async def _require_tables(
        self,
        *table_names: str,
    ) -> dict[str, int]:
        counts = {}

        for table_name in table_names:
            exists = await self._table_exists(table_name)

            if not exists:
                raise ValueError(f"Missing table: {table_name}")

            counts[table_name] = await self._count_table_rows(
                table_name
            )

        return counts

    async def _smoke_check_start(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        roles = await self.repository.get_admin_roles(admin_user_id)

        if "super_admin" not in roles:
            raise ValueError("Current user has no super_admin role.")

        counts = await self._require_tables(
            "users",
            "user_accounts",
            "user_roles",
        )

        return (
            "Start access ok. "
            f"users={counts['users']}, "
            f"user_accounts={counts['user_accounts']}, "
            f"user_roles={counts['user_roles']}."
        )

    async def _smoke_check_registration(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        counts = await self._require_tables(
            "legal_documents",
            "user_consents",
            "specialists",
        )

        return (
            "Registration prerequisites ok. "
            f"legal_documents={counts['legal_documents']}, "
            f"user_consents={counts['user_consents']}, "
            f"specialists={counts['specialists']}."
        )

    async def _smoke_check_search(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        counts = await self._require_tables(
            "specialists",
            "professions",
            "cities",
        )

        active_result = await self.repository.session.execute(
            text("""
                SELECT count(*)
                FROM specialists
                WHERE status = 'active'
            """)
        )
        active_count = int(active_result.scalar_one() or 0)

        return (
            "Search prerequisites ok. "
            f"active_specialists={active_count}, "
            f"professions={counts['professions']}, "
            f"cities={counts['cities']}."
        )

    async def _smoke_check_request(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        counts = await self._require_tables(
            "contact_requests",
            "conversation_threads",
        )

        constraint_result = await self.repository.session.execute(
            text("""
                SELECT count(*)
                FROM information_schema.constraint_column_usage
                WHERE table_name = 'contact_requests'
            """)
        )

        return (
            "Contact request prerequisites ok. "
            f"contact_requests={counts['contact_requests']}, "
            f"conversation_threads={counts['conversation_threads']}, "
            f"constraints={int(constraint_result.scalar_one() or 0)}."
        )

    async def _smoke_check_dialogs(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        counts = await self._require_tables(
            "conversation_threads",
            "conversation_participants",
            "messages",
        )

        return (
            "Dialogs prerequisites ok. "
            f"threads={counts['conversation_threads']}, "
            f"participants={counts['conversation_participants']}, "
            f"messages={counts['messages']}."
        )

    async def _smoke_check_support(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        counts = await self._require_tables(
            "support_tickets",
            "support_messages",
        )

        return (
            "Support prerequisites ok. "
            f"tickets={counts['support_tickets']}, "
            f"messages={counts['support_messages']}."
        )

    async def _smoke_check_moderation(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        counts = await self._require_tables(
            "complaints",
            "admin_actions",
            "event_logs",
            "blacklist",
        )

        return (
            "Moderation prerequisites ok. "
            f"complaints={counts['complaints']}, "
            f"admin_actions={counts['admin_actions']}, "
            f"event_logs={counts['event_logs']}, "
            f"blacklist={counts['blacklist']}."
        )

    async def _smoke_check_admin_access(
        self,
        *,
        admin_user_id: UUID,
    ) -> str:
        roles = await self.repository.get_admin_roles(admin_user_id)

        required = {
            "super_admin",
        }

        missing = required.difference(roles)

        if missing:
            raise ValueError(
                f"Missing admin roles: {', '.join(sorted(missing))}"
            )

        counts = await self._require_tables(
            "role_permissions",
            "permissions",
        )

        return (
            "Admin access prerequisites ok. "
            f"roles={', '.join(sorted(roles))}; "
            f"role_permissions={counts['role_permissions']}, "
            f"permissions={counts['permissions']}."
        )

    @staticmethod
    def _build_super_admin_system_status_card(
        row: SuperAdminSystemStatusRow,
    ) -> SuperAdminSystemStatusCard:
        return SuperAdminSystemStatusCard(
            app_version="unknown",
            db_status=row.db_status,
            db_version=row.db_version.split(" on ")[0],
            telegram_status="configured",
            migration_version=row.migration_version,
            migrations_status=(
                "configured"
                if row.migrations_table_exists
                else "not configured"
            ),
            maintenance_mode="disabled",
            feature_flags_status="not configured",
            env_status="available: yes; secrets hidden",
        )

    @staticmethod
    def _mask_audit_value(value) -> str:
        text = str(value)

        if len(text) >= 32 and "-" in text:
            return f"{text[:8]}..."

        if len(text) > 80:
            return f"{text[:77]}..."

        return text

    @classmethod
    def _summarize_audit_dict(
        cls,
        data: dict,
    ) -> str:
        if not data:
            return "-"

        lines = []

        for key, value in list(data.items())[:8]:
            lines.append(
                f"{key}: {cls._mask_audit_value(value)}"
            )

        return "\n".join(lines) if lines else "-"

    @classmethod
    def _build_super_admin_audit_event_detail_card(
        cls,
        row: SuperAdminAuditEventDetailRow,
    ) -> SuperAdminAuditEventDetailCard:
        actor = (
            f"user-{row.actor_user_id.hex[:8]}"
            if row.actor_user_id
            else "system"
        )

        target = (
            f"{row.target_type}-{row.target_id.hex[:8]}"
            if row.target_id
            else row.target_type
        )

        correlation_id = (
            f"{row.correlation_id[:8]}..."
            if row.correlation_id
            else "-"
        )

        return SuperAdminAuditEventDetailCard(
            action_id=row.action_id,
            timestamp=row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            actor=actor,
            action=row.action,
            target=target,
            target_type=row.target_type,
            reason=row.reason or "-",
            before_summary=cls._summarize_audit_dict(row.before_state),
            after_summary=cls._summarize_audit_dict(row.after_state),
            payload_summary=cls._summarize_audit_dict(row.payload),
            correlation_id=correlation_id,
            source=row.source,
        )

    async def open_global_blacklist_queue(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        view: str,
        page: int,
        page_size: int = 5,
    ) -> AdminGlobalBlacklistPage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        normalized_view = (
            "history"
            if view == "history"
            else "active"
        )

        statuses = (
            {"revoked"}
            if normalized_view == "history"
            else {"active"}
        )

        try:
            rows = await self.repository.list_global_blacklist(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

        has_next = len(rows) > normalized_page_size
        visible_rows = rows[:normalized_page_size]

        cards = tuple(
            AdminGlobalBlacklistCard(
                blacklist_id=row.blacklist_id,
                user_id=row.user_id,
                user_label=(
                    f"user-{row.user_id.hex[:8]}"
                ),
                actor_label=(
                    f"user-{row.created_by.hex[:8]}"
                ),
                reason=row.reason,
                comment=row.comment,
                status=row.status,
                user_status=row.user_status,
                created_at=row.created_at,
                can_revoke=(
                    row.status == "active"
                    and row.user_status == "blocked"
                    and row.user_id != admin_user_id
                ),
            )
            for row in visible_rows
        )

        return AdminGlobalBlacklistPage(
            items=cards,
            page=normalized_page,
            view=normalized_view,
            has_next=has_next,
        )

    async def open_super_admin_global_blacklist_queue(
        self,
        *,
        admin_user_id: UUID,
        view: str,
        page: int,
        page_size: int = 5,
    ) -> AdminGlobalBlacklistPage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        normalized_view = (
            "history"
            if view == "history"
            else "active"
        )

        statuses = (
            {"revoked"}
            if normalized_view == "history"
            else {"active"}
        )

        try:
            rows = await self.repository.list_super_admin_global_blacklist(
                admin_user_id=admin_user_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=normalized_page * normalized_page_size,
            )
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            raise ModerationError(str(exc)) from exc

        has_next = len(rows) > normalized_page_size
        visible_rows = rows[:normalized_page_size]

        cards = tuple(
            AdminGlobalBlacklistCard(
                blacklist_id=row.blacklist_id,
                user_id=row.user_id,
                user_label=f"user-{row.user_id.hex[:8]}",
                actor_label=f"user-{row.created_by.hex[:8]}",
                reason=row.reason,
                comment=row.comment,
                status=row.status,
                user_status=row.user_status,
                created_at=row.created_at,
                can_revoke=(
                    row.status == "active"
                    and row.user_status == "blocked"
                    and row.user_id != admin_user_id
                ),
            )
            for row in visible_rows
        )

        return AdminGlobalBlacklistPage(
            items=cards,
            page=normalized_page,
            view=normalized_view,
            has_next=has_next,
        )

    async def unblock_user(
        self,
        *,
        admin_user_id: UUID,
        user_id: UUID,
        reason: str,
    ) -> ModerationActionResult:
        normalized_reason = self._require_reason(reason)

        try:
            user = await self.repository.unblock_user(
                admin_user_id=admin_user_id,
                user_id=user_id,
                reason=normalized_reason,
            )
            await self.repository.session.commit()
        except (
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise ModerationError(str(exc)) from exc

        return ModerationActionResult(
            entity_id=user.id,
            status=user.status,
            message="Global block removed.",
        )

    async def block_user(
        self,
        *,
        admin_user_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> ModerationActionResult:
        if admin_user_id == user_id:
            raise ModerationError(
                "Administrator cannot globally block themselves."
            )

        target_roles = await self.repository.get_admin_roles(user_id)

        if "root" in target_roles:
            raise ModerationError(
                "Root cannot be globally blocked by this action."
            )

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