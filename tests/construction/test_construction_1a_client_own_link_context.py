"""Local SQL projections and read-only transaction orchestration, not native RLS."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import FrozenInstanceError, replace
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects import postgresql

import database.construction_client_unlink_context as db_context
from database.construction_client_unlink_context import (
    construction_client_unlink_lookup_transaction, bind_client_unlink_lookup_context,
    OWN_LINK_CLIENT_SETTING, OWN_LINK_USER_SETTING,
)
from database.construction_session import ConstructionDatabaseConfigurationError
from database.models import ConstructionClient
from database.repositories.construction.construction_client_unlink_context import (
    ConstructionClientOwnLinkContext as Context, ConstructionClientOwnLinkContextRepository as Repository,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal
from services.construction.construction_client_unlink import ConstructionClientUnlinkDeniedError as Denied
from services.construction.construction_client_unlink_context import (
    ConstructionClientOwnLinkContextService as Service, build_construction_client_own_link_context_service,
)
from services.construction.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from test_construction_1a_client_unlink_workflow import unlink_pipeline, snapshot
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation, seed_request
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


def runtime_row(**changes):
    return dict(role_name='construction_api',read_only=True,is_superuser=False,bypasses_rls=False,
        rls_enabled=True,rls_active=True,owner_member=False,resolves_to_public=True,can_read_projection=True,**changes)


@pytest.fixture
def managed():
    c=SimpleNamespace(user=uuid4(),client=uuid4(),statements=[],active=False,events=[],row=runtime_row(),closed=False)
    class Session:
        def in_transaction(self):return c.active
        async def __aenter__(self):c.events.append('session-enter');return self
        async def __aexit__(self,*exc):c.closed=True;c.events.append('session-exit')
        @asynccontextmanager
        async def begin(self):
            c.active=True;c.events.append('begin')
            try:
                yield
            except BaseException:
                c.events.append('rollback');raise
            else:c.events.append('commit-read-only')
            finally:c.active=False
        async def execute(self,statement,parameters=None):
            c.statements.append((str(statement),parameters))
            return SimpleNamespace(mappings=lambda:SimpleNamespace(one_or_none=lambda:c.row))
    c.session=Session()
    c.factory=lambda:c.session
    async def verified(session):
        assert session is c.session and c.statements[0][0]=='SET TRANSACTION READ ONLY'
        c.events.append('verified')
        return 'construction_api'
    c.guard=SimpleNamespace(ensure_verified=AsyncMock(side_effect=verified))
    return c


@pytest.mark.asyncio
async def test_dedicated_lookup_transaction_binds_no_tenant_and_checks_actual_role_rls(managed):
    c=managed
    async with construction_client_unlink_lookup_transaction(user_id=c.user,client_id=c.client,
        session_factory=c.factory,identity_guard=c.guard) as session:
        assert session is c.session and c.active is True
        assert len(c.statements)==3
        sql,params=c.statements[1]
        assert params=={'user_id':str(c.user),'client_id':str(c.client)}
        for setting in ('app.current_tenant_id','app.current_invitation_id','app.current_invitation_recipient_id'):
            assert "set_config('"+setting+"', '', true)" in sql
        assert "set_config('row_security', 'on', true)" in sql
        assert ':user_id, true' in sql and ':client_id, true' in sql
        assert str(c.user) not in sql and str(c.client) not in sql
        runtime=c.statements[2][0]
        assert 'row_security_active' in runtime and 'rolbypassrls' in runtime and 'pg_has_role' in runtime
        assert 'SELECT *' not in runtime and 'SET ROLE' not in runtime
    assert c.closed and c.active is False and c.events[-2:]==['commit-read-only','session-exit']


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['read_only','rls_enabled','rls_active','resolves_to_public','can_read_projection',
    'is_superuser','bypasses_rls','owner_member','role_name','missing','extra'])
async def test_unsafe_connection_or_catalog_shape_cannot_yield_lookup_session(managed,bad):
    c=managed
    if bad=='missing':c.row=None
    elif bad=='extra':c.row['unreviewed']=True
    elif bad=='role_name':c.row['role_name']=''
    else:c.row[bad]=bad in ('is_superuser','bypasses_rls','owner_member')
    with pytest.raises(ConstructionDatabaseConfigurationError):
        async with construction_client_unlink_lookup_transaction(user_id=c.user,client_id=c.client,
            session_factory=c.factory,identity_guard=c.guard):
            raise AssertionError('Unsafe lookup must not yield.')
    assert c.closed and 'rollback' in c.events


@pytest.mark.asyncio
@pytest.mark.parametrize('which',['user','client'])
async def test_invalid_ids_are_rejected_before_allocating_a_connection(managed,which):
    c=managed
    factory=AsyncMock(side_effect=AssertionError('No connection for invalid IDs.'))
    with pytest.raises(TypeError):
        async with construction_client_unlink_lookup_transaction(user_id='body-user' if which=='user' else c.user,
            client_id='body-client' if which=='client' else c.client,session_factory=factory):pass
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[RuntimeError,asyncio.CancelledError])
async def test_lookup_context_closes_transaction_on_error_and_cancellation(managed,error):
    c=managed
    with pytest.raises(error):
        async with construction_client_unlink_lookup_transaction(user_id=c.user,client_id=c.client,
            session_factory=c.factory,identity_guard=c.guard):raise error()
    assert c.closed and c.active is False and 'rollback' in c.events


@pytest.mark.asyncio
async def test_binding_requires_existing_transaction(managed):
    c=managed
    with pytest.raises(ConstructionDatabaseConfigurationError):
        await bind_client_unlink_lookup_context(c.session,user_id=c.user,client_id=c.client)
    assert c.statements==[]


@pytest.fixture
async def own_link(unlink_pipeline):
    p,c=unlink_pipeline,unlink_pipeline.c
    # Only local SQL fixtures: current_setting predicates are simulated here.
    # PostgreSQL RLS and physical policies require separate native acceptance.
    c.sync.execute(text("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"));c.sync.commit()
    p.settings={'transaction_read_only':'on','app.current_tenant_id':'','app.current_invitation_id':'',
        'app.current_invitation_recipient_id':'',OWN_LINK_USER_SETTING:str(c.target),OWN_LINK_CLIENT_SETTING:str(c.client)}
    raw=c.sync.connection().connection.driver_connection
    raw.create_function('current_setting',1,lambda name:p.settings.get(name))
    raw.create_function('current_setting',2,lambda name,missing:p.settings.get(name))
    class Platform:
        @property
        def no_autoflush(self):return c.sync.no_autoflush
        async def execute(self,statement):
            p.platform_queries.append(statement)
            return c.sync.execute(statement)
    p.platform_queries=[]
    p.platform=Platform()
    p.own_service=build_construction_client_own_link_context_service(session=c.session,platform_session=p.platform)
    p.principal=ApiPlatformPrincipal(c.target)
    c.access.has_active_platform_membership.side_effect=AssertionError('Own lookup must not require membership.')
    c.access.list_active_grants.side_effect=AssertionError('Own lookup must not require Construction grants.')
    return p


@pytest.mark.asyncio
async def test_real_own_lookup_returns_only_three_ids_and_no_tenant_or_card_authority(own_link):
    p,c=own_link,own_link.c
    before=snapshot(p)
    count=len(c.statements)
    context=await p.own_service.resolve_own_link(principal=p.principal,client_id=c.client)
    assert context==Context(c.client,c.tenant,c.target) and snapshot(p)==before
    assert set(context.__dataclass_fields__)=={'client_id','tenant_id','user_id'}
    with pytest.raises(FrozenInstanceError):context.tenant_id=c.foreign
    assert all(not hasattr(context,name) for name in ('display_name','phone','email','notes','row_version','projects','roles','permissions'))
    statement=c.statements[count]
    assert list(statement.selected_columns.keys())==['client_id','tenant_id','user_id']
    sql=str(statement.compile(dialect=postgresql.dialect())).lower()
    assert 'current_setting' in sql and 'deleted_at is null' in sql
    assert 'for update' not in sql and 'display_name' not in sql and 'row_version' not in sql
    assert statement.get_execution_options()['autoflush'] is False
    assert len(p.platform_queries)==2
    assert all('users.status' in str(q) and 'user_roles' not in str(q) and 'users.tenant_id' not in str(q) for q in p.platform_queries)
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['missing','foreign_owner','unlinked','archived','foreign_client'])
async def test_unavailable_or_other_owned_clients_have_one_safe_result(own_link,case):
    p,c=own_link,own_link.c
    client_id=c.client
    if case=='missing':client_id=uuid4();p.settings[OWN_LINK_CLIENT_SETTING]=str(client_id)
    elif case=='foreign_client':
        client_id=uuid4()
        c.sync.execute(insert(ConstructionClient.__table__).values(id=client_id,tenant_id=c.foreign,
            display_name='Private foreign fixture',client_type='person',linked_user_id=c.foreign_user))
        p.settings[OWN_LINK_CLIENT_SETTING]=str(client_id)
    else:
        values={'linked_user_id':c.foreign_user} if case=='foreign_owner' else {'linked_user_id':None} if case=='unlinked' else {'deleted_at':c.now}
        c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(**values))
    c.sync.commit()
    before=snapshot(p)
    with pytest.raises(Denied) as caught:await p.own_service.resolve_own_link(principal=p.principal,client_id=client_id)
    assert str(caught.value)=='Own client link is not available.' and snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('setting',['transaction_read_only','app.current_tenant_id','app.current_invitation_id',
    'app.current_invitation_recipient_id',OWN_LINK_USER_SETTING,OWN_LINK_CLIENT_SETTING])
async def test_wrong_read_mode_tenant_invitation_or_owner_context_cannot_resolve_link(own_link,setting):
    p,c=own_link,own_link.c
    p.settings[setting]='off' if setting=='transaction_read_only' else str(uuid4())
    before=snapshot(p)
    with pytest.raises(Denied):await p.own_service.resolve_own_link(principal=p.principal,client_id=c.client)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_no_bound_owner_settings_cannot_resolve_even_the_actual_owner(own_link):
    p,c=own_link,own_link.c
    p.settings.clear()
    with pytest.raises(Denied):await p.own_service.resolve_own_link(principal=p.principal,client_id=c.client)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['plain','actor','client_id','inactive','disappeared_after_read','different_identity'])
async def test_fresh_canonical_identity_is_required_and_caller_tenant_is_not_accepted(own_link,bad):
    p,c=own_link,own_link.c
    principal=p.principal;client_id=c.client
    if bad=='plain':principal=SimpleNamespace(user_id=c.target)
    elif bad=='actor':principal=ApiActorContext(c.target,c.foreign,None,(),'ru',None,'active')
    elif bad=='client_id':client_id='body-client'
    elif bad=='inactive':
        c.sync.execute(text("UPDATE users SET status='blocked' WHERE id=:id"),{'id':c.target.hex})
    else:
        original=p.own_service.repository.find_own_link
        async def find(**kw):
            context=await original(**kw)
            if bad=='disappeared_after_read':
                c.sync.execute(text("UPDATE users SET status='blocked' WHERE id=:id"),{'id':c.target.hex})
            else:p.own_service.identity.require_principal=AsyncMock(return_value=ApiPlatformPrincipal(c.foreign_user))
            return context
        p.own_service.repository.find_own_link=find
    with pytest.raises(ConstructionClientValidationError if bad=='client_id' else ApiIdentityAccessError):
        await p.own_service.resolve_own_link(principal=principal,client_id=client_id)
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('race',['ownership','version','none'])
async def test_lookup_does_not_replace_mutation_ownership_and_version_checks(own_link,race):
    p,c=own_link,own_link.c
    context=await p.own_service.resolve_own_link(principal=p.principal,client_id=c.client)
    c.sync.rollback()  # Dedicated lookup finishes before the mutation.
    p.settings.clear()
    if race!='none':
        values={'linked_user_id':c.foreign_user,'row_version':3} if race=='ownership' else {'row_version':3}
        c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(**values));c.sync.commit()
    before=snapshot(p)
    actor=ApiActorContext(p.principal.user_id,None,None,(),'ru',None,'active')
    if not c.sync.in_transaction():c.sync.begin()
    operation=p.workflow.unlink_self(actor=actor,tenant_id=context.tenant_id,client_id=context.client_id,
        expected_row_version=2,idempotency_key=None)
    if race=='none':
        result=await operation
        assert result.changed and result.row_version==3 and result.linked_user_id is None
        assert c.audit_rows[-1].payload['authorization_method']=='authenticated_link_owner'
    else:
        with pytest.raises(Denied if race=='ownership' else ConstructionRowVersionConflictError):await operation
        assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['extra','wrong_client','wrong_user','untyped','bad_tenant'])
async def test_repository_rejects_unexpected_or_foreign_projection_receipts(bad):
    user,client,tenant=uuid4(),uuid4(),uuid4()
    row={'client_id':client,'tenant_id':tenant,'user_id':user}
    if bad=='extra':row['display_name']='private client'
    elif bad=='wrong_client':row['client_id']=uuid4()
    elif bad=='wrong_user':row['user_id']=uuid4()
    elif bad=='untyped':row=SimpleNamespace(**row)
    else:row['tenant_id']='body-tenant'
    session=SimpleNamespace(in_transaction=lambda:True,no_autoflush=nullcontext(),execute=AsyncMock(
        return_value=SimpleNamespace(mappings=lambda:SimpleNamespace(one_or_none=lambda:row))))
    with pytest.raises(ValueError):await Repository(session).find_own_link(user_id=user,client_id=client)


def test_no_tenant_argument_no_identity_permissions_and_separate_sessions(own_link):
    p,c=own_link,own_link.c
    for method in (construction_client_unlink_lookup_transaction,Repository.find_own_link,Service.resolve_own_link):
        assert 'tenant_id' not in inspect.signature(method).parameters
    assert p.own_service.repository.session is c.session
    assert p.own_service.identity.session is p.platform and p.platform is not c.session
    with pytest.raises(ValueError):build_construction_client_own_link_context_service(session=c.session,platform_session=c.session)


@pytest.mark.asyncio
async def test_repository_requires_an_active_transaction_without_opening_or_committing_one():
    session=SimpleNamespace(in_transaction=lambda:False,no_autoflush=nullcontext(),execute=AsyncMock())
    with pytest.raises(ValueError):await Repository(session).find_own_link(user_id=uuid4(),client_id=uuid4())
    session.execute.assert_not_called()
