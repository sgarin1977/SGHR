"""Public API boundary: no unapproved project text endpoint."""
from unittest.mock import AsyncMock

import httpx
import pytest

from api.routes.construction_project_read import ProjectDetailsResponse
from test_construction_01_project_read_http import project_http


async def request(c, path, *, method="GET"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.request(
            method, path, headers={"X-Request-ID": "project-api-boundary"})


def test_application_contract_keeps_existing_get_without_extra_text_endpoint(project_http):
    c = project_http
    # Inspect expanded paths: included routers may be represented lazily.
    paths = c.app.openapi()["paths"]
    assert "/api/v1/construction/projects/{id}" in paths
    assert "/api/v1/construction/projects/{id}/texts/{field_name}" not in paths
    assert not any(
        path and path.startswith("/api/v1/construction/projects/")
        and "/texts/" in path
        for path in paths
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "comment"])
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_unapproved_text_endpoint_is_not_available(project_http, field, method):
    c = project_http
    path = f"/api/v1/construction/projects/{c.project_id}/texts/{field}"
    response = await request(c, path, method=method)
    assert response.status_code == 404, response.text
    c.repo.get_active_project.assert_not_awaited()
    c.session.commit.assert_not_awaited()
    c.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_project_get_preserves_original_contract(project_http, monkeypatch):
    c = project_http
    import services.construction.construction_project_translation as translation

    forbidden_factory = AsyncMock(
        side_effect=AssertionError("Translation runtime must not be called here"))
    monkeypatch.setattr(
        translation, "build_construction_project_translation_workflow",
        forbidden_factory)

    c.project.name = "Оригінальна назва"
    c.project.comment = "Оригінальний коментар"
    response = await request(
        c, f"/api/v1/construction/projects/{c.project_id}")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == set(ProjectDetailsResponse.model_fields)
    assert data["name"] == c.project.name
    assert data["comment"] == c.project.comment
    assert data["address_raw"] == c.project.address_raw
    assert data["row_version"] == c.project.row_version
    assert data["tenant_id"] == str(c.tenant)
    assert response.json()["request_id"] == "project-api-boundary"
    c.repo.get_active_project.assert_awaited_once_with(
        tenant_id=c.tenant, project_id=c.project_id)
    forbidden_factory.assert_not_called()
    c.session.commit.assert_not_awaited()
    c.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_wire_openapi_does_not_publish_unapproved_contract(project_http):
    c = project_http
    response = await request(c, "/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    assert "/api/v1/construction/projects/{id}" in document["paths"]
    assert "/api/v1/construction/projects/{id}/texts/{field_name}" not in document["paths"]
    schemas = document.get("components", {}).get("schemas", {})
    titles = {schema.get("title") for schema in schemas.values()}
    assert "ProjectTranslatedTextData" not in titles
    assert "ProjectTranslatedTextEnvelope" not in titles
