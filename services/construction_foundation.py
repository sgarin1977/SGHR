from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from services.api_identity import ApiActorContext


class ConstructionOrganizationUnavailableError(
    Exception
):
    pass


class ConstructionRowVersionConflictError(
    Exception
):
    status_code = 409
    code = "ROW_VERSION_CONFLICT"

    def __init__(self) -> None:
        super().__init__(
            "Construction resource row version "
            "does not match."
        )


def require_expected_row_version(
    *,
    actual_row_version: int,
    expected_row_version: int,
) -> None:
    if (
        isinstance(expected_row_version, bool)
        or not isinstance(
            expected_row_version,
            int,
        )
        or expected_row_version <= 0
        or actual_row_version
        != expected_row_version
    ):
        raise ConstructionRowVersionConflictError()


CONSTRUCTION_TERMINAL_PROJECT_STATUSES = (
    frozenset(
        {
            "completed",
            "cancelled",
        }
    )
)


class ConstructionProjectTerminalError(
    Exception
):
    pass


def require_construction_project_mutable(
    *,
    project_status: str,
) -> None:
    if (
        project_status
        in CONSTRUCTION_TERMINAL_PROJECT_STATUSES
    ):
        raise ConstructionProjectTerminalError(
            "Construction project is terminal."
        )


@dataclass(frozen=True)
class ConstructionOrganizationContext:
    tenant_id: UUID
    name: str
    slug: str
    default_language: str
    default_currency: str


class ConstructionOrganizationRepositoryProtocol(
    Protocol
):
    async def get_active_organization(
        self,
        *,
        tenant_id: UUID,
    ):
        ...


class ConstructionOrganizationService:
    def __init__(
        self,
        *,
        repository: (
            ConstructionOrganizationRepositoryProtocol
        ),
    ) -> None:
        self.repository = repository

    async def require_current(
        self,
        *,
        actor: ApiActorContext,
    ) -> ConstructionOrganizationContext:
        organization = await (
            self.repository.get_active_organization(
                tenant_id=actor.tenant_id,
            )
        )

        if (
            organization is None
            or organization.id
            != actor.tenant_id
            or organization.status
            != "active"
        ):
            raise (
                ConstructionOrganizationUnavailableError(
                    "Construction organization "
                    "is not available."
                )
            )

        return ConstructionOrganizationContext(
            tenant_id=organization.id,
            name=organization.name,
            slug=organization.slug,
            default_language=(
                organization.default_language
            ),
            default_currency=(
                organization.default_currency
            ),
        )
