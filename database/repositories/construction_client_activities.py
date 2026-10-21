from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select

from database.models import ConstructionActivity, ConstructionLead, ConstructionProject
from database.repositories.construction_activities import ConstructionActivityCursor


@dataclass(frozen=True)
class ConstructionClientActivitiesCursor:
    tenant_id: UUID
    client_id: UUID
    due_at: datetime
    activity_id: UUID

    def __post_init__(self):
        if not isinstance(self.client_id, UUID):
            raise ValueError('Client activities cursor requires a client UUID.')
        checked = ConstructionActivityCursor(self.tenant_id, self.due_at, self.activity_id)
        object.__setattr__(self, 'due_at', checked.due_at)


@dataclass(frozen=True)
class ConstructionClientActivityRecord:
    """Internal association evidence from the same SELECT as the activity."""
    activity: object
    linked_lead_id: UUID | None
    linked_lead_tenant_id: UUID | None
    linked_lead_client_id: UUID | None
    linked_project_id: UUID | None
    linked_project_tenant_id: UUID | None
    linked_project_client_id: UUID | None


class ConstructionClientActivitiesRepository:
    """Read client-related planned activities; caller owns authorization/transaction."""

    def __init__(self, session):
        if session is None or not callable(getattr(session, 'execute', None)):
            raise ValueError('Client activities repository requires a session.')
        self.session = session

    async def list_planned_activities_for_client(self, *, tenant_id, client_id, limit=20, after=None):
        if not isinstance(tenant_id, UUID) or not isinstance(client_id, UUID):
            raise ValueError('Client activities require tenant/client UUIDs.')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Client activities page limit must be between 1 and 100.')
        if after is not None:
            if not isinstance(after, ConstructionClientActivitiesCursor):
                raise ValueError('Client activities cursor is invalid.')
            after = ConstructionClientActivitiesCursor(after.tenant_id, after.client_id, after.due_at, after.activity_id)
            if after.tenant_id != tenant_id or after.client_id != client_id:
                raise ValueError('Client activities cursor belongs to another client or tenant.')
        activity, lead, project = ConstructionActivity, ConstructionLead, ConstructionProject
        filters = [activity.tenant_id == tenant_id, activity.deleted_at.is_(None), activity.status == 'planned',
            or_(activity.client_id == client_id, lead.converted_client_id == client_id, project.client_id == client_id)]
        if after is not None:
            filters.append(or_(activity.due_at > after.due_at,
                and_(activity.due_at == after.due_at, activity.id > after.activity_id)))
        # Each parent join targets a unique UUID: an activity appears once even
        # when all three client associations match. Filtering precedes LIMIT.
        statement = select(activity, lead.id, lead.tenant_id, lead.converted_client_id,
            project.id, project.tenant_id, project.client_id).outerjoin(lead,
                and_(lead.id == activity.lead_id, lead.tenant_id == activity.tenant_id, lead.tenant_id == tenant_id)
            ).outerjoin(project,
                and_(project.id == activity.project_id, project.tenant_id == activity.tenant_id, project.tenant_id == tenant_id)
            ).where(*filters).order_by(activity.due_at.asc(), activity.id.asc()).limit(limit + 1).execution_options(
                populate_existing=True, autoflush=False)
        # Parent archival does not erase a canonical client association. Only
        # activity deletion/status filters are specified by the approved contract.
        result = await self.session.execute(statement)
        return [ConstructionClientActivityRecord(*row) for row in result.all()]
