from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, Column, MetaData, Table, Text, Uuid, create_engine, event, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import PlatformTelegramBindingToken as Token
from database.repositories.platform_telegram_binding_tokens import PlatformTelegramBindingTokenRepository as Repository
from services.platform_telegram_binding_tokens import issue_platform_telegram_binding_token


NOW = datetime(2026, 10, 14, 12, 34, 56, 123456, tzinfo=timezone.utc)


class Bridge:
    """Execute real repository SQL on a synchronous SQLite test connection.

    SQLite does not implement PostgreSQL row locks or RLS. Native locking SQL
    is compiled separately; concurrent PostgreSQL behavior remains untested.
    """
    def __init__(self, session):
        self.session, self.statements, self.flushes = session, [], 0
        self.added = []

    @property
    def no_autoflush(self):
        return self.session.no_autoflush

    def add(self, record):
        self.added.append(record)
        self.session.add(record)

    async def flush(self):
        self.flushes += 1
        self.session.flush()

    async def execute(self, statement):
        self.statements.append(statement)
        return self.session.execute(statement)

    async def commit(self):
        raise AssertionError('Repository must leave commit to caller.')

    async def rollback(self):
        raise AssertionError('Repository must leave rollback to caller.')


@pytest.fixture
def store():
    metadata = MetaData()
    users = Table('users', metadata, Column('id', Uuid, primary_key=True), Column('status', Text, nullable=False))
    tenants = Table('tenants', metadata, Column('id', Uuid, primary_key=True))
    table = Token.__table__.to_metadata(metadata)
    # Explicit local adapters for the two PostgreSQL-only checks. These do not
    # establish native PostgreSQL constraint, concurrency or RLS acceptance.
    for constraint in list(table.constraints):
        if isinstance(constraint, CheckConstraint) and constraint.name.endswith(('_hash', '_ttl')):
            table.constraints.remove(constraint)
    table.append_constraint(CheckConstraint("length(token_hash)=64 AND token_hash NOT GLOB '*[^0-9a-f]*'"))
    table.append_constraint(CheckConstraint("abs((julianday(expires_at)-julianday(issued_at))*86400-900)<0.001"))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    owner, other, blocked = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(insert(users), [{'id': owner, 'status': 'active'}, {'id': other, 'status': 'active'},
            {'id': blocked, 'status': 'blocked'}])
        connection.execute(insert(tenants), [{'id': owner}, {'id': other}, {'id': blocked}])
    with Session(engine, expire_on_commit=False) as session:
        bridge = Bridge(session)
        yield Repository(bridge), bridge, owner, other, blocked, table
        session.rollback()
    engine.dispose()


def creation(owner, **changes):
    material = issue_platform_telegram_binding_token(as_of=NOW)
    result = dict(token_id=uuid4(), user_id=owner, tenant_id=owner, initiated_by_user_id=owner, token_hash=material.token_hash,
        issued_at=material.issued_at, expires_at=material.expires_at)
    result.update(changes)
    return result


def mutation(record, **changes):
    result = dict(token_id=record.id, user_id=record.user_id, tenant_id=record.tenant_id, expected_row_version=record.row_version,
        as_of=NOW+timedelta(minutes=1))
    result.update(changes)
    return result


@pytest.mark.asyncio
async def test_create_hash_only_normalizes_utc_and_does_not_commit(store):
    repo, bridge, owner, *_ = store
    material = issue_platform_telegram_binding_token(as_of=NOW)
    offset = timezone(timedelta(hours=3))
    row = await repo.create_token(**creation(owner, token_hash=material.token_hash,
        issued_at=NOW.astimezone(offset), expires_at=(NOW+timedelta(minutes=15)).astimezone(offset)))
    assert row.row_version == 1 and row.issued_at == NOW and row.expires_at == NOW+timedelta(minutes=15)
    assert row.token_hash == material.token_hash and row.consumed_at is None and row.revoked_at is None
    assert not {'token', 'code', 'deep_link', 'platform_user_id'} & vars(row).keys()
    assert bridge.flushes == 1 and bridge.session.in_transaction()
    bridge.session.rollback()
    assert bridge.session.execute(select(Token.id)).first() is None


@pytest.mark.asyncio
async def test_lock_anchor_requires_existing_active_platform_user(store):
    repo, bridge, owner, other, blocked, _ = store
    assert await repo.lock_active_user(user_id=owner) == owner
    assert await repo.lock_active_user(user_id=other) == other
    assert await repo.lock_active_user(user_id=blocked) is None
    assert await repo.lock_active_user(user_id=uuid4()) is None
    assert 'FOR UPDATE' in str(bridge.statements[0].compile(dialect=postgresql.dialect()))
    assert bridge.added == [] and bridge.flushes == 0


@pytest.mark.asyncio
async def test_owner_and_hash_lookups_use_locks_and_refresh_cached_state(store):
    repo, bridge, owner, other, _, table = store
    row = await repo.create_token(**creation(owner))
    assert await repo.get_token_for_update(token_id=row.id, user_id=other, tenant_id=owner) is None
    assert await repo.get_token_for_update(token_id=uuid4(), user_id=owner, tenant_id=owner) is None
    assert await repo.get_token_by_hash_for_update(token_hash='0'*64) is None
    for statement in bridge.statements:
        assert 'FOR UPDATE' in str(statement.compile(dialect=postgresql.dialect()))
        assert statement.get_execution_options()['populate_existing'] is True
    bridge.session.execute(update(table).where(table.c.id == row.id).values(row_version=2,
        consumed_at=NOW+timedelta(seconds=1)))
    assert row.row_version == 1
    loaded = await repo.get_token_for_update(token_id=row.id, user_id=owner, tenant_id=owner)
    assert loaded is row and loaded.row_version == 2 and loaded.consumed_at is not None
    loaded = await repo.get_token_by_hash_for_update(token_hash=row.token_hash)
    assert loaded is row


@pytest.mark.asyncio
async def test_lookup_does_not_flush_unrelated_pending_changes(store):
    repo, bridge, owner, *_ = store
    row = await repo.create_token(**creation(owner))
    pending = Token(id=uuid4(), user_id=owner, tenant_id=owner, initiated_by_user_id=owner, token_hash=row.token_hash,
        issued_at=NOW, expires_at=NOW+timedelta(minutes=15), updated_at=NOW, row_version=1)
    bridge.session.add(pending)
    assert await repo.get_token_for_update(token_id=row.id, user_id=owner, tenant_id=owner) is row
    assert pending in bridge.session.new and bridge.flushes == 1


@pytest.mark.asyncio
async def test_consume_is_conditional_single_use_and_refreshes_loaded_object(store):
    repo, bridge, owner, *_ = store
    row = await repo.create_token(**creation(owner))
    arguments = mutation(row)
    token_hash = row.token_hash
    consumed = await repo.consume_token(**arguments, token_hash=token_hash)
    assert consumed is row and row.row_version == 2 and row.consumed_at is not None and row.revoked_at is None
    assert await repo.consume_token(**arguments, token_hash=token_hash) is None
    assert await repo.revoke_token(**mutation(row)) is None
    statement = bridge.statements[-3]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    for column in ('id', 'user_id', 'token_hash', 'row_version', 'issued_at', 'expires_at', 'consumed_at', 'revoked_at'):
        assert 'platform_telegram_binding_tokens.'+column in sql.split(' WHERE ')[1].split(' RETURNING ')[0]
    assert 'RETURNING ' in sql and 'row_version=(platform_telegram_binding_tokens.row_version +' in sql
    assert statement.get_execution_options()['populate_existing'] is True
    assert bridge.flushes == 1


@pytest.mark.asyncio
async def test_revoke_blocks_consumption_and_repeat_without_an_extra_version_change(store):
    repo, _, owner, *_ = store
    row = await repo.create_token(**creation(owner))
    arguments, token_hash = mutation(row), row.token_hash
    revoked = await repo.revoke_token(**arguments)
    assert revoked is row and row.row_version == 2 and row.revoked_at is not None and row.consumed_at is None
    assert await repo.consume_token(**mutation(row), token_hash=token_hash) is None
    assert await repo.revoke_token(**arguments) is None
    assert row.row_version == 2


@pytest.mark.parametrize('operation', ['consume', 'revoke'])
@pytest.mark.parametrize('fault', ['wrong_owner', 'wrong_id', 'stale_version', 'future_issue', 'exact_expiry', 'after_expiry'])
@pytest.mark.asyncio
async def test_failed_condition_does_not_mutate_state_or_version(store, operation, fault):
    repo, bridge, owner, other, *_ = store
    row = await repo.create_token(**creation(owner))
    arguments = mutation(row)
    if fault == 'wrong_owner': arguments['user_id'] = other
    if fault == 'wrong_id': arguments['token_id'] = uuid4()
    if fault == 'stale_version': arguments['expected_row_version'] = 2
    if fault == 'future_issue': arguments['as_of'] = NOW-timedelta(microseconds=1)
    if fault == 'exact_expiry': arguments['as_of'] = NOW+timedelta(minutes=15)
    if fault == 'after_expiry': arguments['as_of'] = NOW+timedelta(hours=1)
    if operation == 'consume': arguments['token_hash'] = row.token_hash
    assert await getattr(repo, operation+'_token')(**arguments) is None
    bridge.session.expire(row)
    assert row.row_version == 1 and row.consumed_at is None and row.revoked_at is None


@pytest.mark.asyncio
async def test_wrong_hash_cannot_consume_even_with_correct_owner_and_version(store):
    repo, bridge, owner, *_ = store
    row = await repo.create_token(**creation(owner))
    assert await repo.consume_token(**mutation(row), token_hash='0'*64) is None
    bridge.session.expire(row)
    assert row.row_version == 1 and row.consumed_at is None


@pytest.mark.asyncio
async def test_caller_failure_rolls_back_consumption_and_version(store):
    repo, bridge, owner, *_ = store
    row = await repo.create_token(**creation(owner))
    bridge.session.commit()  # Caller owns the original committed checkpoint.
    row_id = row.id
    try:
        assert await repo.consume_token(**mutation(row), token_hash=row.token_hash) is row
        raise RuntimeError('Simulated mandatory audit failure in caller.')
    except RuntimeError:
        bridge.session.rollback()
    restored = await repo.get_token_for_update(token_id=row_id, user_id=owner, tenant_id=owner)
    assert restored.row_version == 1 and restored.consumed_at is None and restored.revoked_at is None


@pytest.mark.asyncio
async def test_duplicate_hash_error_is_propagated_and_rollback_remains_callers_responsibility(store):
    repo, bridge, owner, other, *_ = store
    row = await repo.create_token(**creation(owner))
    with pytest.raises(IntegrityError):
        await repo.create_token(**creation(other, token_hash=row.token_hash))
    assert not bridge.session.is_active
    bridge.session.rollback()
    assert bridge.session.execute(select(Token.id)).first() is None


@pytest.mark.parametrize('changes', [{'token_id': None}, {'user_id': 'private-invalid-id'}, {'token_hash': 'private-secret'},
    {'token_hash': 'G'*64}, {'issued_at': NOW.replace(tzinfo=None)}, {'expires_at': None},
    {'expires_at': NOW+timedelta(minutes=16)}, {'issued_at': datetime.max.replace(tzinfo=timezone.utc)},
    {'tenant_id': None}, {'tenant_id': 'private-invalid-tenant'}, {'initiated_by_user_id': None},
    {'initiated_by_user_id': 'private-invalid-initiator'}])
@pytest.mark.asyncio
async def test_creation_validates_before_adding_or_executing(store, changes):
    repo, bridge, owner, *_ = store
    with pytest.raises(ValueError) as error:
        await repo.create_token(**creation(owner, **changes))
    assert 'private-' not in str(error.value)
    assert bridge.added == [] and bridge.statements == [] and bridge.flushes == 0


@pytest.mark.parametrize('operation', ['consume', 'revoke'])
@pytest.mark.parametrize('changes', [{'token_id': None}, {'user_id': 'private-invalid-id'},
    {'expected_row_version': True}, {'expected_row_version': 0}, {'expected_row_version': -1},
    {'expected_row_version': 2**63}, {'expected_row_version': 1.0}, {'as_of': None}, {'as_of': NOW.replace(tzinfo=None)}])
@pytest.mark.asyncio
async def test_mutation_rejects_invalid_arguments_before_any_query(store, operation, changes):
    repo, bridge, owner, *_ = store
    arguments = dict(token_id=uuid4(), user_id=owner, tenant_id=owner, expected_row_version=1, as_of=NOW)
    arguments.update(changes)
    if operation == 'consume': arguments['token_hash'] = '0'*64
    with pytest.raises(ValueError):
        await getattr(repo, operation+'_token')(**arguments)
    assert bridge.statements == [] and bridge.added == [] and bridge.flushes == 0


@pytest.mark.asyncio
async def test_no_raw_secret_parameter_is_accepted_for_persistence(store):
    repo, bridge, owner, *_ = store
    with pytest.raises(TypeError):
        await repo.create_token(**creation(owner), token='must-not-be-stored')
    assert bridge.added == [] and bridge.statements == []
