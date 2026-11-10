"""Registered document language GET over local SQLite and existing HTTP gates."""
from dataclasses import replace
from urllib.parse import urlencode
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from api.routes import construction_language_settings as routes
from services.api_identity import ApiIdentityAccessError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_document_language import (
    language_case, document_case,
)
from test_construction_02_language_context import (
    language_http, snapshot,
)

PATH = "/api/v1/construction/settings/document-languages"


@pytest.fixture
def document_http(document_case, language_http):
    c = document_case
    assert c is language_http
    c.prepare_calls = AsyncMock(wraps=c.documents.prepare_for_actor)
    c.documents.prepare_for_actor = c.prepare_calls
    c.app.dependency_overrides[routes.get_document_language_service] = lambda: c.documents
    return c


async def request(c, languages=("pt", "en"), *, extra=(), content=None, headers=None):
    query = [
        ("document_languages", code) for code in languages
    ] + list(extra)
    encoded = urlencode(query)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            "GET", PATH + ("?" + encoded if encoded else ""),
            content=content,
            headers={
                "X-Request-ID": "document-language-http",
                **(headers or {}),
            })


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [("en",), ("pt", "en"), ("en", "pt"), ("ja",)])
async def test_http_returns_explicit_order_separate_from_profile_and_default(
    document_http, languages,
):
    c = document_http
    before = snapshot(c)
    response = await request(c, languages, headers={"Accept-Language": "ru"})
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert set(data) == {
        "tenant_id", "user_id", "interface_language",
        "organization_default_language", "organization_row_version",
        "document_languages", "bilingual",
    }
    assert data["tenant_id"] == str(c.tenant)
    assert data["user_id"] == str(c.user)
    assert data["interface_language"] == "es"
    assert data["organization_default_language"] == "pt"
    assert data["organization_row_version"] == 1
    assert data["document_languages"] == list(languages)
    assert data["bilingual"] is (len(languages) == 2)
    assert body["meta"] == {}
    assert body["request_id"] == "document-language-http"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    c.prepare_calls.assert_awaited_once_with(
        actor=c.actor, document_languages=languages)
    assert snapshot(c) == before
    c.db.commit.assert_not_awaited()
    c.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [
    (), ("",), ("EN",), (" en",), ("en", "pt", "es"),
])
async def test_missing_or_malformed_selection_is_rejected_before_service(
    document_http, languages,
):
    c = document_http
    response = await request(c, languages)
    assert response.status_code == 422, response.text
    c.prepare_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [("en", "en"), ("fr",), ("zz",)])
async def test_duplicate_inactive_or_unknown_selection_has_no_fallback(
    document_http, languages,
):
    c = document_http
    before = snapshot(c)
    response = await request(c, languages)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    (("tenant_id", "foreign"),),
    (("user_id", "foreign"),),
    (("interface_language", "ru"),),
    (("bilingual", "false"),),
])
async def test_caller_context_and_mode_selectors_are_not_accepted(document_http, extra):
    c = document_http
    response = await request(c, extra=extra)
    assert response.status_code == 422, response.text
    c.prepare_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_body_is_not_selection_input(document_http):
    c = document_http
    response = await request(
        c, content='{"document_languages":["ru"]}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    c.prepare_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "membership", "suite", "core", "rate"])
async def test_existing_authentication_membership_modules_and_rate_limit(
    document_http, gate,
):
    c = document_http
    expected = 403
    if gate == "authentication":
        c.app.dependency_overrides.pop(get_current_actor)
        expected = 401
    elif gate == "membership":
        c.sync.execute(c.members.update().values(is_active=False))
        c.sync.commit()
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    else:
        rate = c.app.dependency_overrides[get_api_request_rate_limit_service]()
        for _ in range(120):
            await rate.ensure_authenticated_user_allowed(
                tenant_id=c.tenant, user_id=c.user)
        expected = 429
    before = snapshot(c)
    response = await request(c)
    assert response.status_code == expected, response.text
    assert snapshot(c) == before
    c.db.commit.assert_not_awaited()
    c.db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_profile_language_requires_configuration(document_http):
    c = document_http
    c.sync.execute(c.users.update().values(language_code="missing"))
    c.sync.commit()
    response = await request(c, ("en",))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LANGUAGE_CONFIGURATION_REQUIRED"


@pytest.mark.asyncio
async def test_profile_change_during_preparation_is_a_context_conflict(document_http):
    c = document_http
    first = replace(c.actor, language_code="es")
    second = replace(c.actor, language_code="it")
    c.identity.require_actor.side_effect = [first, first, second, second]
    response = await request(c)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DOCUMENT_LANGUAGE_CONTEXT_CHANGED"


@pytest.mark.asyncio
async def test_membership_revocation_between_reads_denies_response(document_http):
    c = document_http
    current = replace(c.actor, language_code="es")
    c.identity.require_actor.side_effect = [
        current, current, ApiIdentityAccessError("private membership state"),
    ]
    response = await request(c)
    assert response.status_code == 403, response.text
    assert "private membership state" not in response.text


@pytest.mark.asyncio
async def test_repository_failure_is_sanitized(document_http):
    c = document_http
    c.repository.list_language_catalog = AsyncMock(
        side_effect=RuntimeError("private database credentials"))
    response = await request(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "private database credentials" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["user", "tenant", "selection"])
async def test_public_projection_rejects_wrong_context(document_http, wrong):
    c = document_http
    good = await c.documents.prepare_for_actor(
        actor=c.actor, document_languages=("pt", "en"))
    altered = (
        replace(good, user_id=uuid4()) if wrong == "user"
        else replace(good, tenant_id=uuid4()) if wrong == "tenant"
        else replace(good, document_languages=("en", "pt"))
    )
    c.prepare_calls.reset_mock()
    c.prepare_calls.return_value = altered
    response = await request(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"


@pytest.mark.asyncio
async def test_registered_factory_reuses_the_existing_platform_context(document_http):
    c = document_http
    c.app.dependency_overrides.pop(routes.get_document_language_service)
    response = await request(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["document_languages"] == ["pt", "en"]
    assert c.identity.require_actor.await_count == 4


@pytest.mark.asyncio
async def test_invalid_runtime_collaborator_is_unavailable(document_http):
    c = document_http
    c.app.dependency_overrides.pop(routes.get_document_language_service)
    c.app.dependency_overrides[routes.get_language_context_service] = lambda: object()
    response = await request(c)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "service_unavailable"
    c.prepare_calls.assert_not_awaited()


def test_openapi_declares_explicit_selection_and_exact_organization_version(document_http):
    document = document_http.app.openapi()
    operation = document["paths"][PATH]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"document_languages"}
    parameter = parameters["document_languages"]
    assert parameter["required"] is True
    assert parameter["schema"]["minItems"] == 1
    assert parameter["schema"]["maxItems"] == 2
    assert "enum" not in parameter["schema"]["items"]
    assert "requestBody" not in operation
    assert {"401", "403", "409", "422", "429", "503"}.issubset(operation["responses"])

    schema = next(
        item for item in document["components"]["schemas"].values()
        if item.get("title") == "DocumentLanguagePlanData")
    assert schema["additionalProperties"] is False
    version = schema["properties"]["organization_row_version"]
    assert type(version["maximum"]) is int
    assert version["maximum"] == 2**63 - 1
    assert version["minimum"] == 1 and version["format"] == "int64"


@pytest.mark.asyncio
async def test_existing_language_context_response_is_preserved(document_http):
    c = document_http
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/construction/settings/languages")
    assert response.status_code == 200, response.text
    assert set(response.json()["data"]) == {
        "tenant_id", "user_id", "interface_language",
        "organization_default_language", "organization_updated_at",
        "row_version", "languages",
    }
