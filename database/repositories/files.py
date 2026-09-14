from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import FileStorageObject


class FileStorageObjectNotFoundError(
    LookupError
):
    pass


class FileRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def create_pending_upload(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        entity_type: str,
        entity_id: UUID | None,
        file_type: str,
        mime_type: str,
        size_bytes: int,
        storage_provider: str,
        storage_path: str,
    ) -> FileStorageObject:
        storage_object = FileStorageObject(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            file_type=file_type,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_provider=storage_provider,
            storage_path=storage_path,
            public_url=None,
            visibility_scope="private",
            status="pending_upload",
            antivirus_status="pending",
            provider_metadata={},
            completed_at=None,
            retention_until=None,
        )

        self.session.add(storage_object)
        await self.session.flush()
        return storage_object
    async def get_pending_upload_for_update(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        file_id: UUID,
    ) -> FileStorageObject:
        result = await self.session.execute(
            select(FileStorageObject)
            .where(
                FileStorageObject.tenant_id
                == tenant_id,
                FileStorageObject.owner_user_id
                == owner_user_id,
                FileStorageObject.id == file_id,
                FileStorageObject.status
                == "pending_upload",
            )
            .with_for_update()
        )
        storage_object = (
            result.scalar_one_or_none()
        )

        if storage_object is None:
            raise (
                FileStorageObjectNotFoundError(
                    "Pending upload not found."
                )
            )

        return storage_object
    async def complete_upload(
        self,
        *,
        storage_object: FileStorageObject,
        provider_metadata: dict,
        completed_at: datetime,
    ) -> FileStorageObject:
        storage_object.status = "ready"
        storage_object.antivirus_status = (
            "not_scanned"
        )
        storage_object.provider_metadata = dict(
            provider_metadata
        )
        storage_object.completed_at = completed_at
        storage_object.retention_until = None

        await self.session.flush()
        return storage_object
    async def get_downloadable_file(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        file_id: UUID,
    ) -> FileStorageObject:
        result = await self.session.execute(
            select(FileStorageObject).where(
                FileStorageObject.tenant_id
                == tenant_id,
                FileStorageObject.owner_user_id
                == owner_user_id,
                FileStorageObject.id == file_id,
                FileStorageObject.status
                == "ready",
                FileStorageObject.antivirus_status
                .in_(
                    (
                        "not_scanned",
                        "clean",
                    )
                ),
            )
        )
        storage_object = (
            result.scalar_one_or_none()
        )

        if storage_object is None:
            raise (
                FileStorageObjectNotFoundError(
                    "Downloadable file not found."
                )
            )

        return storage_object
    async def list_orphan_uploads_for_update(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[FileStorageObject]:
        normalized_limit = max(
            1,
            min(int(limit), 500),
        )

        result = await self.session.execute(
            select(FileStorageObject)
            .where(
                FileStorageObject.status
                == "pending_upload",
                FileStorageObject.created_at
                <= cutoff,
            )
            .order_by(
                FileStorageObject.created_at.asc()
            )
            .limit(normalized_limit)
            .with_for_update(
                skip_locked=True
            )
        )

        return list(result.scalars().all())
    async def delete_storage_record(
        self,
        *,
        storage_object: FileStorageObject,
    ) -> None:
        await self.session.delete(
            storage_object
        )
        await self.session.flush()

