"""CON-C015 address sources; caller owns authorization and suggestion ranking.

Client addresses come exclusively from their undeleted projects. All project
workflow statuses participate. Batches bound memory, not the candidate set:
ranking must examine the stream before returning the best ten suggestions.
No client address columns, geocoding, links, writes or total-row cutoff.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select

from database.models import ConstructionClient, ConstructionProject


@dataclass(frozen=True)
class ConstructionAddressSource:
    project_id: UUID
    tenant_id: UUID
    project_name: str
    project_status: str
    address_raw: str
    address_formatted: str | None
    client_id: UUID | None
    client_name: str | None


class ConstructionAddressSourcesRepository:
    def __init__(self, session):
        if (session is None or not callable(getattr(session, 'execute', None))
                or not callable(getattr(session, 'in_transaction', None))
                or not hasattr(session, 'no_autoflush')):
            raise ValueError('Address sources require a transaction-capable session.')
        self.session = session

    async def iter_address_sources(self, *, tenant_id, allowed_project_ids=None, batch_size=200):
        """Stream eligible sources under the already-authorized project scope.

        None means the caller has tenant-wide read scope; an empty tuple means
        no permitted projects. Service authorization remains mandatory.
        Clients repeat across source rows intentionally: deduplicate after
        matching, so their best matching address determines their ranking.
        """
        if not isinstance(tenant_id, UUID):
            raise ValueError('Address sources require trusted UUID tenant context.')
        if (allowed_project_ids is not None and (not isinstance(allowed_project_ids, tuple)
                or any(not isinstance(value, UUID) for value in allowed_project_ids))):
            raise ValueError('Authorized projects must be a UUID tuple.')
        if type(batch_size) is not int or not 1 <= batch_size <= 500:
            raise ValueError('Internal address batch size must be between 1 and 500.')
        if not self.session.in_transaction():
            raise ValueError('Address sources require an existing caller transaction.')
        if allowed_project_ids == ():
            return
        project, client = ConstructionProject, ConstructionClient
        position = None
        while True:
            if not self.session.in_transaction():
                raise ValueError('Address source transaction ended during iteration.')
            conditions = [project.tenant_id == tenant_id, project.deleted_at.is_(None)]
            if allowed_project_ids is not None:
                conditions.append(project.id.in_(allowed_project_ids))
            if position is not None:
                conditions.append(project.id > position)
            statement = select(
                project.id.label('project_id'), project.tenant_id,
                project.name.label('project_name'), project.status.label('project_status'),
                project.address_raw, project.address_formatted,
                client.id.label('client_id'), client.display_name.label('client_name'),
            ).select_from(project).outerjoin(client, and_(
                client.id == project.client_id, client.tenant_id == project.tenant_id,
                client.tenant_id == tenant_id, client.deleted_at.is_(None),
            )).where(*conditions).order_by(project.id).limit(batch_size).execution_options(autoflush=False)
            with self.session.no_autoflush:
                rows = (await self.session.execute(statement)).mappings().all()
            if len(rows) > batch_size:
                raise ValueError('Address source batch exceeded the requested size.')
            for values in rows:
                if not self.session.in_transaction():
                    raise ValueError('Address source transaction ended during iteration.')
                row = ConstructionAddressSource(**values)
                if (not isinstance(row.project_id, UUID) or row.tenant_id != tenant_id
                        or (position is not None and row.project_id.int <= position.int)
                        or not all(isinstance(value, str) for value in (row.project_name, row.project_status, row.address_raw))
                        or (row.address_formatted is not None and not isinstance(row.address_formatted, str))
                        or ((row.client_id is None) != (row.client_name is None))
                        or (row.client_id is not None and (not isinstance(row.client_id, UUID) or not isinstance(row.client_name, str)))
                        or (allowed_project_ids is not None and row.project_id not in allowed_project_ids)):
                    raise ValueError('Address source projection does not match its tenant and scope.')
                position = row.project_id
                yield row
            if len(rows) < batch_size:
                return
