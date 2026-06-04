import logging
import os
from dataclasses import dataclass
from uuid import UUID, uuid4

from database.models import FileStorageObject, SpecialistPortfolioItem
from database.repositories.portfolio import (
    PortfolioRepository,
    PortfolioRepositoryError,
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


class PortfolioService:
    def __init__(
        self,
        repository: PortfolioRepository,
        storage: SupabasePortfolioStorage | None = None,
    ):
        self.repository = repository
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
                    storage_path=storage_path,
                    file_type=validated.file_type,
                    mime_type=validated.mime_type,
                    size_bytes=validated.size_bytes,
                    title=normalized_title,
                    description=normalized_description,
                )
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

    async def list_active_items(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
    ) -> list[PortfolioItemView]:
        try:
            rows = await self.repository.list_active_items(
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )
            return await self._create_views(rows)
        except (PortfolioRepositoryError, PortfolioStorageError) as exc:
            raise PortfolioServiceError(str(exc)) from exc

    async def list_pending_items(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        limit: int = 20,
    ) -> list[PortfolioItemView]:
        try:
            rows = await self.repository.list_pending_items(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                limit=limit,
            )
            return await self._create_views(rows)
        except (PortfolioRepositoryError, PortfolioStorageError) as exc:
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
    ) -> SpecialistPortfolioItem:
        return await self._moderate_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=item_id,
            status="active",
        )

    async def reject_item(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        item_id: UUID,
    ) -> SpecialistPortfolioItem:
        return await self._moderate_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=item_id,
            status="rejected",
        )

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
    ) -> SpecialistPortfolioItem:
        try:
            item, _storage_object = (
                await self.repository.moderate_item(
                    tenant_id=tenant_id,
                    moderator_user_id=moderator_user_id,
                    item_id=item_id,
                    status=status,
                )
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