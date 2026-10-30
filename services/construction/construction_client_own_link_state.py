"""Only ownership/version metadata, through the approved own-unlink RLS lookup."""
from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import select
from database.models import ConstructionClient
from database.repositories.construction.construction_client_unlink_context import ConstructionClientOwnLinkContextRepository
from services.construction.construction_client_unlink_context import build_construction_client_own_link_context_service
from services.construction.construction_client_unlink import ConstructionClientUnlinkDeniedError
from services.api_identity import ApiIdentityAccessError

@dataclass(frozen=True)
class ConstructionClientOwnLinkState:
    client_id: UUID
    linked_user_id: UUID
    row_version: int

class ConstructionClientOwnLinkStateRepository(ConstructionClientOwnLinkContextRepository):
    async def get_owned_version(self, *, context):
        # First verify the exact dedicated RLS context, using the unchanged
        # three-column lookup. Never use ordinary tenant lookup as authority.
        current = await self.find_own_link(user_id=context.user_id, client_id=context.client_id)
        if current != context:
            return None
        statement = select(ConstructionClient.row_version).where(
            ConstructionClient.id == context.client_id, ConstructionClient.tenant_id == context.tenant_id,
            ConstructionClient.linked_user_id == context.user_id, ConstructionClient.deleted_at.is_(None)
        ).execution_options(autoflush=False)
        with self.session.no_autoflush:
            return (await self.session.execute(statement)).scalar_one_or_none()

class ConstructionClientOwnLinkStateService:
    def __init__(self, *, context_service, repository):
        if context_service.session is not repository.session:
            raise ValueError('Own-link state requires the dedicated shared read-only session.')
        self.context_service, self.repository = context_service, repository

    async def get_state(self, *, principal, client_id):
        context = await self.context_service.resolve_own_link(principal=principal, client_id=client_id)
        version = await self.repository.get_owned_version(context=context)
        if version is None:
            raise ConstructionClientUnlinkDeniedError()
        if type(version) is not int or not 1 <= version <= 2**63-1:
            raise ValueError('Own-link state version is invalid.')
        fresh = await self.context_service.identity.require_principal(user_id=principal.user_id)
        if fresh != principal:
            raise ApiIdentityAccessError('Platform identity is not available.')
        return ConstructionClientOwnLinkState(context.client_id, principal.user_id, version)

def build_construction_client_own_link_state_service(*, session, platform_session):
    return ConstructionClientOwnLinkStateService(
        context_service=build_construction_client_own_link_context_service(session=session, platform_session=platform_session),
        repository=ConstructionClientOwnLinkStateRepository(session))
