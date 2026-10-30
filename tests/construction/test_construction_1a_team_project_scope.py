from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine

from database.models import ConstructionAccessGrant
from database.repositories.construction.construction_team import ConstructionTeamRepository


@pytest.mark.asyncio
async def test_c004_project_team_includes_inherited_tenant_access():
    tenant_id, foreign_tenant, project_id, other_project = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    now = datetime.now(timezone.utc)

    def grant(scope_type, scope_id, **changes):
        row = dict(
            id=uuid4(), tenant_id=tenant_id, user_id=uuid4(),
            role="OWNER" if scope_type == "tenant" else "OBSERVER",
            scope_type=scope_type, scope_id=scope_id, status="active",
            expires_at=None, granted_by_user_id=uuid4(),
            granted_at=now - timedelta(days=1),
            revoked_by_user_id=None, revoked_at=None,
        )
        row.update(changes)
        return row

    project_grant = grant(
        "project", project_id, expires_at=now + timedelta(days=1),
    )
    tenant_grant = grant("tenant", tenant_id)
    rows = [
        project_grant, tenant_grant,
        grant("project", other_project),
        grant("tenant", foreign_tenant, tenant_id=foreign_tenant),
        grant("tenant", tenant_id, expires_at=now),
        grant("tenant", tenant_id, status="revoked",
              revoked_by_user_id=uuid4(), revoked_at=now),
        grant("document", uuid4()),
    ]

    # Copy columns only: PostgreSQL constraints and RLS are outside this test.
    model_table = ConstructionAccessGrant.__table__
    table = Table(
        model_table.name, MetaData(), schema=model_table.schema,
        *[Column(column.name, column.type) for column in model_table.columns],
    )
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            table.create(connection)
            connection.execute(table.insert(), rows)

            class Session:
                async def execute(self, statement):
                    return connection.execute(statement)

            result = await ConstructionTeamRepository(Session()).list_active_grants(
                tenant_id=tenant_id, project_id=project_id, as_of=now,
            )
            assert set(result) == {project_grant["id"], tenant_grant["id"]}
    finally:
        engine.dispose()
