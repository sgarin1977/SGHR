"""CON-005 local transaction tests; no native PG/concurrency acceptance."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from database.models import ApiIdempotencyRecord, ConstructionProjectArea
from database.repositories.api_idempotency import ApiIdempotencyRepository
from database.repositories.construction import ConstructionProjectAreaRepository
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyService, ApiIdempotencyResponseCodec,
)
from services.construction.construction_area_create_workflow import ConstructionAreaCreateWorkflow
from services.construction.construction_areas import (
    ConstructionProjectAreaService, ConstructionProjectAreaValidationError,
    ConstructionProjectAreaOperationError, ConstructionProjectAreaProjectNotFoundError,
)
from services.construction.construction_foundation import ConstructionProjectTerminalError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
)


@pytest.fixture
def area_case():
    c = NS(tenant=uuid4(), user=uuid4(), project_id=uuid4())
    engine = create_engine("sqlite://")
    c.sync = Session(engine)
    for statement in (
        "CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)",
        "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
        """CREATE TABLE construction_projects (
            id CHAR(32) PRIMARY KEY, tenant_id CHAR(32) NOT NULL,
            UNIQUE(tenant_id,id)
        )""",
        "CREATE TABLE area_probe_audit (event_type TEXT, entity_id TEXT)",
    ):
        c.sync.execute(text(statement))
    c.sync.execute(text("INSERT INTO tenants VALUES (:id)"), {"id": c.tenant.hex})
    c.sync.execute(text("INSERT INTO users VALUES (:id)"), {"id": c.user.hex})
    c.sync.execute(text("INSERT INTO construction_projects VALUES (:id,:tenant)"), {
        "id": c.project_id.hex, "tenant": c.tenant.hex,
    })
    c.sync.commit()
    ConstructionProjectArea.__table__.create(engine)
    ApiIdempotencyRecord.__table__.create(engine)

    async def execute(statement):
        return c.sync.execute(statement)
    async def flush():
        c.sync.flush()
    c.session = NS(
        add=c.sync.add, execute=execute, flush=flush,
        in_transaction=c.sync.in_transaction,
        commit=AsyncMock(side_effect=c.sync.commit),
        rollback=AsyncMock(side_effect=c.sync.rollback),
    )
    c.actor = NS(user_id=c.user, tenant_id=c.tenant, status="active")
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.create",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    c.project = NS(
        id=c.project_id, tenant_id=c.tenant, status="active",
        deleted_at=None, row_version=9,
    )
    c.projects = NS(
        session=c.session,
        get_active_project_for_update=AsyncMock(return_value=c.project),
    )
    c.access = NS(
        session=c.session,
        has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[NS(
            role="OWNER", scope_type="tenant", scope_id=c.tenant,
            status="active", expires_at=None,
        )]),
    )
    async def audit(**kwargs):
        c.sync.execute(text(
            "INSERT INTO area_probe_audit VALUES (:event,:entity)"
        ), {"event": kwargs["event_type"], "entity": str(kwargs["entity_id"])})
    c.events = NS(session=c.session, create_event=AsyncMock(side_effect=audit))
    c.repo = ConstructionProjectAreaRepository(c.session)
    c.service = ConstructionProjectAreaService(
        session=c.session, repository=c.repo,
        project_repository=c.projects, event_repository=c.events,
    )
    records = ApiIdempotencyRepository(c.session)
    # Exercise the shared service/ORM and real SQL rollback locally.
    # PostgreSQL ON CONFLICT and actual row locks remain native acceptance.
    c.idempotency = ApiIdempotencyService(
        repository=NS(
            session=c.session,
            get_active_record_for_update=records.get_active_record_for_update,
            create_record=records.create_record,
            complete_record=records.complete_record,
        ),
        response_codec=ApiIdempotencyResponseCodec(
            encryption_key=Fernet.generate_key().decode("ascii"),
        ),
    )
    c.workflow = ConstructionAreaCreateWorkflow(
        session=c.session, area_service=c.service,
        access_repository=c.access, idempotency_service=c.idempotency,
        now_provider=lambda: datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    yield c
    c.sync.close()
    engine.dispose()


async def run(c, **changes):
    if not c.sync.in_transaction():
        c.sync.begin()
    values = dict(
        actor=c.actor, access_context=c.context, project_id=c.project_id,
        area_type="ROOM", name="  Room  ", sort_order=None,
        idempotency_key="area-key", trace_id="area-create-test",
    )
    values.update(changes)
    return await c.workflow.create_area(**values)


def counts(c):
    return tuple(c.sync.execute(text(
        "SELECT count(*) FROM " + table
    )).scalar_one() for table in (
        "construction_project_areas", "area_probe_audit", "api_idempotency_records",
    ))


@pytest.mark.asyncio
async def test_area_create_has_one_commit_and_keeps_nullable_order(area_case):
    c = area_case
    response = await run(c)
    assert counts(c) == (1, 1, 1)
    assert response["status"] == "draft" and response["row_version"] == 1
    assert response["name"] == "Room" and response["sort_order"] is None
    assert c.project.row_version == 9
    record = c.sync.execute(select(ApiIdempotencyRecord)).scalar_one()
    assert c.idempotency.response_codec.decrypt(record.response_ciphertext) == response
    assert c.session.commit.await_count == 1
    c.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_area_create_replay_returns_original_without_overwriting_later_change(area_case):
    c = area_case
    original = await run(c)
    row = c.sync.get(ConstructionProjectArea, UUID(original["id"]))
    row.name = "Later name"
    c.sync.commit()
    assert await run(c) == original
    assert counts(c) == (1, 1, 1)
    assert c.sync.get(ConstructionProjectArea, row.id).name == "Later name"
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_area_create_changed_body_with_same_key_conflicts(area_case):
    c = area_case
    await run(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, name="Different")
    assert counts(c) == (1, 1, 1)


@pytest.mark.asyncio
async def test_area_create_does_not_renumber_equal_sort_orders(area_case):
    c = area_case
    first = await run(c, sort_order=3)
    second = await run(c, name="Second", sort_order=3, idempotency_key="second-key")
    assert first["id"] != second["id"]
    assert first["sort_order"] == second["sort_order"] == 3
    assert counts(c) == (2, 2, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "grants"])
async def test_area_create_refreshes_authority_after_waiting_for_project(area_case, failure):
    c = area_case
    async def after_wait(**kwargs):
        if failure == "membership":
            c.access.has_active_platform_membership.return_value = False
        else:
            c.access.list_active_grants.return_value = []
        return c.project
    c.projects.get_active_project_for_update.side_effect = after_wait
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    assert counts(c) == (0, 0, 0)
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_area_create_rejects_terminal_project(area_case, status):
    c = area_case
    c.project.status = status
    with pytest.raises(ConstructionProjectTerminalError):
        await run(c)
    assert counts(c) == (0, 0, 0)


@pytest.mark.asyncio
async def test_area_create_rejects_missing_project(area_case):
    c = area_case
    c.projects.get_active_project_for_update.return_value = None
    with pytest.raises(ConstructionProjectAreaProjectNotFoundError):
        await run(c)
    assert counts(c) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "cancel"])
async def test_area_create_real_sql_rows_and_receipt_roll_back_together(area_case, failure):
    c = area_case
    if failure in ("audit", "cancel"):
        original = c.events.create_event
        async def fail(**kwargs):
            await original(**kwargs)
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("local audit failure")
        c.events.create_event = fail
    elif failure == "complete":
        original = c.idempotency.complete
        async def fail(**kwargs):
            await original(**kwargs)
            raise RuntimeError("local completion failure")
        c.idempotency.complete = fail
    else:
        async def fail():
            c.sync.flush()
            raise RuntimeError("local commit failure")
        c.session.commit.side_effect = fail
    expected = asyncio.CancelledError if failure == "cancel" else ConstructionProjectAreaOperationError
    with pytest.raises(expected):
        await run(c)
    assert counts(c) == (0, 0, 0)
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, 0, 1, "false"])
async def test_area_create_transaction_mode_requires_boolean(area_case, mode):
    c = area_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await c.service.create_area(
            actor=c.actor, access_context=c.context, project_id=c.project_id,
            area_type="ROOM", name="Room", sort_order=None, manage_transaction=mode,
        )
    assert counts(c) == (0, 0, 0)


@pytest.mark.asyncio
async def test_area_create_unkeyed_internal_workflow_needs_no_replay_configuration(area_case):
    c = area_case
    c.workflow.idempotency = None
    response = await run(c, idempotency_key=None)
    assert response["name"] == "Room"
    assert counts(c) == (1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("sort_order", True), ("sort_order", 2147483648),
    ("area_type", "UNKNOWN"), ("name", " "),
])
async def test_area_create_invalid_input_does_not_write(area_case, field, value):
    c = area_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await run(c, **{field: value})
    assert counts(c) == (0, 0, 0)
