from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_hr_repository_lists_active_candidates_by_tenant():
    from sqlalchemy.dialects import postgresql

    from database.repositories.hr import (
        HrRepository,
    )

    tenant_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = HrRepository(session)

    result = await repository.list_active_candidates(
        tenant_id=tenant_id,
        limit=20,
        offset=0,
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()

    normalized_sql = " ".join(sql.split())

    required_scope = (
        "seekers.tenant_id",
        "seekers.status = 'active'",
        "seekers.created_at desc",
        "limit 20",
        "offset 0",
    )

    for value in required_scope:
        assert value in normalized_sql
