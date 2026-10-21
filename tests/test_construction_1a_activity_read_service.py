from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value

from database.repositories.construction_activity_reads import ConstructionActivityReadRepository
from database.repositories.construction_activities import ConstructionActivityCursor
from services.construction_activity_read import (
    ConstructionActivityReadService, ConstructionActivityReadCursor, ConstructionActivityReadPage,
    ConstructionActivityReadNotFoundError, ConstructionActivityReadOperationError,
    PUBLIC_ACTIVITY_PLANNED_FIELDS, build_construction_activity_read_service,
)
from services.construction_activity_rules import ConstructionActivityValidationError
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_activity_terminal_service import terminal_case, sql_terminal
from test_construction_1a_activity_model import TABLE, activity_db, values
from test_construction_1a_lead_model import lead_db


def service(c):
    return ConstructionActivityReadService(session=c.session,repository=c.repo,lead_repository=c.lead_repo,
        client_repository=c.client_repo,project_repository=c.project_repo,access_repository=c.access,now_provider=lambda:c.now)


@pytest.fixture
def read_case(terminal_case):
    c=terminal_case
    for name in ("lead","client","project"):
        setattr(getattr(c,name+"_repo"),"get_active_"+name,AsyncMock(return_value=getattr(c,name)))
    c.row.private_internal="Never public"
    c.repo=SimpleNamespace(session=c.session,list_visible_planned_activities=AsyncMock(return_value=[c.row]),
        get_next_action_for_lead=AsyncMock(return_value=c.row))
    c.service=service(c)
    return c


def read_only(c):
    c.session.add.assert_not_called();c.session.flush.assert_not_called()
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called();c.events.create_event.assert_not_called()


async def listing(c, **changes):
    options=dict(actor=c.actor,access_context=c.context,lead_id=c.lead.id)
    options.update(changes)
    return await c.service.list_planned_activities(**options)


async def next_action(c, **changes):
    options=dict(actor=c.actor,access_context=c.context,lead_id=c.lead.id)
    options.update(changes)
    return await c.service.get_next_action_for_lead(**options)


def project_context(c):
    c.row.lead_id=None;c.row.project_id=c.project.id
    c.grant.role,c.grant.scope_type,c.grant.scope_id="PROJECT_MANAGER","project",c.project.id
    c.context=ConstructionAccessContext(c.user,c.tenant,("PROJECT_MANAGER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        (ConstructionGrantContext("PROJECT_MANAGER","project",c.project.id),))


@pytest.mark.asyncio
async def test_materialized_projection_next_action_includes_overdue_and_never_mutates(read_case):
    c=read_case;c.row.due_at=c.now-timedelta(days=7)
    page=await listing(c)
    assert isinstance(page,ConstructionActivityReadPage) and page.next_cursor is None
    item=page.items[0]
    assert set(item)==PUBLIC_ACTIVITY_PLANNED_FIELDS and len(item)==18
    assert item["id"]==str(c.row.id) and item["status"]=="planned"
    assert await next_action(c)==item
    c.row.comment="Later change"
    assert item["comment"]=="Private comment"
    assert "deleted_at" not in item and "private_internal" not in item
    assert c.repo.list_visible_planned_activities.await_args.kwargs==dict(tenant_id=c.tenant,allowed_project_ids=None,
        lead_id=c.lead.id,client_id=None,project_id=None,responsible_user_id=None,limit=20,after=None)
    read_only(c)


@pytest.mark.asyncio
async def test_read_only_observer_can_read_without_edit_permission(read_case):
    c=read_case;project_context(c);c.grant.role="OBSERVER"
    c.context=ConstructionAccessContext(c.user,c.tenant,("OBSERVER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["OBSERVER"]),
        (ConstructionGrantContext("OBSERVER","project",c.project.id),))
    assert (await listing(c,lead_id=None,project_id=c.project.id)).items
    with pytest.raises(ConstructionScopeDeniedError):await next_action(c)
    assert "construction.projects.edit" not in c.context.permissions
    read_only(c)


@pytest.mark.asyncio
async def test_multiple_parent_filters_are_conjunctive_and_keep_original_links(read_case):
    c=read_case;c.row.client_id=c.client.id;c.row.project_id=c.project.id
    page=await listing(c,client_id=c.client.id,project_id=c.project.id,responsible_user_id=c.user)
    assert page.items[0]["lead_id"]==str(c.lead.id) and page.items[0]["client_id"]==str(c.client.id)
    assert page.items[0]["project_id"]==str(c.project.id)
    for name in ("lead","client","project"):
        assert getattr(getattr(c,name+"_repo"),"get_active_"+name).await_count==2
    read_only(c)


@pytest.mark.asyncio
async def test_parent_archived_while_activity_query_is_awaited_prevents_publication(read_case):
    c=read_case
    async def query(**kw):c.lead.deleted_at=c.now;return [c.row]
    c.repo.list_visible_planned_activities.side_effect=query
    with pytest.raises(ConstructionActivityReadNotFoundError):await listing(c)
    read_only(c)


@pytest.mark.asyncio
async def test_read_requires_caller_transaction_before_queries(read_case):
    c=read_case;c.session.in_transaction=lambda:False
    with pytest.raises(ConstructionActivityReadOperationError):await listing(c)
    with pytest.raises(ConstructionActivityReadOperationError):await next_action(c)
    c.repo.list_visible_planned_activities.assert_not_called();c.access.has_active_platform_membership.assert_not_called()
    read_only(c)


@pytest.mark.asyncio
async def test_existing_lead_without_planned_activity_has_null_next_action(read_case):
    c=read_case;c.repo.get_next_action_for_lead.return_value=None;c.repo.list_visible_planned_activities.return_value=[]
    assert await next_action(c) is None
    page=await listing(c)
    assert page.items==() and page.next_cursor is None
    read_only(c)


@pytest.mark.asyncio
async def test_project_scope_is_passed_to_sql_and_responsible_does_not_grant_access(read_case):
    c=read_case;project_context(c);c.row.responsible_user_id=uuid4();c.row.created_by_user_id=uuid4()
    assert (await listing(c,lead_id=None,project_id=c.project.id)).items
    assert c.repo.list_visible_planned_activities.await_args.kwargs["allowed_project_ids"]==(c.project.id,)
    with pytest.raises(ConstructionScopeDeniedError):await next_action(c)
    with pytest.raises(ConstructionScopeDeniedError):await listing(c,project_id=c.project.id)
    with pytest.raises(ConstructionScopeDeniedError):await listing(c,lead_id=None,project_id=uuid4())
    assert c.repo.list_visible_planned_activities.await_count==1
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("method",[listing,next_action])
@pytest.mark.parametrize("failure",["inactive","wrong_tenant","wrong_user","permission","membership","revoked","expired"])
async def test_initial_and_current_access_denials_precede_activity_query(read_case,method,failure):
    c=read_case
    if failure=="inactive":c.actor.status="blocked"
    elif failure=="membership":c.access.has_active_platform_membership.return_value=False
    elif failure=="revoked":c.grant.status="revoked"
    elif failure=="expired":c.grant.expires_at=c.now
    else:c.context=ConstructionAccessContext(uuid4() if failure=="wrong_user" else c.user,
        uuid4() if failure=="wrong_tenant" else c.tenant,("OWNER",),() if failure=="permission" else tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await method(c)
    c.repo.list_visible_planned_activities.assert_not_called();c.repo.get_next_action_for_lead.assert_not_called()
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("method",[listing,next_action])
@pytest.mark.parametrize("failure",["missing","foreign","deleted"])
async def test_parent_availability_is_checked_before_activity_query(read_case,method,failure):
    c=read_case
    if failure=="missing":c.lead_repo.get_active_lead.return_value=None
    elif failure=="foreign":c.lead.tenant_id=uuid4()
    else:c.lead.deleted_at=c.now
    with pytest.raises(ConstructionActivityReadNotFoundError):await method(c)
    c.repo.list_visible_planned_activities.assert_not_called();c.repo.get_next_action_for_lead.assert_not_called()
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("method",[listing,next_action])
async def test_access_loss_while_query_is_awaited_prevents_publication(read_case,method):
    c=read_case
    async def revoke(**kw):c.grant.status="revoked";return [c.row] if method is listing else c.row
    getattr(c.repo,"list_visible_planned_activities" if method is listing else "get_next_action_for_lead").side_effect=revoke
    with pytest.raises(ConstructionPermissionDeniedError):await method(c)
    read_only(c)


@pytest.mark.asyncio
async def test_access_loss_during_final_parent_read_is_rechecked(read_case):
    c=read_case
    async def parent(**kw):
        if c.lead_repo.get_active_lead.await_count==2:c.grant.status="revoked"
        return c.lead
    c.lead_repo.get_active_lead.side_effect=parent
    with pytest.raises(ConstructionPermissionDeniedError):await listing(c)
    read_only(c)


@pytest.mark.asyncio
async def test_lost_tenant_scope_cannot_publish_count_or_cursor_even_for_empty_result(read_case):
    c=read_case;c.repo.list_visible_planned_activities.return_value=[]
    initial=c.grant
    narrower=SimpleNamespace(role="PROJECT_MANAGER",scope_type="project",scope_id=c.project.id,status="active",expires_at=None)
    c.access.list_active_grants.side_effect=[[initial],[narrower]]
    with pytest.raises(ConstructionScopeDeniedError):await listing(c,lead_id=None)
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("method",[listing,next_action])
@pytest.mark.parametrize("field,value",[("tenant_id",uuid4()),("deleted_at",True),("status","completed"),
    ("row_version",True),("lead_id",uuid4()),("due_at","bad"),("timezone","Unknown/Zone"),("completed_at",True)])
async def test_malformed_or_out_of_query_activity_is_not_published(read_case,method,field,value):
    c=read_case;setattr(c.row,field,value)
    with pytest.raises(ConstructionActivityReadOperationError):await method(c)
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["duplicate","reversed","oversize"])
async def test_broken_page_order_or_limit_is_rejected(read_case,failure):
    c=read_case
    second=SimpleNamespace(**{**vars(c.row),"id":uuid4(),"due_at":c.now+timedelta(days=1)})
    c.repo.list_visible_planned_activities.return_value=[c.row,c.row] if failure=="duplicate" else [second,c.row] if failure=="reversed" else [c.row,second,second]
    with pytest.raises(ConstructionActivityReadOperationError):await listing(c,limit=1 if failure=="oversize" else 20)
    read_only(c)


@pytest.mark.asyncio
async def test_cursor_is_bound_to_actor_query_and_last_returned_row_not_lookahead(read_case):
    c=read_case;second=SimpleNamespace(**{**vars(c.row),"id":uuid4(),"due_at":c.now+timedelta(days=1)})
    c.repo.list_visible_planned_activities.return_value=[c.row,second]
    page=await listing(c,limit=1);cursor=page.next_cursor
    assert cursor==ConstructionActivityReadCursor(c.tenant,c.user,(c.lead.id,None,None,None),c.row.due_at,c.row.id)
    c.repo.list_visible_planned_activities.return_value=[second]
    last=await listing(c,limit=1,after=cursor)
    assert last.next_cursor is None and last.items[0]["id"]==str(second.id)
    assert c.repo.list_visible_planned_activities.await_args.kwargs["after"]==ConstructionActivityCursor(c.tenant,cursor.due_at,cursor.activity_id)
    for changes in [dict(tenant_id=uuid4()),dict(user_id=uuid4()),dict(filters=(None,None,None,None))]:
        foreign=ConstructionActivityReadCursor(**{**vars(cursor),**changes})
        with pytest.raises(ConstructionActivityValidationError):await listing(c,after=foreign)
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes",[dict(limit=True),dict(limit=0),dict(limit=101),dict(lead_id="uuid"),
    dict(client_id=False),dict(project_id="uuid"),dict(responsible_user_id=1),dict(after={})])
async def test_invalid_query_is_rejected_before_repository_read(read_case,changes):
    c=read_case
    with pytest.raises(ConstructionActivityValidationError):await listing(c,**changes)
    c.repo.list_visible_planned_activities.assert_not_called();read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("method",[listing,next_action])
async def test_query_failure_is_sanitized_and_transaction_left_to_caller(read_case,method):
    c=read_case
    getattr(c.repo,"list_visible_planned_activities" if method is listing else "get_next_action_for_lead").side_effect=RuntimeError("private query detail")
    with pytest.raises(ConstructionActivityReadOperationError) as caught:await method(c)
    assert "private" not in str(caught.value);read_only(c)


def test_factory_shared_session_and_cursor_revalidation(read_case):
    c=read_case;built=build_construction_activity_read_service(session=c.session,now_provider=lambda:c.now)
    assert all(repo.session is c.session for repo in (built.repository,built.lead_repository,built.client_repository,built.project_repository,built.access_repository))
    c.repo.session=object()
    with pytest.raises(ValueError):service(c)
    with pytest.raises(ConstructionActivityValidationError):ConstructionActivityReadCursor(c.tenant,c.user,(None,None,None,None),c.now.replace(tzinfo=None),uuid4())


@pytest.mark.asyncio
async def test_sql_query_applies_scope_filters_before_limit_without_locks_or_autoflush(read_case):
    c=read_case;c.session.execute.return_value=SimpleNamespace(scalars=Mock(return_value=SimpleNamespace(all=Mock(return_value=[]))))
    repo=ConstructionActivityReadRepository(c.session)
    await repo.list_visible_planned_activities(tenant_id=c.tenant,allowed_project_ids=(c.project.id,),limit=2)
    statement=c.session.execute.await_args.args[0];sql=str(statement.compile(dialect=postgresql.dialect()))
    assert "construction_activities.project_id IN" in sql
    assert "construction_activities.lead_id IS NULL" in sql and "construction_activities.client_id IS NULL" in sql
    assert "construction_activities.tenant_id =" in sql and "construction_activities.deleted_at IS NULL" in sql
    assert "ORDER BY construction_activities.due_at ASC, construction_activities.id ASC" in sql
    assert "FOR UPDATE" not in sql and statement._limit_clause.value==3
    assert statement.get_execution_options()["autoflush"] is False
    read_only(c)


@pytest.fixture
def sql_read(sql_terminal):
    c=sql_terminal
    class SQLiteReads(ConstructionActivityReadRepository):
        @staticmethod
        def dates(row):
            if row is not None:
                for field in ("due_at","created_at","updated_at"):
                    value=getattr(row,field)
                    if value is not None and value.tzinfo is None:set_committed_value(row,field,value.replace(tzinfo=timezone.utc))
            return row
        async def list_visible_planned_activities(self,**kw):
            return [self.dates(row) for row in await super().list_visible_planned_activities(**kw)]
        async def get_next_action_for_lead(self,**kw):
            return self.dates(await super().get_next_action_for_lead(**kw))
    c.repo=SQLiteReads(c.session)
    for name in ("client","project"):
        setattr(getattr(c,name+"_repo"),"get_active_"+name,AsyncMock(return_value=getattr(c,name)))
    c.service=service(c);c.session.sync.begin()
    return c


@pytest.mark.asyncio
async def test_real_next_action_and_pagination_exclude_terminal_deleted_and_foreign_rows(sql_read):
    c=sql_read
    first,second=UUID(int=1),UUID(int=2)
    base=dict(tenant_id=c.tenant,type="call",lead_id=c.lead.id,due_at=c.now-timedelta(days=5),timezone="UTC",created_by_user_id=c.user,
        created_at=c.now,updated_at=c.now,row_version=1,status="planned")
    with c.session.sync.get_bind().begin() as connection:
        connection.execute(insert(TABLE).values(**{**base,"id":first}))
        connection.execute(insert(TABLE).values(**{**base,"id":second}))
        connection.execute(insert(TABLE).values(**{**base,"id":uuid4(),"due_at":c.now-timedelta(days=9),"status":"completed","completed_at":c.now}))
        connection.execute(insert(TABLE).values(**{**base,"id":uuid4(),"due_at":c.now-timedelta(days=10),"deleted_at":c.now}))
        from database.models import ConstructionLead
        foreign=connection.execute(select(ConstructionLead.__table__.c.id,ConstructionLead.__table__.c.tenant_id)
            .where(ConstructionLead.__table__.c.tenant_id!=c.tenant)).first()
        connection.execute(insert(TABLE).values(**{**base,"id":uuid4(),"tenant_id":foreign.tenant_id,"lead_id":foreign.id,
            "due_at":c.now-timedelta(days=20)}))
    assert (await next_action(c))["id"]==str(first)
    page1=await listing(c,limit=1);assert page1.items[0]["id"]==str(first)
    page2=await listing(c,limit=1,after=page1.next_cursor);assert page2.items[0]["id"]==str(second)
    page3=await listing(c,limit=1,after=page2.next_cursor);assert page3.items[0]["id"]==str(c.row.id) and page3.next_cursor is None
    assert c.session.commit.await_count==0 and c.session.rollback.await_count==0
    assert c.session.sync.execute(text("SELECT count(*) FROM terminal_service_audit")).scalar_one()==0


@pytest.mark.asyncio
async def test_real_project_scope_filters_before_pagination_and_keeps_crm_rows_private(activity_db,read_case):
    c=read_case;engine,ids=activity_db
    c.tenant,c.user=ids["a"],ids["user"];c.actor.tenant_id,c.actor.user_id=c.tenant,c.user;c.project.id=ids["project_a"]
    project_context(c)
    visible_a,visible_b=UUID(int=11),UUID(int=12)
    with engine.begin() as connection:
        for data in [
            values(ids,id=UUID(int=1),lead_id=ids["lead_a"],project_id=ids["project_a"],due_at=c.now-timedelta(days=10)),
            values(ids,id=visible_a,lead_id=None,project_id=ids["project_a"],due_at=c.now),
            values(ids,id=visible_b,lead_id=None,project_id=ids["project_a"],due_at=c.now),
            values(ids,id=UUID(int=2),tenant_id=ids["b"],lead_id=None,project_id=ids["project_b"],due_at=c.now-timedelta(days=10)),
        ]:connection.execute(insert(TABLE).values(**data))
    from test_construction_1a_lead_repository import AsyncSessionAdapter
    class Adapter(AsyncSessionAdapter):
        @property
        def no_autoflush(self):return self.sync.no_autoflush
    session=Adapter(engine)
    try:
        repo=ConstructionActivityReadRepository(session)
        page1=await repo.list_visible_planned_activities(tenant_id=c.tenant,allowed_project_ids=(c.project.id,),limit=1)
        assert [row.id for row in page1]==[visible_a,visible_b]
        page2=await repo.list_visible_planned_activities(tenant_id=c.tenant,allowed_project_ids=(c.project.id,),limit=1,
            after=ConstructionActivityCursor(c.tenant,c.now,visible_a))
        assert [row.id for row in page2]==[visible_b]
        assert await repo.list_visible_planned_activities(tenant_id=c.tenant,allowed_project_ids=(),limit=1)==[]
        assert session.commit.await_count==0 and session.rollback.await_count==0
    finally:session.sync.close()
