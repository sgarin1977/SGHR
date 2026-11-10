"""CON-010 base settings contract: local HTTP and OpenAPI, no external writes."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from api.app import create_app
from api.auth import get_current_actor
from api.routes import construction_settings as routes
from construction_rate_limit_fixture import (
    with_local_construction_rate_limit, construction_module_dependencies,
)
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)

PATH = "/api/v1/construction/settings/company"


@pytest.fixture
def settings_http():
    actor = NS(user_id=uuid4(), tenant_id=uuid4(), status="active")
    context = ConstructionAccessContext(
        actor.user_id, actor.tenant_id, ("OWNER",),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"])),
        (ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    now = datetime(2026, 10, 30, 12, tzinfo=timezone(timedelta(hours=2)))
    row = NS(**{field: None for field in routes.FIELDS})
    row.tenant_id = actor.tenant_id
    row.display_name_override = "PRIVATE_SETTINGS_NAME"
    row.created_at = row.updated_at = now
    row.row_version = 7
    service = NS(
        get_for_actor=AsyncMock(return_value=row),
        create_for_actor=AsyncMock(return_value=row),
        update_for_actor=AsyncMock(return_value=row),
    )
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[routes.get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[routes.get_construction_access_context] = lambda: context
    app.dependency_overrides[routes.get_settings_service] = lambda: service
    for dependency in construction_module_dependencies(routes.router):
        app.dependency_overrides[dependency.dependency] = lambda: actor
    return NS(app=app, actor=actor, context=context, row=row, service=service, now=now)


async def request(c, method, body=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(method, PATH, json=body,
                                    headers={"X-Request-ID": "settings-contract"})


@pytest.mark.asyncio
async def test_read_returns_only_existing_fields_and_utc_times(settings_http):
    c = settings_http
    response = await request(c, "GET")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"] == {} and body["request_id"] == "settings-contract"
    assert set(body["data"]) == set(routes.FIELDS)
    assert body["data"]["tenant_id"] == str(c.actor.tenant_id)
    for field in ("created_at", "updated_at"):
        instant = datetime.fromisoformat(body["data"][field].replace("Z", "+00:00"))
        assert instant == c.now and instant.utcoffset() == timedelta(0)
    c.service.create_for_actor.assert_not_awaited()
    c.service.update_for_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_settings_stay_null_without_implicit_creation(settings_http):
    c = settings_http
    c.service.get_for_actor.return_value = None
    response = await request(c, "GET")
    assert response.status_code == 200 and response.json()["data"] is None
    c.service.create_for_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_remains_sparse_and_all_inherited_values_are_allowed(settings_http):
    c = settings_http
    response = await request(c, "POST", {})
    assert response.status_code == 201, response.text
    assert c.service.create_for_actor.await_args.kwargs["values"] == {}
    c.service.update_for_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_preserves_omitted_fields_and_explicit_inheritance(settings_http):
    c = settings_http
    body = {"expected_row_version": 7, "display_name_override": "  Crew  ",
            "phone_override": None, "document_header_text": "  \n "}
    response = await request(c, "PATCH", body)
    assert response.status_code == 200, response.text
    arguments = c.service.update_for_actor.await_args.kwargs
    assert arguments == {
        "actor": c.actor, "access_context": c.context, "expected_row_version": 7,
        "changes": {"display_name_override": "  Crew  ", "phone_override": None,
                    "document_header_text": None},
        "trace_id": "settings-contract",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "tenant_id", "user_id", "row_version", "legal_name", "tax_rate", "default_currency",
])
async def test_unapproved_or_authority_fields_never_reach_writer(settings_http, field):
    c = settings_http
    response = await request(c, "POST", {field: "private-spoofed-value"})
    assert response.status_code == 422, response.text
    assert "private-spoofed-value" not in response.text
    c.service.create_for_actor.assert_not_awaited()
    c.service.update_for_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, -1, True, "7", 7.0, 2**63])
async def test_patch_version_is_a_positive_strict_bigint(settings_http, version):
    c = settings_http
    response = await request(c, "PATCH", {
        "expected_row_version": version, "phone_override": None,
    })
    assert response.status_code == 422, response.text
    c.service.update_for_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"phone_override": None}, {"expected_row_version": 7},
])
async def test_patch_requires_both_version_and_an_explicit_field(settings_http, body):
    c = settings_http
    response = await request(c, "PATCH", body)
    assert response.status_code == 422, response.text
    c.service.update_for_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"phone_override": 123}, {"display_name_override": "x" * 256},
    {"logo_file_id_override": "not-a-file-uuid"}, {"accent_color": "red"},
])
async def test_invalid_field_types_and_limits_fail_before_domain(settings_http, body):
    c = settings_http
    response = await request(c, "POST", body)
    assert response.status_code == 422, response.text
    c.service.create_for_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
async def test_foreign_result_cannot_escape_through_response_projection(settings_http, method):
    c = settings_http
    c.row.tenant_id = uuid4()
    body = None if method == "GET" else {"phone_override": None}
    if method == "PATCH":
        body["expected_row_version"] = 7
    response = await request(c, method, body)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_SETTINGS_NAME" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["version", "timestamp", "missing_write_result"])
async def test_corrupt_result_is_a_sanitized_failure(settings_http, failure):
    c = settings_http
    if failure == "version":
        c.row.row_version = True
    elif failure == "timestamp":
        c.row.created_at = c.now.replace(tzinfo=None)
    else:
        c.service.create_for_actor.return_value = None
    response = await request(c, "POST", {"phone_override": None})
    assert response.status_code == 500
    assert "PRIVATE_SETTINGS_NAME" not in response.text


def test_openapi_describes_the_existing_resource_without_new_fields_or_key_requirement(settings_http):
    document = settings_http.app.openapi()
    operations = document["paths"][PATH]
    schemas = document["components"]["schemas"]

    def model(method):
        schema = operations[method]["requestBody"]["content"]["application/json"]["schema"]
        return schemas[schema["$ref"].rsplit("/", 1)[-1]]

    create = model("post")
    patch = model("patch")
    expected = set(routes.FIELDS) - {"tenant_id", "created_at", "updated_at", "row_version"}
    assert set(create["properties"]) == expected
    assert create["additionalProperties"] is False
    assert set(patch["properties"]) == expected | {"expected_row_version"}
    assert patch["required"] == ["expected_row_version"]
    assert patch["minProperties"] == 2
    version = patch["properties"]["expected_row_version"]
    assert version["type"] == "integer" and version["minimum"] == 1
    assert version["maximum"] == 2**63 - 1
    assert "409" in operations["patch"]["responses"]
    assert all(parameter["name"] != "Idempotency-Key"
               for method in ("get", "post", "patch")
               for parameter in operations[method].get("parameters", []))
    assert set(operations) == {"get", "post", "patch"}
