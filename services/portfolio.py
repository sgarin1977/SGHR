import logging
import os
from dataclasses import dataclass
from uuid import UUID, uuid4

from database.models import FileStorageObject, SpecialistPortfolioItem
from database.repositories.event import EventRepository
from database.repositories.moderation import (
    ModerationAccessError,
    ModerationNotFoundError,
    ModerationRepository,
)
from database.repositories.portfolio import (
    PortfolioRepository,
    PortfolioRepositoryError,
)
from database.repositories.specialist import (
    SpecialistRepository,
)
from services.portfolio_storage import (
    PortfolioStorageError,
    SupabasePortfolioStorage,
    validate_portfolio_file,
)


logger = logging.getLogger(__name__)


class PortfolioServiceError(Exception):
    pass


@dataclass(frozen=True)
class PortfolioItemView:
    item: SpecialistPortfolioItem
    storage_object: FileStorageObject
    signed_url: str | None

@dataclass(frozen=True)
class OwnerPortfolioPage:
    items: tuple[PortfolioItemView, ...]
    page: int
    page_size: int
    total: int
    total_pages: int

class PortfolioService:
    def __init__(
        self,
        repository: PortfolioRepository,
        storage: SupabasePortfolioStorage | None = None,
    ):
        self.repository = repository
        self.events = EventRepository(repository.session)
        self.storage = storage or SupabasePortfolioStorage()
        self.signed_url_ttl = int(
            os.getenv(
                "SUPABASE_STORAGE_SIGNED_URL_TTL_SECONDS",
                "900",
            )
        )

    async def upload_item(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        filename: str,
        mime_type: str | None,
        content: bytes,
        title: str | None = None,
        description: str | None = None,
    ) -> SpecialistPortfolioItem:
        try:
            validated = validate_portfolio_file(
            filename=filename,
            mime_type=mime_type,
            content=content,
)
        except PortfolioStorageError as exc:
            raise PortfolioServiceError(str(exc)) from exc

        normalized_title = self._normalize_optional_text(
            title,
            field_name="Title",
            max_length=200,
        )
        normalized_description = self._normalize_optional_text(
            description,
            field_name="Description",
            max_length=1000,
        )

        uploaded = False
        storage_path = ""

        try:
            specialist = await self.repository.get_owned_specialist(
                tenant_id=tenant_id,
                user_id=owner_user_id,
            )

            cabinet = await SpecialistRepository(
                self.repository.session
            ).get_active_professional_cabinet(
                tenant_id=tenant_id,
                specialist_id=specialist.id,
            )

            if not cabinet:
                raise PortfolioServiceError(
                    "Active professional cabinet not found."
                )

            await self.repository.ensure_upload_allowed(
                specialist_id=specialist.id,
                file_type=validated.file_type,
            )

            storage_path = (
                f"{tenant_id}/"
                f"{specialist.id}/"
                f"{uuid4().hex}{validated.extension}"
            )

            await self.storage.upload(
                storage_path=storage_path,
                content=content,
                mime_type=validated.mime_type,
            )
            uploaded = True

            item, _storage_object = (
                await self.repository.create_pending_item(
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    professional_cabinet_id=cabinet.id,
                    storage_path=storage_path,
                    file_type=validated.file_type,
                    mime_type=validated.mime_type,
                    size_bytes=validated.size_bytes,
                    title=normalized_title,
                    description=normalized_description,
                )
            )

            await self.events.create_event(
                tenant_id=tenant_id,
                user_id=owner_user_id,
                event_type="portfolio_uploaded",
                entity_type="user",
                entity_id=owner_user_id,
                payload={
                    "filename": filename,
                    "mime_type": (
                        validated.mime_type
                    ),
                    "size_bytes": (
                        validated.size_bytes
                    ),
                    "has_caption": bool(
                        normalized_description
                    ),
                    "status": (
                        "pending_moderation"
                    ),
                },
                platform="telegram",
            )

            await self.repository.session.commit()

            return item

        except (PortfolioRepositoryError, PortfolioStorageError) as exc:
            await self.repository.session.rollback()

            if uploaded and storage_path:
                try:
                    await self.storage.delete(
                        storage_path=storage_path,
                    )
                except PortfolioStorageError:
                    logger.exception(
                        "portfolio_orphan_cleanup_failed "
                        "storage_path=%s",
                        storage_path,
                    )

            raise PortfolioServiceError(str(exc)) from exc

        except Exception:
            await self.repository.session.rollback()

            if uploaded and storage_path:
                try:
                    await self.storage.delete(
                        storage_path=storage_path,
                    )
                except PortfolioStorageError:
                    logger.exception(
                        "portfolio_orphan_cleanup_failed "
                        "storage_path=%s",
                        storage_path,
                    )

            raise

    async def list_owner_items(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
    ) -> list[PortfolioItemView]:
        try:
            rows = await self.repository.list_owner_items(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            )
            return await self._create_views(rows)
        except (PortfolioRepositoryError, PortfolioStorageError) as exc:
            raise PortfolioServiceError(str(exc)) from exc

    async def list_owner_items_page(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ) -> OwnerPortfolioPage:
        normalized_page = max(int(page), 0)
        normalized_page_size = max(
            1,
            min(int(page_size), 20),
        )

        items = await self.list_owner_items(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
        )

        total = len(items)
        start = normalized_page * normalized_page_size
        page_items = tuple(
            items[start:start + normalized_page_size]
        )
        total_pages = max(
            1,
            (
                total
                + normalized_page_size
                - 1
            )
            // normalized_page_size,
        )

        try:
            await self.events.create_event(
                tenant_id=tenant_id,
                user_id=owner_user_id,
                event_type="portfolio_list",
                entity_type="user",
                entity_id=owner_user_id,
                payload={
                    "page": normalized_page,
                    "count": len(page_items),
                    "total": total,
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception as exc:
            await self.repository.session.rollback()
            raise PortfolioServiceError(
                "Unable to record portfolio list view."
            ) from exc

        return OwnerPortfolioPage(
            items=page_items,
            page=normalized_page,
            page_size=normalized_page_size,
            total=total,
            total_pages=total_pages,
        )

    async def list_active_items(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
    ) -> list[PortfolioItemView]:
        try:
            rows = await self.repository.list_active_items(
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
            return await self._create_views(rows)
        except (PortfolioRepositoryError, PortfolioStorageError) as exc:
            raise PortfolioServiceError(str(exc)) from exc

    async def list_active_items_for_viewer(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
        viewer_user_id: UUID,
        page: int = 0,
    ) -> list[PortfolioItemView]:
        items = await self.list_active_items(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                professional_cabinet_id
            ),
        )

        try:
            await self.events.create_event(
                tenant_id=tenant_id,
                user_id=viewer_user_id,
                event_type="portfolio_viewed",
                entity_type=(
                    "professional_cabinet"
                    if professional_cabinet_id
                    else "specialist"
                ),
                entity_id=(
                    professional_cabinet_id
                    or specialist_id
                ),
                payload={
                    "page": max(int(page), 0),
                    "total_count": len(items),
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception as exc:
            await self.repository.session.rollback()
            raise PortfolioServiceError(
                "Unable to record portfolio view."
            ) from exc

        return items

    async def list_pending_items(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ) -> list[PortfolioItemView]:
        normalized_page = max(int(page), 0)
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        try:
            rows = await self.repository.list_pending_items(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                limit=normalized_page_size + 1,
                offset=normalized_page * normalized_page_size,
            )
            return await self._create_views(rows)
        except (
            PortfolioRepositoryError,
            PortfolioStorageError,
        ) as exc:
            raise PortfolioServiceError(str(exc)) from exc
        
    async def list_rejected_items(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        limit: int = 20,
    ) -> list[PortfolioItemView]:
        try:
            rows = await self.repository.list_rejected_items(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                limit=limit,
            )
            return await self._create_views(rows)
        except (PortfolioRepositoryError, PortfolioStorageError) as exc:
            raise PortfolioServiceError(str(exc)) from exc

    async def approve_item(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        item_id: UUID,
        reason: str,
    ) -> SpecialistPortfolioItem:
        return await self._moderate_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=item_id,
            status="active",
            reason=reason,
        )

    async def reject_item(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        item_id: UUID,
        reason: str,
    ) -> SpecialistPortfolioItem:
        return await self._moderate_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=item_id,
            status="rejected",
            reason=reason,
        )

    async def reject_forbidden_item(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        item_id: UUID,
        reason: str,
    ) -> SpecialistPortfolioItem:
        normalized_reason = (reason or "").strip()

        if len(normalized_reason) < 3:
            raise PortfolioServiceError(
                "Moderation reason is required."
            )

        try:
            (
                item,
                _storage_object,
                before_status,
            ) = await self.repository.moderate_item(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                item_id=item_id,
                status="rejected",
            )

            moderation_repository = ModerationRepository(
                self.repository.session
            )

            await moderation_repository.create_portfolio_risk_flag(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                item_id=item.id,
                reason=normalized_reason,
            )

            await moderation_repository.log_admin_action(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                action_type="moderate_portfolio_item",
                target_type="specialist_portfolio_item",
                target_id=item.id,
                before_state={
                    "status": before_status,
                },
                after_state={
                    "status": item.status,
                    "risk_flagged": True,
                    "risk_code": (
                        "forbidden_portfolio_content"
                    ),
                },
                reason=normalized_reason,
            )

            await moderation_repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="portfolio_moderated",
                entity_type="specialist_portfolio_item",
                entity_id=item.id,
                payload={
                    "decision": "rejected",
                    "reason": normalized_reason,
                    "before_status": before_status,
                    "after_status": item.status,
                    "risk_flagged": True,
                },
            )

            await self.repository.session.commit()
            return item

        except (
            PortfolioRepositoryError,
            ModerationAccessError,
            ModerationNotFoundError,
        ) as exc:
            await self.repository.session.rollback()
            raise PortfolioServiceError(str(exc)) from exc

    async def delete_owner_item(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        item_id: UUID,
    ) -> SpecialistPortfolioItem:
        try:
            item, _storage_object = (
                await self.repository.mark_owner_item_deleted(
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    item_id=item_id,
                )
            )
            await self.repository.session.commit()
            return item
        except PortfolioRepositoryError as exc:
            await self.repository.session.rollback()
            raise PortfolioServiceError(str(exc)) from exc

    async def cleanup_due_items(
        self,
        *,
        limit: int = 100,
    ) -> int:
        cleaned_count = 0

        rows = await self.repository.list_cleanup_due(limit=limit)

        for _item, storage_object in rows:
            try:
                await self.storage.delete(
                    storage_path=storage_object.storage_path,
                )
                await self.repository.mark_storage_cleaned(
                    storage_object_id=storage_object.id,
                )
                await self.repository.session.commit()
                cleaned_count += 1
            except (PortfolioRepositoryError, PortfolioStorageError):
                await self.repository.session.rollback()
                logger.exception(
                    "portfolio_cleanup_failed storage_object_id=%s",
                    storage_object.id,
                )

        return cleaned_count

    async def _moderate_item(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        item_id: UUID,
        status: str,
        reason: str,
    ) -> SpecialistPortfolioItem:
        normalized_reason = (reason or "").strip()

        if len(normalized_reason) < 3:
            raise PortfolioServiceError(
                "Moderation reason is required."
            )

        try:
            (
                item,
                _storage_object,
                before_status,
            ) = await self.repository.moderate_item(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                item_id=item_id,
                status=status,
            )

            moderation_repository = ModerationRepository(
                self.repository.session
            )

            decision = (
                "approved"
                if status == "active"
                else "rejected"
            )

            await moderation_repository.log_admin_action(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                action_type="moderate_portfolio_item",
                target_type="specialist_portfolio_item",
                target_id=item.id,
                before_state={"status": before_status},
                after_state={"status": item.status},
                reason=normalized_reason,
            )

            await moderation_repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="portfolio_moderated",
                entity_type="specialist_portfolio_item",
                entity_id=item.id,
                payload={
                    "decision": decision,
                    "reason": normalized_reason,
                    "before_status": before_status,
                    "after_status": item.status,
                },
            )

            await self.repository.session.commit()
            return item

        except PortfolioRepositoryError as exc:
            await self.repository.session.rollback()
            raise PortfolioServiceError(str(exc)) from exc
        
    async def _create_views(
        self,
        rows: list[
            tuple[SpecialistPortfolioItem, FileStorageObject]
        ],
    ) -> list[PortfolioItemView]:
        views = []

        for item, storage_object in rows:
            signed_url = await self.storage.create_signed_url(
                storage_path=storage_object.storage_path,
                expires_in=self.signed_url_ttl,
            )
            views.append(
                PortfolioItemView(
                    item=item,
                    storage_object=storage_object,
                    signed_url=signed_url,
                )
            )

        return views

    def _normalize_optional_text(
        self,
        value: str | None,
        *,
        field_name: str,
        max_length: int,
    ) -> str | None:
        normalized = (value or "").strip()

        if not normalized:
            return None

        if len(normalized) > max_length:
            raise PortfolioServiceError(
                f"{field_name} is too long."
            )

        return normalized