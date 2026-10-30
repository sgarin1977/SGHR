import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context
import api.routes.construction_client_create as routes
from construction_rate_limit_fixture import with_local_construction_rate_limit
from database.models import ConstructionClient
from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError,
)
from services.construction.construction_client_create_workflow import ConstructionClientCreateWorkflow
from services.construction.construction_clients import ConstructionClientOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from test_construction_01_client_create_transaction import client_create_case, sql_client_create
from test_construction_01_project_create_workflow import MemoryRecords


def access_for(c):
    return NS(
        session=c.session,
        has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[
            NS(role="OWNER", scope_type="tenant", scope_id=c.tenant,
               status="active", expires_at=None),
        ]),
    )

def supplied(value):
    def dependency():
        return value
    return dependency

@pytest.fixture
def client_case(client_create_case):
    c = client_create_case
    c.client.linked_user_id = None
    c.client.row_version = 1
    c.client.created_at = c.client.updated_at = datetime(2026, 10, 23, tzinfo=timezone.utc)
    async def persist(**kwargs):
        for key, value in kwargs.items():
            setattr(c.client, key, value)
        return c.client
    c.repo.create_client.side_effect = persist
    c.repo.get_active_client = AsyncMock(return_value=c.client)
    c.access = access_for(c)
    c.records = MemoryRecords(c.session, [])
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(
            encryption_key=Fernet.generate_key().decode(),
        ),
    )
    c.workflow = ConstructionClientCreateWorkflow(
        session=c.session, client_service=c.service,
        access_repository=c.access, idempotency_service=c.idempotency,
    )
    return c

async def run(c, **changes):
    values = dict(
        actor=c.actor, access_context=c.context,
        display_name="Client", client_type="person",
        phone=None, email=None, notes="Private note",
        idempotency_key="client-key", trace_id="client-create-test",
    )
    values.update(changes)
    return await c.workflow.create_client(**values)

@pytest.mark.asyncio
async def test_replay_uses_encrypted_original_receipt_without_duplicate_client_or_audit(client_case):
    c = client_case
    first = await run(c)
    c.client.display_name, c.client.row_version = "Changed later", 2
    assert await run(c) == first
    c.repo.create_client.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    audit = c.events.create_event.await_args.kwargs
    assert audit["payload"]["related_client_id"] == first["id"]
    assert "Private note" not in json.dumps(audit["payload"])
    record = next(iter(c.records.records.values()))
    assert b"Private note" not in record.response_ciphertext
    assert c.idempotency.response_codec.decrypt(record.response_ciphertext) == first
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, display_name="Different request")
    assert c.repo.create_client.await_count == 1

@pytest.mark.asyncio
async def test_authority_is_refreshed_after_reservation_wait(client_case):
    c = client_case
    reserve = c.idempotency.reserve
    async def revoke(**kwargs):
        result = await reserve(**kwargs)
        c.access.list_active_grants.return_value = []
        return result
    c.idempotency.reserve = revoke
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    c.repo.create_client.assert_not_awaited()
    c.events.create_event.assert_not_awaited()
    c.session.rollback.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_real_sqlite_client_and_audit_roll_back_before_failed_completion(sql_client_create, failure):
    c = sql_client_create
    c.sync.begin()
    access = access_for(c)
    idempotency = None
    if failure == "audit":
        c.events.fail_after_insert = True
    elif failure == "complete":
        idempotency = ApiIdempotencyService(
            repository=MemoryRecords(c.session, []),
            response_codec=ApiIdempotencyResponseCodec(
                encryption_key=Fernet.generate_key().decode(),
            ),
        )
        idempotency.complete = AsyncMock(side_effect=RuntimeError("private-completion"))
    else:
        c.session.commit.side_effect = RuntimeError("private-commit")
    workflow = ConstructionClientCreateWorkflow(
        session=c.session, client_service=c.service,
        access_repository=access, idempotency_service=idempotency,
    )
    with pytest.raises(ConstructionClientOperationError):
        await workflow.create_client(
            actor=c.actor, access_context=c.context,
            display_name="SQL client", client_type="person",
            idempotency_key="sql-key" if idempotency is not None else None,
        )
    assert c.sync.execute(select(ConstructionClient.id)).all() == []
    assert c.sync.execute(text("SELECT count(*) FROM client_test_audit")).scalar_one() == 0
    c.session.rollback.assert_awaited_once()

@pytest.fixture
def client_http(client_case):
    c = client_case
    c.app = with_local_construction_rate_limit(create_app())
    for dependency, value in (
        (get_current_actor, c.actor),
        (get_construction_access_context, c.context),
        (get_tenant_suite_access_service, NS(require_enabled=AsyncMock())),
        (get_tenant_module_access_service, NS(require_enabled=AsyncMock())),
        (routes.get_client_create_workflow, c.workflow),
    ):
        c.app.dependency_overrides[dependency] = supplied(value)
    return c

async def post(c, body=None, key="client-http"):
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        return await http.post(
            "/api/v1/construction/clients",
            json=body if body is not None else {"display_name": "Client", "client_type": "person"},
            headers={"Idempotency-Key": key},
        )

@pytest.mark.asyncio
async def test_registered_http_creates_client_without_lead_or_required_contact(client_http):
    c = client_http
    first = await post(c)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["data"]["phone"] is None and body["data"]["email"] is None
    assert body["data"]["linked_user_id"] is None and body["data"]["row_version"] == 1
    assert set(body["data"]) == set(routes.ClientReadResponse.model_fields)
    replay = await post(c)
    assert replay.status_code == 201
    assert replay.json()["data"] == body["data"]
    c.repo.create_client.assert_awaited_once()
    c.events.create_event.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "user_id", "linked_user_id", "row_version"])
async def test_http_rejects_caller_authority_or_identity_fields_before_writes(client_http, field):
    c = client_http
    response = await post(c, {
        "display_name": "Client", "client_type": "person",
        field: 1 if field == "row_version" else str(uuid4()),
    })
    assert response.status_code == 422
    c.repo.create_client.assert_not_awaited()
    c.events.create_event.assert_not_awaited()
