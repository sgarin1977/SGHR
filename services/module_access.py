from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.white_label import (
    WhiteLabelRepository,
)


class ModuleDisabledError(Exception):
    pass


class TenantModuleRepositoryProtocol(
    Protocol
):
    async def is_module_enabled(
        self,
        *,
        tenant_id: UUID,
        module_code: str,
    ) -> bool:
        ...


class TenantModuleAccessService:
    def __init__(
        self,
        *,
        repository: (
            TenantModuleRepositoryProtocol
        ),
    ) -> None:
        self.repository = repository

    async def require_enabled(
        self,
        *,
        tenant_id: UUID,
        module_code: str,
    ) -> None:
        enabled = await (
            self.repository.is_module_enabled(
                tenant_id=tenant_id,
                module_code=module_code,
            )
        )

        if not enabled:
            raise ModuleDisabledError(
                "Module is disabled."
            )


def build_tenant_module_access_service(
    session: AsyncSession,
) -> TenantModuleAccessService:
    return TenantModuleAccessService(
        repository=WhiteLabelRepository(
            session
        ),
    )


class SuiteDisabledError(Exception):
    pass


class TenantSuiteRepositoryProtocol(
    Protocol
):
    async def is_suite_enabled(
        self,
        *,
        tenant_id: UUID,
        suite_code: str,
    ) -> bool:
        ...


class TenantSuiteAccessService:
    def __init__(
        self,
        *,
        repository: (
            TenantSuiteRepositoryProtocol
        ),
    ) -> None:
        self.repository = repository

    async def require_enabled(
        self,
        *,
        tenant_id: UUID,
        suite_code: str,
    ) -> None:
        enabled = await (
            self.repository.is_suite_enabled(
                tenant_id=tenant_id,
                suite_code=suite_code,
            )
        )

        if not enabled:
            raise SuiteDisabledError(
                "Suite is disabled."
            )


def build_tenant_suite_access_service(
    session: AsyncSession,
) -> TenantSuiteAccessService:
    return TenantSuiteAccessService(
        repository=WhiteLabelRepository(
            session
        ),
    )

