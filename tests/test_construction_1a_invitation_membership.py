from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import (
    Column, DateTime, JSON, MetaData, Table, Text, UniqueConstraint,
    create_engine, func, select, text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import User, UserRoleMapping
from database.repositories.user import (
    PlatformInvitationMembershipConflictError,
    PlatformInvitationMembershipUnavailableError,
    UserRepository,
)


@pytest.fixture
def membership():
    metadata = MetaData()
    users = Table(
        "users", metadata,
        Column("id", User.__table__.c.id.type, primary_key=True),
        Column("tenant_id", User.__table__.c.tenant_id.type, nullable=False),
        Column("status", Text, nullable=False),
    )
    roles = Table(
        "user_roles", metadata,
        Column("id", UserRoleMapping.__table__.c.id.type, primary_key=True),
        Column("user_id", UserRoleMapping.__table__.c.user_id.type, nullable=False),
        Column("tenant_id", UserRoleMapping.__table__.c.tenant_id.type, nullable=False),
        Column("role", Text, nullable=False),
        Column("status", Text, nullable=False),
        Column("granted_by", UserRoleMapping.__table__.c.granted_by.type),
        Column("granted_at", DateTime(timezone=True), nullable=False, default=func.now()),
        Column("revoked_by", UserRoleMapping.__table__.c.granted_by.type),
        Column("revoked_at", DateTime(timezone=True)),
        Column("metadata", JSON, nullable=False, default=dict, server_default=text("'{}'")),
        UniqueConstraint("user_id", "tenant_id", "role"),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    session = Session(engine)
    state = SimpleNamespace(
        session=session, users=users, roles=roles,
        user_id=uuid4(), tenant_id=uuid4(), home_tenant_id=uuid4(),
        statements=[], before_insert=None, fail_insert=False,
    )
    session.execute(users.insert().values(
        id=state.user_id, tenant_id=state.home_tenant_id, status="active",
    ))
    session.commit()
    session.begin()

    class Adapter:
        def __init__(self):
            self.commit = AsyncMock(side_effect=AssertionError("Unexpected commit"))
            self.rollback = AsyncMock(side_effect=AssertionError("Unexpected rollback"))
            self.flush = AsyncMock(side_effect=AssertionError("Unexpected flush"))

        def in_transaction(self):
            return session.in_transaction()

        async def execute(self, statement):
            state.statements.append(statement)
            if statement.is_insert:
                callback, state.before_insert = state.before_insert, None
                if callback is not None:
                    callback()
                if state.fail_insert:
                    raise RuntimeError("simulated database failure")
            return session.execute(statement)

    state.adapter = Adapter()
    state.repository = UserRepository(state.adapter)
    try:
        yield state
    finally:
        session.close()
        engine.dispose()


def add_role(state, *, role="client", status="active", **overrides):
    fields = {
        "id": uuid4(), "user_id": state.user_id, "tenant_id": state.tenant_id,
        "role": role, "status": status, "granted_by": uuid4(),
        "metadata": {"keep": "existing-platform-metadata"},
    }
    fields.update(overrides)
    state.session.execute(state.roles.insert().values(**fields))


def rows(state):
    return [
        dict(row) for row in state.session.execute(
            select(state.roles).order_by(state.roles.c.id)
        ).mappings().all()
    ]


def user_row(state):
    return dict(state.session.execute(select(state.users)).mappings().one())


def lifecycle_unchanged(state):
    state.adapter.commit.assert_not_awaited()
    state.adapter.rollback.assert_not_awaited()
    state.adapter.flush.assert_not_awaited()


async def ensure(state, **overrides):
    arguments = {"user_id": state.user_id, "tenant_id": state.tenant_id}
    arguments.update(overrides)
    return await state.repository.ensure_invitation_tenant_membership(**arguments)


@pytest.mark.asyncio
async def test_missing_membership_creates_only_platform_client(membership):
    state = membership
    original_user = user_row(state)
    assert await ensure(state) is True
    stored = rows(state)
    assert len(stored) == 1
    item = stored[0]
    assert item["user_id"] == state.user_id
    assert item["tenant_id"] == state.tenant_id
    assert item["role"] == "client" and item["status"] == "active"
    assert item["granted_by"] is None
    assert item["granted_at"] is not None
    assert item["revoked_by"] is None and item["revoked_at"] is None
    assert item["metadata"] == {}
    assert user_row(state) == original_user
    lifecycle_unchanged(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["client", "specialist", "admin"])
async def test_active_membership_is_reused_without_client_duplicate(membership, role):
    state = membership
    add_role(state, role=role)
    before = deepcopy(rows(state))
    assert await ensure(state) is False
    assert rows(state) == before
    assert not any(item.is_insert for item in state.statements)
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_existing_home_tenant_membership_is_reused(membership):
    state = membership
    state.session.execute(state.users.update().values(tenant_id=state.tenant_id))
    assert await ensure(state) is False
    assert rows(state) == []
    lifecycle_unchanged(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["suspended", "revoked"])
@pytest.mark.parametrize("home_matches", [False, True])
@pytest.mark.parametrize("other_active", [False, True])
async def test_blocked_client_wins_over_other_membership(
    membership, status, home_matches, other_active,
):
    state = membership
    add_role(state, status=status)
    if home_matches:
        state.session.execute(state.users.update().values(tenant_id=state.tenant_id))
    if other_active:
        add_role(state, role="specialist")
    before_roles, before_user = deepcopy(rows(state)), user_row(state)
    with pytest.raises(PlatformInvitationMembershipConflictError) as caught:
        await ensure(state)
    assert caught.value.status_code == 409
    assert caught.value.code == "PLATFORM_MEMBERSHIP_CONFLICT"
    assert rows(state) == before_roles
    assert user_row(state) == before_user
    assert not any(item.is_insert for item in state.statements)
    lifecycle_unchanged(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["suspended", "revoked"])
async def test_inactive_other_role_is_preserved_when_client_is_created(membership, status):
    state = membership
    add_role(state, role="specialist", status=status)
    original = deepcopy(rows(state)[0])
    assert await ensure(state) is True
    stored = rows(state)
    assert len(stored) == 2
    assert next(item for item in stored if item["id"] == original["id"]) == original
    assert next(item for item in stored if item["role"] == "client")["status"] == "active"
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_memberships_of_other_tenants_and_users_are_untouched(membership):
    state = membership
    add_role(state, status="revoked", tenant_id=uuid4())
    add_role(state, status="suspended", user_id=uuid4())
    original = deepcopy(rows(state))
    assert await ensure(state) is True
    by_id = {item["id"]: item for item in rows(state)}
    assert len(by_id) == 3
    assert all(by_id[item["id"]] == item for item in original)
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_repeated_operation_does_not_duplicate_client(membership):
    state = membership
    assert await ensure(state) is True
    assert await ensure(state) is False
    assert len(rows(state)) == 1
    lifecycle_unchanged(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "suspended", "revoked"])
async def test_unique_conflict_rechecks_client_and_never_restores_it(membership, status):
    state = membership
    state.before_insert = lambda: add_role(state, status=status)
    if status == "active":
        assert await ensure(state) is False
    else:
        with pytest.raises(PlatformInvitationMembershipConflictError):
            await ensure(state)
    stored = rows(state)
    assert len(stored) == 1 and stored[0]["status"] == status
    assert stored[0]["metadata"] == {"keep": "existing-platform-metadata"}
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_caller_rollback_removes_new_membership(membership):
    state = membership
    assert await ensure(state) is True
    assert len(rows(state)) == 1
    state.session.rollback()
    assert rows(state) == []
    lifecycle_unchanged(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["missing", "blocked", "restricted", "deleted", "pending"])
async def test_unavailable_platform_user_cannot_get_membership(membership, status):
    state = membership
    if status == "missing":
        state.session.execute(state.users.delete())
    else:
        state.session.execute(state.users.update().values(status=status))
    with pytest.raises(PlatformInvitationMembershipUnavailableError):
        await ensure(state)
    assert rows(state) == []
    assert len(state.statements) == 1
    lifecycle_unchanged(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("argument", ["user_id", "tenant_id"])
async def test_untrusted_uuid_arguments_do_not_query_database(membership, argument):
    state = membership
    with pytest.raises(ValueError, match="UUIDs"):
        await ensure(state, **{argument: str(uuid4())})
    assert state.statements == []
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_operation_requires_existing_transaction(membership):
    state = membership
    state.session.commit()
    with pytest.raises(ValueError, match="active transaction"):
        await ensure(state)
    assert state.statements == []
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_queries_lock_user_and_exact_tenant_roles_and_insert_without_update(membership):
    state = membership
    assert await ensure(state) is True
    user_query, role_query, insertion = state.statements
    user_sql = str(user_query.compile(dialect=postgresql.dialect()))
    role_sql = str(role_query.compile(dialect=postgresql.dialect()))
    insert_sql = str(insertion.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in user_sql and "FOR UPDATE" in role_sql
    assert set(user_query.compile().params.values()) == {state.user_id}
    assert set(role_query.compile().params.values()) == {state.user_id, state.tenant_id}
    assert {column.key for column in user_query.selected_columns} == {
        "id", "tenant_id", "status",
    }
    assert {column.key for column in role_query.selected_columns} == {
        "id", "role", "status",
    }
    assert "ON CONFLICT (user_id, tenant_id, role) DO NOTHING" in insert_sql
    assert "RETURNING user_roles.id" in insert_sql
    assert not any(item.is_update or item.is_delete for item in state.statements)
    lifecycle_unchanged(state)


@pytest.mark.asyncio
async def test_database_failure_is_left_to_outer_transaction(membership):
    state = membership
    state.fail_insert = True
    with pytest.raises(RuntimeError, match="simulated"):
        await ensure(state)
    assert rows(state) == []
    lifecycle_unchanged(state)
