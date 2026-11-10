"""Exact settings version bounds in published OpenAPI; local checks only."""
import httpx
import pytest
from pydantic import ValidationError

from api.construction_settings_contract import (
    ConstructionCompanySettingsUpdate,
    install_settings_openapi_precision,
)
from test_construction_02_settings_contract import settings_http


def assert_exact_bounds(document):
    fields = []
    for schema in document["components"]["schemas"].values():
        field_name = {
            "ConstructionCompanySettingsUpdate": "expected_row_version",
            "ConstructionCompanySettingsData": "row_version",
        }.get(schema.get("title"))
        if field_name:
            field = schema["properties"][field_name]
            assert type(field["minimum"]) is int and field["minimum"] == 1
            assert type(field["maximum"]) is int and field["maximum"] == 2**63 - 1
            assert field["type"] == "integer" and field["format"] == "int64"
            fields.append(field_name)
    assert set(fields) == {"expected_row_version", "row_version"}


@pytest.mark.asyncio
async def test_published_json_preserves_exact_bigint_bounds(settings_http):
    app = settings_http.app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    assert_exact_bounds(response.json())


@pytest.mark.parametrize("invalid", [
    2**63, float(2**63 - 1), True, "9223372036854775807",
])
def test_runtime_keeps_exact_strict_bigint_validation(invalid):
    accepted = ConstructionCompanySettingsUpdate.model_validate({
        "expected_row_version": 2**63 - 1, "phone_override": None,
    })
    assert accepted.expected_row_version == 2**63 - 1
    with pytest.raises(ValidationError):
        ConstructionCompanySettingsUpdate.model_validate({
            "expected_row_version": invalid, "phone_override": None,
        })


def test_hook_preserves_cache_and_reapplies_after_schema_rebuild(settings_http):
    app = settings_http.app
    hook = app.openapi
    install_settings_openapi_precision(app)
    assert app.openapi is hook
    first = app.openapi()
    assert app.openapi() is first
    assert_exact_bounds(first)

    @app.get("/__local_schema_probe", include_in_schema=True)
    async def local_probe():
        return {"ready": True}

    app.openapi_schema = None
    rebuilt = app.openapi()
    assert rebuilt is not first
    assert "/__local_schema_probe" in rebuilt["paths"]
    assert_exact_bounds(rebuilt)
