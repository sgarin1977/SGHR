import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from services.file_storage import (
    FileStorageObjectMetadata,
)


_portfolio_storage_http_client: (
    httpx.AsyncClient | None
) = None


def _get_portfolio_storage_http_client(
) -> httpx.AsyncClient:
    global _portfolio_storage_http_client

    if (
        _portfolio_storage_http_client
        is None
        or (
            _portfolio_storage_http_client
            .is_closed
        )
    ):
        _portfolio_storage_http_client = (
            httpx.AsyncClient()
        )

    return _portfolio_storage_http_client


async def close_portfolio_storage_http_client(
) -> None:
    global _portfolio_storage_http_client

    client = _portfolio_storage_http_client
    _portfolio_storage_http_client = None

    if (
        client is not None
        and not client.is_closed
    ):
        await client.aclose()


PORTFOLIO_BUCKET = os.getenv(
    "SUPABASE_STORAGE_BUCKET",
    "specialist-portfolio",
)

PHOTO_MAX_SIZE = 10 * 1024 * 1024
PDF_MAX_SIZE = 20 * 1024 * 1024

ALLOWED_FILES = {
    ".jpg": ("photo", "image/jpeg"),
    ".jpeg": ("photo", "image/jpeg"),
    ".png": ("photo", "image/png"),
    ".webp": ("photo", "image/webp"),
    ".pdf": ("pdf", "application/pdf"),
}


class PortfolioStorageError(Exception):
    pass


class PortfolioFileValidationError(PortfolioStorageError):
    pass


@dataclass(frozen=True)
class ValidatedPortfolioFile:
    file_type: str
    mime_type: str
    size_bytes: int
    extension: str


def validate_portfolio_file_metadata(
    *,
    filename: str,
    mime_type: str | None,
    size_bytes: int,
) -> ValidatedPortfolioFile:
    extension = Path(
        filename or ""
    ).suffix.lower()

    if extension not in ALLOWED_FILES:
        raise PortfolioFileValidationError(
            "Only JPG, JPEG, PNG, WEBP and "
            "PDF files are allowed."
        )

    file_type, expected_mime = (
        ALLOWED_FILES[extension]
    )
    normalized_mime = (
        (mime_type or "")
        .lower()
        .split(";", 1)[0]
        .strip()
    )

    if normalized_mime != expected_mime:
        raise PortfolioFileValidationError(
            "File MIME type does not match "
            "its extension."
        )

    normalized_size = int(size_bytes)

    if normalized_size <= 0:
        raise PortfolioFileValidationError(
            "File is empty."
        )

    max_size = (
        PHOTO_MAX_SIZE
        if file_type == "photo"
        else PDF_MAX_SIZE
    )

    if normalized_size > max_size:
        limit_mb = max_size // (
            1024 * 1024
        )
        raise PortfolioFileValidationError(
            f"File exceeds the "
            f"{limit_mb} MB limit."
        )

    return ValidatedPortfolioFile(
        file_type=file_type,
        mime_type=expected_mime,
        size_bytes=normalized_size,
        extension=extension,
    )


def validate_portfolio_file(
    *,
    filename: str,
    mime_type: str | None,
    content: bytes,
) -> ValidatedPortfolioFile:
    validated = (
        validate_portfolio_file_metadata(
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
        )
    )
    extension = validated.extension

    if extension in {".jpg", ".jpeg"}:
        valid_signature = content.startswith(
            b"\xff\xd8\xff"
        )
    elif extension == ".png":
        valid_signature = content.startswith(
            b"\x89PNG\r\n\x1a\n"
        )
    elif extension == ".webp":
        valid_signature = (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    elif extension == ".pdf":
        valid_signature = content.startswith(
            b"%PDF-"
        )
    else:
        valid_signature = False

    if not valid_signature:
        raise PortfolioFileValidationError(
            "File content does not match "
            "its declared type."
        )

    return validated

class SupabasePortfolioStorage:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_role_key: str | None = None,
        bucket: str | None = None,
        timeout_seconds: float = 30,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = (
            base_url or os.getenv("SUPABASE_URL") or ""
        ).rstrip("/")
        self.service_role_key = (
            service_role_key
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or ""
        )
        self.bucket = bucket or PORTFOLIO_BUCKET
        self.timeout_seconds = timeout_seconds
        self.client = client

        if not self.base_url:
            raise PortfolioStorageError("SUPABASE_URL is missing.")

        if not self.service_role_key:
            raise PortfolioStorageError(
                "SUPABASE_SERVICE_ROLE_KEY is missing."
            )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }

    def object_url(self, storage_path: str) -> str:
        encoded_bucket = quote(self.bucket, safe="")
        encoded_path = quote(storage_path, safe="/")
        return (
            f"{self.base_url}/storage/v1/object/"
            f"{encoded_bucket}/{encoded_path}"
        )

    async def upload(
        self,
        *,
        storage_path: str,
        content: bytes,
        mime_type: str,
    ) -> None:
        headers = {
            **self.headers,
            "Content-Type": mime_type,
            "x-upsert": "false",
        }

        try:
            client = (
                self.client
                or (
                    _get_portfolio_storage_http_client()
                )
            )
            response = await client.post(
                self.object_url(storage_path),
                headers=headers,
                content=content,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PortfolioStorageError(
                f"Supabase upload failed: {exc}"
            ) from exc

    async def create_signed_upload_url(
        self,
        *,
        storage_path: str,
        mime_type: str,
        expires_in: int = 900,
    ) -> str:
        encoded_bucket = quote(
            self.bucket,
            safe="",
        )
        encoded_path = quote(
            storage_path,
            safe="/",
        )

        try:
            client = (
                self.client
                or _get_portfolio_storage_http_client()
            )
            response = await client.post(
                (
                    f"{self.base_url}/storage/v1/"
                    f"object/upload/sign/"
                    f"{encoded_bucket}/{encoded_path}"
                ),
                headers={
                    **self.headers,
                    "Content-Type": mime_type,
                },
                json={},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise PortfolioStorageError(
                "Signed upload URL creation "
                f"failed: {exc}"
            ) from exc

        signed_url = (
            payload.get("signedURL")
            or payload.get("signedUrl")
            or payload.get("url")
        )
        if not signed_url:
            raise PortfolioStorageError(
                "Supabase returned no signed "
                "upload URL."
            )

        if signed_url.startswith(
            "/storage/v1/"
        ):
            return (
                f"{self.base_url}{signed_url}"
            )

        if signed_url.startswith("/"):
            return (
                f"{self.base_url}/storage/v1"
                f"{signed_url}"
            )

        return str(signed_url)

    async def create_signed_url(
        self,
        *,
        storage_path: str,
        expires_in: int = 900,
    ) -> str:
        encoded_bucket = quote(self.bucket, safe="")
        encoded_path = quote(storage_path, safe="/")

        try:
            client = (
                self.client
                or (
                    _get_portfolio_storage_http_client()
                )
            )
            response = await client.post(
                (
                    f"{self.base_url}/storage/v1/object/sign/"
                    f"{encoded_bucket}/{encoded_path}"
                ),
                headers=self.headers,
                json={"expiresIn": expires_in},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise PortfolioStorageError(
                f"Signed URL creation failed: {exc}"
            ) from exc

        signed_url = payload.get("signedURL") or payload.get("signedUrl")
        if not signed_url:
            raise PortfolioStorageError(
                "Supabase returned no signed URL."
            )

        if signed_url.startswith("/storage/v1/"):
            return f"{self.base_url}{signed_url}"

        if signed_url.startswith("/"):
            return f"{self.base_url}/storage/v1{signed_url}"

        return str(signed_url)

    async def create_signed_download_url(
        self,
        *,
        storage_path: str,
        expires_in: int = 900,
    ) -> str:
        return await self.create_signed_url(
            storage_path=storage_path,
            expires_in=expires_in,
        )

    async def inspect_object(
        self,
        *,
        storage_path: str,
    ) -> FileStorageObjectMetadata:
        try:
            client = (
                self.client
                or _get_portfolio_storage_http_client()
            )
            response = await client.head(
                self.object_url(storage_path),
                headers=self.headers,
                timeout=self.timeout_seconds,
            )

            if int(response.status_code) == 404:
                return FileStorageObjectMetadata(
                    exists=False,
                    size_bytes=None,
                    mime_type=None,
                    provider_metadata={},
                )

            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PortfolioStorageError(
                f"Object inspection failed: {exc}"
            ) from exc

        raw_size = response.headers.get(
            "content-length"
        )
        try:
            size_bytes = (
                int(raw_size)
                if raw_size is not None
                else None
            )
        except (TypeError, ValueError):
            size_bytes = None

        raw_mime = response.headers.get(
            "content-type"
        )
        mime_type = (
            str(raw_mime)
            .split(";", 1)[0]
            .strip()
            .lower()
            if raw_mime
            else None
        )

        provider_metadata = {}
        etag = response.headers.get("etag")
        last_modified = response.headers.get(
            "last-modified"
        )

        if etag:
            provider_metadata["etag"] = str(
                etag
            )
        if last_modified:
            provider_metadata[
                "last_modified"
            ] = str(last_modified)

        return FileStorageObjectMetadata(
            exists=True,
            size_bytes=size_bytes,
            mime_type=mime_type,
            provider_metadata=provider_metadata,
        )

    async def delete(self, *, storage_path: str) -> None:
        encoded_bucket = quote(self.bucket, safe="")

        try:
            client = (
                self.client
                or (
                    _get_portfolio_storage_http_client()
                )
            )
            response = await client.request(
                "DELETE",
                (
                    f"{self.base_url}/storage/v1/object/"
                    f"{encoded_bucket}"
                ),
                headers=self.headers,
                json={"prefixes": [storage_path]},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PortfolioStorageError(
                f"Supabase delete failed: {exc}"
            ) from exc

# Provider-neutral name for the shared storage adapter.
SupabaseFileStorage = SupabasePortfolioStorage
