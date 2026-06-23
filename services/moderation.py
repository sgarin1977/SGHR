from dataclasses import dataclass
from uuid import UUID

from database.models import AdminAction, Complaint, EventLog, Specialist, User
from database.repositories.moderation import (
    AdminSpecialistQueueItem,
    ModerationAccessError,
    AdminUserSearchRow,
    ModerationNotFoundError,
    ModerationRepository,
    PendingSpecialistQueueItem,
    PendingSpecialistDetails,
    ComplaintQueueItem,
    ComplaintModerationDetails,
    ScopedBlacklistQueueItem,
    GlobalBlacklistQueueItem,
    AdminUserDetailsRow,
    AdminUserHistoryRow,
    AdminAuditQueueItem,
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
class AdminMenuSummary:
    users: int
    specialists: int
    tickets: int
    complaints: int
    blacklist: int
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