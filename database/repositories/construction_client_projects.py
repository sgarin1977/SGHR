from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select

from database.models import ConstructionProject


@dataclass(frozen=True)
class ConstructionClientProjectsCursor:
    tenant_id: UUID
    client_id: UUID
    updated_at: datetime
    project_id: UUID

    def __post_init__(self):
        if (
            any(not isinstance(value,UUID) for value in (self.tenant_id,self.client_id,self.project_id))
            or not isinstance(self.updated_at,datetime) or self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None
        ):
            raise ValueError("Client projects cursor requires UUID references and an aware timestamp.")
        object.__setattr__(self,"updated_at",self.updated_at.astimezone(timezone.utc))


class ConstructionClientProjectsRepository:
    """Read direct client/project links; caller owns authorization and transaction."""

    def __init__(self,session):
        if session is None or not callable(getattr(session,"execute",None)):
            raise ValueError("Client projects repository requires a session.")
        self.session=session

    @staticmethod
    def _uuid(value):
        if not isinstance(value,UUID):
            raise ValueError("Client projects context requires UUID references.")

    async def list_projects_for_client(self, *, tenant_id, client_id, limit=20, after=None, allowed_project_ids=None):
        self._uuid(tenant_id);self._uuid(client_id)
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Client projects limit must be between 1 and 100.")
        if after is not None:
            if not isinstance(after,ConstructionClientProjectsCursor):
                raise ValueError("Client projects cursor is invalid.")
            after=ConstructionClientProjectsCursor(after.tenant_id,after.client_id,after.updated_at,after.project_id)
            if after.tenant_id!=tenant_id or after.client_id!=client_id:
                raise ValueError("Client projects cursor does not match this client and tenant.")
        model=ConstructionProject
        filters=[model.tenant_id==tenant_id,model.client_id==client_id,model.deleted_at.is_(None)]
        if allowed_project_ids is not None:
            if not isinstance(allowed_project_ids,tuple):
                raise ValueError("Allowed projects must be a UUID tuple.")
            for project_id in allowed_project_ids:self._uuid(project_id)
            if not allowed_project_ids:return []
            filters.append(model.id.in_(allowed_project_ids))
        if after is not None:
            filters.append(or_(model.updated_at<after.updated_at,
                and_(model.updated_at==after.updated_at,model.id<after.project_id)))
        statement=select(model).where(*filters).order_by(model.updated_at.desc(),model.id.desc()).limit(
            limit+1).execution_options(populate_existing=True,autoflush=False)
        result=await self.session.execute(statement)
        return list(result.scalars().all())
