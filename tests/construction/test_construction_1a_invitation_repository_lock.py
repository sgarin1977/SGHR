from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_c007_invitation_lock_checks_tenant_and_refreshes_state():
    from database.repositories.construction import ConstructionInvitationRepository

    tenant_id, invitation_id = uuid4(), uuid4()
    invitation = SimpleNamespace(id=invitation_id, status="accepted")

    class Result:
        def scalar_one_or_none(self):
            return invitation

    class Session:
        async def execute(self, statement):
            self.statement = statement
            return Result()

        async def commit(self):
            pytest.fail("Repository must not commit")

    session = Session()
    result = await ConstructionInvitationRepository(
        session
    ).get_invitation_for_update(
        tenant_id=tenant_id,
        invitation_id=invitation_id,
    )

    assert result is invitation
    statement = session.statement
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    conditions = str(statement.whereclause).lower()

    assert "construction_invitations.tenant_id =" in conditions
    assert "construction_invitations.id =" in conditions
    assert tenant_id in compiled.params.values()
    assert invitation_id in compiled.params.values()
    assert "for update" in sql
    assert statement.get_execution_options().get("populate_existing") is True
    assert "construction_invitations.status" not in conditions
