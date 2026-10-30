from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, Text, UniqueConstraint, Uuid, create_engine, event, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from database.models import UserAccount
from database.repositories.platform_telegram_accounts import (
    PlatformTelegramAccountRepository as Repository,
    PlatformTelegramAccountAlreadyLinkedError as AlreadyLinked,
    PlatformTelegramIdentityUnavailableError as Unavailable,
)


class Bridge:
    def __init__(self, session):
        self.session, self.statements = session, []

    @property
    def no_autoflush(self):
        return self.session.no_autoflush

    async def execute(self, statement):
        self.statements.append(statement)
        return self.session.execute(statement)

    async def commit(self):
        raise AssertionError('Only caller may commit.')

    async def rollback(self):
        raise AssertionError('Only caller may roll back.')

    def add(self, value):
        raise AssertionError('Use conditional SQL against existing user_accounts.')


@pytest.fixture
def store():
    # Local SQL/persistence checks only: no native PostgreSQL locking or RLS.
    # JSONB is adapted for SQLite; the confirmed native UNIQUE from the supplied
    # Supabase preflight is represented explicitly without changing the ORM.
    metadata = MetaData()
    users = Table('users', metadata, Column('id', Uuid, primary_key=True), Column('status', Text, nullable=False))
    accounts = UserAccount.__table__.to_metadata(metadata)
    for column in accounts.c:
        if isinstance(column.type, JSONB): column.type = JSON()
    accounts.append_constraint(UniqueConstraint('platform', 'platform_user_id',
        name='user_accounts_platform_platform_user_id_key'))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enforce_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    owner, other, blocked = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(insert(users), [{'id': owner, 'status': 'active'}, {'id': other, 'status': 'active'},
            {'id': blocked, 'status': 'blocked'}])
    with Session(engine, expire_on_commit=False) as session:
        bridge = Bridge(session)
        yield Repository(bridge), bridge, owner, other, blocked, users, accounts
        session.rollback()
    engine.dispose()


@pytest.mark.asyncio
async def test_create_uses_existing_user_and_canonical_account_table_with_no_commit(store):
    repo, bridge, owner, _, _, users, _ = store
    account, created = await repo.create_or_get_account(user_id=owner, telegram_user_id=123456789)
    assert created is True and account.user_id == owner and account.platform == 'telegram'
    assert account.platform_user_id == '123456789' and account.raw_profile == {}
    assert len(bridge.session.execute(select(users.c.id)).scalars().all()) == 3
    assert bridge.session.in_transaction()
    statement = bridge.statements[1]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.startswith('INSERT INTO user_accounts ')
    assert 'ON CONFLICT (platform, platform_user_id) DO NOTHING RETURNING ' in sql
    assert 'DO UPDATE' not in sql and statement.get_execution_options()['populate_existing'] is True
    bridge.session.rollback()
    assert bridge.session.execute(select(UserAccount.id)).first() is None


@pytest.mark.asyncio
async def test_existing_same_owner_preserves_all_identity_fields_and_has_no_duplicate(store):
    repo, bridge, owner, *_ = store
    existing, _ = await repo.create_or_get_account(user_id=owner, telegram_user_id=111)
    existing.username, existing.display_name = 'canonical-name', 'Existing profile'
    existing.email, existing.phone = 'existing@example.com', '+12025550101'
    existing.raw_profile, existing.source = {'kept': True}, 'existing-source'
    bridge.session.flush()
    original = dict(bridge.session.execute(select(UserAccount.__table__)).mappings().one())
    current, created = await repo.create_or_get_account(user_id=owner, telegram_user_id=111)
    assert current is existing and created is False
    assert dict(bridge.session.execute(select(UserAccount.__table__)).mappings().one()) == original


@pytest.mark.asyncio
async def test_foreign_owner_conflict_has_approved_code_and_leaves_original_ownership(store):
    repo, bridge, owner, other, *_ = store
    existing, _ = await repo.create_or_get_account(user_id=owner, telegram_user_id=222)
    original_id = existing.id
    with pytest.raises(AlreadyLinked) as caught:
        await repo.create_or_get_account(user_id=other, telegram_user_id=222)
    assert caught.value.status_code == 409 and caught.value.code == 'TELEGRAM_ACCOUNT_ALREADY_LINKED'
    assert str(caught.value) == 'Telegram account is already linked.'
    assert str(owner) not in str(caught.value) and '222' not in str(caught.value)
    rows = bridge.session.execute(select(UserAccount)).scalars().all()
    assert len(rows) == 1 and rows[0].id == original_id and rows[0].user_id == owner
    assert bridge.session.is_active  # ON CONFLICT did not poison the transaction.


@pytest.mark.asyncio
async def test_multiple_telegram_accounts_share_one_platform_user_and_other_platforms_are_separate(store):
    repo, bridge, owner, other, _, _, _ = store
    first, _ = await repo.create_or_get_account(user_id=owner, telegram_user_id=333)
    second, _ = await repo.create_or_get_account(user_id=owner, telegram_user_id=444)
    third, _ = await repo.create_or_get_account(user_id=other, telegram_user_id=555)
    bridge.session.add(UserAccount(id=uuid4(), user_id=other, platform='email', platform_user_id='333', raw_profile={}))
    bridge.session.flush()
    accounts = await repo.list_accounts(user_id=owner)
    assert {row.id for row in accounts} == {first.id, second.id}
    assert {row.user_id for row in accounts} == {owner}
    assert await repo.get_account_for_update(telegram_user_id=333) is first
    assert await repo.list_accounts(user_id=other) == (third,)
    assert await repo.list_accounts(user_id=uuid4()) == ()


@pytest.mark.asyncio
async def test_missing_or_inactive_owner_cannot_create_account(store):
    repo, bridge, _, _, blocked, *_ = store
    for user_id in (blocked, uuid4()):
        bridge.statements.clear()
        with pytest.raises(Unavailable):
            await repo.create_or_get_account(user_id=user_id, telegram_user_id=666)
        assert len(bridge.statements) == 1
        assert 'FOR UPDATE' in str(bridge.statements[0].compile(dialect=postgresql.dialect()))
    assert bridge.session.execute(select(UserAccount.id)).first() is None


@pytest.mark.asyncio
async def test_no_autoflush_and_locked_reads_refresh_a_cached_identity(store):
    repo, bridge, owner, other, *_ = store
    first, _ = await repo.create_or_get_account(user_id=owner, telegram_user_id=777)
    pending = UserAccount(id=uuid4(), user_id=other, platform='telegram', platform_user_id='777', raw_profile={})
    bridge.session.add(pending)
    assert await repo.get_account_for_update(telegram_user_id=777) is first
    assert pending in bridge.session.new
    assert await repo.list_accounts(user_id=owner) == (first,)
    # This unsaved ownership change must not be written by lookup or conflict handling.
    first.user_id = other
    refreshed = await repo.get_account_for_update(telegram_user_id=777)
    assert refreshed is first and refreshed.user_id == owner
    current, created = await repo.create_or_get_account(user_id=owner, telegram_user_id=777)
    assert current is first and created is False and pending in bridge.session.new
    reads = [statement for statement in bridge.statements if statement.is_select and 'user_accounts' in str(statement)]
    assert reads and all(statement.get_execution_options()['populate_existing'] is True for statement in reads)
    assert any('FOR UPDATE' in str(statement.compile(dialect=postgresql.dialect())) for statement in reads)


@pytest.mark.asyncio
async def test_caller_can_roll_back_new_account_after_audit_failure(store):
    repo, bridge, owner, *_ = store
    account, created = await repo.create_or_get_account(user_id=owner, telegram_user_id=888)
    assert created is True and bridge.session.execute(select(UserAccount.id)).first() is not None
    bridge.session.rollback()  # Caller handles a subsequent mandatory audit error.
    assert bridge.session.execute(select(UserAccount.id)).first() is None
    assert await repo.get_account_for_update(telegram_user_id=888) is None


@pytest.mark.parametrize('value', [None, True, False, 0, -1, 1.0, '123', '00123', '@name', {}, []])
@pytest.mark.asyncio
async def test_invalid_telegram_identity_is_rejected_before_any_query(store, value):
    repo, bridge, owner, *_ = store
    for call, parameters in ((repo.get_account_for_update, {}), (repo.create_or_get_account, {'user_id': owner})):
        with pytest.raises(ValueError) as caught:
            await call(telegram_user_id=value, **parameters)
        assert str(caught.value) == 'Telegram user identifier is invalid.'
    assert bridge.statements == []


@pytest.mark.parametrize('value', [None, True, 'private-invalid-id', 123])
@pytest.mark.asyncio
async def test_invalid_platform_owner_is_rejected_before_any_query(store, value):
    repo, bridge, *_ = store
    with pytest.raises(ValueError): await repo.list_accounts(user_id=value)
    with pytest.raises(ValueError): await repo.create_or_get_account(user_id=value, telegram_user_id=123)
    assert bridge.statements == []


@pytest.mark.parametrize('fault', ['missing', 'wrong_platform', 'wrong_identifier', 'invalid_account_id', 'invalid_owner_id'])
def test_malformed_repository_result_fails_closed(fault):
    owner = uuid4()
    row = SimpleNamespace(id=uuid4(), user_id=owner, platform='telegram', platform_user_id='123')
    if fault == 'missing': row = None
    if fault == 'wrong_platform': row.platform = 'email'
    if fault == 'wrong_identifier': row.platform_user_id = '456'
    if fault == 'invalid_account_id': row.id = 'invalid'
    if fault == 'invalid_owner_id': row.user_id = 'invalid'
    with pytest.raises(Unavailable): Repository._require_owner(row, user_id=owner, identifier='123')


@pytest.mark.asyncio
async def test_conflict_row_disappearing_during_unlink_is_not_treated_as_a_success(store):
    repo, bridge, owner, *_ = store
    await repo.create_or_get_account(user_id=owner, telegram_user_id=999)
    repo.get_account_for_update = AsyncMock(return_value=None)
    with pytest.raises(Unavailable):
        await repo.create_or_get_account(user_id=owner, telegram_user_id=999)


@pytest.mark.asyncio
async def test_profile_or_ownership_fields_cannot_be_injected_into_new_account(store):
    repo, bridge, owner, *_ = store
    with pytest.raises(TypeError):
        await repo.create_or_get_account(user_id=owner, telegram_user_id=123, platform='email')
    with pytest.raises(TypeError):
        await repo.create_or_get_account(user_id=owner, telegram_user_id=123, raw_profile={'untrusted': True})
    assert bridge.statements == []
