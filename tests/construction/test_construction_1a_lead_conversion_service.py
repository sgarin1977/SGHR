import json
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_lead_conversion_rules import ConstructionLeadConversionSelection
from services.construction.construction_lead_conversion import (
    ConstructionLeadConversionCommand, ConstructionLeadConversionService,
    ConstructionLeadConversionNotFoundError, ConstructionLeadConversionOperationError,
)
from services.construction.construction_lead_rules import ConstructionLeadValidationError, ConstructionLeadTransitionError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


@pytest.fixture
def conversion_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), now=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
    c.actor = SimpleNamespace(tenant_id=c.tenant, user_id=c.user, status="active")
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",),
        tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]), (ConstructionGrantContext("OWNER", "tenant", c.tenant),))
    c.session = SimpleNamespace(in_transaction=lambda: True, no_autoflush=nullcontext(),
        commit=AsyncMock(), rollback=AsyncMock())
    c.lead = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, row_version=4, status="contacted",
        converted_client_id=None, converted_project_id=None, deleted_at=None,
        source="partner", name="Client from lead", phone="+351 123", email="private@example.com",
        address_raw="Lead address", work_type_description="Work context", comment="Private comment",
        responsible_user_id=c.user, updated_at=c.now)
    c.client = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, deleted_at=None,
        display_name=c.lead.name, client_type="person", phone=c.lead.phone, email=c.lead.email, notes=None)
    c.project = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, deleted_at=None, client_id=c.client.id,
        name="Project name", address_raw=c.lead.address_raw, responsible_user_id=c.user,
        comment="Work context\n\nPrivate comment", created_by=c.user, status="draft", row_version=1)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[
            SimpleNamespace(role="OWNER", scope_type="tenant", scope_id=c.tenant, status="active", expires_at=None)]))
    async def complete(**kwargs):
        return SimpleNamespace(**{**vars(c.lead), "status": "won", "row_version": 5,
            "converted_client_id": kwargs["client_id"], "converted_project_id": kwargs["project_id"],
            "updated_at": kwargs["as_of"]})
    c.repo = SimpleNamespace(session=c.session, get_active_lead_for_update=AsyncMock(return_value=c.lead),
        complete_conversion=AsyncMock(side_effect=complete))
    c.clients = SimpleNamespace(session=c.session, get_active_client_for_update=AsyncMock(return_value=c.client),
        get_active_client=AsyncMock(return_value=c.client), create_client=AsyncMock(return_value=c.client))
    c.projects = SimpleNamespace(session=c.session, get_active_project_for_update=AsyncMock(return_value=c.project),
        create_project=AsyncMock(return_value=c.project))
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock())
    c.service = ConstructionLeadConversionService(session=c.session, repository=c.repo,
        client_repository=c.clients, project_repository=c.projects, access_repository=c.access,
        event_repository=c.events, now_provider=lambda: c.now)
    return c


def command(c, mode="new", **changes):
    selection = ConstructionLeadConversionSelection(
        None if mode == "new" else c.client.id, c.project.id if mode == "existing" else None)
    data = dict(expected_row_version=4, selection=selection,
        client_type="person" if mode == "new" else None,
        project_name=None if mode == "existing" else "Project name")
    data.update(changes)
    return ConstructionLeadConversionCommand(**data)


async def run(c, mode="new", **changes):
    options = dict(actor=c.actor, access_context=c.context, lead_id=c.lead.id,
        command=command(c, mode), trace_id="convert-trace")
    options.update(changes)
    return await c.service.convert_lead(**options)


def no_transaction_ownership(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


def no_writes(c):
    c.clients.create_client.assert_not_called()
    c.projects.create_project.assert_not_called()
    c.repo.complete_conversion.assert_not_called()
    c.events.create_event.assert_not_called()
    no_transaction_ownership(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
@pytest.mark.parametrize("status", ["new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation"])
async def test_three_modes_and_every_nonterminal_status_use_one_conversion_audit(conversion_case, mode, status):
    c = conversion_case
    c.lead.status = status
    result = await run(c, mode)
    assert not result.is_replay and result.lead.status == "won" and result.lead.row_version == 5
    assert result.client is c.client and result.project is c.project
    assert c.clients.create_client.await_count == (mode == "new")
    assert c.projects.create_project.await_count == (mode != "existing")
    c.repo.complete_conversion.assert_awaited_once_with(tenant_id=c.tenant, lead_id=c.lead.id,
        expected_row_version=4, client_id=c.client.id, project_id=c.project.id, as_of=c.now)
    c.events.create_event.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_lead_converted"
    assert event["entity_id"] == c.lead.id and event["entity_type"] == "construction_lead"
    assert event["tenant_id"] == c.tenant and event["user_id"] == c.user
    assert event["trace_id"] == "convert-trace"
    assert event["payload"] == {
        "operation": "convert", "reason": None,
        "changes": [
            {"field": "status", "old_value": status, "new_value": "won"},
            {"field": "converted_client_id", "old_value": None, "new_value": str(c.client.id)},
            {"field": "converted_project_id", "old_value": None, "new_value": str(c.project.id)}],
        "related_client_id": str(c.client.id),
        "created_client_id": str(c.client.id) if mode == "new" else None,
        "created_project_id": str(c.project.id) if mode != "existing" else None}
    assert c.lead.email not in json.dumps(event["payload"]) and c.lead.comment not in json.dumps(event["payload"])
    if mode == "new":
        c.clients.create_client.assert_awaited_once_with(tenant_id=c.tenant, display_name=c.lead.name,
            client_type="person", phone=c.lead.phone, email=c.lead.email, notes=None)
    if mode != "existing":
        c.projects.create_project.assert_awaited_once_with(tenant_id=c.tenant, client_id=c.client.id,
            name="Project name", address_raw="Lead address", responsible_user_id=c.user,
            comment="Work context\n\nPrivate comment", created_by=c.user)
    no_transaction_ownership(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
async def test_won_replay_returns_recorded_links_without_version_check_or_writes(conversion_case, mode):
    c = conversion_case
    c.lead.status, c.lead.row_version = "won", 18
    c.lead.converted_client_id, c.lead.converted_project_id = c.client.id, c.project.id
    result = await run(c, mode)
    assert result.is_replay and result.lead.row_version == 18
    c.clients.get_active_client_for_update.assert_awaited_once_with(tenant_id=c.tenant, client_id=c.client.id)
    c.projects.get_active_project_for_update.assert_awaited_once_with(tenant_id=c.tenant, project_id=c.project.id)
    no_writes(c)


@pytest.mark.asyncio
async def test_replay_cannot_select_different_existing_targets(conversion_case):
    c = conversion_case
    c.lead.status = "won"
    c.lead.converted_client_id, c.lead.converted_project_id = uuid4(), c.project.id
    with pytest.raises(ConstructionLeadValidationError):
        await run(c, "existing")
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lost", "won_without_links", "nonterminal_with_links", "stale", "max_version"])
async def test_ineligible_state_or_version_never_creates_resources(conversion_case, failure):
    c = conversion_case
    error = ConstructionLeadValidationError
    if failure == "lost":
        c.lead.status = "lost"
        error = ConstructionLeadTransitionError
    elif failure == "won_without_links":
        c.lead.status = "won"
    elif failure == "nonterminal_with_links":
        c.lead.converted_client_id = c.client.id
    elif failure == "stale":
        c.lead.row_version = 5
        error = ConstructionRowVersionConflictError
    else:
        c.lead.row_version = 2**63-1
    cmd = command(c, expected_row_version=c.lead.row_version if failure == "max_version" else 4)
    with pytest.raises(error):
        await run(c, command=cmd)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "foreign_context", "project_only", "no_membership", "expired_grant",
    "lost_fresh_permission", "no_transaction"])
async def test_denial_precedes_writes(conversion_case, failure):
    c = conversion_case
    errors = (ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionLeadConversionOperationError)
    if failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "foreign_context":
        c.context = ConstructionAccessContext(c.user, uuid4(), c.context.roles, c.context.permissions, c.context.grants)
    elif failure == "project_only":
        c.context = ConstructionAccessContext(c.user, c.tenant, c.context.roles, c.context.permissions,
            (ConstructionGrantContext("OWNER", "project", c.project.id),))
    elif failure == "no_membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure in ("expired_grant", "lost_fresh_permission"):
        c.access.list_active_grants.return_value = [] if failure == "lost_fresh_permission" else [
            SimpleNamespace(role="OWNER", scope_type="tenant", scope_id=c.tenant, status="active", expires_at=c.now)]
    else:
        c.session.in_transaction = lambda: False
    with pytest.raises(errors):
        await run(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["lead", "client", "project"])
@pytest.mark.parametrize("failure", ["missing", "foreign", "deleted"])
async def test_unavailable_resources_are_hidden_and_cannot_be_reassigned(conversion_case, target, failure):
    c = conversion_case
    resource = getattr(c, target)
    method = {"lead": c.repo.get_active_lead_for_update,
        "client": c.clients.get_active_client_for_update, "project": c.projects.get_active_project_for_update}[target]
    if failure == "missing":
        method.return_value = None
    elif failure == "foreign":
        resource.tenant_id = uuid4()
    else:
        resource.deleted_at = c.now
    with pytest.raises(ConstructionLeadConversionNotFoundError):
        await run(c, "existing")
    no_writes(c)


@pytest.mark.asyncio
async def test_existing_project_must_belong_to_selected_client(conversion_case):
    c = conversion_case
    c.project.client_id = uuid4()
    with pytest.raises(ConstructionLeadValidationError):
        await run(c, "existing")
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["address", "responsible"])
async def test_project_requirements_are_checked_before_client_creation(conversion_case, failure):
    c = conversion_case
    if failure == "address":
        c.lead.address_raw = None
    else:
        c.access.has_active_platform_membership.side_effect = lambda **kw: kw["user_id"] == c.user
        c.lead.responsible_user_id = uuid4()
    with pytest.raises(ConstructionLeadValidationError):
        await run(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_missing_lead_address_can_be_supplied_without_overwriting_lead(conversion_case):
    c = conversion_case
    c.lead.address_raw = None
    c.project.address_raw = "Entered address"
    await run(c, command=command(c, project_address_raw=" Entered address "))
    assert c.lead.address_raw is None
    assert c.projects.create_project.await_args.kwargs["address_raw"] == "Entered address"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["client", "project", "conditional", "audit", "corrupt_result",
    "revoked_after_creates", "missing_child_audit", "changed_lead_contact"])
async def test_failure_cannot_succeed_or_commit_and_caller_owns_rollback(conversion_case, failure, monkeypatch):
    c = conversion_case
    if failure == "client":
        c.clients.create_client.side_effect = RuntimeError("private failure")
    elif failure == "project":
        c.projects.create_project.side_effect = RuntimeError("private failure")
    elif failure == "conditional":
        c.repo.complete_conversion.side_effect = None
        c.repo.complete_conversion.return_value = None
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private failure")
    elif failure == "corrupt_result":
        c.project.client_id = uuid4()
    elif failure == "changed_lead_contact":
        original = c.repo.complete_conversion.side_effect
        async def corrupted(**kwargs):
            result = await original(**kwargs)
            result.email = "unexpected@example.com"
            return result
        c.repo.complete_conversion.side_effect = corrupted
    elif failure == "revoked_after_creates":
        c.access.list_active_grants.side_effect = [
            c.access.list_active_grants.return_value, c.access.list_active_grants.return_value, []]
    else:
        async def bad_audit(self, **kwargs):
            return None
        monkeypatch.setattr("services.construction.construction_lead_conversion._ConversionCreationAudit.create_event", bad_audit)
    with pytest.raises((ConstructionLeadConversionOperationError, ConstructionPermissionDeniedError)) as caught:
        await run(c)
    assert "private failure" not in str(caught.value)
    assert c.events.create_event.await_count == (failure == "audit")
    no_transaction_ownership(c)


@pytest.mark.parametrize("changes", [
    {"expected_row_version": True}, {"expected_row_version": 0}, {"expected_row_version": 2**63},
    {"client_type": None}, {"client_type": "unknown"}, {"project_name": None},
    {"project_address_raw": " "}, {"selection": None},
])
def test_command_requires_normal_client_and_project_fields(conversion_case, changes):
    with pytest.raises(ConstructionLeadValidationError):
        command(conversion_case, **changes)


@pytest.mark.parametrize("field", ["repository", "client_repository", "project_repository", "access_repository", "event_repository"])
def test_constructor_rejects_mixed_sessions(conversion_case, field):
    c = conversion_case
    args = dict(session=c.session, repository=c.repo, client_repository=c.clients,
        project_repository=c.projects, access_repository=c.access, event_repository=c.events)
    args[field] = SimpleNamespace(session=object())
    with pytest.raises(ValueError):
        ConstructionLeadConversionService(**args)


@pytest.mark.asyncio
async def test_invalid_clock_precedes_writes(conversion_case):
    c = conversion_case
    c.service.now_provider = lambda: c.now.replace(tzinfo=None)
    with pytest.raises(ConstructionLeadConversionOperationError):
        await run(c)
    no_writes(c)


from sqlalchemy import create_engine, event as sql_event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from database.models import ConstructionClient, ConstructionProject, ConstructionLead
from database.repositories.construction import ConstructionClientRepository, ConstructionProjectRepository
from database.repositories.construction.construction_leads import ConstructionLeadRepository


@pytest.fixture
def sql_conversion(conversion_case):
    c = conversion_case
    engine = create_engine("sqlite://")
    @sql_event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.create_function("btrim", 1, lambda value: None if value is None else value.strip(" "))
        connection.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE users(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO tenants VALUES(?)", (c.tenant.hex,))
        connection.exec_driver_sql("INSERT INTO users VALUES(?)", (c.user.hex,))
        connection.exec_driver_sql("CREATE TABLE conversion_test_audit(id TEXT PRIMARY KEY,event_type TEXT,payload TEXT)")
        connection.exec_driver_sql("CREATE TABLE conversion_test_checkpoint(id TEXT PRIMARY KEY)")
    for model in (ConstructionClient, ConstructionProject, ConstructionLead):
        model.__table__.create(engine)
    c.sync = Session(engine, expire_on_commit=False)
    lead_id, client_id, project_id = c.lead.id, c.client.id, c.project.id
    with c.sync.begin():
        c.sync.add(ConstructionClient(id=client_id, tenant_id=c.tenant, display_name="Existing client",
            client_type="company", deleted_at=None))
        c.sync.flush()
        c.sync.add(ConstructionProject(id=project_id, tenant_id=c.tenant, client_id=client_id,
            name="Existing project", address_raw="Existing address", comment="Existing context",
            created_by=c.user, status="active", row_version=7, deleted_at=None))
        c.sync.add(ConstructionLead(**{**vars(c.lead), "created_at": c.now}))
        c.sync.flush()
        c.sync.execute(ConstructionLead.__table__.update().where(ConstructionLead.id == lead_id).values(row_version=4))
        c.sync.execute(ConstructionProject.__table__.update().where(ConstructionProject.id == project_id).values(row_version=7))
    class Adapter:
        def __init__(self):
            self.commit = AsyncMock(side_effect=c.sync.commit)
            self.rollback = AsyncMock(side_effect=c.sync.rollback)
        def in_transaction(self):
            return c.sync.in_transaction()
        @property
        def no_autoflush(self):
            return c.sync.no_autoflush
        def add(self, value):
            c.sync.add(value)
        async def execute(self, statement):
            return c.sync.execute(statement)
        async def flush(self):
            c.sync.flush()
    c.session = Adapter()
    c.access.session = c.session
    class SQLiteLeadRepository(ConstructionLeadRepository):
        fail_conditional = False
        async def complete_conversion(self, **kwargs):
            if self.fail_conditional:
                return None
            result = await super().complete_conversion(**kwargs)
            # SQLite loses timezone metadata; the production PostgreSQL path has no adapter.
            if result is not None and result.updated_at.tzinfo is None:
                set_committed_value(result, "updated_at", result.updated_at.replace(tzinfo=timezone.utc))
            return result
    c.repo = SQLiteLeadRepository(c.session)
    c.clients = ConstructionClientRepository(c.session)
    c.projects = ConstructionProjectRepository(c.session)
    class Events:
        session = c.session
        fail_after_insert = False
        async def create_event(self, **kwargs):
            c.sync.execute(text("INSERT INTO conversion_test_audit VALUES(:id,:event,:payload)"),
                dict(id=str(uuid4()), event=kwargs["event_type"], payload=json.dumps(kwargs["payload"])))
            if self.fail_after_insert:
                raise RuntimeError("private failure after real audit write")
    c.events = Events()
    c.service = ConstructionLeadConversionService(session=c.session, repository=c.repo,
        client_repository=c.clients, project_repository=c.projects, access_repository=c.access,
        event_repository=c.events, now_provider=lambda: c.now)
    try:
        yield c
    finally:
        c.sync.close()
        engine.dispose()


def counts(c):
    return tuple(c.sync.execute(text("SELECT count(*) FROM "+table)).scalar_one()
        for table in ("construction_clients", "construction_projects", "conversion_test_audit", "conversion_test_checkpoint"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
async def test_real_conversion_commits_resources_single_audit_and_later_work_together(sql_conversion, mode):
    c = sql_conversion
    old_client, old_project = c.client.id, c.project.id
    with c.sync.begin():
        existing = c.sync.get(ConstructionProject, old_project)
        old_values = (existing.name, existing.address_raw, existing.comment, existing.row_version)
        result = await run(c, mode)
        assert result.lead.status == "won" and result.lead.row_version == 5
        c.sync.execute(text("INSERT INTO conversion_test_checkpoint VALUES(:id)"), dict(id=str(c.lead.id)))
        assert counts(c) == (2 if mode == "new" else 1, 1 if mode == "existing" else 2, 1, 1)
        no_transaction_ownership(c)
    assert counts(c) == (2 if mode == "new" else 1, 1 if mode == "existing" else 2, 1, 1)
    original_project = c.sync.get(ConstructionProject, old_project)
    assert (original_project.name, original_project.address_raw, original_project.comment, original_project.row_version) == old_values
    assert c.sync.get(ConstructionClient, old_client).display_name == "Existing client"
    c.sync.rollback()
    with c.sync.begin():
        replay = await run(c, mode)
        assert replay.is_replay and replay.lead.row_version == 5
        assert replay.client.id == result.client.id and replay.project.id == result.project.id
        assert counts(c) == (2 if mode == "new" else 1, 1 if mode == "existing" else 2, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["conditional", "audit_after_insert", "later_work"])
async def test_real_failure_rolls_back_created_targets_lead_audit_and_later_work(sql_conversion, failure):
    c = sql_conversion
    c.repo.fail_conditional = failure == "conditional"
    c.events.fail_after_insert = failure == "audit_after_insert"
    with pytest.raises(ConstructionLeadConversionOperationError if failure != "later_work" else RuntimeError):
        with c.sync.begin():
            await run(c)
            c.sync.execute(text("INSERT INTO conversion_test_checkpoint VALUES(:id)"), dict(id=str(c.lead.id)))
            assert counts(c) == (2, 2, 1, 1)
            raise RuntimeError("caller failure after complete conversion")
    assert counts(c) == (1, 1, 0, 0)
    lead = c.sync.get(ConstructionLead, c.lead.id)
    assert lead.status == "contacted" and lead.row_version == 4
    assert lead.converted_client_id is None and lead.converted_project_id is None
    no_transaction_ownership(c)


def test_client_type_uses_normal_client_normalization(conversion_case):
    assert command(conversion_case, client_type=" PERSON ").client_type == "person"


@pytest.mark.asyncio
async def test_nested_frozen_selection_is_revalidated_before_access_queries(conversion_case):
    c = conversion_case
    cmd = command(c, "existing")
    object.__setattr__(cmd.selection, "client_id", "unparsed")
    with pytest.raises(ConstructionLeadValidationError):
        await run(c, command=cmd)
    c.access.has_active_platform_membership.assert_not_called()
    no_writes(c)
