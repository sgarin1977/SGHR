from dataclasses import dataclass
from typing import Protocol, runtime_checkable


FILE_SIGNED_URL_TTL_SECONDS = 15 * 60


class FileStorageError(Exception):
    pass


@dataclass(frozen=True)
class FileStorageObjectMetadata:
    exists: bool
    size_bytes: int | None
    mime_type: str | None
    provider_metadata: dict[str, object]


@runtime_checkable
class FileStorageProvider(Protocol):
    async def create_signed_upload_url(
        self,
        *,
        storage_path: str,
        mime_type: str,
        expires_in: int,
    ) -> str:
        ...

    async def create_signed_download_url(
        self,
        *,
        storage_path: str,
        expires_in: int,
    ) -> str:
        ...

    async def inspect_object(
        self,
        *,
        storage_path: str,
    ) -> FileStorageObjectMetadata:
        ...

    async def delete(
        self,
        *,
        storage_path: str,
    ) -> None:
        ...
