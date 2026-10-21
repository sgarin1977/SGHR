from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.user import UserRepository


@pytest.mark.asyncio
async def test_c004_identity_reader_checks_tenant_and_email_proof():
    tenant_id, user_id = uuid4(), uuid4()
    statements = []

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    repository = UserRepository(Session())
    result = await repository.list_team_identity_accounts(
        tenant_id=tenant_id, user_ids=(user_id,),
    )
    assert result == []
    assert len(statements) == 1

    compiled = statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    values = list(compiled.params.values())

    assert tenant_id in values
    assert any(
        isinstance(value, (list, tuple)) and set(value) == {user_id}
        for value in values
    )
    for column in (
        "user_accounts.user_id",
        "users.status",
        "user_roles.tenant_id",
        "user_roles.status",
        "event_logs.user_id",
        "event_logs.entity_id",
        "api_email_auth_challenges.email",
        "api_email_auth_challenges.used_at",
    ):
        assert column in sql
    assert "api_email_auth_succeeded" in values
    assert "used" in values
    assert "email" in values


@pytest.mark.asyncio
async def test_c004_empty_identity_lookup_does_not_query_database():
    class Session:
        async def execute(self, statement):
            pytest.fail("Empty user list must not query the database")

    repository = UserRepository(Session())
    assert await repository.list_team_identity_accounts(
        tenant_id=uuid4(), user_ids=(),
    ) == []
