from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select

from database.models import ConstructionActivity
from services.construction_activity_rules import ConstructionActivityLinks, ConstructionActivitySchedule, normalize_construction_activity_type, ACTIVITY_RESULT_UNSET


@dataclass(frozen=True)
class ConstructionActivityCursor:
    tenant_id: UUID
    due_at: datetime
    activity_id: UUID

    def __post_init__(self):
        if (
            not isinstance(self.tenant_id, UUID) or not isinstance(self.activity_id, UUID)
            or not isinstance(self.due_at, datetime) or self.due_at.tzinfo is None or self.due_at.utcoffset() is None
        ):
            raise ValueError("Activity cursor requires UUID context and an aware deadline.")
        object.__setattr__(self, "due_at", self.due_at.astimezone(timezone.utc))


class ConstructionActivityRepository:
    """Tenant-scoped data access; caller owns authorization, audit and transaction."""

    def __init__(self, session):
        if session is None or any(not callable(getattr(session, name, None))
                                 for name in ("execute", "add", "flush", "in_transaction")):
            raise ValueError("Activity repository requires a valid session.")
        self.session = session

    @staticmethod
    def _uuid(value):
        if not isinstance(value, UUID):
            raise ValueError("Activity context requires UUID references.")

    def _transaction(self):
        if self.session.in_transaction() is not True:
            raise ValueError("Activity writes and locks require an active transaction.")

    @staticmethod
    def _instant(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Activity requires an aware server timestamp.")
        return value.astimezone(timezone.utc)

    async def create_activity(
        self, *, tenant_id, activity_type, links, schedule, created_by_user_id, as_of,
        responsible_user_id=None, comment=None,
    ):
        self._uuid(tenant_id)
        self._uuid(created_by_user_id)
        if responsible_user_id is not None:
            self._uuid(responsible_user_id)
        self._transaction()
        kind = normalize_construction_activity_type(activity_type)
        if not isinstance(links, ConstructionActivityLinks) or not isinstance(schedule, ConstructionActivitySchedule):
            raise ValueError("Activity creation requires validated links and schedule.")
        # Reconstruct frozen values so tampered data cannot bypass their invariants.
        links = ConstructionActivityLinks(lead_id=links.lead_id, client_id=links.client_id, project_id=links.project_id)
        schedule = ConstructionActivitySchedule(due_at=schedule.due_at, timezone=schedule.timezone)
        if comment is not None and not isinstance(comment, str):
            raise ValueError("Activity comment must be text.")
        instant = self._instant(as_of)
        activity = ConstructionActivity(
            tenant_id=tenant_id, type=kind, status="planned",
            lead_id=links.lead_id, client_id=links.client_id, project_id=links.project_id,
            due_at=schedule.due_at, timezone=schedule.timezone,
            responsible_user_id=responsible_user_id, comment=comment.strip() or None if comment is not None else None,
            result=None, completed_at=None, cancelled_at=None, created_by_user_id=created_by_user_id,
            created_at=instant, updated_at=instant, deleted_at=None, row_version=1,
        )
        self.session.add(activity)
        await self.session.flush()
        return activity

    async def _get_active(self, *, tenant_id, activity_id, for_update):
        self._uuid(tenant_id)
        self._uuid(activity_id)
        if for_update:
            self._transaction()
        model = ConstructionActivity
        statement = select(model).where(
            model.tenant_id == tenant_id, model.id == activity_id, model.deleted_at.is_(None),
        ).limit(1).execution_options(populate_existing=True, autoflush=False)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_activity(self, *, tenant_id, activity_id):
        return await self._get_active(tenant_id=tenant_id, activity_id=activity_id, for_update=False)

    async def get_active_activity_for_update(self, *, tenant_id, activity_id):
        return await self._get_active(tenant_id=tenant_id, activity_id=activity_id, for_update=True)

    async def get_next_action_for_lead(self, *, tenant_id, lead_id):
        self._uuid(tenant_id)
        self._uuid(lead_id)
        model = ConstructionActivity
        statement = select(model).where(
            model.tenant_id == tenant_id, model.lead_id == lead_id,
            model.status == "planned", model.deleted_at.is_(None),
        ).order_by(model.due_at.asc(), model.id.asc()).limit(1).execution_options(
            populate_existing=True, autoflush=False,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_planned_activities(
        self, *, tenant_id, limit=20, after=None, lead_id=None, client_id=None,
        project_id=None, responsible_user_id=None,
    ):
        self._uuid(tenant_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Activity page limit must be between 1 and 100.")
        if after is not None:
            if not isinstance(after, ConstructionActivityCursor):
                raise ValueError("Activity cursor is invalid.")
            after = ConstructionActivityCursor(after.tenant_id, after.due_at, after.activity_id)
            if after.tenant_id != tenant_id:
                raise ValueError("Activity cursor does not match the current tenant.")
        model = ConstructionActivity
        filters = [model.tenant_id == tenant_id, model.status == "planned", model.deleted_at.is_(None)]
        for column, value in ((model.lead_id, lead_id), (model.client_id, client_id),
                              (model.project_id, project_id), (model.responsible_user_id, responsible_user_id)):
            if value is not None:
                self._uuid(value)
                filters.append(column == value)
        if after is not None:
            filters.append(or_(model.due_at > after.due_at,
                and_(model.due_at == after.due_at, model.id > after.activity_id)))
        statement = select(model).where(*filters).order_by(model.due_at.asc(), model.id.asc()).limit(
            limit + 1,
        ).execution_options(populate_existing=True, autoflush=False)
        result = await self.session.execute(statement)
        return list(result.scalars().all())


    async def _finalize_activity(
        self, *, tenant_id, activity_id, expected_row_version, next_status, result, as_of,
    ):
        """Conditional terminal write; caller owns authorization, audit and transaction."""
        from sqlalchemy import update
        from services.construction_activity_rules import require_construction_activity_transition

        self._uuid(tenant_id)
        self._uuid(activity_id)
        self._transaction()
        if type(expected_row_version) is not int or not 0 < expected_row_version < 2**63-1:
            raise ValueError("Activity finalization requires an incrementable positive BIGINT version.")
        require_construction_activity_transition(current_status="planned", next_status=next_status)
        if result is not ACTIVITY_RESULT_UNSET and result is not None and not isinstance(result, str):
            raise ValueError("Activity result must be text.")
        normalized_result = result.strip() or None if isinstance(result, str) else result
        instant = self._instant(as_of)
        model = ConstructionActivity
        changes = dict(status=next_status,
            completed_at=instant if next_status == "completed" else None,
            cancelled_at=instant if next_status == "cancelled" else None,
            updated_at=instant, row_version=model.row_version + 1)
        if result is not ACTIVITY_RESULT_UNSET:
            changes["result"] = normalized_result
        statement = update(model).where(
            model.tenant_id == tenant_id, model.id == activity_id, model.deleted_at.is_(None),
            model.row_version == expected_row_version, model.status == "planned",
            model.completed_at.is_(None), model.cancelled_at.is_(None),
        ).values(**changes).returning(model).execution_options(populate_existing=True, autoflush=False, synchronize_session=False)
        response = await self.session.execute(statement)
        return response.scalar_one_or_none()

    async def complete_activity(
        self, *, tenant_id, activity_id, expected_row_version, as_of, result=ACTIVITY_RESULT_UNSET,
    ):
        return await self._finalize_activity(tenant_id=tenant_id, activity_id=activity_id,
            expected_row_version=expected_row_version, next_status="completed", result=result, as_of=as_of)

    async def cancel_activity(
        self, *, tenant_id, activity_id, expected_row_version, as_of, result=ACTIVITY_RESULT_UNSET,
    ):
        return await self._finalize_activity(tenant_id=tenant_id, activity_id=activity_id,
            expected_row_version=expected_row_version, next_status="cancelled", result=result, as_of=as_of)


    async def find_active_activity_links(self, *, tenant_id, activity_id, allowed_project_ids=None):
        """Read minimal scope metadata; project-only scope excludes tenant CRM links."""
        self._uuid(tenant_id)
        self._uuid(activity_id)
        model = ConstructionActivity
        filters = [model.tenant_id == tenant_id, model.id == activity_id, model.deleted_at.is_(None)]
        if allowed_project_ids is not None:
            if not isinstance(allowed_project_ids, tuple):
                raise ValueError("Allowed activity projects must be a UUID tuple.")
            for value in allowed_project_ids:
                self._uuid(value)
            if not allowed_project_ids:
                return None
            filters.extend((model.project_id.in_(allowed_project_ids), model.lead_id.is_(None), model.client_id.is_(None)))
        statement = select(model.id, model.lead_id, model.client_id, model.project_id).where(*filters).limit(1).execution_options(autoflush=False)
        response = await self.session.execute(statement)
        row = response.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_active_activity_for_update_by_links(self, *, tenant_id, activity_id, links):
        """Lock only the previously authorized links; never flush pending ORM changes."""
        self._uuid(tenant_id)
        self._uuid(activity_id)
        self._transaction()
        if not isinstance(links, ConstructionActivityLinks):
            raise ValueError("Activity lock requires validated links.")
        links = ConstructionActivityLinks(links.lead_id, links.client_id, links.project_id)
        model = ConstructionActivity
        statement = select(model).where(
            model.tenant_id == tenant_id, model.id == activity_id, model.deleted_at.is_(None),
            model.lead_id == links.lead_id, model.client_id == links.client_id, model.project_id == links.project_id,
        ).limit(1).with_for_update().execution_options(populate_existing=True, autoflush=False)
        response = await self.session.execute(statement)
        return response.scalar_one_or_none()
