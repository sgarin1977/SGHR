import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import EventLog


class EventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_event(
        self,
        event_type: str,
        tenant_id: Optional[uuid.UUID],
        user_id: Optional[uuid.UUID],
        entity_type: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
        payload: Optional[dict] = None,
        platform: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> EventLog:
        event = EventLog(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            platform=platform,
            trace_id=trace_id,
        )

        self.session.add(event)
        await self.session.flush()

        return event