"""Local project PATCH with real ORM/audit rollback, without external providers."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.exc import StaleDataError

import api.routes.construction_project_patch as routes
from database.models import ConstructionProject
from database.repositories.construction import ConstructionProjectRepository
from services.construction.construction_projects import ConstructionProjectService
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_link_model import client_store
from test_construction_client_version_noop import version_case, snapshot
from test_construction_client_patch_http import patch_http


ADDRESS_FIELDS = (
    "address_formatted", "country_code", "region", "city", "postal_code",
    "latitude", "longitude", "address_provider", "provider_place_id",
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
def project_patch_http(patch_http):
    c = patch_http
    c.sync.execute(
        update(ConstructionProject.__table__)
        .where(ConstructionProject.id == c.project_id)
        .values(
            name="Original project", address_raw="Original address",
            responsible_user_id=c.target, comment="Keep comment",
            address_formatted="Verified original address",
            country_code="PT", region="Region", city="City", postal_code="1000-001",
            latitude=Decimal("38.7"), longitude=Decimal("-9.1"),
            address_provider="fixture", provider_place_id="fixture-place",
            address_verification_status="verified",
        )
    )
    c.sync.commit()
    c.project_repo = ConstructionProjectRepository(c.session)
    read_project = c.project_repo.get_active_project_for_update

    async def read_with_aware_timestamps(**kwargs):
        project = await read_project(**kwargs)
        if project is not None:
            for name in ("created_at", "updated_at"):
                value = getattr(project, name)
                if isinstance(value, datetime) and value.tzinfo is None:
                    set_committed_value(project, name, value.replace(tzinfo=timezone.utc))
        return project

    c.project_repo.get_active_project_for_update = AsyncMock(
        side_effect=read_with_aware_timestamps,
    )
    c.project_repo.update_project = AsyncMock(wraps=c.project_repo.update_project)
    c.project_clients = NS(get_active_client=AsyncMock(
        side_effect=AssertionError("Unchanged client must not be reassigned."),
    ))
    c.project_access = NS(has_active_platform_membership=AsyncMock(
        side_effect=AssertionError("Unchanged responsible must not be reassigned."),
    ))
    c.project_service = _with_project_currency_fixture(ConstructionProjectService(
        session=c.session, repository=c.project_repo,
        client_repository=c.project_clients, access_repository=c.project_access,
        event_repository=c.events,
    ))
    c.app.dependency_overrides[routes.get_project_patch_service] = lambda: c.project_service
    return c


async def patch(c, fields=None, *, expected=1, identifier=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.patch(
            "/api/v1/construction/projects/" + str(identifier or c.project_id),
            json={"expected_row_version": expected, **(fields or {})},
            headers={"X-Request-ID": "project-patch-http"},
        )


@pytest.mark.asyncio
async def test_name_edit_preserves_address_context_client_responsible_and_workflow(project_patch_http):
    c = project_patch_http
    before = snapshot(c)
    response = await patch(c, {"name": "  Edited project  "})
    assert response.status_code == 200, response.text
    body = response.json()
    item = body["data"]
    assert item["id"] == str(c.project_id) and item["tenant_id"] == str(c.tenant)
    assert item["name"] == "Edited project" and item["row_version"] == 2
    assert item["client_id"] == str(c.client_id)
    assert item["responsible_user_id"] == str(c.target)
    assert item["status"] == "draft" and item["comment"] == "Keep comment"
    assert item["address_raw"] == "Original address"
    assert item["address_verification_status"] == "verified"
    after = snapshot(c)
    assert after["client"] == before["client"]
    for name in ADDRESS_FIELDS:
        assert getattr(after["project"], name) == getattr(before["project"], name)
    assert body["request_id"] == "project-patch-http"
    assert len(after["audit"]) == 1
    payload = json.loads(after["audit"][0].payload)
    assert payload["related_client_id"] == str(c.client_id)
    assert payload["changes"] == [
        {"field": "name", "old_value": "Original project", "new_value": "Edited project"},
    ]
    c.session.commit.assert_awaited_once()
    c.project_clients.get_active_client.assert_not_awaited()
    c.project_access.has_active_platform_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_address_edit_resets_old_verification_and_audits_changes(project_patch_http):
    c = project_patch_http
    response = await patch(c, {"address_raw": "  New address  "})
    assert response.status_code == 200, response.text
    item = response.json()["data"]
    assert item["address_raw"] == "New address" and item["row_version"] == 2
    assert item["address_verification_status"] == "pending"
    assert all(item[name] is None for name in ADDRESS_FIELDS)
    assert item["client_id"] == str(c.client_id)
    assert item["responsible_user_id"] == str(c.target)
    changes = json.loads(snapshot(c)["audit"][0].payload)["changes"]
    assert {change["field"] for change in changes} == (
        set(ADDRESS_FIELDS) | {"address_raw", "address_verification_status"}
    )


@pytest.mark.asyncio
async def test_explicit_null_clears_comment_only(project_patch_http):
    c = project_patch_http
    response = await patch(c, {"comment": None})
    assert response.status_code == 200, response.text
    item = response.json()["data"]
    assert item["comment"] is None and item["row_version"] == 2
    assert item["name"] == "Original project"
    assert item["address_verification_status"] == "verified"


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {}, {"name": " Original project "}, {"address_raw": " Original address "},
    {"comment": "Keep comment"},
])
async def test_current_noop_has_no_write_version_change_or_audit(project_patch_http, fields):
    c = project_patch_http
    before = snapshot(c)
    response = await patch(c, fields)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["row_version"] == 1
    assert snapshot(c) == before
    c.project_repo.update_project.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"name": "Changed"}])
async def test_stale_version_is_conflict_even_for_noop(project_patch_http, fields):
    c = project_patch_http
    before = snapshot(c)
    response = await patch(c, fields, expected=2)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before
    c.project_repo.update_project.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"name": None}, {"name": " "}, {"address_raw": None}, {"address_raw": ""},
    {"comment": 42}, {"client_id": None}, {"responsible_user_id": None},
    {"status": "completed"}, {"deleted_at": None},
    {"address_verification_status": "verified"}, {"tenant_id": str(uuid4())},
])
async def test_invalid_or_unexposed_fields_are_rejected(project_patch_http, fields):
    response = await patch(project_patch_http, fields)
    assert response.status_code == 422, response.text
    project_patch_http.project_repo.update_project.assert_not_called()
    project_patch_http.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [True, 0, -1, "1", 1.0, None, 2**31])
async def test_expected_version_is_strict_positive_database_integer(project_patch_http, expected):
    response = await patch(project_patch_http, {"name": "Changed"}, expected=expected)
    assert response.status_code == 422, response.text
    project_patch_http.project_repo.update_project.assert_not_called()


@pytest.mark.asyncio
async def test_exact_project_scope_is_allowed(project_patch_http):
    c = project_patch_http
    c.context = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",),
        ("construction.projects.edit",),
        (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
    )
    response = await patch(c, {"name": "Scoped project"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["name"] == "Scoped project"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_scope", "permission", "suite", "module"])
async def test_security_gates_prevent_mutation(project_patch_http, failure):
    c = project_patch_http
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
            (ConstructionGrantContext("PROJECT_MANAGER", "project", uuid4()),),
        )
    before = snapshot(c)
    response = await patch(c, {"name": "Forbidden"})
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.project_repo.update_project.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_missing_project_is_safe_not_found(project_patch_http):
    c = project_patch_http
    before = snapshot(c)
    response = await patch(c, {"name": "Changed"}, identifier=uuid4())
    assert response.status_code == 404, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "stale_flush"])
async def test_actual_mutation_and_audit_roll_back_together(project_patch_http, failure):
    c = project_patch_http
    if failure == "audit":
        original_audit = c.events.create_event.side_effect
        async def fail_after_insert(**values):
            await original_audit(**values)
            raise RuntimeError("private project audit failure")
        c.events.create_event.side_effect = fail_after_insert
    elif failure == "commit":
        c.session.commit.side_effect = RuntimeError("private project commit failure")
    else:
        async def stale_after_flush():
            c.sync.flush()
            raise StaleDataError("private stale project write")
        c.session.flush.side_effect = stale_after_flush
    before = snapshot(c)
    response = await patch(c, {"name": "Must roll back"})
    assert response.status_code == (409 if failure == "stale_flush" else 500), response.text
    if failure == "stale_flush":
        assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert "private" not in response.text
    assert snapshot(c) == before
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_after_real_audit_insert_rolls_back(project_patch_http):
    c = project_patch_http
    original_audit = c.events.create_event.side_effect
    async def cancel_after_insert(**values):
        await original_audit(**values)
        raise asyncio.CancelledError()
    c.events.create_event.side_effect = cancel_after_insert
    before = snapshot(c)
    with pytest.raises(asyncio.CancelledError):
        await c.project_service.patch_project(
            actor=c.actor, access_context=c.context, project_id=c.project_id,
            expected_row_version=1, fields={"name": "Must roll back"},
        )
    assert snapshot(c) == before
    c.session.rollback.assert_awaited_once()
