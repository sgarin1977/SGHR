import os
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


from database.repositories.files import (
    FileRepository,
)
from services.file_storage import (
    FILE_SIGNED_URL_TTL_SECONDS,
    FileStorageProvider,
)


IMAGE_MAX_SIZE_BYTES = 10 * 1024 * 1024
DOCUMENT_MAX_SIZE_BYTES = 20 * 1024 * 1024

DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-"
    "officedocument.wordprocessingml."
    "document"
)

ALLOWED_FILE_OBJECT_TYPES = frozenset(
    {
        "portfolio",
        "message",
        "cv",
        "document",
    }
)

_FILE_TYPES_BY_EXTENSION = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"),
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", DOCX_MIME_TYPE),
}


class FileServiceError(Exception):
    pass


class FileUploadValidationError(
    FileServiceError
):
    pass


class FileUploadVerificationError(
    FileServiceError
):
    pass


@dataclass(frozen=True)
class ValidatedFileUpload:
    object_type: str
    filename: str
    file_type: str
    mime_type: str
    size_bytes: int
    extension: str


def validate_file_upload(
    *,
    object_type: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> ValidatedFileUpload:
    normalized_object_type = str(
        object_type or ""
    ).strip().lower()

    if (
        normalized_object_type
        not in ALLOWED_FILE_OBJECT_TYPES
    ):
        raise FileUploadValidationError(
            "Unsupported attachment object type."
        )

    normalized_filename = str(
        filename or ""
    ).strip()

    if (
        not normalized_filename
        or Path(normalized_filename).name
        != normalized_filename
    ):
        raise FileUploadValidationError(
            "Invalid filename."
        )

    extension = Path(
        normalized_filename
    ).suffix.lower()

    file_contract = (
        _FILE_TYPES_BY_EXTENSION.get(
            extension
        )
    )
    if file_contract is None:
        raise FileUploadValidationError(
            "Unsupported file type."
        )

    file_type, expected_mime = file_contract
    normalized_mime = str(
        mime_type or ""
    ).split(";", 1)[0].strip().lower()

    if normalized_mime != expected_mime:
        raise FileUploadValidationError(
            "File MIME type does not match "
            "its extension."
        )

    if (
        file_type == "docx"
        and normalized_object_type
        not in {"cv", "document"}
    ):
        raise FileUploadValidationError(
            "DOCX is allowed only for CV "
            "and documents."
        )

    try:
        normalized_size = int(size_bytes)
    except (TypeError, ValueError) as exc:
        raise FileUploadValidationError(
            "Invalid file size."
        ) from exc

    if normalized_size <= 0:
        raise FileUploadValidationError(
            "File must not be empty."
        )

    max_size = (
        IMAGE_MAX_SIZE_BYTES
        if file_type == "image"
        else DOCUMENT_MAX_SIZE_BYTES
    )
    if normalized_size > max_size:
        raise FileUploadValidationError(
            "File exceeds the allowed size."
        )

    return ValidatedFileUpload(
        object_type=normalized_object_type,
        filename=normalized_filename,
        file_type=file_type,
        mime_type=normalized_mime,
        size_bytes=normalized_size,
        extension=extension,
    )

@dataclass(frozen=True)
class FileUploadRequestView:
    id: UUID
    upload_url: str
    expires_in: int
    status: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class FileCompletedView:
    id: UUID
    status: str
    antivirus_status: str
    mime_type: str
    size_bytes: int
    completed_at: datetime


@dataclass(frozen=True)
class FileDownloadView:
    id: UUID
    download_url: str
    expires_in: int
    mime_type: str
    size_bytes: int


class FileService:
    def __init__(
        self,
        *,
        repository: FileRepository,
        storage: FileStorageProvider,
        storage_provider_name: str,
    ):
        normalized_provider = str(
            storage_provider_name or ""
        ).strip().lower()

        if not normalized_provider:
            raise ValueError(
                "Storage provider is not configured."
            )

        self.repository = repository
        self.storage = storage
        self.storage_provider_name = (
            normalized_provider
        )

    async def create_upload_request(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        object_type: str,
        entity_id: UUID | None,
        filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> FileUploadRequestView:
        validated = validate_file_upload(
            object_type=object_type,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )

        storage_path = (
            f"{tenant_id}/{owner_user_id}/"
            f"{uuid4().hex}"
            f"{validated.extension}"
        )

        try:
            storage_object = await (
                self.repository
                .create_pending_upload(
                    tenant_id=tenant_id,
                    owner_user_id=(
                        owner_user_id
                    ),
                    entity_type=(
                        validated.object_type
                    ),
                    entity_id=entity_id,
                    file_type=(
                        validated.file_type
                    ),
                    mime_type=(
                        validated.mime_type
                    ),
                    size_bytes=(
                        validated.size_bytes
                    ),
                    storage_provider=(
                        self.storage_provider_name
                    ),
                    storage_path=storage_path,
                )
            )

            upload_url = await (
                self.storage
                .create_signed_upload_url(
                    storage_path=storage_path,
                    mime_type=(
                        validated.mime_type
                    ),
                    expires_in=(
                        FILE_SIGNED_URL_TTL_SECONDS
                    ),
                )
            )

            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

        return FileUploadRequestView(
            id=storage_object.id,
            upload_url=upload_url,
            expires_in=(
                FILE_SIGNED_URL_TTL_SECONDS
            ),
            status=storage_object.status,
            mime_type=validated.mime_type,
            size_bytes=validated.size_bytes,
        )
    async def complete_upload(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        file_id: UUID,
    ) -> FileCompletedView:
        try:
            storage_object = await (
                self.repository
                .get_pending_upload_for_update(
                    tenant_id=tenant_id,
                    owner_user_id=(
                        owner_user_id
                    ),
                    file_id=file_id,
                )
            )

            actual = await (
                self.storage.inspect_object(
                    storage_path=(
                        storage_object.storage_path
                    ),
                )
            )

            if not actual.exists:
                raise FileUploadVerificationError(
                    "Uploaded object was not found."
                )

            expected_mime = str(
                storage_object.mime_type
            ).strip().lower()
            actual_mime = str(
                actual.mime_type or ""
            ).split(";", 1)[0].strip().lower()

            if actual_mime != expected_mime:
                raise FileUploadVerificationError(
                    "Uploaded object MIME type "
                    "does not match reservation."
                )

            if (
                actual.size_bytes is None
                or int(actual.size_bytes)
                != int(storage_object.size_bytes)
            ):
                raise FileUploadVerificationError(
                    "Uploaded object size does "
                    "not match reservation."
                )

            if not isinstance(
                actual.provider_metadata,
                dict,
            ):
                raise FileUploadVerificationError(
                    "Invalid provider metadata."
                )

            completed_at = datetime.now(UTC)
            completed = await (
                self.repository.complete_upload(
                    storage_object=storage_object,
                    provider_metadata=dict(
                        actual.provider_metadata
                    ),
                    completed_at=completed_at,
                )
            )

            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

        return FileCompletedView(
            id=completed.id,
            status=completed.status,
            antivirus_status=(
                completed.antivirus_status
            ),
            mime_type=completed.mime_type,
            size_bytes=int(
                completed.size_bytes
            ),
            completed_at=completed.completed_at,
        )
    async def create_download_url(
        self,
        *,
        tenant_id: UUID,
        owner_user_id: UUID,
        file_id: UUID,
    ) -> FileDownloadView:
        storage_object = await (
            self.repository.get_downloadable_file(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                file_id=file_id,
            )
        )

        download_url = await (
            self.storage
            .create_signed_download_url(
                storage_path=(
                    storage_object.storage_path
                ),
                expires_in=(
                    FILE_SIGNED_URL_TTL_SECONDS
                ),
            )
        )

        return FileDownloadView(
            id=storage_object.id,
            download_url=download_url,
            expires_in=(
                FILE_SIGNED_URL_TTL_SECONDS
            ),
            mime_type=storage_object.mime_type,
            size_bytes=int(
                storage_object.size_bytes
            ),
        )


class FileOrphanCleanupService:
    def __init__(
        self,
        *,
        repository: FileRepository,
        storage: FileStorageProvider,
    ):
        self.repository = repository
        self.storage = storage

    async def cleanup(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        current_time = (
            now or datetime.now(UTC)
        )
        cutoff = current_time - timedelta(
            hours=24
        )

        try:
            storage_objects = await (
                self.repository
                .list_orphan_uploads_for_update(
                    cutoff=cutoff,
                    limit=limit,
                )
            )

            for storage_object in storage_objects:
                await self.storage.delete(
                    storage_path=(
                        storage_object.storage_path
                    ),
                )
                await (
                    self.repository
                    .delete_storage_record(
                        storage_object=(
                            storage_object
                        ),
                    )
                )

            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

        return len(storage_objects)


def build_file_service(
    session,
) -> FileService:
    provider_name = str(
        os.getenv(
            "FILE_STORAGE_PROVIDER",
            "supabase",
        )
    ).strip().lower()

    if provider_name != "supabase":
        raise ValueError(
            "Unsupported file storage provider."
        )

    from services.portfolio_storage import (
        SupabaseFileStorage,
    )

    storage = SupabaseFileStorage(
        bucket=os.getenv(
            "SUPABASE_STORAGE_BUCKET",
            "specialist-portfolio",
        ),
    )

    return FileService(
        repository=FileRepository(session),
        storage=storage,
        storage_provider_name=provider_name,
    )

