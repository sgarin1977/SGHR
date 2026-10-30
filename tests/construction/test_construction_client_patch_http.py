"""Local HTTP and actual SQLite client/audit transactions; no native acceptance."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text, update
from sqlalchemy.orm.attributes import set_committed_value

import api.routes.construction_client_patch as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.routes.construction import get_construction_access_context
from construction_rate_limit_fixture import with_local_construction_rate_limit
from database.models import ConstructionClient
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_link_model import client_store
from test_construction_client_version_noop import version_case, snapshot


@pytest.fixture
def patch_http(version_case):
    c = version_case
    c.actor.permissions = ()
    c.suite = NS(require_enabled=AsyncMock())
    c.module = NS(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_client_patch_service] = lambda: c.service

    # SQLite drops timezone information when loading DateTime(timezone=True).
    # Preserve real repository SQL, ORM versioning, audit and transactions;
    # adapt only SQLite timestamp results to the PostgreSQL timestamptz contract.
    read_client = c.repo.get_active_client_for_update

    async def read_with_aware_timestamps(**kwargs):
        row = await read_client(**kwargs)
        if row is not None:
            for name in ("created_at", "updated_at"):
                value = getattr(row, name)
                if isinstance(value, datetime) and value.tzinfo is None:
                    set_committed_value(
                        row, name, value.replace(tzinfo=timezone.utc),
                    )
        return row

    c.repo.get_active_client_for_update = AsyncMock(
        side_effect=read_with_aware_timestamps,
    )
    return c


async def patch(c, fields=None, expected=1, identifier=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.patch(
            "/api/v1/construction/clients/" + str(identifier or c.client_id),
            json={"expected_row_version": expected, **(fields or {})},
            headers={"X-Request-ID": "client-patch-http"},
        )


@pytest.mark.asyncio
async def test_partial_edit_preserves_unprovided_fields_link_and_project(patch_http):
    c = patch_http
    c.sync.execute(
        update(ConstructionClient.__table__)
        .where(ConstructionClient.id == c.client_id)
        .values(phone="keep-phone", email="keep@example.test", linked_user_id=c.target)
    )
    c.sync.commit()
    before = snapshot(c)
    response = await patch(c, {"display_name": "  Edited client  ", "notes": "Note"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["display_name"] == "Edited client"
    assert body["data"]["notes"] == "Note"
    assert body["data"]["phone"] == "keep-phone"
    assert body["data"]["email"] == "keep@example.test"
    assert body["data"]["client_type"] == "person"
    assert body["data"]["linked_user_id"] == str(c.target)
    assert body["data"]["row_version"] == 2
    assert body["request_id"] == "client-patch-http"
    after = snapshot(c)
    assert after["project"] == before["project"]
    assert len(after["audit"]) == 1
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_null_clears_only_selected_contact(patch_http):
    c = patch_http
    c.sync.execute(
        update(ConstructionClient.__table__)
        .where(ConstructionClient.id == c.client_id)
        .values(phone="remove-phone", email="keep@example.test", notes="Keep note")
    )
    c.sync.commit()
    response = await patch(c, {"phone": None})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["phone"] is None
    assert data["email"] == "keep@example.test" and data["notes"] == "Keep note"
    assert data["row_version"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"display_name": " Original client "}, {"phone": None}])
async def test_current_noop_has_no_write_or_audit(patch_http, fields):
    c = patch_http
    before = snapshot(c)
    response = await patch(c, fields)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["row_version"] == 1
    assert snapshot(c) == before
    c.repo.update_client.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"notes": "Changed"}])
async def test_stale_version_including_noop_is_conflict(patch_http, fields):
    c = patch_http
    before = snapshot(c)
    response = await patch(c, fields, expected=2)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before
    c.repo.update_client.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"display_name": None}, {"display_name": "  "}, {"display_name": 42},
    {"client_type": None}, {"client_type": "other"}, {"phone": 42},
    {"email": []}, {"notes": {}}, {"linked_user_id": str(uuid4())},
    {"tenant_id": str(uuid4())}, {"deleted_at": None},
])
async def test_invalid_or_noneditable_fields_are_rejected(patch_http, fields):
    c = patch_http
    before = snapshot(c)
    response = await patch(c, fields)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.repo.update_client.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [True, 0, -1, "1", 1.0, None, 2**63])
async def test_expected_version_is_required_strict_positive_bigint(patch_http, expected):
    response = await patch(patch_http, {"notes": "Changed"}, expected=expected)
    assert response.status_code == 422, response.text
    patch_http.repo.update_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "project_scope", "suite", "module"])
async def test_security_gates_deny_before_client_mutation(patch_http, failure):
    c = patch_http
    if failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "permission":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("OWNER",), (), c.context.grants,
        )
    else:
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",),
            ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    before = snapshot(c)
    response = await patch(c, {"notes": "Forbidden"})
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.repo.update_client.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_client_is_safe_not_found(patch_http):
    c = patch_http
    before = snapshot(c)
    response = await patch(c, {"notes": "Changed"}, identifier=uuid4())
    assert response.status_code == 404, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_real_mutation_and_audit_roll_back_together(patch_http, failure):
    c = patch_http
    if failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private audit failure")
    else:
        c.session.commit.side_effect = RuntimeError("private commit failure")
    before = snapshot(c)
    response = await patch(c, {"notes": "Must roll back"})
    assert response.status_code == 500, response.text
    assert "private" not in response.text
    assert snapshot(c) == before
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlite_timestamp_adapter_preserves_strict_http_contract(patch_http):
    from pydantic import ValidationError

    c = patch_http
    raw = snapshot(c)["client"]
    assert raw.created_at.tzinfo is None

    row = await c.repo.get_active_client_for_update(
        tenant_id=c.actor.tenant_id, client_id=c.client_id,
    )
    values = {
        name: getattr(row, name)
        for name in routes.ClientReadResponse.model_fields
    }
    item = routes.ClientReadResponse.model_validate(values)
    assert item.created_at.utcoffset() is not None
    assert item.updated_at.utcoffset() is not None
    assert not c.sync.is_modified(row)

    for name in ("created_at", "updated_at"):
        invalid = dict(values)
        invalid[name] = values[name].replace(tzinfo=None)
        with pytest.raises(ValidationError):
            routes.ClientReadResponse.model_validate(invalid)

    c.repo.update_client.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
