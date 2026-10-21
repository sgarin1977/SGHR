from sqlalchemy import and_, or_, select

from database.models import ConstructionActivity
from database.repositories.construction_activities import ConstructionActivityCursor, ConstructionActivityRepository


class ConstructionActivityReadRepository(ConstructionActivityRepository):
    """Apply authorized project scope before order/limit; no locks or writes."""

    async def list_visible_planned_activities(
        self, *, tenant_id, allowed_project_ids=None, limit=20, after=None,
        lead_id=None, client_id=None, project_id=None, responsible_user_id=None,
    ):
        self._uuid(tenant_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Activity page limit must be between 1 and 100.")
        if after is not None:
            if not isinstance(after, ConstructionActivityCursor):
                raise ValueError("Activity cursor is invalid.")
            after=ConstructionActivityCursor(after.tenant_id, after.due_at, after.activity_id)
            if after.tenant_id != tenant_id:
                raise ValueError("Activity cursor does not match the current tenant.")
        model=ConstructionActivity
        filters=[model.tenant_id == tenant_id, model.status == "planned", model.deleted_at.is_(None)]
        for column, value in ((model.lead_id, lead_id), (model.client_id, client_id),
                              (model.project_id, project_id), (model.responsible_user_id, responsible_user_id)):
            if value is not None:
                self._uuid(value)
                filters.append(column == value)
        if allowed_project_ids is not None:
            if not isinstance(allowed_project_ids, tuple):
                raise ValueError("Allowed activity projects must be a UUID tuple.")
            for value in allowed_project_ids:
                self._uuid(value)
            if not allowed_project_ids:
                return []
            filters.extend((model.project_id.in_(allowed_project_ids), model.lead_id.is_(None), model.client_id.is_(None)))
        if after is not None:
            filters.append(or_(model.due_at > after.due_at,
                and_(model.due_at == after.due_at, model.id > after.activity_id)))
        statement=select(model).where(*filters).order_by(model.due_at.asc(), model.id.asc()).limit(
            limit+1).execution_options(populate_existing=True, autoflush=False)
        result=await self.session.execute(statement)
        return list(result.scalars().all())
