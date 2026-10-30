from dataclasses import replace
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value

from database.models import PlatformTelegramBindingToken as Token, EventLog, UserAccount
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingConsumeService, build_platform_telegram_binding_consume_service,
    PlatformTelegramBindingOperationError as OperationError,
)
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError as Denied
from test_construction_1a_platform_telegram_binding_repository import store, creation, mutation
from test_construction_1a_platform_telegram_binding_consume import environment, proof, checkpoint


def test_context_is_required_hash_only_storage_linked_to_canonical_platform_entities():
    table = Token.__table__
    for name, target in (('tenant_id', 'tenants.id'), ('initiated_by_user_id', 'users.id')):
        column = table.c[name]
        assert not column.nullable and column.default is None and column.server_default is None
        foreign_key, = column.foreign_keys
        assert foreign_key.target_fullname == target
    assert not {'token', 'code', 'deep_link', 'raw_context', 'response_ciphertext'} & set(table.c.keys())
    assert 'initiated_by_user_id' not in UserAccount.__table__.c


@pytest.mark.asyncio
async def test_repository_persists_explicit_context_without_deriving_it_from_target_user(store):
    repo, bridge, owner, other, *_ = store
    row = await repo.create_token(**creation(owner, tenant_id=other, initiated_by_user_id=other))
    assert (row.user_id, row.tenant_id, row.initiated_by_user_id) == (owner, other, other)
    assert await repo.get_token_locator(token_hash=row.token_hash) == (row.id, owner, other)
    assert await repo.get_token_for_update(token_id=row.id, user_id=owner, tenant_id=owner) is None
    assert await repo.get_token_for_update(token_id=row.id, user_id=owner, tenant_id=other) is row
    bridge.session.rollback()
    assert bridge.session.execute(select(Token.id)).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['tenant_id', 'initiated_by_user_id'])
async def test_context_has_no_silent_default_at_repository_boundary(store, field):
    repo, bridge, owner, *_ = store
    options = creation(owner)
    del options[field]
    with pytest.raises(TypeError): await repo.create_token(**options)
    assert bridge.added == [] and bridge.flushes == 0 and bridge.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['consume', 'revoke'])
async def test_conditional_token_mutations_are_bound_to_persisted_tenant(store, operation):
    repo, bridge, owner, other, *_ = store
    row = await repo.create_token(**creation(owner))
    options = mutation(row, tenant_id=other)
    if operation == 'consume': options['token_hash'] = row.token_hash
    assert await getattr(repo, operation+'_token')(**options) is None
    assert (row.tenant_id, row.initiated_by_user_id, row.row_version) == (owner, owner, 1)
    assert row.consumed_at is row.revoked_at is None
    options['tenant_id'] = owner
    assert await getattr(repo, operation+'_token')(**options) is row
    statement = bridge.statements[-1]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert 'platform_telegram_binding_tokens.tenant_id =' in sql.split(' WHERE ')[1].split(' RETURNING ')[0]
    assignments = sql.split(' SET ')[1].split(' WHERE ')[0]
    assert 'tenant_id=' not in assignments and 'initiated_by_user_id=' not in assignments
    assert (row.tenant_id, row.initiated_by_user_id) == (owner, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('home_tenant', [None, 'different'])
async def test_consume_does_not_use_current_home_tenant_or_require_platform_session(environment, home_tenant):
    env = environment
    # Add a home-tenant column to the explicit local users fixture. The binding
    # must use its initiation context whether the present home tenant is NULL
    # or has changed. This is local SQL/ORM, not native PostgreSQL acceptance.
    env.session.execute(text('ALTER TABLE users ADD COLUMN tenant_id CHAR(32)'))
    value = None if home_tenant is None else uuid4().hex
    env.session.execute(text('UPDATE users SET tenant_id=:home WHERE id=:owner'),
        dict(home=value, owner=env.owner.hex))
    env.session.commit(); env.session.begin()
    result = await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert result.user_id == env.owner and result.tenant_id == env.tenant
    assert env.session.execute(select(EventLog)).scalar_one().tenant_id == env.tenant
    assert env.session.execute(select(UserAccount)).scalar_one().user_id == env.owner


def test_caller_cannot_choose_user_tenant_or_initiator_for_consume(environment):
    env = environment
    consume = inspect.signature(env.service.consume_token).parameters
    assert set(consume) == {'token', 'telegram_init_data', 'trace_id'}
    for target in (PlatformTelegramBindingConsumeService, build_platform_telegram_binding_consume_service):
        assert 'audit_tenant_id' not in inspect.signature(target).parameters
    with pytest.raises(TypeError):
        build_platform_telegram_binding_consume_service(session=env.bridge,
            telegram_verifier=env.service.verifier, audit_tenant_id=uuid4())
    assert env.bridge.statements == []


@pytest.mark.asyncio
async def test_obsolete_instance_audit_hint_cannot_override_stored_tenant(environment):
    env = environment
    env.service.audit_tenant_id = uuid4()
    result = await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert result.tenant_id == env.tenant
    assert env.session.execute(select(EventLog)).scalar_one().tenant_id == env.tenant


@pytest.mark.asyncio
async def test_stale_locator_context_is_rechecked_under_token_lock(environment):
    env = environment
    env.service.tokens.get_token_locator = AsyncMock(return_value=(env.binding_id, env.owner, uuid4()))
    with pytest.raises(Denied):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    checkpoint(env)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['tenant', 'missing_tenant', 'initiator', 'missing_initiator'])
async def test_malformed_locked_context_is_denied_without_creating_account(environment, fault):
    env = environment
    row = env.session.get(Token, env.binding_id)
    values = {column.name: getattr(row, column.name) for column in Token.__table__.c}
    field = 'tenant_id' if 'tenant' in fault else 'initiated_by_user_id'
    if fault.startswith('missing'): del values[field]
    else: values[field] = uuid4() if fault == 'tenant' else 'private-invalid-initiator'
    env.service.tokens.get_token_for_update = AsyncMock(return_value=SimpleNamespace(**values))
    with pytest.raises(Denied) as caught:
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    assert str(caught.value) == 'Telegram binding token is not available.'
    checkpoint(env)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['tenant_id', 'initiated_by_user_id'])
async def test_changed_context_in_real_update_receipt_rolls_back_consumption(environment, field):
    env = environment
    original = env.service.tokens.consume_token
    async def corrupted(**kwargs):
        row = await original(**kwargs)
        # Repository UPDATE refreshes the same ORM object; receipt validation
        # must compare against context captured before that UPDATE.
        set_committed_value(row, field, uuid4())
        return row
    env.service.tokens.consume_token = corrupted
    with pytest.raises(OperationError):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    checkpoint(env)
    row = env.session.get(Token, env.binding_id)
    assert row.tenant_id == env.tenant and row.initiated_by_user_id == env.owner


@pytest.mark.asyncio
async def test_wrong_audit_tenant_receipt_rolls_back_real_token_account_and_audit(environment):
    env = environment
    original = env.service.events.create_event
    async def wrong_tenant(**kwargs):
        audit = await original(**kwargs)
        assert env.session.execute(select(EventLog.id)).first() is not None
        return SimpleNamespace(id=audit.id, user_id=audit.user_id, tenant_id=uuid4(),
            event_type=audit.event_type, entity_type=audit.entity_type,
            entity_id=audit.entity_id, payload=audit.payload)
    env.service.events.create_event = wrong_tenant
    with pytest.raises(OperationError):
        await env.service.consume_token(token=env.material.token, telegram_init_data=proof())
    checkpoint(env)
