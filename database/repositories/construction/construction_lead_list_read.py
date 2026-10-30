"""Bounded read-only next-action projection for an authorized CRM lead page."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from database.models import ConstructionActivity
from database.repositories.construction.construction_leads import ConstructionLeadRepository


class ConstructionLeadListReadRepository(ConstructionLeadRepository):
    async def list_next_actions_for_leads(self, *, tenant_id, lead_ids):
        self._uuid(tenant_id)
        if (not isinstance(lead_ids, tuple) or len(lead_ids)>100
                or any(not isinstance(identifier, UUID) for identifier in lead_ids)
                or len(set(lead_ids))!=len(lead_ids)):
            raise ValueError('Next-action batch requires at most 100 distinct lead UUIDs.')
        if not lead_ids:
            return []
        activity = ConstructionActivity
        # Rank within each requested lead, after tenant/lifecycle filtering.
        # No global cutoff can discard another lead's earliest action.
        ranked = select(activity, func.row_number().over(
            partition_by=activity.lead_id,
            order_by=(activity.due_at.asc(), activity.id.asc()),
        ).label('_next_action_rank')).where(
            activity.tenant_id==tenant_id, activity.lead_id.in_(lead_ids),
            activity.status=='planned', activity.deleted_at.is_(None),
        ).subquery()
        first = aliased(activity, ranked)
        statement = select(first).where(ranked.c._next_action_rank==1).order_by(
            first.lead_id.asc()).execution_options(populate_existing=True, autoflush=False)
        result = await self.session.execute(statement)
        return list(result.scalars().all())
