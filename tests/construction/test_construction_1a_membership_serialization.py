"""SQL contract checks only; real PostgreSQL parallel acceptance is separate."""
import asyncio
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import BigInteger
from sqlalchemy.dialects import postgresql

from database.repositories.platform_membership_serialization import (
    PlatformMembershipSerializationRepository as Repository,
    PlatformMembershipSerializationError as Error,
    PlatformMembershipSerializationReceipt as Receipt, membership_lock_key, ownership_lock_key,
)


@pytest.fixture
def serialization():
    transaction = SimpleNamespace(is_active=True)
    settings = {'read_only': 'off', 'isolation': 'read committed'}
    result = SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: settings))
    session = SimpleNamespace(no_autoflush=nullcontext(), in_transaction=Mock(return_value=True),
        get_transaction=Mock(return_value=transaction), get_nested_transaction=Mock(return_value=None),
        get_bind=Mock(return_value=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))),
        execute=AsyncMock(return_value=result), commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
    return SimpleNamespace(session=session, transaction=transaction, settings=settings,
        repository=Repository(session), tenant=uuid4(), user=uuid4())


@pytest.mark.asyncio
async def test_member_lock_is_parameterized_transaction_scoped_and_never_changes_privileges(serialization):
    c = serialization
    receipt = await c.repository.lock_member(tenant_id=c.tenant, user_id=c.user)
    assert receipt == Receipt(c.tenant, c.user, c.transaction)
    assert receipt.transaction is c.transaction and 'transaction=' not in repr(receipt)
    assert c.session.execute.await_count == 3
    settings, ownership, lock = [call.args[0] for call in c.session.execute.await_args_list]
    assert 'transaction_isolation' in str(settings) and 'transaction_read_only' in str(settings)
    owner_sql = ownership.compile(dialect=postgresql.dialect())
    assert owner_sql.params == {'ownership_key': ownership_lock_key(tenant_id=c.tenant)}
    assert 'pg_advisory_xact_lock' in str(owner_sql)
    compiled = lock.compile(dialect=postgresql.dialect())
    assert str(compiled) == 'SELECT pg_catalog.pg_advisory_xact_lock(CAST(%(lock_key)s AS bigint))'
    assert compiled.params == {'lock_key': membership_lock_key(tenant_id=c.tenant, user_id=c.user)}
    assert isinstance(lock._bindparams['lock_key'].type, BigInteger)
    assert str(c.tenant) not in str(compiled) and str(c.user) not in str(compiled)
    for name in ('commit', 'rollback', 'flush'): getattr(c.session, name).assert_not_called()
    with pytest.raises(FrozenInstanceError): receipt.user_id = uuid4()


def test_key_is_stable_for_canonical_pair_and_separates_tenants_targets_and_order():
    a, b = UUID(int=1), UUID(int=2)
    key = membership_lock_key(tenant_id=a, user_id=b)
    assert type(key) is int and -(2**63) <= key < 2**63
    assert key == membership_lock_key(tenant_id=UUID(str(a)), user_id=UUID(str(b)))
    assert len({key, membership_lock_key(tenant_id=b, user_id=a),
        membership_lock_key(tenant_id=a, user_id=a), membership_lock_key(tenant_id=b, user_id=b)}) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('tenant_id', None), ('user_id', 'bad'), ('user_id', True)])
async def test_invalid_scope_never_executes_sql(serialization, field, value):
    c = serialization
    with pytest.raises(ValueError):
        await c.repository.lock_member(**dict({'tenant_id': c.tenant, 'user_id': c.user}, **{field: value}))
    c.session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['no_transaction', 'inactive', 'missing', 'nested', 'sqlite', 'unknown_bind'])
async def test_unsafe_transactions_and_non_postgres_fail_before_sql(serialization, case):
    c = serialization
    if case == 'no_transaction': c.session.in_transaction.return_value = False
    elif case == 'inactive': c.transaction.is_active = False
    elif case == 'missing': c.session.get_transaction.return_value = None
    elif case == 'nested': c.session.get_nested_transaction.return_value = object()
    elif case == 'sqlite': c.session.get_bind.return_value.dialect.name = 'sqlite'
    else: c.session.get_bind.return_value = None
    with pytest.raises(Error): await c.repository.lock_member(tenant_id=c.tenant, user_id=c.user)
    c.session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('read_only,isolation', [('on','read committed'), ('off','repeatable read'),
    ('off','serializable'), ('off','read uncommitted'), ('off','unknown')])
async def test_snapshot_modes_that_cannot_refresh_membership_are_rejected(serialization, read_only, isolation):
    c = serialization
    c.settings.update(read_only=read_only, isolation=isolation)
    with pytest.raises(Error): await c.repository.lock_member(tenant_id=c.tenant, user_id=c.user)
    assert c.session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', [1, 2, 3])
async def test_transaction_replacement_during_settings_or_wait_is_rejected(serialization, phase):
    c = serialization
    real_result = c.session.execute.return_value
    calls = 0
    async def execute(statement):
        nonlocal calls
        calls += 1
        if calls == phase: c.session.get_transaction.return_value = SimpleNamespace(is_active=True)
        return real_result
    c.session.execute.side_effect = execute
    with pytest.raises(Error): await c.repository.lock_member(tenant_id=c.tenant, user_id=c.user)
    assert c.session.execute.await_count == phase


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('driver failed'), asyncio.CancelledError()])
async def test_lock_failure_or_cancel_never_creates_a_receipt_or_commits(serialization, failure):
    c = serialization
    c.session.execute.side_effect = [c.session.execute.return_value, failure]
    with pytest.raises(type(failure)): await c.repository.lock_member(tenant_id=c.tenant, user_id=c.user)
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


@pytest.mark.parametrize('missing', ['execute', 'get_bind', 'get_transaction', 'get_nested_transaction', 'in_transaction', 'no_autoflush'])
def test_session_contract_is_required(serialization, missing):
    delattr(serialization.session, missing)
    with pytest.raises(ValueError): Repository(serialization.session)
