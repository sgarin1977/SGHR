from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select

from database.models import ConstructionLead
from services.construction.construction_lead_rules import (
    ConstructionLeadInput, normalize_construction_lead_source, require_construction_lead_status,
)


@dataclass(frozen=True)
class ConstructionLeadCursor:
    tenant_id: UUID
    updated_at: datetime
    lead_id: UUID

    def __post_init__(self):
        if (
            not isinstance(self.tenant_id, UUID) or not isinstance(self.lead_id, UUID)
            or not isinstance(self.updated_at, datetime) or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
        ):
            raise ValueError("Lead cursor requires UUID context and an aware timestamp.")
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(timezone.utc))


class ConstructionLeadRepository:
    """Tenant-scoped data access; the caller owns authorization, audit and transaction."""

    def __init__(self, session):
        if session is None or any(
            not callable(getattr(session, name, None)) for name in ("execute", "add", "flush", "in_transaction")
        ):
            raise ValueError("Lead repository requires a valid session.")
        self.session = session

    @staticmethod
    def _uuid(value):
        if not isinstance(value, UUID):
            raise ValueError("Lead context requires UUID references.")

    def _transaction(self):
        if not self.session.in_transaction():
            raise ValueError("Lead writes and locks require an active transaction.")

    async def create_lead(self, *, tenant_id, data, as_of):
        self._uuid(tenant_id)
        self._transaction()
        if not isinstance(data, ConstructionLeadInput):
            raise ValueError("Lead creation requires validated input.")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Lead creation requires an aware server timestamp.")
        instant = as_of.astimezone(timezone.utc)
        lead = ConstructionLead(
            tenant_id=tenant_id, source=data.source, name=data.name, phone=data.phone, email=data.email,
            address_raw=data.address_raw, work_type_description=data.work_type_description,
            responsible_user_id=data.responsible_user_id, comment=data.comment,
            status="new", converted_client_id=None, converted_project_id=None,
            created_at=instant, updated_at=instant, deleted_at=None, row_version=1,
        )
        self.session.add(lead)
        await self.session.flush()
        return lead

    async def _get_active(self, *, tenant_id, lead_id, for_update):
        self._uuid(tenant_id)
        self._uuid(lead_id)
        if for_update:
            self._transaction()
        lead = ConstructionLead
        statement = (
            select(lead)
            .where(lead.tenant_id == tenant_id, lead.id == lead_id, lead.deleted_at.is_(None))
            .limit(1)
            .execution_options(populate_existing=True, autoflush=False)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_lead(self, *, tenant_id, lead_id):
        return await self._get_active(tenant_id=tenant_id, lead_id=lead_id, for_update=False)

    async def get_active_lead_for_update(self, *, tenant_id, lead_id):
        return await self._get_active(tenant_id=tenant_id, lead_id=lead_id, for_update=True)

    async def list_active_leads(
        self, *, tenant_id, limit=20, after=None, status=None, source=None,
        responsible_user_id=None, converted_client_id=None, converted_project_id=None,
    ):
        self._uuid(tenant_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Lead page limit must be between 1 and 100.")
        if after is not None and (
            not isinstance(after, ConstructionLeadCursor) or after.tenant_id != tenant_id
        ):
            raise ValueError("Lead cursor does not match the current tenant.")
        lead = ConstructionLead
        filters = [lead.tenant_id == tenant_id, lead.deleted_at.is_(None)]
        if status is not None:
            require_construction_lead_status(status)
            filters.append(lead.status == status)
        if source is not None:
            filters.append(lead.source == normalize_construction_lead_source(source))
        for column, value in (
            (lead.responsible_user_id, responsible_user_id),
            (lead.converted_client_id, converted_client_id),
            (lead.converted_project_id, converted_project_id),
        ):
            if value is not None:
                self._uuid(value)
                filters.append(column == value)
        if after is not None:
            filters.append(or_(
                lead.updated_at < after.updated_at,
                and_(lead.updated_at == after.updated_at, lead.id < after.lead_id),
            ))
        statement = (
            select(lead).where(*filters)
            .order_by(lead.updated_at.desc(), lead.id.desc())
            .limit(limit+1)
            .execution_options(populate_existing=True, autoflush=False)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def complete_conversion(
        self, *, tenant_id, lead_id, expected_row_version, client_id, project_id, as_of,
    ):
        """Conditional write only; caller locks resources, authorizes and audits."""
        from sqlalchemy import update
        from database.models import ConstructionClient, ConstructionProject
        from services.construction.construction_lead_rules import (
            CONSTRUCTION_LEAD_STATUSES, CONSTRUCTION_LEAD_TERMINAL_STATUSES,
        )

        for value in (tenant_id, lead_id, client_id, project_id):
            self._uuid(value)
        self._transaction()
        if type(expected_row_version) is not int or not 0 < expected_row_version < 2**63 - 1:
            raise ValueError("Lead conversion requires an incrementable positive BIGINT version.")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Lead conversion requires an aware server timestamp.")
        instant = as_of.astimezone(timezone.utc)
        lead, client, project = ConstructionLead, ConstructionClient, ConstructionProject
        client_exists = select(client.id).where(
            client.tenant_id == tenant_id, client.id == client_id, client.deleted_at.is_(None),
        ).exists()
        project_exists = select(project.id).where(
            project.tenant_id == tenant_id, project.id == project_id,
            project.client_id == client_id, project.deleted_at.is_(None),
        ).exists()
        statement = (
            update(lead)
            .where(
                lead.tenant_id == tenant_id, lead.id == lead_id,
                lead.deleted_at.is_(None), lead.row_version == expected_row_version,
                lead.status.in_(sorted(CONSTRUCTION_LEAD_STATUSES - CONSTRUCTION_LEAD_TERMINAL_STATUSES)),
                lead.converted_client_id.is_(None), lead.converted_project_id.is_(None),
                client_exists, project_exists,
            )
            .values(
                status="won", converted_client_id=client_id, converted_project_id=project_id,
                row_version=lead.row_version + 1, updated_at=instant,
            )
            .returning(lead)
            .execution_options(populate_existing=True, autoflush=False, synchronize_session=False)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()


    async def transition_status(
        self, *, tenant_id, lead_id, expected_row_version,
        current_status, next_status, as_of,
    ):
        """Conditional mutation; caller handles authorization, no-op, audit and transaction."""
        from sqlalchemy import update
        from services.construction.construction_lead_rules import require_construction_lead_transition

        self._uuid(tenant_id)
        self._uuid(lead_id)
        self._transaction()
        if type(expected_row_version) is not int or not 0 < expected_row_version < 2**63 - 1:
            raise ValueError("Lead transition requires an incrementable positive BIGINT version.")
        require_construction_lead_transition(
            current_status=current_status, next_status=next_status,
        )
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Lead transition requires an aware server timestamp.")
        instant = as_of.astimezone(timezone.utc)
        lead = ConstructionLead
        statement = (
            update(lead)
            .where(
                lead.tenant_id == tenant_id, lead.id == lead_id,
                lead.deleted_at.is_(None), lead.row_version == expected_row_version,
                lead.status == current_status,
            )
            .values(status=next_status, row_version=lead.row_version + 1, updated_at=instant)
            .returning(lead)
            .execution_options(populate_existing=True, autoflush=False, synchronize_session=False)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
