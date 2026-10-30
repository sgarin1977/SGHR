from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_c007_invitation_scopes_are_read_only_and_fully_tenant_scoped():
    from database.repositories.construction import ConstructionInvitationRepository

    tenant_id, invitation_id = uuid4(), uuid4()
    rows = [
        SimpleNamespace(
            id=uuid4(), tenant_id=tenant_id, invitation_id=invitation_id,
            scope_type="project", scope_id=uuid4(),
        )
        for _ in range(2)
    ]
    statements = []

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: rows),
            )

        async def flush(self):
            pytest.fail("Reading scopes must not flush changes.")

        async def commit(self):
            pytest.fail("Reading scopes must not commit.")

    result = await ConstructionInvitationRepository(Session()).list_invitation_scopes(
        tenant_id=tenant_id, invitation_id=invitation_id,
    )
    assert result == rows
    assert len(statements) == 1
    sql = " ".join(str(statements[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )).lower().split())
    where = sql.split(" where ", 1)[1]
    assert (
        f"construction_invitation_scopes.tenant_id = '{tenant_id}'"
        in where
    )
    assert (
        f"construction_invitation_scopes.invitation_id = '{invitation_id}'"
        in where
    )
