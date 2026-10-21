"""Local SQL/ORM behavior; native RLS and concurrency acceptance are separate."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, String, Table, Uuid, create_engine, event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from database.models import RoleScope, User, UserRoleMapping
from database.repositories.platform_membership import PlatformMembershipRepository as Repository


class Bridge:
    def __init__(self, session):
        self.raw, self.statements = session, []
        for name in ('commit', 'rollback', 'flush', 'add', 'delete'):
            setattr(self, name, AsyncMock(side_effect=AssertionError('Caller owns the transaction.')))

    @property
    def no_autoflush(self):
        return self.raw.no_autoflush

    def in_transaction(self):
        return self.raw.in_transaction()

    async def execute(self, statement):
        self.statements.append(statement)
        if 'platform_organization_permission_grants' not in str(statement):
            return self.raw.execute(statement)
        result = self.raw.execute(statement.execution_options(schema_translate_map={'public': None}))
        def values():
            for row in result.mappings().all():
                item = dict(row)
                for name in ('granted_at', 'revoked_at'):
                    if item.get(name) is not None and item[name].tzinfo is None:
                        item[name] = item[name].replace(tzinfo=timezone.utc)
                yield item
        data = list(values())
        return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:data,
            one=lambda:data[0] if len(data)==1 else (_ for _ in ()).throw(AssertionError('Expected one row.'))))


@pytest.fixture
def membership_store():
    metadata = MetaData()
    for name in ('tenants', 'countries', 'cities', 'root_identities'):
        Table(name, metadata, Column('id', Uuid, primary_key=True))
    Table('languages', metadata, Column('code', String(10), primary_key=True))
    for model in (User, UserRoleMapping, RoleScope):
        table = model.__table__.to_metadata(metadata)
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    from database.repositories.platform_organization_permissions import organization_permission_grants
    organization_permission_grants.to_metadata(metadata, schema=None)
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    a, b, user_id, other_id, actor_id = (uuid4() for _ in range(5))
    with engine.begin() as connection:
        connection.execute(metadata.tables['tenants'].insert(), [{'id': a}, {'id': b}])
    with Session(engine, expire_on_commit=False) as session:
        user = User(id=user_id, tenant_id=a, active_role='specialist', language_code='uk',
            status='active', extra_metadata={'preserve': True})
        session.add_all([user, User(id=other_id, tenant_id=a), User(id=actor_id, tenant_id=b)])
        session.flush()
        def role(tenant, name, status='active', owner=user_id):
            return UserRoleMapping(id=uuid4(), tenant_id=tenant, user_id=owner, role=name,
                status=status, granted_by=actor_id)
        roles = dict(client=role(a, 'client'), specialist=role(a, 'specialist'),
            suspended=role(a, 'observer', 'suspended'), historical=role(a, 'employer', 'revoked'),
            foreign=role(b, 'client'), global_role=role(None, 'admin'), other=role(a, 'client', owner=other_id))
        session.add_all(roles.values())
        session.flush()
        def scope(parent, *, tenant=None, owner=None, status='active', name=None):
            return RoleScope(id=uuid4(), tenant_id=tenant or parent.tenant_id or a,
                user_id=owner or parent.user_id, user_role_id=parent.id,
                role=name or parent.role, scope_type='tenant', status=status)
        scopes = {name: scope(row) for name, row in roles.items()}
        scopes['already_revoked'] = scope(roles['client'], status='revoked')
        scopes['mismatched_role'] = scope(roles['client'], name='agency_user')
        session.add_all(scopes.values())
        session.commit()
        bridge = Bridge(session)
        session.begin()
        yield SimpleNamespace(session=session, bridge=bridge, repo=Repository(bridge),
            a=a, b=b, user_id=user_id, other_id=other_id, actor_id=actor_id,
            user=user, roles=roles, scopes=scopes)
        session.rollback()
    engine.dispose()


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))


def caller_owns_transaction(c):
    for name in ('commit', 'rollback', 'flush', 'add', 'delete'):
        getattr(c.bridge, name).assert_not_called()


async def revoke(c):
    return await c.repo.revoke_active_tenant_roles(user_id=c.user_id, tenant_id=c.a)


@pytest.mark.asyncio
async def test_user_lock_refreshes_canonical_state_without_filtering_inactive_users(membership_store):
    c = membership_store
    c.session.execute(update(User).where(User.id == c.user_id).values(status='blocked')
        .execution_options(synchronize_session=False))
    assert c.user.status == 'active'
    assert await c.repo.get_user_for_update(user_id=c.user_id) is c.user
    assert c.user.status == 'blocked'
    statement = c.bridge.statements[-1]
    assert 'FOR UPDATE' in sql(statement)
    assert statement.get_execution_options()['populate_existing'] is True
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_role_lock_targets_one_user_tenant_and_keeps_all_historical_states(membership_store):
    c = membership_store
    rows = await c.repo.list_tenant_roles_for_update(user_id=c.user_id, tenant_id=c.a)
    assert {row.id for row in rows} == {c.roles[name].id for name in ('client', 'specialist', 'suspended', 'historical')}
    assert [row.id.int for row in rows] == sorted(row.id.int for row in rows)
    assert 'FROM users' in sql(c.bridge.statements[0])
    assert all('FOR UPDATE' in sql(statement) for statement in c.bridge.statements)
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_revoke_preserves_history_global_foreign_and_other_users_roles(membership_store):
    c = membership_store
    before = {name: (row.status, row.granted_by, row.granted_at, row.tenant_id) for name, row in c.roles.items()}
    rows = await revoke(c)
    assert {row.id for row in rows} == {c.roles['client'].id, c.roles['specialist'].id}
    assert all(row.status == 'revoked' for row in rows)
    c.session.flush()
    for name, row in c.roles.items():
        expected = 'revoked' if name in ('client', 'specialist') else before[name][0]
        assert (row.status, row.granted_by, row.granted_at, row.tenant_id) == (expected, *before[name][1:])
    assert c.user.status == 'active' and c.user.extra_metadata == {'preserve': True}
    mutation = sql(c.bridge.statements[-1])
    assert mutation.split(' SET ', 1)[1].split(' WHERE ', 1)[0] == "status='revoked'"
    assert str(c.a) in mutation and str(c.user_id) in mutation and "status = 'active'" in mutation
    assert 'RETURNING user_roles.id' in mutation
    assert await revoke(c) == ()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_scope_revoke_requires_matching_revoked_parent_and_preserves_all_other_scopes(membership_store):
    c = membership_store
    await revoke(c)
    instant = datetime(2026, 10, 16, 10, tzinfo=timezone(timedelta(hours=3)))
    rows = await c.repo.revoke_active_scopes_of_revoked_tenant_roles(user_id=c.user_id,
        tenant_id=c.a, revoked_by_user_id=c.actor_id, as_of=instant)
    changed = {'client', 'specialist', 'historical'}
    assert {row.id for row in rows} == {c.scopes[name].id for name in changed}
    for name, row in c.scopes.items():
        if name in changed:
            assert row.status == 'revoked' and row.revoked_by == c.actor_id
            assert row.revoked_at == datetime(2026, 10, 16, 7)
        else:
            assert row.status == ('revoked' if name == 'already_revoked' else 'active')
            assert row.revoked_by is None and row.revoked_at is None
    mutation = sql(c.bridge.statements[-1])
    assert 'EXISTS (SELECT user_roles.id' in mutation
    for predicate in ('user_roles.id = role_scopes.user_role_id', 'user_roles.role = role_scopes.role',
            "user_roles.status = 'revoked'", 'role_scopes.tenant_id =', 'role_scopes.user_id ='):
        assert predicate in mutation
    assert await c.repo.revoke_active_scopes_of_revoked_tenant_roles(user_id=c.user_id,
        tenant_id=c.a, revoked_by_user_id=c.actor_id, as_of=instant) == ()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_primary_tenant_clear_is_conditional_and_does_not_choose_a_replacement(membership_store):
    c = membership_store
    assert await c.repo.clear_primary_tenant_if_matches(user_id=c.user_id, tenant_id=c.b) is None
    assert c.user.tenant_id == c.a
    assert await c.repo.clear_primary_tenant_if_matches(user_id=c.user_id, tenant_id=c.a) is c.user
    assert c.user.tenant_id is None and c.user.active_role == 'specialist'
    assert c.user.status == 'active' and c.user.language_code == 'uk'
    assert await c.repo.clear_primary_tenant_if_matches(user_id=c.user_id, tenant_id=c.a) is None
    assignments = sql(c.bridge.statements[-1]).split(' SET ', 1)[1].split(' WHERE ', 1)[0]
    assert assignments == 'tenant_id=NULL'
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('selected,expected', [('specialist', None), ('client', 'client'),
    ('admin', 'admin'), ('observer', None), (None, None)])
async def test_active_role_is_cleared_only_when_no_remaining_assignment_supports_it(membership_store, selected, expected):
    c = membership_store
    c.user.active_role = selected
    c.session.flush()
    await revoke(c)
    row = await c.repo.clear_invalid_active_role(user_id=c.user_id)
    assert c.user.active_role == expected
    assert (row is c.user) == (selected != expected)
    mutation = sql(c.bridge.statements[-1])
    assert 'NOT (EXISTS' in mutation and 'user_roles.role = users.active_role' in mutation
    assert 'user_roles.tenant_id =' not in mutation
    assert await c.repo.clear_invalid_active_role(user_id=c.user_id) is None
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_local_rollback_restores_roles_scopes_primary_tenant_and_selection(membership_store):
    c = membership_store
    await revoke(c)
    await c.repo.revoke_active_scopes_of_revoked_tenant_roles(user_id=c.user_id, tenant_id=c.a,
        revoked_by_user_id=c.actor_id, as_of=datetime.now(timezone.utc))
    await c.repo.clear_primary_tenant_if_matches(user_id=c.user_id, tenant_id=c.a)
    await c.repo.clear_invalid_active_role(user_id=c.user_id)
    c.session.rollback()
    c.session.expire_all()
    assert c.user.tenant_id == c.a and c.user.active_role == 'specialist'
    assert c.roles['client'].status == c.roles['specialist'].status == 'active'
    assert c.scopes['client'].status == 'active' and c.scopes['client'].revoked_by is None
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['get_user_for_update', 'list_tenant_roles_for_update',
    'revoke_active_tenant_roles', 'clear_primary_tenant_if_matches', 'clear_invalid_active_role',
    'revoke_active_scopes_of_revoked_tenant_roles'])
async def test_missing_user_is_a_no_write_result(membership_store, method):
    c = membership_store
    values = dict(user_id=uuid4())
    if method not in ('get_user_for_update', 'clear_invalid_active_role'):
        values['tenant_id'] = c.a
    if method == 'revoke_active_scopes_of_revoked_tenant_roles':
        values.update(revoked_by_user_id=c.actor_id, as_of=datetime.now(timezone.utc))
    result = await getattr(c.repo, method)(**values)
    assert result == () if method.startswith(('list_', 'revoke_')) else result is None
    assert len(c.bridge.statements) == 1 and c.bridge.statements[0].is_select
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('user_id', None), ('user_id', 'untrusted'), ('tenant_id', None),
    ('tenant_id', True), ('revoked_by_user_id', 'untrusted'), ('as_of', None),
    ('as_of', datetime(2026, 10, 16)), ('as_of', '2026-10-16')])
async def test_invalid_scope_context_is_rejected_before_any_sql(membership_store, field, value):
    c = membership_store
    values = dict(user_id=c.user_id, tenant_id=c.a, revoked_by_user_id=c.actor_id, as_of=datetime.now(timezone.utc))
    values[field] = value
    with pytest.raises(ValueError):
        await c.repo.revoke_active_scopes_of_revoked_tenant_roles(**values)
    assert c.bridge.statements == []
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_caller_transaction_required_and_db_failure_is_not_hidden(membership_store):
    c = membership_store
    c.session.rollback()
    with pytest.raises(ValueError):
        await c.repo.revoke_active_tenant_roles(user_id=c.user_id, tenant_id=c.a)
    assert c.bridge.statements == []
    c.session.begin()
    error = RuntimeError('storage failure')
    c.bridge.execute = AsyncMock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        await c.repo.clear_primary_tenant_if_matches(user_id=c.user_id, tenant_id=c.a)
    assert caught.value is error
    caller_owns_transaction(c)


@pytest.mark.parametrize('session', [None, object(), SimpleNamespace(execute=AsyncMock())])
def test_invalid_repository_wiring_is_rejected(session):
    with pytest.raises(ValueError):
        Repository(session)
