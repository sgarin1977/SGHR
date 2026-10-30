from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, Column, MetaData, Table, Uuid, create_engine, event, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, registry
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateIndex, CreateTable

from database.models import Base, PlatformTelegramBindingToken, UserAccount
from services.platform_telegram_binding_tokens import issue_platform_telegram_binding_token


NOW = datetime(2026, 10, 14, 12, 34, 56, 123456, tzinfo=timezone.utc)


def test_storage_has_hash_only_and_references_canonical_platform_user():
    table = PlatformTelegramBindingToken.__table__
    assert table is Base.metadata.tables['platform_telegram_binding_tokens']
    assert set(table.c.keys()) == {'id', 'user_id', 'token_hash', 'issued_at', 'expires_at',
        'consumed_at', 'revoked_at', 'updated_at', 'row_version', 'tenant_id', 'initiated_by_user_id'}
    foreign_key, = table.c.user_id.foreign_keys
    assert foreign_key.target_fullname == 'users.id' and foreign_key.ondelete == 'CASCADE'
    assert not table.c.user_id.nullable
    assert UserAccount.__tablename__ == 'user_accounts'
    assert not {'platform_user_id', 'username', 'token', 'code', 'deep_link', 'ciphertext'} & set(table.c.keys())
    for name in ('issued_at', 'expires_at', 'consumed_at', 'revoked_at', 'updated_at'):
        assert table.c[name].type.timezone is True
    assert PlatformTelegramBindingToken.__mapper__.version_id_col is table.c.row_version


def test_native_postgresql_ddl_has_server_side_hash_ttl_and_uniqueness_guards():
    table = PlatformTelegramBindingToken.__table__
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "token_hash ~ '^[0-9a-f]{64}$'" in ddl
    assert "expires_at = issued_at + interval '15 minutes'" in ddl
    assert 'consumed_at >= issued_at AND consumed_at < expires_at' in ddl
    assert 'revoked_at IS NULL OR revoked_at >= issued_at' in ddl
    assert 'UNIQUE (token_hash)' in ddl and 'FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE' in ddl
    assert 'row_version > 0' in ddl and 'DEFAULT gen_random_uuid()' in ddl
    indexes = [str(CreateIndex(index).compile(dialect=postgresql.dialect())) for index in table.indexes]
    assert len(indexes) == 2 and all('CREATE INDEX ' in value for value in indexes)


@pytest.fixture
def local_store():
    # SQLite exercises persistence, rollback and SQLAlchemy optimistic locking.
    # These two translated checks are local adapters, not native PostgreSQL/RLS
    # acceptance. The production PostgreSQL DDL is compiled separately above.
    metadata = MetaData()
    users = Table('users', metadata, Column('id', Uuid, primary_key=True))
    Table('tenants', metadata, Column('id', Uuid, primary_key=True))
    table = PlatformTelegramBindingToken.__table__.to_metadata(metadata)
    for constraint in list(table.constraints):
        if isinstance(constraint, CheckConstraint) and constraint.name.endswith(('_hash', '_ttl')):
            table.constraints.remove(constraint)
    table.append_constraint(CheckConstraint("length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'"))
    table.append_constraint(CheckConstraint("abs((julianday(expires_at)-julianday(issued_at))*86400-900) < 0.001"))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(insert(users).values(id=user_id))
        connection.execute(insert(metadata.tables['tenants']).values(id=user_id))
    mapping = registry()
    class LocalToken:
        pass
    mapping.map_imperatively(LocalToken, table, version_id_col=table.c.row_version)
    try:
        yield engine, table, user_id, LocalToken
    finally:
        mapping.dispose()
        engine.dispose()


def values(user_id, /, **changes):
    material = issue_platform_telegram_binding_token(as_of=NOW)
    result = dict(id=uuid4(), user_id=user_id, tenant_id=user_id, initiated_by_user_id=user_id, token_hash=material.token_hash,
        issued_at=material.issued_at, expires_at=material.expires_at,
        consumed_at=None, revoked_at=None, updated_at=NOW, row_version=1)
    result.update(changes)
    return result


def test_roundtrip_persists_hash_without_secret_or_identity_copy(local_store):
    engine, table, user_id, _ = local_store
    material = issue_platform_telegram_binding_token(as_of=NOW)
    data = values(user_id, token_hash=material.token_hash)
    with engine.begin() as connection:
        connection.execute(insert(table).values(**data))
    with engine.connect() as connection:
        record = connection.execute(select(table)).mappings().one()
        assert record['token_hash'] == material.token_hash and record['user_id'] == user_id
        assert material.token not in repr(dict(record))
        assert record['row_version'] == 1 and record['consumed_at'] is None and record['revoked_at'] is None


@pytest.mark.parametrize('changes', [
    {'token_hash': ''}, {'token_hash': 'G'*64}, {'token_hash': 'a'*63},
    {'expires_at': NOW+timedelta(minutes=14)}, {'expires_at': NOW+timedelta(minutes=16)},
    {'consumed_at': NOW-timedelta(microseconds=1)}, {'consumed_at': NOW+timedelta(minutes=15)},
    {'revoked_at': NOW-timedelta(microseconds=1)}, {'row_version': 0},
    {'user_id': None}, {'user_id': uuid4()},
    {'tenant_id': None}, {'tenant_id': uuid4()},
    {'initiated_by_user_id': None}, {'initiated_by_user_id': uuid4()},
])
def test_invalid_stored_state_is_rejected_and_leaves_no_row(local_store, changes):
    engine, table, user_id, _ = local_store
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert(table).values(**values(user_id, **changes)))
    with engine.connect() as connection:
        assert connection.execute(select(table)).first() is None


def test_token_hash_is_unique_across_platform_users(local_store):
    engine, table, user_id, _ = local_store
    data = values(user_id)
    with engine.begin() as connection:
        connection.execute(insert(table).values(**data))
        other_user = uuid4()
        connection.execute(insert(table.metadata.tables['users']).values(id=other_user))
        connection.execute(insert(table.metadata.tables['tenants']).values(id=other_user))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(insert(table).values(**values(other_user, token_hash=data['token_hash'])))


def test_caller_rollback_removes_token_without_repository_side_commit(local_store):
    engine, table, user_id, local_class = local_store
    with Session(engine) as session:
        session.add(local_class(**values(user_id)))
        session.flush()
        assert session.execute(select(table)).first() is not None
        session.rollback()
    with engine.connect() as connection:
        assert connection.execute(select(table)).first() is None


def test_versioned_mutations_reject_a_stale_snapshot_and_do_not_overwrite_consumption(local_store):
    engine, table, user_id, local_class = local_store
    data = values(user_id)
    with engine.begin() as connection:
        connection.execute(insert(table).values(**data))
    with Session(engine, expire_on_commit=False) as first, Session(engine, expire_on_commit=False) as second:
        consumed = first.get(local_class, data['id'])
        stale = second.get(local_class, data['id'])
        consumed.consumed_at = NOW+timedelta(minutes=1)
        first.commit()
        assert consumed.row_version == 2
        stale.revoked_at = NOW+timedelta(minutes=2)
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
    with engine.connect() as connection:
        record = connection.execute(select(table)).mappings().one()
        assert record['row_version'] == 2 and record['consumed_at'] is not None and record['revoked_at'] is None


def test_read_only_no_op_does_not_increase_version(local_store):
    engine, table, user_id, local_class = local_store
    data = values(user_id)
    with engine.begin() as connection:
        connection.execute(insert(table).values(**data))
    with Session(engine) as session:
        record = session.get(local_class, data['id'])
        record.consumed_at = None
        session.commit()
    with engine.connect() as connection:
        assert connection.execute(select(table.c.row_version)).scalar_one() == 1
