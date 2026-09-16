import os

from uuid import UUID

from services.construction_access import (
    require_construction_tenant_scope,
)
from services.file_storage import (
    FILE_SIGNED_URL_TTL_SECONDS,
    FileStorageProvider,
)


CONSTRUCTION_SIGNED_DOWNLOAD_URL_TTL_SECONDS = (
    FILE_SIGNED_URL_TTL_SECONDS
)

# Supabase signed upload tokens have a fixed two-hour lifetime.
CONSTRUCTION_SIGNED_UPLOAD_URL_TTL_SECONDS = (
    2 * 60 * 60
)

# Backward-compatible alias for short-lived download URLs.
CONSTRUCTION_SIGNED_URL_TTL_SECONDS = (
    CONSTRUCTION_SIGNED_DOWNLOAD_URL_TTL_SECONDS
)

CONSTRUCTION_PROJECT_STORAGE_SECTIONS = (
    frozenset(
        {
            "measurements",
            "offers",
        }
    )
)


def construction_project_storage_prefix(
    *,
    organization_id: UUID,
    project_id: UUID,
    section: str,
) -> str:
    normalized_section = section.strip().lower()

    if (
        normalized_section
        not in CONSTRUCTION_PROJECT_STORAGE_SECTIONS
    ):
        raise ValueError(
            "Construction project storage "
            "section is not supported."
        )

    return (
        f"organization/{organization_id}"
        f"/construction/projects/{project_id}"
        f"/{normalized_section}/"
    )


def construction_branding_storage_prefix(
    *,
    organization_id: UUID,
) -> str:
    return (
        f"organization/{organization_id}"
        "/construction/branding/"
    )



def _matches_construction_storage_convention(
    *,
    path_parts: list[str],
    tenant_id: UUID,
) -> bool:
    expected_root = [
        "organization",
        str(tenant_id),
        "construction",
    ]

    if path_parts[:3] != expected_root:
        return False

    if (
        len(path_parts) >= 5
        and path_parts[3] == "branding"
    ):
        return True

    if (
        len(path_parts) < 7
        or path_parts[3] != "projects"
        or path_parts[5]
        not in CONSTRUCTION_PROJECT_STORAGE_SECTIONS
    ):
        return False

    try:
        UUID(path_parts[4])
    except (TypeError, ValueError):
        return False

    return True


class ConstructionStoragePathError(ValueError):
    pass


class ConstructionStorageAccessService:
    def __init__(
        self,
        storage: FileStorageProvider,
    ) -> None:
        self.storage = storage

    @staticmethod
    def _require_scoped_storage_path(
        *,
        actor_tenant_id: UUID | None,
        resource_tenant_id: UUID | None,
        storage_path: str,
    ) -> str:
        tenant_id = (
            require_construction_tenant_scope(
                actor_tenant_id=actor_tenant_id,
                resource_tenant_id=(
                    resource_tenant_id
                ),
            )
        )

        expected_prefix = (
            f"organization/{tenant_id}"
            "/construction/"
        )
        if not isinstance(storage_path, str):
            raise ConstructionStoragePathError(
                "Construction storage path "
                "is not available."
            )

        path_parts = storage_path.split(
            "/"
        )

        if (
            not storage_path.startswith(
                expected_prefix
            )
            or storage_path
            == expected_prefix
            or any(
                part in {"", ".", ".."}
                for part in path_parts
            )
            or "\\" in storage_path
            or not (
                _matches_construction_storage_convention(
                    path_parts=path_parts,
                    tenant_id=tenant_id,
                )
            )
        ):
            raise ConstructionStoragePathError(
                "Construction storage path "
                "is not available."
            )

        return storage_path

    async def create_signed_upload_url(
        self,
        *,
        actor_tenant_id: UUID | None,
        resource_tenant_id: UUID | None,
        storage_path: str,
        mime_type: str,
    ) -> str:
        scoped_path = (
            self._require_scoped_storage_path(
                actor_tenant_id=actor_tenant_id,
                resource_tenant_id=(
                    resource_tenant_id
                ),
                storage_path=storage_path,
            )
        )

        return await (
            self.storage
            .create_signed_upload_url(
                storage_path=scoped_path,
                mime_type=mime_type,
                expires_in=(
                    CONSTRUCTION_SIGNED_UPLOAD_URL_TTL_SECONDS
                ),
            )
        )

    async def create_signed_download_url(
        self,
        *,
        actor_tenant_id: UUID | None,
        resource_tenant_id: UUID | None,
        storage_path: str,
    ) -> str:
        scoped_path = (
            self._require_scoped_storage_path(
                actor_tenant_id=actor_tenant_id,
                resource_tenant_id=(
                    resource_tenant_id
                ),
                storage_path=storage_path,
            )
        )

        return await (
            self.storage
            .create_signed_download_url(
                storage_path=scoped_path,
                expires_in=(
                    CONSTRUCTION_SIGNED_DOWNLOAD_URL_TTL_SECONDS
                ),
            )
        )



def build_construction_storage_access_service(
) -> ConstructionStorageAccessService:
    provider_name = str(
        os.getenv(
            "FILE_STORAGE_PROVIDER",
            "supabase",
        )
    ).strip().lower()

    if provider_name != "supabase":
        raise ValueError(
            "Unsupported Construction storage "
            "provider."
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

    return ConstructionStorageAccessService(
        storage
    )
