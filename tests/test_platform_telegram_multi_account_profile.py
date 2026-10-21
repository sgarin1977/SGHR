"""Canonical profile behavior using local SQL, without configured services."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, UniqueConstraint, Uuid, create_engine, event, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from database.models import City, User, UserAccount, UserRoleMapping
from database.repositories.user import UserRepository, PlatformTelegramAccountUnlinkedError
from services.user import UserService


class Bridge:
    def __init__(self, session):
        self.session, self.statements = session, []
        self.commits = self.rollbacks = 0

    @property
    def no_autoflush(self):
        return self.session.no_autoflush

    async def execute(self, statement):
        self.statements.append(statement)
        return self.session.execute(statement)

    async def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    async def commit(self):
        self.commits += 1
        self.session.commit()

    async def rollback(self):
        self.rollbacks += 1
        self.session.rollback()


@pytest.fixture
def profile_store():
    metadata = MetaData()
    for name in ('tenants', 'countries'):
        Table(name, metadata, Column('id', Uuid, primary_key=True))
    for model in (City, User, UserAccount, UserRoleMapping):
        table = model.__table__.to_metadata(metadata)
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    metadata.tables['user_accounts'].append_constraint(UniqueConstraint('platform', 'platform_user_id'))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    tenant, country, city, owner, other = (uuid4() for _ in range(5))
    with engine.begin() as connection:
        connection.execute(metadata.tables['tenants'].insert().values(id=tenant))
        connection.execute(metadata.tables['countries'].insert().values(id=country))
    with Session(engine, expire_on_commit=False) as session:
        session.add(City(id=city, country_id=country, name='City', name_uk='Місто', name_ru='Город'))
        session.flush()
        session.add_all([User(id=owner, tenant_id=tenant, city_id=city, language_code='uk', active_role='client'),
                         User(id=other, tenant_id=tenant)])
        session.flush()
        first = UserAccount(id=uuid4(), user_id=owner, platform='telegram', platform_user_id='123456789',
            display_name='First identity', username='first', first_name='First', last_name='Person')
        second = UserAccount(id=uuid4(), user_id=owner, platform='telegram', platform_user_id='987654321',
            display_name='Second identity', username='second')
        foreign = UserAccount(id=uuid4(), user_id=other, platform='telegram', platform_user_id='112233445',
            display_name='Foreign identity', username='foreign')
        session.add_all([first, second, foreign,
            UserAccount(id=uuid4(), user_id=owner, platform='email', platform_user_id='test@example.com'),
            UserRoleMapping(id=uuid4(), user_id=owner, tenant_id=tenant, role='client'),
            UserRoleMapping(id=uuid4(), user_id=owner, tenant_id=tenant, role='specialist', status='revoked')])
        session.commit()
        bridge = Bridge(session)
        yield SimpleNamespace(session=session, bridge=bridge, owner=owner, other=other,
            first=first, second=second, foreign=foreign, service=UserService(bridge), repo=UserRepository(bridge))
        session.rollback()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('identity,name,username', [
    (123456789, 'First identity', 'first'),
    ('987654321', 'Second identity', 'second'),
])
async def test_incoming_telegram_identity_controls_own_profile_with_two_accounts(profile_store, identity, name, username):
    c = profile_store
    profile = await c.service.get_client_profile(telegram_id=identity, language='uk')
    assert profile.name == name and profile.username == username
    assert profile.city_name == 'Місто' and profile.language_code == 'uk'
    assert profile.user_number == f'user-{str(c.owner)[:8]}'
    assert profile.available_roles == ['client'] and profile.active_role == 'client'
    assert c.bridge.commits == c.bridge.rollbacks == 0


@pytest.mark.asyncio
async def test_user_only_profile_keeps_user_city_and_roles_when_accounts_are_ambiguous(profile_store):
    c = profile_store
    row = await c.repo.get_client_profile_row(c.owner, language='ru')
    assert len(row) == 3 and row[0].id == c.owner and row[1] is None and row[2] == 'Город'
    profile = await c.service.get_client_profile_by_user_id(user_id=c.owner, language='uk')
    assert profile.name is profile.username is None
    assert profile.city_name == 'Місто' and profile.available_roles == ['client']


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', ['112233445', 'missing', ''])
async def test_profile_projection_never_joins_foreign_or_missing_identity(profile_store, identity):
    c = profile_store
    row = await c.repo.get_client_profile_row(c.owner, telegram_platform_user_id=identity)
    assert row[0].id == c.owner and row[1] is None


@pytest.mark.asyncio
async def test_user_only_profile_uses_only_remaining_active_telegram_account(profile_store):
    c = profile_store
    c.first.status = 'unlinked'
    c.session.commit()
    profile = await c.service.get_client_profile_by_user_id(user_id=c.owner)
    assert profile.name == 'Second identity' and profile.username == 'second'
    exact = await c.service.get_client_profile(telegram_id='987654321')
    assert exact.name == 'Second identity'
    with pytest.raises(PlatformTelegramAccountUnlinkedError):
        await c.service.get_client_profile(telegram_id='123456789')
    c.second.status = 'unlinked'
    c.session.commit()
    profile = await c.service.get_client_profile_by_user_id(user_id=c.owner)
    assert profile.name is profile.username is None and profile.available_roles == ['client']
    assert c.bridge.commits == c.bridge.rollbacks == 0


@pytest.mark.asyncio
async def test_explicit_unlinked_identity_never_falls_back_to_other_active_identity(profile_store):
    c = profile_store
    c.first.status = 'unlinked'
    c.session.commit()
    row = await c.repo.get_client_profile_row(c.owner, telegram_platform_user_id='123456789')
    assert row[0].id == c.owner and row[1] is None
    assert c.second.status == 'active'


@pytest.mark.asyncio
async def test_unknown_platform_user_does_not_query_or_return_foreign_accounts(profile_store):
    c = profile_store
    assert await c.repo.get_client_profile_row(uuid4()) is None
    assert len(c.bridge.statements) == 1
    assert await c.service.get_client_profile(telegram_id='missing') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('language,expected', [('uk', 'Місто'), ('ru', 'Город'), ('pt', 'Город'), ('invalid', 'Город')])
async def test_profile_localized_city_fallback_survives_multiple_accounts(profile_store, language, expected):
    row = await profile_store.repo.get_client_profile_row(profile_store.owner, language=language)
    assert row[2] == expected and row[1] is None


@pytest.mark.asyncio
async def test_profile_without_city_and_without_any_account_still_exists(profile_store):
    c = profile_store
    c.foreign.status = 'unlinked'
    c.session.commit()
    profile = await c.service.get_client_profile_by_user_id(user_id=c.other)
    assert profile is not None and profile.name is profile.username is profile.city_name is None
    assert profile.available_roles == []


@pytest.mark.asyncio
async def test_profile_name_fallback_stays_with_exact_identity(profile_store):
    c = profile_store
    c.first.display_name = None
    c.session.commit()
    profile = await c.service.get_client_profile(telegram_id='123456789')
    assert profile.name == 'First Person' and profile.username == 'first'


@pytest.mark.asyncio
async def test_profile_refreshes_unlinked_cached_identity_without_flushing_reactivation(profile_store):
    c = profile_store
    c.session.execute(update(UserAccount).where(UserAccount.id == c.first.id)
        .values(status='unlinked', row_version=2).execution_options(synchronize_session=False))
    c.session.commit()
    assert c.first.status == 'active'
    c.first.username = 'unsaved'
    row = await c.repo.get_client_profile_row(c.owner)
    assert row[1].id == c.second.id
    raw = c.session.execute(select(UserAccount.__table__.c.username)
        .where(UserAccount.id == c.first.id).execution_options(autoflush=False)).scalar_one()
    assert raw == 'first' and c.bridge.commits == c.bridge.rollbacks == 0


@pytest.mark.asyncio
async def test_profile_reads_never_write_accounts_roles_or_sessions(profile_store):
    c = profile_store
    before = tuple(c.session.execute(select(UserAccount.id, UserAccount.status, UserAccount.row_version)
        .order_by(UserAccount.id)).all())
    for identity in ('123456789', '987654321'):
        await c.service.get_client_profile(telegram_id=identity)
    await c.service.get_client_profile_by_user_id(user_id=c.owner)
    after = tuple(c.session.execute(select(UserAccount.id, UserAccount.status, UserAccount.row_version)
        .order_by(UserAccount.id)).all())
    assert before == after
    assert c.bridge.commits == c.bridge.rollbacks == 0
    assert all(statement.is_select for statement in c.bridge.statements)
