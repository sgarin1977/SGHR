"""Approved own-link tenant resolution for a verified Platform principal.

Only the later unlink service may authorize a mutation. It must recheck live
ownership and expected_row_version in its own transaction. This routing result
grants no Construction data access. Caller owns the read-only transaction.
"""
from uuid import UUID

from database.repositories.construction_client_unlink_context import (
    ConstructionClientOwnLinkContext, ConstructionClientOwnLinkContextRepository,
)
from services.api_identity import ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal, build_api_platform_principal_service
from services.construction_client_unlink import ConstructionClientUnlinkDeniedError
from services.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError


class ConstructionClientOwnLinkContextService:
    def __init__(self, *, repository, principal_service):
        session=getattr(repository,'session',None)
        if (session is None or not callable(getattr(repository,'find_own_link',None))
                or not callable(getattr(session,'in_transaction',None))
                or getattr(principal_service,'session',None) is None or principal_service.session is session
                or not callable(getattr(principal_service,'require_principal',None))):
            raise ValueError('Own-link context requires Construction reads and a separate canonical Platform identity service.')
        self.repository,self.session,self.identity=repository,session,principal_service

    async def _active(self, principal):
        current=await self.identity.require_principal(user_id=principal.user_id)
        if not isinstance(current,ApiPlatformPrincipal) or current.user_id!=principal.user_id:
            raise ApiIdentityAccessError('Authenticated Platform identity is not available.')

    async def resolve_own_link(self, *, principal, client_id):
        try:
            if not isinstance(principal,ApiPlatformPrincipal) or not isinstance(principal.user_id,UUID):
                raise ApiIdentityAccessError('Authenticated Platform identity is not available.')
            if not isinstance(client_id,UUID):
                raise ConstructionClientValidationError('Own-link client identifier is invalid.')
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Own-link context requires a caller read-only transaction.')
            await self._active(principal)
            context=await self.repository.find_own_link(user_id=principal.user_id,client_id=client_id)
            if context is None:
                raise ConstructionClientUnlinkDeniedError()
            if (not isinstance(context,ConstructionClientOwnLinkContext)
                    or context.client_id!=client_id or context.user_id!=principal.user_id):
                raise ConstructionClientOperationError('Own-link context receipt is invalid.')
            await self._active(principal)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Own-link context transaction ended unexpectedly.')
            return context
        except (ApiIdentityAccessError,ConstructionClientValidationError,ConstructionClientOperationError,ConstructionClientUnlinkDeniedError):
            raise
        except Exception:
            raise ConstructionClientOperationError('Own-link context lookup failed.') from None


def build_construction_client_own_link_context_service(*, session, platform_session):
    return ConstructionClientOwnLinkContextService(repository=ConstructionClientOwnLinkContextRepository(session),
        principal_service=build_api_platform_principal_service(session=platform_session))
