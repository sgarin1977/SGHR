"""Tenant client catalog query; caller owns authorization and transaction."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from database.models import ConstructionClient


@dataclass(frozen=True)
class ConstructionClientCatalogCursor:
    tenant_id: UUID
    updated_at: datetime
    client_id: UUID

    def __post_init__(self):
        if (
            not isinstance(self.tenant_id, UUID) or not isinstance(self.client_id, UUID)
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None
        ):
            raise ValueError("Client catalog cursor requires UUIDs and an aware timestamp.")
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(timezone.utc))


class ConstructionClientCatalogRepository:
    def __init__(self, session):
        if session is None or not callable(getattr(session, "execute", None)):
            raise ValueError("Client catalog requires a session.")
        self.session = session

    async def list_clients(self, *, tenant_id, limit=20, after=None):
        if not isinstance(tenant_id, UUID) or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid client catalog query.")
        if after is not None:
            if not isinstance(after, ConstructionClientCatalogCursor):
                raise ValueError("Invalid client catalog cursor.")
            after = ConstructionClientCatalogCursor(
                after.tenant_id, after.updated_at, after.client_id,
            )
            if after.tenant_id != tenant_id:
                raise ValueError("Client cursor belongs to another tenant.")
        model = ConstructionClient
        conditions = [model.tenant_id == tenant_id, model.deleted_at.is_(None)]
        if after is not None:
            conditions.append(or_(
                model.updated_at < after.updated_at,
                and_(model.updated_at == after.updated_at, model.id < after.client_id),
            ))
        statement = (
            select(model).where(*conditions)
            .order_by(model.updated_at.desc(), model.id.desc())
            .limit(limit + 1)
            .execution_options(populate_existing=True, autoflush=False)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
