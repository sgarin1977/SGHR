from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, Column, JSON, MetaData, Table, Text, UniqueConstraint, Uuid, create_engine, event, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from api.telegram_auth import TelegramAuthenticationError, TelegramInitDataVerifier
from database.models import EventLog, PlatformTelegramBindingToken as Token, UserAccount
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingConsumeService, PlatformTelegramBindingOperationError as OperationError,
    PlatformTelegramBindingVersionConflictError as VersionConflict, build_platform_telegram_binding_consume_service,
)
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError as Denied, issue_platform_telegram_binding_token
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountAlreadyLinkedError as AlreadyLinked


NOW = datetime(2026, 10, 14, 12, 34, 56, 123456, tzinfo=timezone.utc)
BOT_TOKEN = '123456:local-binding-test-secret'


def proof(*, telegram_id=123456789, as_of=NOW+timedelta(minutes=1)):
    values = {'auth_date': str(int(as_of.timestamp())),
        'user': json.dumps({'id': telegram_id, 'first_name': 'Test Person', 'username': 'private_test_name'}, separators=(',', ':'))}
    secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, '\n'.join(f'{name}={values[name]}' for name in sorted(values)).encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class Result:
    def __init__(self, result): self.result = result
    def scalar_one_or_none(self):
        value = self.result.scalar_one_or_none()
        if isinstance(value, Token):
            # SQLite drops timezone metadata. Normalize its UTC test records
            # without marking ORM fields dirty. Native PostgreSQL behavior is
            # not established by this explicit local test adapter.
            for name in ('issued_at', 'expires_at', 'consumed_at', 'revoked_at', 'updated_at'):
                instant = getattr(value, name)
                if instant is not None and instant.tzinfo is None:
                    set_committed_value(value, name, instant.replace(tzinfo=timezone.utc))
        return value
    def one_or_none(self): return self.result.one_or_none()
    def scalars(self): return self.result.scalars()


class Bridge:
    def __init__(self, session): self.session, self.statements, self.rollbacks = session, [], 0
    @property
    def no_autoflush(self): return self.session.no_autoflush
    def in_transaction(self): return self.session.in_transaction()
    def add(self, row): self.session.add(row)
    async def flush(self): self.session.flush()
    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.session.execute(statement))
    async def rollback(self):
        self.rollbacks += 1
        self.session.rollback()
    async def commit(self): raise AssertionError('Service must not commit the caller transaction.')


@pytest.fixture
def environment():
    # This fixture checks real local SQL/ORM rollback, not native PostgreSQL
    # locking, RLS, durable deployment or concurrent transaction behavior.
    metadata = MetaData()
    users = Table('users', metadata, Column('id', Uuid, primary_key=True), Column('status', Text, nullable=False))
    tenants = Table('tenants', metadata, Column('id', Uuid, primary_key=True))
    Table('countries', metadata, Column('id', Uuid, primary_key=True))
    Table('languages', metadata, Column('code', Text, primary_key=True))
    token_table = Token.__table__.to_metadata(metadata)
    for constraint in list(token_table.constraints):
        if isinstance(constraint, CheckConstraint) and constraint.name.endswith(('_hash', '_ttl')):
            token_table.constraints.remove(constraint)
    token_table.append_constraint(CheckConstraint("length(token_hash)=64 AND token_hash NOT GLOB '*[^0-9a-f]*'"))
    token_table.append_constraint(CheckConstraint("abs((julianday(expires_at)-julianday(issued_at))*86400-900)<0.001"))
    accounts = UserAccount.__table__.to_metadata(metadata)
    accounts.append_constraint(UniqueConstraint('platform', 'platform_user_id', name='user_accounts_platform_platform_user_id_key'))
    events = EventLog.__table__.to_metadata(metadata)
    for table in (accounts, events):
        for column in table.c:
            if isinstance(column.type, JSONB): column.type = JSON()
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    owner, other, blocked, tenant = uuid4(), uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(insert(users), [{'id': owner, 'status': 'active'}, {'id': other, 'status': 'active'},
            {'id': blocked, 'status': 'blocked'}])
        connection.execute(insert(tenants).values(id=tenant))
    with Session(engine, expire_on_commit=False) as session:
        bridge = Bridge(session)
        verifier = TelegramInitDataVerifier(bot_token=BOT_TOKEN, max_age_seconds=300)
        service = build_platform_telegram_binding_consume_service(session=bridge, telegram_verifier=verifier,
            now_provider=lambda: NOW+timedelta(minutes=1))
        material = issue_platform_telegram_binding_token(as_of=NOW)
        binding_id = uuid4()
        session.add(Token(id=binding_id, user_id=owner, tenant_id=tenant, initiated_by_user_id=owner,
            token_hash=material.token_hash, issued_at=material.issued_at,
            expires_at=material.expires_at, row_version=1, updated_at=NOW))
        session.commit()  # Original checkpoint belongs to the caller.
        session.begin()
        yield SimpleNamespace(service=service, bridge=bridge, session=session, owner=owner, other=other,
            blocked=blocked, tenant=tenant, material=material, binding_id=binding_id, users=users, token_table=token_table)
        session.rollback()
    engine.dispose()


def checkpoint(env, *, account_count=0, audit_count=0, version=1):
    row = env.session.get(Token, env.binding_id)
    assert row.row_version == version
    assert row.consumed_at is None and row.revoked_at is None
    assert len(env.session.execute(select(UserAccount)).scalars().all()) == account_count
    assert len(env.session.execute(select(EventLog)).scalars().all()) == audit_count
    assert len(env.session.execute(select(env.users.c.id)).all()) == 3


@pytest.mark.asyncio
async def test_consume_verifies_proof_links_canonical_user_and_flushes_exact_secret_free_audit(environment):
    env = environment
    signed = proof()
    result = await env.service.consume_token(token=env.material.token, telegram_init_data=signed, trace_id='binding:test')
    assert result.binding_id == env.binding_id and result.user_id == env.owner and result.row_version == 2
    assert result.consumed_at == NOW+timedelta(minutes=1) and result.account_created is True
    account = env.session.execute(select(UserAccount)).scalar_one()
    audit = env.session.execute(select(EventLog)).scalar_one()
    assert account.id == result.account_id and account.user_id == env.owner and account.platform == 'telegram'
    assert account.platform_user_id == '123456789'
    assert audit.user_id == env.owner and audit.tenant_id == env.tenant and audit.entity_id == env.binding_id
    assert audit.event_type == 'platform_telegram_binding_consumed' and audit.entity_type == 'platform_telegram_binding_token'
    assert audit.platform == 'api' and audit.trace_id == 'binding:test'
    assert audit.payload == {'operation': 'platform.telegram.binding.consume', 'account_id': str(account.id),
        'account_created': True, 'changes': [
            {'field': 'consumed_at', 'old_value': None, 'new_value': result.consumed_at.isoformat()},
            {'field': 'row_version', 'old_value': 1, 'new_value': 2}]}
    serialized = json.dumps(audit.payload)+repr(result)
    for private in (env.material.token, env.material.token_hash, signed, BOT_TOKEN, 'private_test_name'):
        assert private not in serialized
    assert env.bridge.rollbacks == 0 and env.session.in_transaction()
    with pytest.raises(FrozenInstanceError): result.row_version = 99
    statements = [str(statement.compile(dialect=postgresql.dialect())) for statement in env.bridge.statements]
    assert 'FOR UPDATE' not in statements[0]  # Locator is not authorization.
    assert 'users.id' in statements[1] and 'FOR UPDATE' in statements[1]
    assert 'platform_telegram_binding_tokens' in statements[2] and 'FOR UPDATE' in statements[2]
    assert statements[3].startswith('UPDATE platform_telegram_binding_tokens ')
    env.session.rollback()  # All three effects are still owned by the caller.
    checkpoint(env)


@pytest.mark.asyncio
async def test_duplicate_direct_consume_is_denied_but_workflow_replay_is_not_implemented_here(environment):
    env = environment
    await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    env.session.commit()
    env.session.begin()
    with pytest.raises(Denied):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert env.session.get(Token, env.binding_id).row_version == 2
    assert len(env.session.execute(select(UserAccount)).all()) == 1
    assert len(env.session.execute(select(EventLog)).all()) == 1


@pytest.mark.parametrize('fault', ['before_flush', 'after_flush', 'missing_receipt'])
@pytest.mark.asyncio
async def test_mandatory_audit_failure_rolls_back_token_and_account_even_after_real_audit_flush(environment, fault):
    env = environment
    original = env.service.events.create_event
    async def failing_audit(**kwargs):
        if fault == 'before_flush': raise RuntimeError('Simulated mandatory audit error')
        await original(**kwargs)
        assert env.session.execute(select(EventLog.id)).first() is not None
        if fault == 'after_flush': raise RuntimeError('Simulated error after audit flush')
        return None
    env.service.events.create_event = failing_audit
    with pytest.raises((RuntimeError, OperationError)):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert env.bridge.rollbacks == 1
    checkpoint(env)


@pytest.mark.asyncio
async def test_foreign_telegram_owner_conflict_rolls_back_token_and_preserves_existing_identity(environment):
    env = environment
    existing, _ = await env.service.accounts.create_or_get_account(user_id=env.other, telegram_user_id=123456789)
    existing_id = existing.id
    env.session.commit(); env.session.begin()
    with pytest.raises(AlreadyLinked) as caught:
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert caught.value.code == 'TELEGRAM_ACCOUNT_ALREADY_LINKED' and caught.value.status_code == 409
    checkpoint(env, account_count=1)
    existing = env.session.execute(select(UserAccount)).scalar_one()
    assert existing.id == existing_id and existing.user_id == env.other


@pytest.mark.parametrize('existing_id,created,account_count', [(123456789, False, 1), (111, True, 2)])
@pytest.mark.asyncio
async def test_same_owner_existing_identity_or_second_account_preserves_original_profile(environment, existing_id, created, account_count):
    env = environment
    existing, _ = await env.service.accounts.create_or_get_account(user_id=env.owner, telegram_user_id=existing_id)
    existing.display_name, existing.raw_profile = 'Original profile', {'keep': True}
    env.session.commit(); env.session.begin()
    result = await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert result.account_created is created
    assert len(env.session.execute(select(UserAccount)).scalars().all()) == account_count
    assert existing.display_name == 'Original profile' and existing.raw_profile == {'keep': True}
    assert env.session.execute(select(EventLog)).scalar_one().payload['account_created'] is created


@pytest.mark.parametrize('signed', ['', None, 'not-valid-init-data', proof().replace('Test', 'Changed')])
@pytest.mark.asyncio
async def test_missing_or_forged_telegram_proof_is_denied_before_database_access(environment, signed):
    env = environment
    with pytest.raises((Denied, TelegramAuthenticationError)):
        await env.service.consume_token(token=env.material.token, telegram_init_data=signed)
    assert env.bridge.statements == [] and env.bridge.rollbacks == 1
    checkpoint(env)


@pytest.mark.parametrize('raw', [None, '', 'wrong-private-secret'])
@pytest.mark.asyncio
async def test_invalid_or_unknown_binding_secret_changes_nothing(environment, raw):
    env = environment
    with pytest.raises(Denied) as caught:
        await env.service.consume_token(token=raw, telegram_init_data=proof())
    assert str(caught.value) == 'Telegram binding token is not available.'
    checkpoint(env)


@pytest.mark.parametrize('state', ['expired', 'future', 'consumed', 'revoked', 'blocked_owner'])
@pytest.mark.asyncio
async def test_unavailable_token_or_platform_owner_does_not_create_identity_or_audit(environment, state):
    env = environment
    if state == 'expired': env.service.now_provider = lambda: NOW+timedelta(minutes=15)
    if state == 'future': env.service.now_provider = lambda: NOW-timedelta(seconds=1)
    if state in ('consumed', 'revoked'):
        env.session.execute(update(env.token_table).values(**{state+'_at': NOW, 'row_version': 2}))
    if state == 'blocked_owner': env.session.execute(update(env.users).where(env.users.c.id == env.owner).values(status='blocked'))
    env.session.commit(); env.session.begin()
    signed = proof(as_of=env.service.now_provider())
    with pytest.raises(Denied):
        await env.service.consume_token(token=env.material.token, telegram_init_data=signed)
    assert env.session.execute(select(UserAccount.id)).first() is None
    assert env.session.execute(select(EventLog.id)).first() is None
    assert env.session.get(Token, env.binding_id).row_version == (2 if state in ('consumed', 'revoked') else 1)


@pytest.mark.parametrize('fault', ['token_expired_while_waiting', 'proof_expired_while_waiting'])
@pytest.mark.asyncio
async def test_time_is_checked_again_after_lock_wait(environment, fault):
    env = environment
    if fault == 'token_expired_while_waiting': first, last = NOW+timedelta(minutes=14, seconds=30), NOW+timedelta(minutes=15)
    else: first, last = NOW+timedelta(minutes=1), NOW+timedelta(minutes=6, seconds=1)
    clock = iter((first, last))
    env.service.now_provider = lambda: next(clock)
    with pytest.raises((Denied, TelegramAuthenticationError)):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof(as_of=first))
    checkpoint(env)


@pytest.mark.asyncio
async def test_conditional_write_miss_has_row_version_conflict_and_rolls_back(environment):
    env = environment
    env.service.tokens.consume_token = AsyncMock(return_value=None)
    with pytest.raises(VersionConflict) as caught:
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert caught.value.status_code == 409 and caught.value.code == 'ROW_VERSION_CONFLICT'
    checkpoint(env)


@pytest.mark.parametrize('locator', [None, [], (uuid4(), 'invalid'), (uuid4(), uuid4()), (uuid4(), uuid4(), uuid4())])
@pytest.mark.asyncio
async def test_stale_or_malformed_locator_is_not_used_as_authorization(environment, locator):
    env = environment
    env.service.tokens.get_token_locator = AsyncMock(return_value=locator)
    with pytest.raises(Denied):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    checkpoint(env)


@pytest.mark.asyncio
async def test_account_receipt_mismatch_rolls_back_a_real_insert(environment):
    env = environment
    original = env.service.accounts.create_or_get_account
    async def wrong_receipt(**kwargs):
        row, created = await original(**kwargs)
        return SimpleNamespace(id=row.id, user_id=env.other, platform='telegram', platform_user_id=row.platform_user_id), created
    env.service.accounts.create_or_get_account = wrong_receipt
    with pytest.raises(OperationError):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    checkpoint(env)


@pytest.mark.asyncio
async def test_caller_transaction_is_required_and_service_does_not_open_or_commit_one(environment):
    env = environment
    env.session.rollback()
    with pytest.raises(OperationError, match='active caller transaction'):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert env.bridge.statements == [] and env.bridge.rollbacks == 0


@pytest.mark.parametrize('clock', [None, NOW.replace(tzinfo=None)])
@pytest.mark.asyncio
async def test_invalid_clock_is_denied_without_queries(environment, clock):
    env = environment
    env.service.now_provider = lambda: clock
    with pytest.raises(OperationError):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert env.bridge.statements == []
    checkpoint(env)


def test_factory_rejects_separate_audit_session_and_unconfigured_verifier(environment):
    env = environment
    arguments = dict(session=env.bridge, token_repository=env.service.tokens, account_repository=env.service.accounts,
        event_repository=SimpleNamespace(session=object()), telegram_verifier=env.service.verifier)
    with pytest.raises(ValueError, match='share one session'): PlatformTelegramBindingConsumeService(**arguments)
    arguments['event_repository'] = env.service.events
    arguments['telegram_verifier'] = SimpleNamespace(verify=lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match='signature verifier'): PlatformTelegramBindingConsumeService(**arguments)


@pytest.mark.asyncio
async def test_consume_uses_persisted_context_without_platform_session_or_caller_audit_scope(environment):
    env = environment
    result = await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert result.tenant_id == env.tenant
    assert env.session.execute(select(EventLog)).scalar_one().tenant_id == env.tenant


@pytest.mark.asyncio
async def test_locator_reads_only_ids_without_locking_or_flushing(environment):
    env = environment
    locator = await env.service.tokens.get_token_locator(token_hash=env.material.token_hash)
    assert locator == (env.binding_id, env.owner, env.tenant)
    sql = str(env.bridge.statements[-1].compile(dialect=postgresql.dialect()))
    assert 'FOR UPDATE' not in sql and 'token_hash =' in sql
    assert 'SELECT platform_telegram_binding_tokens.id, platform_telegram_binding_tokens.user_id' in sql
    assert await env.service.tokens.get_token_locator(token_hash='0'*64) is None
    count = len(env.bridge.statements)
    with pytest.raises(ValueError): await env.service.tokens.get_token_locator(token_hash='private-invalid-hash')
    assert len(env.bridge.statements) == count
