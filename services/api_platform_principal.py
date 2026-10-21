"""Shared authenticated Platform user reference, separate from tenant authority.

This principal has no organization, roles or permissions. It cannot authorize
ordinary tenant API operations. A domain operation must separately verify its
approved recipient/link-ownership or tenant-authorization rule.
"""
from dataclasses import dataclass
from uuid import UUID

from services.api_identity import ApiIdentityAccessError


@dataclass(frozen=True)
class ApiPlatformPrincipal:
    user_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id, UUID):
            raise ApiIdentityAccessError('Platform identity is not available.')


class ApiPlatformPrincipalOperationError(Exception):
    pass


class ApiPlatformPrincipalService:
    def __init__(self, *, repository):
        if getattr(repository, 'session', None) is None or not callable(getattr(repository, 'get_active_user_id', None)):
            raise ValueError('Platform principal service requires canonical identity reads.')
        self.repository, self.session = repository, repository.session

    async def require_principal(self, *, user_id):
        if not isinstance(user_id, UUID):
            raise ApiIdentityAccessError('Platform identity is not available.')
        try:
            actual = await self.repository.get_active_user_id(user_id=user_id)
            if not isinstance(actual, UUID) or actual != user_id:
                raise ApiIdentityAccessError('Platform identity is not available.')
            return ApiPlatformPrincipal(actual)
        except ApiIdentityAccessError:
            raise
        except Exception:
            raise ApiPlatformPrincipalOperationError('Platform identity read failed.') from None


def build_api_platform_principal_service(*, session):
    from database.repositories.api_platform_principal import ApiPlatformPrincipalRepository
    return ApiPlatformPrincipalService(repository=ApiPlatformPrincipalRepository(session))
