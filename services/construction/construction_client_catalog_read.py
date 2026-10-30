"""Client catalog under the existing tenant-wide CRM read boundary."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.construction.construction_client_catalog import ConstructionClientCatalogCursor
from services.construction.construction_client_card_read import (
    ClientCardClientResponse, ConstructionClientCardReadService,
)
from services.construction.construction_clients import (
    ConstructionClientValidationError, ConstructionClientOperationError,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ConstructionClientCatalogReadError(ConstructionClientOperationError):
    pass


@dataclass(frozen=True)
class ConstructionClientCatalogReadCursor:
    tenant_id: UUID
    user_id: UUID
    updated_at: datetime
    client_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id, UUID):
            raise ConstructionClientValidationError("Client catalog actor must be a UUID.")
        try:
            checked = ConstructionClientCatalogCursor(
                self.tenant_id, self.updated_at, self.client_id,
            )
        except (ValueError, TypeError, OverflowError):
            raise ConstructionClientValidationError("Invalid client catalog cursor.") from None
        object.__setattr__(self, "updated_at", checked.updated_at)


@dataclass(frozen=True)
class ConstructionClientCatalogReadPage:
    items: tuple
    next_cursor: ConstructionClientCatalogReadCursor | None


class ConstructionClientCatalogReadService:
    """Fresh tenant membership/grants; no project or identity-link shortcut."""

    def __init__(self, *, session, repository, access_repository, now_provider=None):
        protocols = (
            (repository, ("list_clients",)),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
        )
        if (
            session is None or not callable(getattr(session, "in_transaction", None))
            or not hasattr(session, "no_autoflush")
            or any(
                getattr(repo, "session", None) is not session
                or any(not callable(getattr(repo, name, None)) for name in methods)
                for repo, methods in protocols
            )
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Client catalog requires shared-session repositories.")
        self.session, self.repository = session, repository
        self.access_repository = access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now,
        )

    def _now(self):
        value = self.now_provider()
        if (
            not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ConstructionClientCatalogReadError("Client catalog clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        ConstructionClientCardReadService._authorize(actor, context)

    async def _fresh_context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Client catalog membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context)
        return context

    @staticmethod
    def _client(row, actor):
        if (
            row is None or not isinstance(getattr(row, "id", None), UUID)
            or getattr(row, "tenant_id", None) != actor.tenant_id
            or getattr(row, "deleted_at", "missing") is not None
        ):
            raise ConstructionClientCatalogReadError("Client catalog row context differs.")
        return ClientCardClientResponse.model_validate({
            field: getattr(row, field)
            for field in ClientCardClientResponse.model_fields
        })

    async def list_clients(self, *, actor, access_context, limit=20, after=None):
        try:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ConstructionClientValidationError("Client catalog limit must be 1–100.")
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientCatalogReadError("Active caller transaction required.")
            position = None
            if after is not None:
                if not isinstance(after, ConstructionClientCatalogReadCursor):
                    raise ConstructionClientValidationError("Invalid client catalog read cursor.")
                after = ConstructionClientCatalogReadCursor(
                    after.tenant_id, after.user_id, after.updated_at, after.client_id,
                )
                if after.tenant_id != actor.tenant_id or after.user_id != actor.user_id:
                    raise ConstructionClientValidationError("Client catalog cursor context differs.")
                position = ConstructionClientCatalogCursor(
                    after.tenant_id, after.updated_at, after.client_id,
                )
            with self.session.no_autoflush:
                await self._fresh_context(actor)
                rows = await self.repository.list_clients(
                    tenant_id=actor.tenant_id, limit=limit, after=position,
                )
                if not isinstance(rows, list) or len(rows) > limit + 1:
                    raise ConstructionClientCatalogReadError("Invalid client catalog page.")
                models = [self._client(row, actor) for row in rows]
                previous = (
                    (position.updated_at, position.client_id)
                    if position is not None else None
                )
                seen = set()
                for model in models:
                    key = model.updated_at, model.id
                    if model.id in seen or (previous is not None and key >= previous):
                        raise ConstructionClientCatalogReadError("Client catalog order differs.")
                    seen.add(model.id)
                    previous = key
                await self._fresh_context(actor)
                cursor = None
                if len(models) > limit:
                    last = models[limit - 1]
                    cursor = ConstructionClientCatalogReadCursor(
                        actor.tenant_id, actor.user_id, last.updated_at, last.id,
                    )
                return ConstructionClientCatalogReadPage(
                    tuple(model.model_dump(mode="json") for model in models[:limit]),
                    cursor,
                )
        except (
            ConstructionClientValidationError, ConstructionClientCatalogReadError,
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        ):
            raise
        except Exception:
            raise ConstructionClientCatalogReadError("Client catalog read failed.") from None


def build_construction_client_catalog_read_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_client_catalog import ConstructionClientCatalogRepository
    return ConstructionClientCatalogReadService(
        session=session, repository=ConstructionClientCatalogRepository(session),
        access_repository=ConstructionAccessRepository(session), now_provider=now_provider,
    )
