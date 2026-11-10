"""Stored project currency read contract; local ASGI and HTTP transport only."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

import api.routes.construction_project_read as routes
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_permissions import ConstructionGrantContext
from services.construction.construction_project_create_workflow import PUBLIC_PROJECT_FIELDS
from test_construction_01_project_read_http import project_http, get, no_write


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["EUR", "USD", "JPY", "DEM", None])
async def test_get_returns_exact_stored_currency_without_touching_project(project_http, currency):
    c = project_http
    c.project.currency = currency
    original = dict(vars(c.project))
    response = await get(c)
    assert response.status_code == 200, response.text
    assert "currency" in response.json()["data"]
    assert response.json()["data"]["currency"] == currency
    assert vars(c.project) == original
    c.repo.get_active_project.assert_awaited_once_with(
        tenant_id=c.tenant, project_id=c.project_id)
    no_write(c)


@pytest.mark.asyncio
async def test_country_actor_and_current_organization_default_do_not_supply_project_currency(project_http):
    c = project_http
    c.project.country_code = "PT"
    c.project.currency = "JPY"
    c.actor.default_currency = "USD"
    c.organization = SimpleNamespace(default_currency="EUR")
    first = await get(c)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["currency"] == "JPY"
    c.organization.default_currency = "GBP"
    c.project.currency = None
    second = await get(c)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["currency"] is None
    assert c.project.row_version == 7
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["eur", "", "EU", "EURO", "ÉUR", "USD\n", 123, True, {}, []])
async def test_invalid_stored_currency_is_sanitized_server_error(project_http, currency):
    c = project_http
    c.project.currency = currency
    response = await get(c)
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "data" not in body and "Read project" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_missing_currency_projection_is_not_silently_treated_as_legacy_null(project_http):
    c = project_http
    del c.project.currency
    response = await get(c)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["permission", "scope", "foreign"])
async def test_currency_is_not_exposed_outside_authorized_project(project_http, denial):
    c = project_http
    c.project.currency = "JPY"
    if denial == "permission":
        c.context = replace(c.context, permissions=())
    elif denial == "scope":
        c.context = replace(c.context, grants=(
            ConstructionGrantContext("PROJECT_MANAGER", "project", uuid4()),
        ))
    else:
        c.project.tenant_id = c.foreign
    response = await get(c)
    assert response.status_code == (404 if denial == "foreign" else 403)
    assert "data" not in response.json() and "JPY" not in response.text
    if denial != "foreign":
        c.repo.get_active_project.assert_not_called()
    no_write(c)


def test_openapi_extends_only_details_and_keeps_original_receipt_shape(project_http):
    schema = project_http.app.openapi()
    schemas = schema["components"]["schemas"]
    operation = schema["paths"]["/api/v1/construction/projects/{id}"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProjectDetailsEnvelope")
    details = schemas["ProjectDetailsResponse"]
    assert "currency" in details["required"]
    alternatives = details["properties"]["currency"]["anyOf"]
    assert {"type": "null"} in alternatives
    code = next(item for item in alternatives if item.get("type") == "string")
    assert code["minLength"] == code["maxLength"] == 3
    assert code["pattern"] == "^[A-Z]{3}$"
    assert "currency" not in schemas["ProjectReadResponse"]["properties"]
    create = schema["paths"]["/api/v1/construction/projects"]["post"]
    assert create["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProjectReadEnvelope")
    assert set(routes.ProjectReadResponse.model_fields) == PUBLIC_PROJECT_FIELDS
    assert set(routes.ProjectDetailsResponse.model_fields) == PUBLIC_PROJECT_FIELDS | {"currency"}


def test_creation_projection_stays_independent_of_current_project_currency(project_http):
    c = project_http
    c.project.currency = "JPY"
    receipt = routes.public_project(c.project, tenant_id=c.tenant, project_id=c.project_id)
    assert set(receipt) == PUBLIC_PROJECT_FIELDS
    assert "currency" not in receipt
    with pytest.raises(ValidationError):
        routes.ProjectDetailsResponse.model_validate(receipt)
    current = routes.public_project_details(c.project, tenant_id=c.tenant, project_id=c.project_id)
    assert current == {**receipt, "currency": "JPY"}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["project_details", "project_card"])
@pytest.mark.parametrize("currency", ["EUR", None])
async def test_actual_get_and_existing_api_client_preserve_currency(project_http, method, currency):
    c = project_http
    c.project.currency = currency
    client_id = uuid4()
    c.project.client_id = client_id
    session = PlatformApiClientSession(c.user, c.tenant, SecretStr("local-currency-read"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        client = ConstructionApiClient(http=http)
        options = dict(session=session, project_id=c.project_id)
        if method == "project_card":
            options["client_id"] = client_id
        result = await getattr(client, method)(**options)
    assert result.data["currency"] == currency
    assert result.data["id"] == str(c.project_id)
    assert result.data["tenant_id"] == str(c.tenant)
    assert result.data["client_id"] == str(client_id)
    assert "can_edit_project" in result.meta["capabilities"]
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["project_details", "project_card"])
@pytest.mark.parametrize("change", ["legacy", "bad_currency", "extra", "foreign_client"])
async def test_typed_client_supports_original_shape_and_rejects_invalid_details(project_http, method, change):
    c = project_http
    client_id = uuid4()
    c.project.client_id = client_id
    original = routes.public_project(c.project, tenant_id=c.tenant, project_id=c.project_id)
    payload = deepcopy(original)
    if change != "legacy":
        payload["currency"] = "EUR"
    if change == "bad_currency":
        payload["currency"] = "eur"
    elif change == "extra":
        payload["private_field"] = "private"
    elif change == "foreign_client":
        payload["client_id"] = str(uuid4())
    seen = []
    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/construction/projects/" + str(c.project_id)
        assert not request.url.query and request.content == b""
        assert "X-Tenant-ID" not in request.headers
        return httpx.Response(200, json={
            "data": payload, "meta": {}, "request_id": "local-currency-read",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        client = ConstructionApiClient(http=http)
        options = dict(
            session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-only")),
            project_id=c.project_id,
        )
        if method == "project_card":
            options["client_id"] = client_id
        denied = change in {"bad_currency", "extra"} or (change == "foreign_client" and method == "project_card")
        if denied:
            with pytest.raises(ConstructionApiClientError) as caught:
                await getattr(client, method)(**options)
            assert caught.value.status_code == (404 if change == "foreign_client" else 502)
        else:
            result = await getattr(client, method)(**options)
            assert result.data == payload
    assert len(seen) == 1
    no_write(c)
