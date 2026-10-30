"""Internal project catalog read; no public route is registered here."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.construction.construction_project_catalog import (
    ConstructionProjectCatalogCursor,
)
from services.construction.construction_client_projects_read import ClientProjectReadResponse
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)
from services.construction.construction_projects import (
    ConstructionProjectOperationError, ConstructionProjectValidationError,
)

READ_PERMISSION = "construction.projects.read"


class ConstructionProjectCatalogReadError(ConstructionProjectOperationError):
    pass


@dataclass(frozen=True)
class ConstructionProjectCatalogReadCursor:
    tenant_id: UUID
    user_id: UUID
    updated_at: datetime
    project_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id, UUID):
            raise ConstructionProjectValidationError("Project catalog actor must be a UUID.")
        try:
            checked = ConstructionProjectCatalogCursor(
                self.tenant_id, self.updated_at, self.project_id,
            )
        except (ValueError, TypeError, OverflowError):
            raise ConstructionProjectValidationError("Invalid project catalog cursor.") from None
        object.__setattr__(self, "updated_at", checked.updated_at)


@dataclass(frozen=True)
class ConstructionProjectCatalogReadPage:
    items: tuple
    next_cursor: ConstructionProjectCatalogReadCursor | None


class ConstructionProjectCatalogReadService:
    """Fresh membership and grants; SQL scope filtering precedes pagination.

    Caller owns the Construction/RLS transaction and Suite/Core boundary.
    The existing public project projection is reused without client-card access.
    """

    def __init__(
        self, *, session, repository, access_repository, now_provider=None,
    ):
        protocols = (
            (repository, ("list_projects",)),
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
            raise ValueError("Project catalog requires shared-session repositories.")
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
            raise ConstructionProjectCatalogReadError("Project catalog clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _scope(actor, context):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
            or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id
        ):
            raise ConstructionPermissionDeniedError("Project catalog access is unavailable.")
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Invalid project catalog scope.")
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)

        try:
            require_construction_access(
                actor=actor, access_context=context, permission_code=READ_PERMISSION,
            )
            return None
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            pass

        allowed = set()
        for grant in context.grants:
            if grant.scope_type != "project":
                continue
            try:
                require_construction_access(
                    actor=actor, access_context=context,
                    permission_code=READ_PERMISSION, project_id=grant.scope_id,
                )
            except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                continue
            allowed.add(grant.scope_id)
        if not allowed:
            raise ConstructionPermissionDeniedError("No readable project scope.")
        return tuple(sorted(allowed))

    async def _fresh_context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Project catalog membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        self._scope(actor, context)
        return context

    @staticmethod
    def _project(row, actor, context):
        if (
            row is None or not isinstance(getattr(row, "id", None), UUID)
            or getattr(row, "tenant_id", None) != actor.tenant_id
            or getattr(row, "deleted_at", "missing") is not None
        ):
            raise ConstructionProjectCatalogReadError("Project catalog row context differs.")
        require_construction_access(
            actor=actor, access_context=context,
            permission_code=READ_PERMISSION, project_id=row.id,
        )
        return ClientProjectReadResponse.model_validate({
            field: getattr(row, field)
            for field in ClientProjectReadResponse.model_fields
        })

    async def list_projects(self, *, actor, access_context, limit=20, after=None):
        try:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ConstructionProjectValidationError("Project catalog limit must be 1–100.")
            self._scope(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionProjectCatalogReadError("Active caller transaction required.")

            position = None
            if after is not None:
                if not isinstance(after, ConstructionProjectCatalogReadCursor):
                    raise ConstructionProjectValidationError("Invalid project catalog read cursor.")
                after = ConstructionProjectCatalogReadCursor(
                    after.tenant_id, after.user_id, after.updated_at, after.project_id,
                )
                if after.tenant_id != actor.tenant_id or after.user_id != actor.user_id:
                    raise ConstructionProjectValidationError("Project catalog cursor context differs.")
                position = ConstructionProjectCatalogCursor(
                    after.tenant_id, after.updated_at, after.project_id,
                )

            with self.session.no_autoflush:
                context = await self._fresh_context(actor)
                allowed = self._scope(actor, context)
                rows = await self.repository.list_projects(
                    tenant_id=actor.tenant_id, allowed_project_ids=allowed,
                    limit=limit, after=position,
                )
                if not isinstance(rows, list) or len(rows) > limit + 1:
                    raise ConstructionProjectCatalogReadError("Invalid project catalog page.")
                models = [self._project(row, actor, context) for row in rows]
                previous = (
                    (position.updated_at, position.project_id)
                    if position is not None else None
                )
                seen = set()
                for model in models:
                    key = (model.updated_at, model.id)
                    if model.id in seen or (previous is not None and key >= previous):
                        raise ConstructionProjectCatalogReadError("Project catalog order differs.")
                    seen.add(model.id)
                    previous = key

                current = await self._fresh_context(actor)
                for model in models:
                    require_construction_access(
                        actor=actor, access_context=current,
                        permission_code=READ_PERMISSION, project_id=model.id,
                    )
                cursor = None
                if len(models) > limit:
                    last = models[limit - 1]
                    cursor = ConstructionProjectCatalogReadCursor(
                        actor.tenant_id, actor.user_id, last.updated_at, last.id,
                    )
                return ConstructionProjectCatalogReadPage(
                    tuple(model.model_dump(mode="json") for model in models[:limit]),
                    cursor,
                )
        except (
            ConstructionProjectValidationError, ConstructionProjectCatalogReadError,
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        ):
            raise
        except Exception:
            raise ConstructionProjectCatalogReadError("Project catalog read failed.") from None


def build_construction_project_catalog_read_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_project_catalog import (
        ConstructionProjectCatalogRepository,
    )
    return ConstructionProjectCatalogReadService(
        session=session, repository=ConstructionProjectCatalogRepository(session),
        access_repository=ConstructionAccessRepository(session),
        now_provider=now_provider,
    )
