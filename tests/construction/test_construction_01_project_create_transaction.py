import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from database.models import ConstructionProject
from database.repositories.construction import ConstructionProjectRepository
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
)
from services.construction.construction_projects import (
    ConstructionProjectOperationError, ConstructionProjectService, ConstructionProjectValidationError,
)



def _with_project_currency_fixture(value):
    """Explicit organization currency for pre-existing isolated service doubles."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    target = getattr(value, "service", value)
    target.currency_reader = SimpleNamespace(
        session=target.session, get_for_tenant=AsyncMock(return_value="EUR"),
    )
    return value

@pytest.fixture
def create_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), client_id=uuid4(), responsible=uuid4())
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active")
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.create",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    c.project = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, client_id=None, row_version=1, status="draft")
    c.session = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock(), in_transaction=lambda: True,
    )
    c.repo = SimpleNamespace(session=c.session, create_project=AsyncMock(return_value=c.project))
    c.clients = SimpleNamespace(get_active_client=AsyncMock(return_value=SimpleNamespace(id=c.client_id)))
    c.access = SimpleNamespace(has_active_platform_membership=AsyncMock(return_value=True))
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock())
    c.service = _with_project_currency_fixture(ConstructionProjectService(
        session=c.session, repository=c.repo, client_repository=c.clients,
        access_repository=c.access, event_repository=c.events,
    ))
    return c


async def create(c, **options):
    return await c.service.create_project(
        actor=c.actor, access_context=c.context, name="  Transaction project  ",
        address_raw="  Project address  ", client_id=None, responsible_user_id=None,
        comment="Project comment", trace_id="project-create-transaction", **options,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, True, False])
async def test_create_preserves_validation_and_audit_with_explicit_transaction_owner(create_case, mode):
    c = create_case
    options = {} if mode is None else {"manage_transaction": mode}
    assert await create(c, **options) is c.project
    c.repo.create_project.assert_awaited_once_with(
        tenant_id=c.tenant, client_id=None, name="Transaction project",
        responsible_user_id=None, comment="Project comment",
        address_raw="Project address", created_by=c.user, currency="EUR",
    )
    assert c.events.create_event.await_count == 1
    audit = c.events.create_event.await_args.kwargs
    assert audit["event_type"] == "construction_project_created"
    assert audit["entity_id"] == c.project.id
    assert audit["tenant_id"] == c.tenant and audit["user_id"] == c.user
    assert audit["trace_id"] == "project-create-transaction"
    if mode is False:
        c.session.commit.assert_not_called()
    else:
        c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, False])
@pytest.mark.parametrize("failure", ["audit", "repository", "client_lookup", "responsible_lookup"])
async def test_failures_leave_rollback_to_the_selected_transaction_owner(create_case, mode, failure):
    c = create_case
    error = RuntimeError("private-storage-detail")
    arguments = dict(
        actor=c.actor, access_context=c.context, name="Project", address_raw="Address",
        client_id=None, responsible_user_id=None, comment=None, manage_transaction=mode,
    )
    if failure == "audit": c.events.create_event.side_effect = error
    elif failure == "repository": c.repo.create_project.side_effect = error
    elif failure == "client_lookup":
        arguments["client_id"] = c.client_id
        c.clients.get_active_client.side_effect = error
    else:
        arguments["responsible_user_id"] = c.responsible
        c.access.has_active_platform_membership.side_effect = error
    with pytest.raises(ConstructionProjectOperationError) as caught:
        await c.service.create_project(**arguments)
    assert "private-storage-detail" not in str(caught.value)
    c.session.commit.assert_not_called()
    if mode:
        c.session.rollback.assert_awaited_once()
    else:
        c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, 0, 1, "false"])
async def test_invalid_transaction_mode_is_rejected_before_queries(create_case, mode):
    c = create_case
    with pytest.raises(ConstructionProjectValidationError):
        await create(c, manage_transaction=mode)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_transaction", "missing_transaction_method"])
async def test_borrowed_mode_requires_an_active_caller_transaction(create_case, failure):
    c = create_case
    if failure == "missing_transaction": c.session.in_transaction = lambda: False
    else: del c.session.in_transaction
    with pytest.raises(ConstructionProjectOperationError):
        await create(c, manage_transaction=False)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_borrowed_mode_does_not_bypass_permission(create_case):
    c = create_case
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",), (), c.context.grants)
    with pytest.raises(ConstructionPermissionDeniedError):
        await create(c, manage_transaction=False)
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_commit_failure_still_rolls_back(create_case):
    c = create_case
    c.session.commit.side_effect = RuntimeError("private-commit-detail")
    with pytest.raises(ConstructionProjectOperationError):
        await create(c)
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_awaited_once()


@pytest.fixture
def real_create(create_case):
    c = create_case
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def foreign_keys(db, _record):
        db.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE users (id CHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE construction_clients (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), UNIQUE(tenant_id,id))"
        )
        conn.exec_driver_sql("INSERT INTO tenants(id) VALUES (?)", (c.tenant.hex,))
        conn.exec_driver_sql("INSERT INTO users(id) VALUES (?)", (c.user.hex,))
        conn.exec_driver_sql(
            "CREATE TABLE tx_test_audit (id TEXT PRIMARY KEY, event_type TEXT, entity_id TEXT, payload TEXT)"
        )
        conn.exec_driver_sql("CREATE TABLE tx_test_result (id TEXT PRIMARY KEY)")
    ConstructionProject.__table__.create(engine)
    c.sql_session = Session(engine)
    async def flush():
        c.sql_session.flush()
    c.session = SimpleNamespace(
        add=c.sql_session.add, flush=flush,
        in_transaction=lambda: c.sql_session.in_transaction(),
        commit=AsyncMock(), rollback=AsyncMock(),
    )
    c.repo = ConstructionProjectRepository(c.session)
    class Events:
        def __init__(self):
            self.session = c.session
            self.fail_after_insert = False
        async def create_event(self, **kwargs):
            c.sql_session.execute(text(
                "INSERT INTO tx_test_audit(id,event_type,entity_id,payload) VALUES(:id,:event,:entity,:payload)"
            ), {
                "id": str(uuid4()), "event": kwargs["event_type"],
                "entity": str(kwargs["entity_id"]), "payload": json.dumps(kwargs["payload"], default=str),
            })
            if self.fail_after_insert:
                raise RuntimeError("forced-audit-storage-failure")
    c.events = Events()
    c.service = _with_project_currency_fixture(ConstructionProjectService(
        session=c.session, repository=c.repo, event_repository=c.events,
    ))
    try:
        yield c
    finally:
        c.sql_session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_real_caller_commits_project_audit_and_later_result_together(real_create):
    c = real_create
    with c.sql_session.begin():
        project = await create(c, manage_transaction=False)
        assert c.sql_session.in_transaction()
        assert project.status == "draft" and project.row_version == 1
        c.sql_session.execute(text("INSERT INTO tx_test_result(id) VALUES(:id)"), {"id": str(project.id)})
        c.session.commit.assert_not_called()
        c.session.rollback.assert_not_called()
    assert c.sql_session.execute(text("SELECT count(*) FROM construction_projects")).scalar_one() == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_result")).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "later_workflow"])
async def test_real_caller_failure_rolls_back_flushed_project_and_audit(real_create, failure):
    c = real_create
    c.events.fail_after_insert = failure == "audit"
    with pytest.raises(ConstructionProjectOperationError if failure == "audit" else RuntimeError):
        with c.sql_session.begin():
            project = await create(c, manage_transaction=False)
            c.sql_session.execute(text("INSERT INTO tx_test_result(id) VALUES(:id)"), {"id": str(project.id)})
            assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1
            raise RuntimeError("forced-later-workflow-failure")
    assert c.sql_session.execute(text("SELECT count(*) FROM construction_projects")).scalar_one() == 0
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 0
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_result")).scalar_one() == 0
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
