from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    FileStorageObject,
    Specialist,
    SpecialistPortfolioItem,
    UserRoleMapping,
)


PORTFOLIO_MODERATION_ROLES = {
    "super_admin",
    "admin",
    "moderator",
}

MAX_PORTFOLIO_ITEMS = 20
MAX_PORTFOLIO_PHOTOS = 10
MAX_PORTFOLIO_PDFS = 10


class PortfolioRepositoryError(Exception):
    pass


class PortfolioRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_owned_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> Specialist:
        query = select(Specialist).where(
            Specialist.tenant_id == tenant_id,
            Specialist.user_id == user_id,
            Specialist.status != "deleted",
        )

        if for_update:
            query = query.with_for_update()

        specialist = (
            await self.session.execute(query)
        ).scalar_one_or_none()

        if not specialist:
            raise PortfolioRepositoryError(
                "Specialist profile not found."
            )

        return specialist

    async def require_moderator(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> set[str]:
        result = await self.session.execute(
            select(UserRoleMapping.role).where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(PORTFOLIO_MODERATION_ROLES),
            )
        )
        roles = set(result.scalars().all())

        if not roles:
            raise PortfolioRepositoryError(
                "Portfolio moderation access denied."
            )

        return roles

    async def get_portfolio_counts(
        self,
        *,
        specialist_id: UUID,
    ) -> dict[str, int]:
        result = await self.session.execute(
            select(
                func.count(SpecialistPortfolioItem.id),
                func.count(SpecialistPortfolioItem.id).filter(
                    FileStorageObject.file_type == "photo"
                ),
                func.count(SpecialistPortfolioItem.id).filter(
                    FileStorageObject.file_type == "pdf"
                ),
            )
            .join(
                FileStorageObject,
                FileStorageObject.id == SpecialistPortfolioItem.file_id,
            )
            .where(
                SpecialistPortfolioItem.specialist_id == specialist_id,
                SpecialistPortfolioItem.status != "deleted",
            )
        )

        total, photos, pdfs = result.one()

        return {
            "total": int(total or 0),
            "photo": int(photos or 0),
            "pdf": int(pdfs or 0),
        }

    async def ensure_upload_allowed(
        self,
        *,
        specialist_id: UUID,
        file_type: str,
    ) -> None:
        if file_type not in {"photo", "pdf"}:
            raise PortfolioRepositoryError(
                "Unsupported portfolio file type."
            )

        counts = await self.get_portfolio_counts(
            specialist_id=specialist_id,
        )

        if counts["total"] >= MAX_PORTFOLIO_ITEMS:
            raise PortfolioRepositoryError(
                "Portfolio item limit reached."
            )

        if (
            file_type == "photo"
            and counts["photo"] >= MAX_PORTFOLIO_PHOTOS
        ):
            raise PortfolioRepositoryError(
                "Portfolio photo limit reached."
            )

        if (
            file_type == "pdf"
            and counts["pdf"] >= MAX_PORTFOLIO_PDFS
        ):
            raise PortfolioRepositoryError(
                "Portfolio PDF limit reached."
            )

    async def create_pending_item(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        professional_cabinet_id: UUID,
        storage_path: str,
        file_type: str,
        mime_type: str,
        size_bytes: int,
        title: str | None = None,
        description: str | None = None,
    ) -> tuple[SpecialistPortfolioItem, FileStorageObject]:
        specialist = await self.get_owned_specialist(
            tenant_id=tenant_id,
            user_id=owner_user_id,
            for_update=True,
        )
        if (
            not specialist.active_professional_cabinet_id
            or specialist.active_professional_cabinet_id
            != professional_cabinet_id
        ):
            raise PortfolioRepositoryError(
                "Portfolio cabinet does not match the active cabinet."
            )
        await self.ensure_upload_allowed(
            specialist_id=specialist.id,
            file_type=file_type,
        )

        storage_object = FileStorageObject(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            entity_type="specialist_portfolio_item",
            entity_id=None,
            file_type=file_type,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_provider="supabase",
            storage_path=storage_path,
            public_url=None,
            visibility_scope="private",
            retention_until=None,
        )
        self.session.add(storage_object)
        await self.session.flush()

        item = SpecialistPortfolioItem(
            tenant_id=tenant_id,
            specialist_id=specialist.id,
            professional_cabinet_id=(
                professional_cabinet_id
            ),
            title=(title or "").strip() or None,
            description=(description or "").strip() or None,
            file_url=None,
            file_id=storage_object.id,
            status="pending_moderation",
        )
        self.session.add(item)
        await self.session.flush()

        storage_object.entity_id = item.id
        await self.session.flush()

        return item, storage_object

    async def list_owner_items(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
    ) -> list[tuple[SpecialistPortfolioItem, FileStorageObject]]:
        specialist = await self.get_owned_specialist(
            tenant_id=tenant_id,
            user_id=owner_user_id,
        )
        if not specialist.active_professional_cabinet_id:
            raise PortfolioRepositoryError(
                "Active professional cabinet not found."
            )
        result = await self.session.execute(
            select(SpecialistPortfolioItem, FileStorageObject)
            .join(
                FileStorageObject,
                FileStorageObject.id == SpecialistPortfolioItem.file_id,
            )
            .where(
                SpecialistPortfolioItem.tenant_id == tenant_id,
                SpecialistPortfolioItem.specialist_id == specialist.id,
                SpecialistPortfolioItem.professional_cabinet_id
                == specialist.active_professional_cabinet_id,
                SpecialistPortfolioItem.status != "deleted",
                FileStorageObject.owner_user_id == owner_user_id,
            )
            .order_by(
                SpecialistPortfolioItem.sort_order,
                SpecialistPortfolioItem.created_at.desc(),
            )
        )

        return list(result.tuples().all())

    async def list_active_items(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
    ) -> list[tuple[SpecialistPortfolioItem, FileStorageObject]]:
        filters = [
            SpecialistPortfolioItem.tenant_id
            == tenant_id,
            SpecialistPortfolioItem.specialist_id
            == specialist_id,
            SpecialistPortfolioItem.status
            == "active",
            FileStorageObject.visibility_scope
            == "private",
        ]

        if professional_cabinet_id is not None:
            filters.append(
                SpecialistPortfolioItem.professional_cabinet_id
                == professional_cabinet_id
            )

        result = await self.session.execute(
            select(
                SpecialistPortfolioItem,
                FileStorageObject,
            )
            .join(
                FileStorageObject,
                FileStorageObject.id == SpecialistPortfolioItem.file_id,
            )
            .where(*filters)
            .order_by(
                SpecialistPortfolioItem.sort_order,
                SpecialistPortfolioItem.created_at.desc(),
            )
        )

        return list(result.tuples().all())

    async def list_pending_items(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        limit: int = 6,
        offset: int = 0,
    ) -> list[
        tuple[
            SpecialistPortfolioItem,
            FileStorageObject,
        ]
    ]:
        await self.require_moderator(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
        )

        normalized_limit = max(1, min(int(limit), 20))
        normalized_offset = max(0, int(offset))

        result = await self.session.execute(
            select(
                SpecialistPortfolioItem,
                FileStorageObject,
            )
            .join(
                FileStorageObject,
                FileStorageObject.id
                == SpecialistPortfolioItem.file_id,
            )
            .where(
                SpecialistPortfolioItem.tenant_id == tenant_id,
                SpecialistPortfolioItem.status
                == "pending_moderation",
                FileStorageObject.tenant_id == tenant_id,
                FileStorageObject.owner_user_id
                != moderator_user_id,
            )
            .order_by(
                SpecialistPortfolioItem.created_at.asc(),
                SpecialistPortfolioItem.id.asc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return list(result.tuples().all())
    
    async def list_rejected_items(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        limit: int = 20,
    ) -> list[tuple[SpecialistPortfolioItem, FileStorageObject]]:
        await self.require_moderator(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
        )

        result = await self.session.execute(
            select(SpecialistPortfolioItem, FileStorageObject)
            .join(
                FileStorageObject,
                FileStorageObject.id == SpecialistPortfolioItem.file_id,
            )
            .where(
                SpecialistPortfolioItem.tenant_id == tenant_id,
                SpecialistPortfolioItem.status == "rejected",
                FileStorageObject.tenant_id == tenant_id,
            )
            .order_by(SpecialistPortfolioItem.created_at.desc())
            .limit(max(1, min(int(limit), 50)))
        )

        return list(result.tuples().all())

    async def moderate_item(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        item_id: UUID,
        status: str,
    ) -> tuple[
        SpecialistPortfolioItem,
        FileStorageObject,
        str,
    ]:
        await self.require_moderator(
            tenant_id=tenant_id,
            user_id=moderator_user_id,
        )

        if status not in {"active", "rejected"}:
            raise PortfolioRepositoryError(
                "Unsupported moderation status."
            )

        result = await self.session.execute(
            select(
                SpecialistPortfolioItem,
                FileStorageObject,
                Specialist.user_id,
            )
            .join(
                FileStorageObject,
                FileStorageObject.id
                == SpecialistPortfolioItem.file_id,
            )
            .join(
                Specialist,
                Specialist.id
                == SpecialistPortfolioItem.specialist_id,
            )
            .where(
                SpecialistPortfolioItem.id == item_id,
                SpecialistPortfolioItem.tenant_id == tenant_id,
                FileStorageObject.tenant_id == tenant_id,
                Specialist.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        row = result.first()

        if not row:
            raise PortfolioRepositoryError(
                "Portfolio item not found."
            )

        item, storage_object, owner_user_id = row

        if owner_user_id == moderator_user_id:
            raise PortfolioRepositoryError(
                "You cannot moderate your own portfolio item."
            )

        if item.status != "pending_moderation":
            raise PortfolioRepositoryError(
                "Portfolio item is no longer pending moderation."
            )

        before_status = item.status
        item.status = status

        if status == "active":
            storage_object.retention_until = None
        else:
            storage_object.retention_until = (
                datetime.now(timezone.utc)
                + timedelta(days=90)
            )

        await self.session.flush()

        return item, storage_object, before_status
    
    async def mark_owner_item_deleted(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        item_id: UUID,
    ) -> tuple[SpecialistPortfolioItem, FileStorageObject]:
        specialist = await self.get_owned_specialist(
            tenant_id=tenant_id,
            user_id=owner_user_id,
        )

        result = await self.session.execute(
            select(SpecialistPortfolioItem, FileStorageObject)
            .join(
                FileStorageObject,
                FileStorageObject.id == SpecialistPortfolioItem.file_id,
            )
            .where(
                SpecialistPortfolioItem.id == item_id,
                SpecialistPortfolioItem.tenant_id == tenant_id,
                SpecialistPortfolioItem.specialist_id == specialist.id,
                FileStorageObject.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )
        row = result.first()

        if not row:
            raise PortfolioRepositoryError(
                "Portfolio item not found."
            )

        item, storage_object = row
        item.status = "deleted"
        storage_object.retention_until = (
            datetime.now(timezone.utc) + timedelta(days=30)
        )

        await self.session.flush()
        return item, storage_object

    async def list_cleanup_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[tuple[SpecialistPortfolioItem, FileStorageObject]]:
        cleanup_time = now or datetime.now(timezone.utc)

        result = await self.session.execute(
            select(SpecialistPortfolioItem, FileStorageObject)
            .join(
                FileStorageObject,
                FileStorageObject.id == SpecialistPortfolioItem.file_id,
            )
            .where(
                SpecialistPortfolioItem.status.in_(
                    {"deleted", "rejected"}
                ),
                FileStorageObject.retention_until.is_not(None),
                FileStorageObject.retention_until <= cleanup_time,
            )
            .order_by(FileStorageObject.retention_until.asc())
            .limit(max(1, min(int(limit), 500)))
        )

        return list(result.tuples().all())

    async def mark_storage_cleaned(
        self,
        *,
        storage_object_id: UUID,
    ) -> None:
        storage_object = await self.session.get(
            FileStorageObject,
            storage_object_id,
        )

        if not storage_object:
            raise PortfolioRepositoryError(
                "Storage object not found."
            )

        storage_object.retention_until = None
        await self.session.flush()