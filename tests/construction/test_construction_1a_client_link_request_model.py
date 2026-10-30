from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, UniqueConstraint, create_engine, event, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateIndex, CreateTable

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request

TABLE = Request.__table__
NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def test_exact_hash_only_confirmation_context_and_no_new_identity_or_authorization_columns():
    optional = {'expected_linked_user_id', 'consumed_at', 'invalidated_at'}
    required = {'id', 'tenant_id', 'client_id', 'initiated_by_user_id', 'target_user_id',
        'expected_client_row_version', 'token_hash', 'issued_at', 'expires_at', 'updated_at', 'row_version'}
    assert set(TABLE.c.keys()) == optional | required
    assert all(TABLE.c[n].nullable for n in optional)
    assert all(not TABLE.c[n].nullable for n in required)
    for n in ('id', 'tenant_id', 'client_id', 'initiated_by_user_id', 'target_user_id', 'expected_linked_user_id'):
        assert TABLE.c[n].type.python_type is UUID
    for n in ('issued_at', 'expires_at', 'consumed_at', 'invalidated_at', 'updated_at'):
        assert TABLE.c[n].type.timezone is True
    assert isinstance(TABLE.c.expected_client_row_version.type, BigInteger)
    assert isinstance(TABLE.c.row_version.type, BigInteger)
    for n in ('token', 'code', 'deep_link', 'email', 'phone', 'telegram_user_id', 'permissions', 'role'):
        assert n not in TABLE.c


def test_composite_client_fk_enforces_tenant_but_target_only_references_canonical_user():
    constraints = {c.name:c for c in TABLE.constraints}
    assert len(constraints) == 12
    client_fk = constraints['fk_con_client_link_requests_client']
    assert isinstance(client_fk, ForeignKeyConstraint)
    assert tuple(client_fk.columns.keys()) == ('tenant_id','client_id')
    assert [f.target_fullname for f in client_fk.elements] == ['construction_clients.tenant_id','construction_clients.id']
    for n in ('target_user_id','initiated_by_user_id'):
        fk = next(iter(TABLE.c[n].foreign_keys))
        assert fk.target_fullname == 'users.id' and fk.ondelete == fk.onupdate == 'NO ACTION'
        assert tuple(fk.constraint.columns.keys()) == (n,)
    assert not TABLE.c.expected_linked_user_id.foreign_keys
    assert {tuple(c.columns.keys()) for c in TABLE.constraints if isinstance(c,UniqueConstraint)} == {('token_hash',)}
    assert all(len(c.name) <= 63 for c in TABLE.constraints if c.name)


def test_explicit_expiry_and_positive_versions_do_not_inherit_telegram_ttl():
    assert TABLE.c.expires_at.default is None and TABLE.c.expires_at.server_default is None
    assert TABLE.c.expected_client_row_version.default is None
    assert TABLE.c.row_version.default.arg == 1 and str(TABLE.c.row_version.server_default.arg) == '1'
    assert Request.__mapper__.version_id_col is TABLE.c.row_version
    expiry = next(c for c in TABLE.constraints if isinstance(c,CheckConstraint) and c.name == 'ck_con_client_link_requests_expiry')
    assert str(expiry.sqltext) == 'expires_at > issued_at'
    assert TABLE.c.updated_at.default.arg(None).tzinfo is timezone.utc


def test_postgresql_ddl_and_client_index_compile_with_real_parent_metadata():
    ddl = str(CreateTable(TABLE).compile(dialect=postgresql.dialect()))
    assert 'CREATE TABLE construction_client_user_link_requests' in ddl
    assert 'FOREIGN KEY(tenant_id, client_id) REFERENCES construction_clients (tenant_id, id)' in ddl
    assert 'expected_client_row_version BIGINT NOT NULL' in ddl
    assert "translate(token_hash, '0123456789abcdef', '') = ''" in ddl
    assert '15 minutes' not in ddl and 'CASCADE' not in ddl
    index = next(iter(TABLE.indexes))
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert '(tenant_id, client_id, issued_at, id)' in sql


@pytest.fixture
def requests_db():
    c = SimpleNamespace(tenant=uuid4(), foreign=uuid4(), client=uuid4(), other_client=uuid4(),
        operator=uuid4(), target=uuid4(), foreign_user=uuid4(), engine=create_engine('sqlite://'))
    @event.listens_for(c.engine,'connect')
    def setup(connection,_):
        connection.execute('PRAGMA foreign_keys=ON')
        def translate(value, old, new):
            return value.translate(str.maketrans({ch:new[i] if i < len(new) else None for i,ch in enumerate(old)}))
        connection.create_function('translate',3,translate)
    with c.engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY,tenant_id CHAR(32))')
        conn.exec_driver_sql('INSERT INTO tenants VALUES(?),(?)',(c.tenant.hex,c.foreign.hex))
        conn.exec_driver_sql('INSERT INTO users VALUES(?,?),(?,NULL),(?,?)',
            (c.operator.hex,c.tenant.hex,c.target.hex,c.foreign_user.hex,c.foreign.hex))
    ConstructionClient.__table__.create(c.engine)
    with c.engine.begin() as conn:
        for identifier in (c.client,c.other_client):
            conn.execute(insert(ConstructionClient.__table__).values(id=identifier,tenant_id=c.tenant,
                display_name='Existing client',client_type='person'))
    TABLE.create(c.engine)
    try:
        yield c
    finally:
        c.engine.dispose()


def values(c,**changes):
    identifier = uuid4()
    result = dict(id=identifier,tenant_id=c.tenant,client_id=c.client,initiated_by_user_id=c.operator,
        target_user_id=c.target,expected_linked_user_id=None,expected_client_row_version=1,
        token_hash=sha256(identifier.bytes).hexdigest(),issued_at=NOW,expires_at=NOW+timedelta(minutes=3))
    result.update(changes)
    return result


@pytest.mark.parametrize('target', ['no_membership','other_primary_tenant'])
def test_target_does_not_need_organization_membership_or_telegram_and_request_does_not_link_client(requests_db,target):
    c = requests_db
    with c.engine.begin() as conn:
        conn.execute(insert(TABLE).values(**values(c,target_user_id=c.target if target == 'no_membership' else c.foreign_user)))
        clients = conn.execute(select(ConstructionClient.__table__)).mappings().all()
        assert all(row['linked_user_id'] is None and row['row_version'] == 1 for row in clients)
        assert len(conn.exec_driver_sql('SELECT id FROM users').all()) == 3


def test_same_target_can_have_requests_for_multiple_clients_and_hash_alone_is_unique(requests_db):
    c = requests_db
    first = values(c)
    with c.engine.begin() as conn:
        conn.execute(insert(TABLE).values(**first))
        conn.execute(insert(TABLE).values(**values(c,client_id=c.other_client)))
    with pytest.raises(IntegrityError):
        with c.engine.begin() as conn:
            conn.execute(insert(TABLE).values(**values(c,token_hash=first['token_hash'])))


@pytest.mark.parametrize('changes', [
    {'tenant_id':uuid4()}, {'client_id':uuid4()},
    {'target_user_id':uuid4()}, {'initiated_by_user_id':uuid4()},
])
def test_missing_or_foreign_context_is_rejected_by_database(requests_db,changes):
    c = requests_db
    with pytest.raises(IntegrityError):
        with c.engine.begin() as conn:
            conn.execute(insert(TABLE).values(**values(c,**changes)))


def test_existing_other_tenant_cannot_reuse_this_client_identifier(requests_db):
    c = requests_db
    with pytest.raises(IntegrityError):
        with c.engine.begin() as conn:
            conn.execute(insert(TABLE).values(**values(c,tenant_id=c.foreign)))


@pytest.mark.parametrize('digest', ['a'*63,'A'*64,'g'*64,'0'*63+' ','é'*64])
def test_only_full_lowercase_hex_hash_is_accepted(requests_db,digest):
    c = requests_db
    with pytest.raises(IntegrityError):
        with c.engine.begin() as conn:
            conn.execute(insert(TABLE).values(**values(c,token_hash=digest)))


@pytest.mark.parametrize('changes', [
    {'expires_at':NOW}, {'expires_at':NOW-timedelta(seconds=1)},
    {'consumed_at':NOW-timedelta(seconds=1)}, {'consumed_at':NOW+timedelta(minutes=3)},
    {'expected_client_row_version':0}, {'row_version':0},
    {'target_user_id':None}, {'token_hash':None},
])
def test_lifetime_consumption_required_fields_and_positive_versions_are_enforced(requests_db,changes):
    c = requests_db
    with pytest.raises(IntegrityError):
        with c.engine.begin() as conn:
            conn.execute(insert(TABLE).values(**values(c,**changes)))


@pytest.mark.parametrize('seconds', [30,3600])
def test_storage_accepts_explicit_positive_expiry_without_selecting_a_business_default(requests_db,seconds):
    c = requests_db
    with c.engine.begin() as conn:
        conn.execute(insert(TABLE).values(**values(c,expires_at=NOW+timedelta(seconds=seconds))))


def test_orm_version_prevents_stale_second_consumption_and_keeps_context_unchanged(requests_db):
    c = requests_db
    data = values(c)
    with Session(c.engine) as session:
        session.add(Request(**data))
        session.commit()
    with Session(c.engine) as first, Session(c.engine) as stale_session:
        winner = first.get(Request,data['id'])
        stale = stale_session.get(Request,data['id'])
        winner.consumed_at = NOW+timedelta(seconds=20)
        first.commit()
        assert winner.row_version == 2
        stale.consumed_at = NOW+timedelta(seconds=30)
        with pytest.raises(StaleDataError):
            stale_session.commit()
        stale_session.rollback()
    with Session(c.engine) as session:
        row = session.get(Request,data['id'])
        assert row.target_user_id == c.target and row.expected_client_row_version == 1
        assert row.expected_linked_user_id is None and row.token_hash == data['token_hash']
        assert row.consumed_at.replace(tzinfo=timezone.utc) == NOW+timedelta(seconds=20)
        assert session.get(ConstructionClient,c.client).linked_user_id is None


def test_caller_rollback_restores_confirmation_and_request_version(requests_db):
    c = requests_db
    data = values(c)
    with Session(c.engine) as session:
        session.add(Request(**data))
        session.commit()
        row = session.get(Request,data['id'])
        row.consumed_at = NOW+timedelta(seconds=20)
        session.flush()
        assert row.row_version == 2
        session.rollback()
        session.refresh(row)
        assert row.consumed_at is None and row.row_version == 1
