from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from database.models import UserRoleMapping
from database.repositories.user import UserRepository


@pytest.fixture
def role_store():
    metadata = MetaData()
    original = UserRoleMapping.__table__
    table = Table(
        original.name,
        metadata,
        *[
            Column(
                column.name,
                column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in original.columns
        ],
        schema=original.schema,
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    session = Session(engine)

    class Adapter:
        async def execute(self, statement):
            return session.execute(statement)

        def add(self, instance):
            session.add(instance)

        async def flush(self):
            session.flush()

        async def commit(self):
            raise AssertionError("Repository must not commit.")

        async def rollback(self):
            raise AssertionError("Repository must not own rollback.")

    try:
        yield session, table, UserRepository(Adapter())
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_status", ["active", "revoked"])
async def test_foreign_tenant_role_is_not_reused_or_reactivated(
    role_store, foreign_status,
):
    session, table, repository = role_store
    user_id, tenant_id, foreign_tenant = uuid4(), uuid4(), uuid4()
    foreign = UserRoleMapping(
        id=uuid4(),
        user_id=user_id,
        tenant_id=foreign_tenant,
        role="client",
        status=foreign_status,
    )
    session.add(foreign)
    session.flush()

    changed = await repository.ensure_active_role(
        user_id=user_id,
        tenant_id=tenant_id,
        role="client",
    )

    assert changed is True
    rows = session.execute(
        select(table).where(table.c.user_id == user_id)
    ).mappings().all()
    assert len(rows) == 2

    by_tenant = {row["tenant_id"]: row for row in rows}
    assert by_tenant[foreign_tenant]["id"] == foreign.id
    assert by_tenant[foreign_tenant]["status"] == foreign_status
    assert by_tenant[tenant_id]["status"] == "active"
    assert by_tenant[tenant_id]["role"] == "client"
    assert by_tenant[tenant_id]["id"] != foreign.id


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status", ["active", "revoked"])
async def test_existing_target_role_preserves_platform_behavior(
    role_store, target_status,
):
    session, table, repository = role_store
    user_id, tenant_id, foreign_tenant = uuid4(), uuid4(), uuid4()
    target = UserRoleMapping(
        id=uuid4(),
        user_id=user_id,
        tenant_id=tenant_id,
        role="client",
        status=target_status,
    )
    foreign = UserRoleMapping(
        id=uuid4(),
        user_id=user_id,
        tenant_id=foreign_tenant,
        role="client",
        status="revoked",
    )
    session.add_all([target, foreign])
    session.flush()

    changed = await repository.ensure_active_role(
        user_id=user_id,
        tenant_id=tenant_id,
        role="client",
    )

    assert changed is (target_status != "active")
    rows = session.execute(
        select(table).where(table.c.user_id == user_id)
    ).mappings().all()
    assert len(rows) == 2

    by_tenant = {row["tenant_id"]: row for row in rows}
    assert by_tenant[tenant_id]["id"] == target.id
    assert by_tenant[tenant_id]["status"] == "active"
    assert by_tenant[foreign_tenant]["id"] == foreign.id
    assert by_tenant[foreign_tenant]["status"] == "revoked"
