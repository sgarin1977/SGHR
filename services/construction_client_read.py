from uuid import UUID

from services.construction_clients import (
    ConstructionClientNotFoundError,
    ConstructionClientOperationError,
    ConstructionClientValidationError,
)
from services.construction_permissions import (
    ConstructionAccessContext,
    ConstructionPermissionDeniedError,
    require_construction_access,
)


class ConstructionClientReadService:
    """Read an undeleted client through the existing CRM permission boundary."""

    def __init__(self, *, repository):
        if not callable(getattr(repository, "get_active_client", None)):
            raise ValueError("Client read repository is required.")
        self.repository = repository

    async def get_client(self, *, actor, access_context, client_id):
        if not isinstance(client_id, UUID):
            raise ConstructionClientValidationError("Client identifier must be a UUID.")
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(access_context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Construction client read is not available.")
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code="construction.projects.read",
        )
        try:
            client = await self.repository.get_active_client(
                tenant_id=actor.tenant_id, client_id=client_id,
            )
            if (
                client is None
                or getattr(client, "id", None) != client_id
                or getattr(client, "tenant_id", None) != actor.tenant_id
                or getattr(client, "deleted_at", object()) is not None
            ):
                raise ConstructionClientNotFoundError("Construction client is not available.")
            return client
        except ConstructionClientNotFoundError:
            raise
        except Exception as exc:
            raise ConstructionClientOperationError("Construction client read failed.") from exc


def build_construction_client_read_service(session):
    from database.repositories.construction import ConstructionClientRepository

    return ConstructionClientReadService(repository=ConstructionClientRepository(session))
