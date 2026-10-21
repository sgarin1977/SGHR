from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import ConstructionClient
from database.repositories.construction_client_user_links import (
    ConstructionClientUserLinkRepository as Repository,
    MAX_CLIENT_ROW_VERSION,
)
from test_construction_1a_client_link_model import client_store

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
TABLE = ConstructionClient.__table__


@pytest.fixture
def link_repo(client_store):
    c = client_store
    c.sync = Session(c.engine, expire_on_commit=False)
    c.statements, c.flushes = [], []
    event.listen(c.sync, 'before_flush', lambda *_: c.flushes.append(True))
    class Adapter:
        commit = AsyncMock()
        rollback = AsyncMock()
        flush = AsyncMock()
        @property
        def no_autoflush(self):
            return c.sync.no_autoflush
        def in_transaction(self):
            return c.sync.in_transaction()
        async def execute(self, statement):
            c.statements.append(statement)
            return c.sync.execute(statement)
    c.session = Adapter()
    c.repo = Repository(c.session)
    c.sync.begin()
    try:
        yield c
    finally:
        c.sync.close()


async def change(c, **changes):
    arguments = dict(tenant_id=c.tenant, client_id=c.client_id, expected_row_version=1,
        expected_linked_user_id=None, linked_user_id=c.target, as_of=NOW)
    arguments.update(changes)
    return await c.repo.change_linked_user(**arguments)


def caller_owns_transaction(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.session.flush.assert_not_called()
    assert c.flushes == []


@pytest.mark.asyncio
async def test_locked_projection_is_tenant_scoped_live_and_contains_no_client_contact_fields(link_repo):
    c = link_repo
    state = await c.repo.get_state_for_update(tenant_id=c.tenant, client_id=c.client_id)
    assert (state.client_id, state.tenant_id, state.linked_user_id, state.row_version) == (c.client_id, c.tenant, None, 1)
    with pytest.raises(FrozenInstanceError):
        state.row_version = 9
    statement = c.statements[-1]
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert 'for update' in sql and 'construction_clients.tenant_id =' in sql
    assert 'construction_clients.id =' in sql and 'construction_clients.deleted_at is null' in sql
    assert {col.key for col in statement.selected_columns} == {'client_id', 'tenant_id', 'linked_user_id', 'row_version'}
    assert statement.get_execution_options()['autoflush'] is False
    for secret in ('phone', 'email', 'notes', 'display_name'):
        assert secret not in sql
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_link_replace_and_unlink_are_conditional_single_version_updates(link_repo):
    c = link_repo
    first = await change(c)
    assert first.changed and first.state.linked_user_id == c.target and first.state.row_version == 2
    second = await change(c, expected_row_version=2, expected_linked_user_id=c.target, linked_user_id=c.other_user)
    assert second.changed and second.state.linked_user_id == c.other_user and second.state.row_version == 3
    third = await change(c, expected_row_version=3, expected_linked_user_id=c.other_user, linked_user_id=None)
    assert third.changed and third.state.linked_user_id is None and third.state.row_version == 4
    row = c.sync.execute(select(TABLE).where(TABLE.c.id == c.client_id)).mappings().one()
    assert row['display_name'] == 'Original client'
    assert row['updated_at'].replace(tzinfo=timezone.utc) == NOW
    for number, statement in enumerate(c.statements, start=1):
        sql = str(statement.compile(dialect=postgresql.dialect())).lower()
        assert sql.startswith('update construction_clients set ')
        where = sql.split(' where ', 1)[1].split(' returning ', 1)[0]
        assert 'construction_clients.id =' in where and 'construction_clients.tenant_id =' in where
        assert 'construction_clients.row_version =' in where and 'construction_clients.deleted_at is null' in where
        assert ('construction_clients.linked_user_id is null' if number == 1 else 'construction_clients.linked_user_id =') in where
        assert 'returning construction_clients.id as client_id' in sql
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['foreign_tenant', 'missing', 'archived', 'stale_version', 'different_previous_link'])
async def test_unavailable_or_stale_transition_cannot_change_client(link_repo, failure):
    c = link_repo
    changes = {}
    if failure == 'foreign_tenant': changes['tenant_id'] = c.other_tenant
    elif failure == 'missing': changes['client_id'] = uuid4()
    elif failure == 'archived':
        c.sync.execute(update(TABLE).where(TABLE.c.id == c.client_id).values(deleted_at=NOW))
    elif failure == 'stale_version': changes['expected_row_version'] = 2
    else: changes['expected_linked_user_id'] = c.other_user
    before = c.sync.execute(select(TABLE)).mappings().all()
    assert await change(c, **changes) is None
    assert c.sync.execute(select(TABLE)).mappings().all() == before
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_version_and_current_link_must_both_match_even_after_an_earlier_success(link_repo):
    c = link_repo
    assert (await change(c)).state.row_version == 2
    assert await change(c) is None
    assert await change(c, expected_row_version=2) is None
    assert (await change(c, expected_row_version=2, expected_linked_user_id=c.target,
        linked_user_id=c.other_user)).state.row_version == 3
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('linked', [False, True])
async def test_matching_noop_only_locks_and_preserves_version_timestamp_and_state(link_repo, linked):
    c = link_repo
    version = 1
    if linked:
        version = (await change(c)).state.row_version
    identifier = c.target if linked else None
    before = c.sync.execute(select(TABLE)).mappings().all()
    c.statements.clear()
    result = await change(c, expected_row_version=version, expected_linked_user_id=identifier,
        linked_user_id=identifier)
    assert not result.changed and result.state.row_version == version
    assert c.sync.execute(select(TABLE)).mappings().all() == before
    assert len(c.statements) == 1 and str(c.statements[0]).lower().startswith('select ')
    assert await change(c, expected_row_version=version+1, expected_linked_user_id=identifier,
        linked_user_id=identifier) is None
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_noop_does_not_accept_wrong_current_identity_even_with_matching_version(link_repo):
    c = link_repo
    assert await change(c, expected_linked_user_id=c.target, linked_user_id=c.target) is None
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_missing_target_failure_propagates_and_caller_can_roll_back(link_repo):
    c = link_repo
    with pytest.raises(IntegrityError):
        await change(c, linked_user_id=uuid4())
    caller_owns_transaction(c)
    c.sync.rollback()
    row = c.sync.execute(select(TABLE)).mappings().one()
    assert row['linked_user_id'] is None and row['row_version'] == 1


@pytest.mark.asyncio
async def test_caller_rollback_restores_real_link_and_version_update(link_repo):
    c = link_repo
    assert (await change(c)).changed
    caller_owns_transaction(c)
    c.sync.rollback()
    row = c.sync.execute(select(TABLE)).mappings().one()
    assert row['linked_user_id'] is None and row['row_version'] == 1


@pytest.mark.asyncio
async def test_projection_and_conditional_write_do_not_flush_unrelated_pending_orm_changes(link_repo):
    c = link_repo
    cached = c.sync.get(ConstructionClient, c.client_id)
    cached.notes = 'Unsaved local note'
    assert (await c.repo.get_state_for_update(tenant_id=c.tenant, client_id=c.client_id)).row_version == 1
    assert (await change(c)).changed
    with c.sync.no_autoflush:
        row = c.sync.execute(select(TABLE)).mappings().one()
    assert row['notes'] is None and row['linked_user_id'] == c.target
    assert cached.notes == 'Unsaved local note' and cached in c.sync.dirty
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'tenant_id': 'caller-tenant'}, {'client_id': None}, {'linked_user_id': 'caller-user'},
    {'expected_linked_user_id': False}, {'expected_row_version': True}, {'expected_row_version': 0},
    {'expected_row_version': MAX_CLIENT_ROW_VERSION+1}, {'as_of': datetime(2026, 10, 16)},
])
async def test_invalid_storage_arguments_are_rejected_before_queries(link_repo, changes):
    c = link_repo
    with pytest.raises(ValueError):
        await change(c, **changes)
    assert c.statements == []
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_transaction_must_already_exist(link_repo):
    c = link_repo
    c.sync.rollback()
    with pytest.raises(ValueError):
        await change(c)
    with pytest.raises(ValueError):
        await c.repo.get_state_for_update(tenant_id=c.tenant, client_id=c.client_id)
    assert c.statements == []


@pytest.mark.asyncio
async def test_noop_accepts_maximum_version_but_mutation_never_overflows_bigint(link_repo):
    c = link_repo
    c.sync.execute(update(TABLE).where(TABLE.c.id == c.client_id).values(row_version=MAX_CLIENT_ROW_VERSION))
    assert not (await change(c, expected_row_version=MAX_CLIENT_ROW_VERSION, linked_user_id=None)).changed
    c.statements.clear()
    with pytest.raises(ValueError):
        await change(c, expected_row_version=MAX_CLIENT_ROW_VERSION)
    assert c.statements == []
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_malformed_or_foreign_write_receipts_are_rejected(link_repo):
    c = link_repo
    for changes in ({'tenant_id': c.other_tenant}, {'row_version': True},
            {'linked_user_id': c.other_user}, {'row_version': 5}, {'extra': 'private'}):
        receipt = dict(client_id=c.client_id, tenant_id=c.tenant, linked_user_id=c.target, row_version=2)
        receipt.update(changes)
        fake = SimpleNamespace(in_transaction=lambda: True, no_autoflush=nullcontext(),
            execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: receipt))))
        with pytest.raises(ValueError):
            await Repository(fake).change_linked_user(tenant_id=c.tenant, client_id=c.client_id,
                expected_row_version=1, expected_linked_user_id=None, linked_user_id=c.target, as_of=NOW)


def test_repository_rejects_nontransactional_session():
    with pytest.raises(ValueError):
        Repository(SimpleNamespace(execute=AsyncMock()))
