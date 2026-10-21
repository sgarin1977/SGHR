from contextlib import nullcontext
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from database.repositories.construction_client_link_requests import (
    ConstructionClientLinkRequestRepository as Repository, ConstructionClientLinkRequestState as State, MAX_VERSION,
)
from database.repositories.construction_client_user_links import ConstructionClientUserLinkRepository
from test_construction_1a_client_link_request_model import requests_db, values

NOW = datetime(2026,10,16,12,tzinfo=timezone.utc)


def arguments(c, **changes):
    data = values(c, **changes)
    data['request_id'] = data.pop('id')
    return data


@pytest.fixture
def request_repo(requests_db):
    c = requests_db
    with c.engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE request_repo_test_audit(id TEXT PRIMARY KEY)')
    c.sync = Session(c.engine, expire_on_commit=False)
    c.statements = []
    class Adapter:
        commit = AsyncMock()
        rollback = AsyncMock()
        flush = AsyncMock()
        @property
        def no_autoflush(self): return c.sync.no_autoflush
        def in_transaction(self): return c.sync.in_transaction()
        async def execute(self, statement):
            c.statements.append(statement)
            result = c.sync.execute(statement)
            # Emulate PostgreSQL aware timestamp decoding only. SQLite's
            # datetime storage drops timezone metadata, unlike timestamptz.
            def normalize(row):
                if row is None: return None
                return {k:v.replace(tzinfo=timezone.utc) if isinstance(v,datetime) and v.tzinfo is None else v
                    for k,v in row.items()}
            return SimpleNamespace(mappings=lambda:SimpleNamespace(
                one_or_none=lambda:normalize(result.mappings().one_or_none()),
                all=lambda:[normalize(row) for row in result.mappings().all()]))
    c.session = Adapter()
    c.repo = Repository(c.session)
    c.sync.begin()
    try:
        yield c
    finally:
        c.sync.close()


def caller_owns(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.session.flush.assert_not_called()


async def get(c, state, **changes):
    kw = dict(tenant_id=state.tenant_id, request_id=state.request_id,
        target_user_id=state.target_user_id, token_hash=state.token_hash)
    kw.update(changes)
    return await c.repo.get_request_for_update(**kw)


@pytest.mark.asyncio
async def test_create_persists_only_hash_and_exact_base_context_without_client_or_user_changes(request_repo):
    c = request_repo
    data = arguments(c)
    state = await c.repo.create_request(**data)
    assert isinstance(state,State) and state.row_version == 1 and state.consumed_at is None
    assert state.request_id == data['request_id'] and state.expected_client_row_version == 1
    assert state.target_user_id == c.target and state.initiated_by_user_id == c.operator
    assert state.token_hash == data['token_hash'] and state.token_hash not in repr(state)
    assert 'token_hash=' not in repr(state)
    with pytest.raises(FrozenInstanceError): state.target_user_id = c.foreign_user
    assert await get(c,state) == state
    clients = c.sync.execute(select(ConstructionClient.__table__)).mappings().all()
    assert all(row['linked_user_id'] is None and row['row_version'] == 1 for row in clients)
    assert len(c.sync.execute(text('SELECT id FROM users')).all()) == 3
    sql = str(c.statements[0].compile(dialect=postgresql.dialect())).lower()
    assert sql.startswith('insert into construction_client_user_link_requests')
    assert ' select ' in sql and ' from construction_clients ' in ' '.join(sql.split())
    assert 'construction_clients.deleted_at is null' in sql
    assert 'construction_clients.row_version =' in sql and 'construction_clients.linked_user_id is null' in sql
    assert 'returning' in sql
    caller_owns(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch',['foreign_tenant','missing_client','stale_version','previous_identity','archived'])
async def test_creation_rejects_unavailable_or_changed_client_base_before_persisting_request(request_repo,mismatch):
    c = request_repo
    changes = {}
    if mismatch == 'foreign_tenant': changes['tenant_id'] = c.foreign
    elif mismatch == 'missing_client': changes['client_id'] = uuid4()
    elif mismatch == 'stale_version': changes['expected_client_row_version'] = 2
    elif mismatch == 'previous_identity': changes['expected_linked_user_id'] = c.foreign_user
    else: c.sync.execute(update(ConstructionClient).where(ConstructionClient.id == c.client).values(deleted_at=NOW))
    assert await c.repo.create_request(**arguments(c,**changes)) is None
    assert c.sync.execute(select(Request.id)).all() == []
    caller_owns(c)


@pytest.mark.asyncio
async def test_recipient_lock_is_bound_to_tenant_request_target_and_hash(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    for changes in ({'tenant_id':c.foreign},{'request_id':uuid4()},
            {'target_user_id':c.foreign_user},{'token_hash':'0'*64}):
        assert await get(c,state,**changes) is None
    assert await get(c,state) == state
    statement = c.statements[-1]
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert 'for update' in sql and 'tenant_id =' in sql and 'target_user_id =' in sql and 'token_hash =' in sql
    assert statement.get_execution_options()['autoflush'] is False
    assert 'user_accounts' not in sql and 'user_roles' not in sql
    caller_owns(c)


@pytest.mark.asyncio
async def test_real_consume_is_one_time_and_changes_only_consumption_version_and_updated_at(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    consumed = await c.repo.consume_request(state=state,as_of=NOW+timedelta(seconds=10))
    assert consumed == replace(state,consumed_at=NOW+timedelta(seconds=10),
        updated_at=NOW+timedelta(seconds=10),row_version=2)
    statement = c.statements[-1]
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    set_part = sql.split(' set ',1)[1].split(' where ',1)[0]
    assert set_part.count('=') == 3
    for field in ('consumed_at','updated_at','row_version'): assert field+'=' in set_part
    assert 'exists (select construction_clients.id' in sql
    assert 'expected_client_row_version =' in sql and 'expected_linked_user_id is null' in sql
    assert 'consumed_at is null' in sql and 'expires_at >' in sql and 'issued_at <=' in sql
    assert await c.repo.consume_request(state=state,as_of=NOW+timedelta(seconds=11)) is None
    count = len(c.statements)
    assert await c.repo.consume_request(state=consumed,as_of=NOW+timedelta(seconds=12)) is None
    assert len(c.statements) == count
    assert (await get(c,state)).consumed_at == consumed.consumed_at
    client = c.sync.get(ConstructionClient,c.client)
    assert client.linked_user_id is None and client.row_version == 1
    caller_owns(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('as_of',[NOW-timedelta(microseconds=1), NOW+timedelta(minutes=3), NOW+timedelta(days=1)])
async def test_lifetime_boundaries_refuse_consume_without_query_or_record_change(request_repo,as_of):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.statements.clear()
    assert await c.repo.consume_request(state=state,as_of=as_of) is None
    assert c.statements == []
    assert await get(c,state) == state


@pytest.mark.asyncio
async def test_issued_at_boundary_and_explicit_non_telegram_lifetime_are_allowed(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c,expires_at=NOW+timedelta(seconds=25)))
    assert (await c.repo.consume_request(state=state,as_of=NOW)).consumed_at == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['version','identity','archived'])
async def test_consume_does_not_mark_request_used_when_client_base_no_longer_matches(request_repo,change):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    values = {'row_version':2} if change == 'version' else {'linked_user_id':c.foreign_user} if change == 'identity' else {'deleted_at':NOW}
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(**values))
    assert await c.repo.consume_request(state=state,as_of=NOW+timedelta(seconds=10)) is None
    assert await get(c,state) == state


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['tenant','request','client','initiator','target','base_version','base_identity','hash','issued','expiry','updated','request_version'])
async def test_modified_snapshot_cannot_consume_saved_request(request_repo,change):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    changes = {'tenant':{'tenant_id':c.foreign},'request':{'request_id':uuid4()},
        'client':{'client_id':c.other_client},'initiator':{'initiated_by_user_id':c.foreign_user},
        'target':{'target_user_id':c.foreign_user},'base_version':{'expected_client_row_version':2},
        'base_identity':{'expected_linked_user_id':c.foreign_user},'hash':{'token_hash':'0'*64},
        'issued':{'issued_at':NOW-timedelta(seconds=1)},'expiry':{'expires_at':NOW+timedelta(minutes=4)},
        'updated':{'updated_at':NOW+timedelta(seconds=1)},'request_version':{'row_version':2}}[change]
    assert await c.repo.consume_request(state=replace(state,**changes),as_of=NOW+timedelta(seconds=10)) is None
    assert await get(c,state) == state
    caller_owns(c)


@pytest.mark.asyncio
async def test_replacement_base_uses_equality_and_preserves_previous_user_snapshot(request_repo):
    c = request_repo
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(linked_user_id=c.foreign_user))
    state = await c.repo.create_request(**arguments(c,expected_linked_user_id=c.foreign_user))
    assert state.expected_linked_user_id == c.foreign_user
    assert (await c.repo.consume_request(state=state,as_of=NOW)).expected_linked_user_id == c.foreign_user
    sql = str(c.statements[-1].compile(dialect=postgresql.dialect())).lower()
    assert 'expected_linked_user_id =' in sql and 'construction_clients.linked_user_id =' in sql


@pytest.mark.asyncio
async def test_projection_reads_and_consume_do_not_autoflush_dirty_cached_request(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    cached = c.sync.get(Request,state.request_id)
    cached.target_user_id = c.foreign_user
    assert await get(c,state) == state
    assert (await c.repo.consume_request(state=state,as_of=NOW)).target_user_id == c.target
    with c.sync.no_autoflush:
        assert c.sync.execute(select(Request.__table__.c.target_user_id)).scalar_one() == c.target
    assert cached.target_user_id == c.foreign_user and cached in c.sync.dirty
    caller_owns(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing_target','duplicate_hash'])
async def test_missing_target_and_duplicate_hash_fail_without_hidden_commit_or_rollback(request_repo,failure):
    c = request_repo
    data = arguments(c)
    state = await c.repo.create_request(**data)
    changes = {'target_user_id':uuid4()} if failure == 'missing_target' else {'token_hash':state.token_hash}
    with pytest.raises(IntegrityError): await c.repo.create_request(**arguments(c,**changes))
    caller_owns(c)
    c.sync.rollback()


@pytest.mark.asyncio
async def test_caller_rollback_can_undo_request_creation(request_repo):
    c = request_repo
    await c.repo.create_request(**arguments(c))
    c.sync.rollback()
    assert c.sync.execute(select(Request.id)).all() == []


@pytest.mark.asyncio
async def test_request_consume_client_link_and_audit_share_one_caller_commit_and_rollback(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.sync.commit()
    for commit in (False,True):
        c.sync.begin()
        assert (await c.repo.consume_request(state=state,as_of=NOW)).row_version == 2
        result = await ConstructionClientUserLinkRepository(c.session).change_linked_user(
            tenant_id=c.tenant,client_id=c.client,expected_row_version=1,expected_linked_user_id=None,
            linked_user_id=c.target,as_of=NOW)
        assert result.changed and result.state.row_version == 2
        c.sync.execute(text('INSERT INTO request_repo_test_audit VALUES(:id)'),{'id':str(state.request_id)})
        if commit: c.sync.commit()
        else: c.sync.rollback()
        with Session(c.engine) as reader:
            request = reader.get(Request,state.request_id)
            client = reader.get(ConstructionClient,c.client)
            assert (request.consumed_at is not None) == commit
            assert request.row_version == client.row_version == (2 if commit else 1)
            assert client.linked_user_id == (c.target if commit else None)
            assert reader.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one() == int(commit)
    caller_owns(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'tenant_id':'caller-tenant'},{'target_user_id':'caller-user'},
    {'expected_client_row_version':True},{'token_hash':'raw-secret'},
    {'issued_at':datetime(2026,10,16)},{'expires_at':NOW}])
async def test_invalid_create_arguments_are_rejected_before_sql(request_repo,changes):
    c = request_repo
    with pytest.raises(ValueError): await c.repo.create_request(**arguments(c,**changes))
    assert c.statements == []


@pytest.mark.asyncio
async def test_an_existing_transaction_is_required_for_all_storage_methods(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.sync.rollback()
    c.statements.clear()
    for action in (lambda:c.repo.create_request(**arguments(c)), lambda:get(c,state),
            lambda:c.repo.consume_request(state=state,as_of=NOW)):
        with pytest.raises(ValueError): await action()
    assert c.statements == []


@pytest.mark.asyncio
async def test_consumption_rejects_dicts_and_naive_clock_before_sql(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.statements.clear()
    for kwargs in ({'state':{'confirmed':True},'as_of':NOW},
            {'state':state,'as_of':NOW.replace(tzinfo=None)}):
        with pytest.raises(ValueError): await c.repo.consume_request(**kwargs)
    assert c.statements == []


@pytest.mark.asyncio
async def test_maximum_version_never_overflows_and_write_receipts_are_checked(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.sync.execute(update(Request.__table__).where(Request.id == state.request_id).values(row_version=MAX_VERSION))
    maximum = await get(c,state)
    c.statements.clear()
    with pytest.raises(ValueError): await c.repo.consume_request(state=maximum,as_of=NOW)
    assert c.statements == []
    bad = {**vars(state),'request_id':uuid4(),'consumed_at':NOW,'updated_at':NOW,'row_version':2}
    fake = SimpleNamespace(in_transaction=lambda:True,no_autoflush=nullcontext(),
        execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda:SimpleNamespace(one_or_none=lambda:bad))))
    with pytest.raises(ValueError): await Repository(fake).consume_request(state=state,as_of=NOW)


def test_repository_rejects_nontransactional_session():
    with pytest.raises(ValueError): Repository(SimpleNamespace(execute=AsyncMock()))
