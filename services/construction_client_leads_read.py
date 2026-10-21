from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StrictStr,field_validator,model_validator

from database.repositories.construction_leads import ConstructionLeadCursor
from services.construction_clients import ConstructionClientNotFoundError,ConstructionClientValidationError,ConstructionClientOperationError
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext,ConstructionGrantContext,ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,require_construction_access,require_construction_role_scope,
)


CLIENT_LEADS_READ_PERMISSION="construction.projects.read"


class ConstructionClientLeadsReadOperationError(ConstructionClientOperationError):
    pass


class ClientLeadReadResponse(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    source: StrictStr = Field(min_length=1)
    name: StrictStr = Field(min_length=1)
    work_type_description: StrictStr = Field(min_length=1)
    phone: StrictStr | None
    email: StrictStr | None
    address_raw: StrictStr | None
    comment: StrictStr | None
    responsible_user_id: UUID | None
    status: Literal["new","contacted","measurement_scheduled","measurement_completed","estimate_preparation","proposal_sent","negotiation","won","lost"]
    converted_client_id: UUID
    converted_project_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1,le=2**63-1)

    @field_validator("created_at","updated_at")
    @classmethod
    def utc_timestamp(cls,value):
        return value.astimezone(timezone.utc)

    @field_validator("source","name","work_type_description")
    @classmethod
    def nonblank_text(cls,value):
        if not value.strip():raise ValueError("Stored lead text is blank.")
        return value

    @model_validator(mode="after")
    def contact_present(self):
        if not (self.phone is not None and self.phone.strip()) and not (self.email is not None and self.email.strip()):
            raise ValueError("Stored lead requires a contact.")
        return self


PUBLIC_CLIENT_LEAD_FIELDS=frozenset(ClientLeadReadResponse.model_fields)


@dataclass(frozen=True)
class ConstructionClientLeadsReadCursor:
    tenant_id: UUID
    user_id: UUID
    client_id: UUID
    updated_at: datetime
    lead_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id,UUID) or not isinstance(self.client_id,UUID):
            raise ConstructionClientValidationError("Client leads cursor requires actor/client UUIDs.")
        try:
            checked=ConstructionLeadCursor(self.tenant_id,self.updated_at,self.lead_id)
        except ValueError:
            raise ConstructionClientValidationError("Client leads cursor is invalid.") from None
        object.__setattr__(self,"updated_at",checked.updated_at)


@dataclass(frozen=True)
class ConstructionClientLeadsReadPage:
    items: tuple
    next_cursor: ConstructionClientLeadsReadCursor | None


class ConstructionClientLeadsReadService:
    """Read canonical client-linked leads; caller owns the read transaction."""

    def __init__(self, *,session,repository,client_repository,access_repository,now_provider=None):
        protocols=((repository,("list_active_leads",)),(client_repository,("get_active_client",)),
            (access_repository,("has_active_platform_membership","list_active_grants")))
        if (
            session is None or not callable(getattr(session,"in_transaction",None)) or not hasattr(session,"no_autoflush")
            or any(getattr(repo,"session",None) is not session
                or any(not callable(getattr(repo,name,None)) for name in methods) for repo,methods in protocols)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Client leads read requires shared-session repositories.")
        self.session,self.repository=session,repository
        self.client_repository,self.access_repository=client_repository,access_repository
        self.now_provider=now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver=ConstructionAccessContextResolver(repository=access_repository,now_provider=self._now)

    def _now(self):
        value=self.now_provider()
        if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientLeadsReadOperationError("Client leads clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor,context):
        if (
            getattr(actor,"status",None)!="active" or not isinstance(getattr(actor,"user_id",None),UUID)
            or not isinstance(getattr(actor,"tenant_id",None),UUID) or not isinstance(context,ConstructionAccessContext)
            or context.user_id!=actor.user_id or context.tenant_id!=actor.tenant_id
        ):
            raise ConstructionPermissionDeniedError("Client leads access is not available.")
        for grant in context.grants:
            if not isinstance(grant,ConstructionGrantContext) or not isinstance(grant.scope_id,UUID):
                raise ConstructionScopeDeniedError("Client leads scope is invalid.")
            require_construction_role_scope(role=grant.role,scope_type=grant.scope_type)
        require_construction_access(actor=actor,access_context=context,permission_code=CLIENT_LEADS_READ_PERMISSION)

    async def _context(self,actor):
        if await self.access_repository.has_active_platform_membership(tenant_id=actor.tenant_id,user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError("Client leads membership is not available.")
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
    def _lead(row, *,actor,client_id,context):
        if (
            row is None or not isinstance(getattr(row,"id",None),UUID) or getattr(row,"tenant_id",None)!=actor.tenant_id
            or getattr(row,"converted_client_id",None)!=client_id or getattr(row,"deleted_at","missing") is not None
        ):
            raise ConstructionClientLeadsReadOperationError("Client lead result context is invalid.")
        model=ClientLeadReadResponse.model_validate({field:getattr(row,field) for field in PUBLIC_CLIENT_LEAD_FIELDS})
        if model.converted_project_id is not None:
            require_construction_access(actor=actor,access_context=context,
                permission_code=CLIENT_LEADS_READ_PERMISSION,project_id=model.converted_project_id)
        return model

    async def list_leads_for_client(self, *,actor,access_context,client_id,limit=20,after=None):
        try:
            if not isinstance(client_id,UUID) or type(limit) is not int or not 1<=limit<=100:
                raise ConstructionClientValidationError("Client leads require a UUID and page limit between 1 and 100.")
            self._authorize(actor,access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientLeadsReadOperationError("Client leads require an active caller transaction.")
            position=None
            if after is not None:
                if not isinstance(after,ConstructionClientLeadsReadCursor):
                    raise ConstructionClientValidationError("Client leads read cursor is invalid.")
                after=ConstructionClientLeadsReadCursor(after.tenant_id,after.user_id,after.client_id,after.updated_at,after.lead_id)
                if after.tenant_id!=actor.tenant_id or after.user_id!=actor.user_id or after.client_id!=client_id:
                    raise ConstructionClientValidationError("Client leads cursor does not match this actor,client and tenant.")
                position=ConstructionLeadCursor(after.tenant_id,after.updated_at,after.lead_id)
            with self.session.no_autoflush:
                await self._context(actor)
                await self._client(actor,client_id)
                context=await self._context(actor)
                rows=await self.repository.list_active_leads(tenant_id=actor.tenant_id,converted_client_id=client_id,limit=limit,after=position)
                if not isinstance(rows,list) or len(rows)>limit+1:
                    raise ConstructionClientLeadsReadOperationError("Client leads page result is invalid.")
                models=[self._lead(row,actor=actor,client_id=client_id,context=context) for row in rows]
                previous=(position.updated_at,position.lead_id) if position is not None else None
                ids=set()
                for model in models:
                    key=(model.updated_at,model.id)
                    if model.id in ids or (previous is not None and key>=previous):
                        raise ConstructionClientLeadsReadOperationError("Client leads page order is invalid.")
                    previous=key;ids.add(model.id)
                public=tuple(model.model_dump(mode="json") for model in models[:limit])
                cursor=None
                if len(models)>limit:
                    last=models[limit-1]
                    cursor=ConstructionClientLeadsReadCursor(actor.tenant_id,actor.user_id,client_id,last.updated_at,last.id)
                await self._client(actor,client_id)
                current=await self._context(actor)
                for model in models:
                    if model.converted_project_id is not None:
                        require_construction_access(actor=actor,access_context=current,
                            permission_code=CLIENT_LEADS_READ_PERMISSION,project_id=model.converted_project_id)
                return ConstructionClientLeadsReadPage(public,cursor)
        except (ConstructionClientValidationError,ConstructionClientNotFoundError,ConstructionClientLeadsReadOperationError,
                ConstructionPermissionDeniedError,ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionClientLeadsReadOperationError("Client leads read failed.") from None


def build_construction_client_leads_read_service(*,session,now_provider=None):
    from database.repositories.construction_leads import ConstructionLeadRepository
    from database.repositories.construction import ConstructionClientRepository,ConstructionAccessRepository
    return ConstructionClientLeadsReadService(session=session,repository=ConstructionLeadRepository(session),
        client_repository=ConstructionClientRepository(session),access_repository=ConstructionAccessRepository(session),now_provider=now_provider)
