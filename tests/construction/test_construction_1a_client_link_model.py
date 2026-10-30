"""Client link storage and ORM concurrency, without a public linking operation."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint, create_engine, event, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateTable

from database.models import ConstructionClient

TABLE = ConstructionClient.__table__


def test_client_orm_matches_applied_link_columns_and_canonical_identity_fk():
    assert set(TABLE.c.keys()) == {
        'id', 'tenant_id', 'display_name', 'client_type', 'phone', 'email', 'notes',
        'created_at', 'updated_at', 'deleted_at', 'linked_user_id', 'row_version',
    }
    assert TABLE.c.linked_user_id.nullable and TABLE.c.linked_user_id.default is None
    fk = next(iter(TABLE.c.linked_user_id.foreign_keys))
    assert fk.target_fullname == 'users.id'
    assert fk.constraint.name == 'fk_construction_clients_linked_user'
    assert fk.ondelete == fk.onupdate == 'NO ACTION'
    assert tuple(fk.constraint.columns.keys()) == ('linked_user_id',)
    assert isinstance(TABLE.c.row_version.type, BigInteger)
    assert not TABLE.c.row_version.nullable
    assert TABLE.c.row_version.default.arg == 1
    assert str(TABLE.c.row_version.server_default.arg) == '1'
    assert ConstructionClient.__mapper__.version_id_col is TABLE.c.row_version
    assert {tuple(c.columns.keys()) for c in TABLE.constraints if isinstance(c, UniqueConstraint)} == {('tenant_id', 'id')}
    check = next(c for c in TABLE.constraints if isinstance(c, CheckConstraint) and c.name == 'ck_construction_clients_row_version')
    assert str(check.sqltext) == 'row_version > 0'


def test_postgresql_ddl_uses_optional_user_identity_without_membership_or_cascade():
    ddl = str(CreateTable(TABLE).compile(dialect=postgresql.dialect()))
    assert 'linked_user_id UUID' in ddl and 'linked_user_id UUID NOT NULL' not in ddl
    assert 'row_version BIGINT DEFAULT 1 NOT NULL' in ddl
    assert 'FOREIGN KEY(linked_user_id) REFERENCES users (id) ON DELETE NO ACTION ON UPDATE NO ACTION' in ddl
    assert 'UNIQUE (linked_user_id)' not in ddl


@pytest.fixture
def client_store():
    c = SimpleNamespace(tenant=uuid4(), other_tenant=uuid4(), target=uuid4(), other_user=uuid4(), client_id=uuid4())
    c.engine = create_engine('sqlite://')
    @event.listens_for(c.engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    with c.engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY, tenant_id CHAR(32))')
        connection.exec_driver_sql('INSERT INTO tenants VALUES(?),(?)', (c.tenant.hex, c.other_tenant.hex))
        # Target has no primary tenant or organization membership.
        connection.exec_driver_sql('INSERT INTO users VALUES(?,NULL),(?,?)', (c.target.hex, c.other_user.hex, c.other_tenant.hex))
    TABLE.create(c.engine)
    with Session(c.engine) as session:
        session.add(ConstructionClient(id=c.client_id, tenant_id=c.tenant, display_name='Original client', client_type='person'))
        session.commit()
    try:
        yield c
    finally:
        c.engine.dispose()


def test_existing_insert_without_new_fields_keeps_client_unlinked_and_gets_server_version(client_store):
    c = client_store
    identifier = uuid4()
    with c.engine.begin() as connection:
        connection.execute(text('INSERT INTO construction_clients '
            '(id,tenant_id,display_name,client_type,created_at,updated_at) '
            'VALUES(:id,:tenant,:name,:kind,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'),
            dict(id=identifier.hex, tenant=c.tenant.hex, name='Legacy insertion', kind='person'))
        row = connection.execute(select(TABLE).where(TABLE.c.id == identifier)).mappings().one()
        assert row['linked_user_id'] is None and row['row_version'] == 1


def test_same_platform_user_can_link_multiple_clients_without_organization_membership(client_store):
    c = client_store
    with c.engine.begin() as connection:
        for tenant in (c.tenant, c.tenant, c.other_tenant):
            connection.execute(insert(TABLE).values(tenant_id=tenant, display_name='Storage fixture',
                client_type='person', linked_user_id=c.target))
        assert len(connection.execute(select(TABLE.c.id).where(TABLE.c.linked_user_id == c.target)).all()) == 3


def test_foreign_primary_tenant_of_target_does_not_change_identity_fk_validity(client_store):
    c = client_store
    with c.engine.begin() as connection:
        connection.execute(insert(TABLE).values(tenant_id=c.tenant, display_name='Storage fixture',
            client_type='company', linked_user_id=c.other_user))


def test_missing_platform_user_is_rejected_without_creating_client(client_store):
    c = client_store
    with pytest.raises(IntegrityError):
        with c.engine.begin() as connection:
            connection.execute(insert(TABLE).values(tenant_id=c.tenant, display_name='Invalid target',
                client_type='person', linked_user_id=uuid4()))
    with c.engine.connect() as connection:
        assert connection.execute(select(TABLE.c.id)).all() == [(c.client_id,)]


@pytest.mark.parametrize('version', [0, -1])
def test_nonpositive_client_versions_are_rejected(client_store, version):
    c = client_store
    with pytest.raises(IntegrityError):
        with c.engine.begin() as connection:
            connection.execute(insert(TABLE).values(tenant_id=c.tenant, display_name='Invalid version',
                client_type='person', row_version=version))


def test_user_delete_cannot_silently_unlink_existing_client(client_store):
    c = client_store
    with Session(c.engine) as session:
        client = session.get(ConstructionClient, c.client_id)
        client.linked_user_id = c.target
        session.commit()
    with pytest.raises(IntegrityError):
        with c.engine.begin() as connection:
            connection.execute(text('DELETE FROM users WHERE id=:id'), {'id': c.target.hex})
    with Session(c.engine) as session:
        client = session.get(ConstructionClient, c.client_id)
        assert client.linked_user_id == c.target and client.row_version == 2


@pytest.mark.parametrize('changed_field', ['display_name', 'linked_user_id'])
def test_stale_profile_or_identity_edit_cannot_overwrite_newer_identity_change(client_store, changed_field):
    c = client_store
    with Session(c.engine) as stale_session, Session(c.engine) as winner_session:
        stale = stale_session.get(ConstructionClient, c.client_id)
        winner = winner_session.get(ConstructionClient, c.client_id)
        assert stale.row_version == winner.row_version == 1
        winner.linked_user_id = c.target
        winner_session.commit()
        assert winner.row_version == 2
        setattr(stale, changed_field, 'Stale profile' if changed_field == 'display_name' else c.other_user)
        with pytest.raises(StaleDataError):
            stale_session.commit()
        stale_session.rollback()
    with Session(c.engine) as session:
        client = session.get(ConstructionClient, c.client_id)
        assert (client.display_name, client.linked_user_id, client.row_version) == ('Original client', c.target, 2)


def test_unchanged_orm_identity_assignment_emits_no_update_or_version_increment(client_store):
    c = client_store
    updates = []
    @event.listens_for(c.engine, 'before_cursor_execute')
    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith('UPDATE '):
            updates.append(statement)
    with Session(c.engine) as session:
        client = session.get(ConstructionClient, c.client_id)
        client.linked_user_id = None
        session.commit()
        assert client.row_version == 1 and updates == []


def test_flushed_identity_change_rolls_back_with_client_version(client_store):
    c = client_store
    with Session(c.engine) as session:
        client = session.get(ConstructionClient, c.client_id)
        client.linked_user_id = c.target
        session.flush()
        assert client.row_version == 2
        session.rollback()
        session.refresh(client)
        assert client.linked_user_id is None and client.row_version == 1
