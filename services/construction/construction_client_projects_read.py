from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from database.repositories.construction.construction_client_projects import ConstructionClientProjectsCursor
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError, ConstructionClientOperationError
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


CLIENT_PROJECTS_READ_PERMISSION="construction.projects.read"


class ConstructionClientProjectsReadOperationError(ConstructionClientOperationError):
    pass


class ClientProjectReadResponse(BaseModel):
    """Same project fields as the existing project GET; no arbitrary ORM data."""
    model_config=ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    client_id: UUID | None
    name: StrictStr
    status: Literal["draft", "planning", "active", "completed", "cancelled"]
    responsible_user_id: UUID | None
    comment: StrictStr | None
    address_raw: StrictStr
    address_formatted: StrictStr | None
    country_code: StrictStr | None
    region: StrictStr | None
    city: StrictStr | None
    postal_code: StrictStr | None
    latitude: Decimal | None = Field(allow_inf_nan=False)
    longitude: Decimal | None = Field(allow_inf_nan=False)
    address_provider: StrictStr | None
    provider_place_id: StrictStr | None
    address_verification_status: Literal["pending", "verified", "failed"]
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls,value):
        return value.astimezone(timezone.utc)


PUBLIC_CLIENT_PROJECT_FIELDS=frozenset(ClientProjectReadResponse.model_fields)


@dataclass(frozen=True)
class ConstructionClientProjectsReadCursor:
    tenant_id: UUID
    user_id: UUID
    client_id: UUID
    updated_at: datetime
    project_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id,UUID):
            raise ConstructionClientValidationError("Client projects cursor requires an actor UUID.")
        try:
            checked=ConstructionClientProjectsCursor(self.tenant_id,self.client_id,self.updated_at,self.project_id)
        except ValueError:
            raise ConstructionClientValidationError("Client projects cursor is invalid.") from None
        object.__setattr__(self,"updated_at",checked.updated_at)


@dataclass(frozen=True)
class ConstructionClientProjectsReadPage:
    items: tuple
    next_cursor: ConstructionClientProjectsReadCursor | None


class ConstructionClientProjectsReadService:
    """Read projects after checking current tenant CRM access and client availability."""

    def __init__(self, *, session, repository, client_repository, access_repository, now_provider=None):
        protocols=(
            (repository,("list_projects_for_client",)),(client_repository,("get_active_client",)),
            (access_repository,("has_active_platform_membership","list_active_grants")),
        )
        if (
            session is None or not callable(getattr(session,"in_transaction",None)) or not hasattr(session,"no_autoflush")
            or any(getattr(repo,"session",None) is not session
                or any(not callable(getattr(repo,name,None)) for name in methods) for repo,methods in protocols)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Client projects read requires shared-session repositories.")
        self.session,self.repository=session,repository
        self.client_repository,self.access_repository=client_repository,access_repository
        self.now_provider=now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver=ConstructionAccessContextResolver(repository=access_repository,now_provider=self._now)

    def _now(self):
        value=self.now_provider()
        if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientProjectsReadOperationError("Client projects clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor,context):
        if (
            getattr(actor,"status",None)!="active" or not isinstance(getattr(actor,"user_id",None),UUID)
            or not isinstance(getattr(actor,"tenant_id",None),UUID) or not isinstance(context,ConstructionAccessContext)
            or context.user_id!=actor.user_id or context.tenant_id!=actor.tenant_id
        ):
            raise ConstructionPermissionDeniedError("Client projects access is not available.")
        for grant in context.grants:
            if not isinstance(grant,ConstructionGrantContext) or not isinstance(grant.scope_id,UUID):
                raise ConstructionScopeDeniedError("Client projects scope is invalid.")
            require_construction_role_scope(role=grant.role,scope_type=grant.scope_type)
        # Keep the existing tenant CRM client-read boundary. Project responsibility,
        # creation or a project/document grant does not authorize reading a client card.
        require_construction_access(actor=actor,access_context=context,permission_code=CLIENT_PROJECTS_READ_PERMISSION)

    async def _context(self,actor):
        if await self.access_repository.has_active_platform_membership(tenant_id=actor.tenant_id,user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError("Client projects membership is not available.")
        context=await self.resolver.resolve(actor=actor)
        self._authorize(actor,context)
        return context

    async def _client(self,actor,client_id):
        row=await self.client_repository.get_active_client(tenant_id=actor.tenant_id,client_id=client_id)
        if (
            row is None or getattr(row,"id",None)!=client_id or getattr(row,"tenant_id",None)!=actor.tenant_id
            or getattr(row,"deleted_at","missing") is not None
        ):
            raise ConstructionClientNotFoundError("Client is not available in the authorized tenant.")

    @staticmethod
    def _project(row, *, actor,client_id,context):
        if (
            row is None or not isinstance(getattr(row,"id",None),UUID) or getattr(row,"tenant_id",None)!=actor.tenant_id
            or getattr(row,"client_id",None)!=client_id or getattr(row,"deleted_at","missing") is not None
        ):
            raise ConstructionClientProjectsReadOperationError("Client project result context is invalid.")
        require_construction_access(actor=actor,access_context=context,
            permission_code=CLIENT_PROJECTS_READ_PERMISSION,project_id=row.id)
        return ClientProjectReadResponse.model_validate({field:getattr(row,field) for field in PUBLIC_CLIENT_PROJECT_FIELDS})

    async def list_projects_for_client(self, *, actor,access_context,client_id,limit=20,after=None):
        try:
            if not isinstance(client_id,UUID) or type(limit) is not int or not 1<=limit<=100:
                raise ConstructionClientValidationError("Client projects require a UUID and page limit between 1 and 100.")
            self._authorize(actor,access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientProjectsReadOperationError("Client projects require an active caller transaction.")
            position=None
            if after is not None:
                if not isinstance(after,ConstructionClientProjectsReadCursor):
                    raise ConstructionClientValidationError("Client projects read cursor is invalid.")
                after=ConstructionClientProjectsReadCursor(after.tenant_id,after.user_id,after.client_id,after.updated_at,after.project_id)
                if after.tenant_id!=actor.tenant_id or after.user_id!=actor.user_id or after.client_id!=client_id:
                    raise ConstructionClientValidationError("Client projects cursor does not match this actor,client and tenant.")
                position=ConstructionClientProjectsCursor(after.tenant_id,after.client_id,after.updated_at,after.project_id)
            with self.session.no_autoflush:
                await self._context(actor)
                await self._client(actor,client_id)
                context=await self._context(actor)
                rows=await self.repository.list_projects_for_client(tenant_id=actor.tenant_id,client_id=client_id,
                    limit=limit,after=position,allowed_project_ids=None)
                if not isinstance(rows,list) or len(rows)>limit+1:
                    raise ConstructionClientProjectsReadOperationError("Client projects page result is invalid.")
                models=[self._project(row,actor=actor,client_id=client_id,context=context) for row in rows]
                previous=(position.updated_at,position.project_id) if position is not None else None
                ids=set()
                for model in models:
                    key=(model.updated_at,model.id)
                    if model.id in ids or (previous is not None and key>=previous):
                        raise ConstructionClientProjectsReadOperationError("Client projects page order is invalid.")
                    previous=key;ids.add(model.id)
                public=tuple(model.model_dump(mode="json") for model in models[:limit])
                cursor=None
                if len(models)>limit:
                    last=models[limit-1]
                    cursor=ConstructionClientProjectsReadCursor(actor.tenant_id,actor.user_id,client_id,last.updated_at,last.id)
                await self._client(actor,client_id)
                current=await self._context(actor)
                for model in models:
                    require_construction_access(actor=actor,access_context=current,
                        permission_code=CLIENT_PROJECTS_READ_PERMISSION,project_id=model.id)
                return ConstructionClientProjectsReadPage(public,cursor)
        except (ConstructionClientValidationError,ConstructionClientNotFoundError,ConstructionClientProjectsReadOperationError,
                ConstructionPermissionDeniedError,ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionClientProjectsReadOperationError("Client projects read failed.") from None


def build_construction_client_projects_read_service(*,session,now_provider=None):
    from database.repositories.construction.construction_client_projects import ConstructionClientProjectsRepository
    from database.repositories.construction import ConstructionClientRepository,ConstructionAccessRepository
    return ConstructionClientProjectsReadService(session=session,repository=ConstructionClientProjectsRepository(session),
        client_repository=ConstructionClientRepository(session),access_repository=ConstructionAccessRepository(session),now_provider=now_provider)
