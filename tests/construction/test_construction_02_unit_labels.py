"""Unit names, exact profile language and versioned cache; local SQLite/fake provider."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func

from database.models import TranslationCache
from services.construction import construction_unit_catalog as service_module
from services.construction.construction_unit_labels import unit_label_source
from services.construction.construction_units import list_construction_units
from test_construction_02_units import units_http, call
from test_construction_02_language_http import http_language, snapshot


def set_language(c, code, active=True):
    present = c.sync.execute(select(c.languages.c.code).where(c.languages.c.code == code)).scalar_one_or_none()
    if present is None:
        c.sync.execute(c.languages.insert().values(code=code, name=code, native_name=None, is_active=active))
    else:
        c.sync.execute(c.languages.update().where(c.languages.c.code == code).values(is_active=active))
    c.sync.execute(c.users.update().where(c.users.c.id == c.user).values(language_code=code))
    c.sync.commit()


def cache_count(c):
    return c.sync.execute(select(func.count()).select_from(TranslationCache)).scalar_one()


@pytest.fixture
def machine_units(units_http):
    c = units_http
    TranslationCache.__table__.create(c.sync.get_bind())
    set_language(c, "fi")
    async def translate(**kwargs):
        assert kwargs["source_language"] == "en"
        assert kwargs["target_language"] == "fi"
        return "fi " + kwargs["text"]
    c.provider = NS(
        provider_name="local-unit-test", timeout_seconds=15,
        translate=AsyncMock(side_effect=translate),
    )
    c.units.localizer.provider = c.provider
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("language,name", [
    ("ru", "миллиметр"), ("pt", "milímetro"), ("en", "millimetre"),
    ("es", "milímetro"), ("it", "millimetro"),
])
async def test_required_languages_use_profile_and_keep_codes_symbols_and_business_rows(units_http, language, name):
    c = units_http
    set_language(c, language)
    c.units.localizer.provider = NS(translate=AsyncMock(side_effect=AssertionError("No provider for supplied names")))
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["language_code"] == language
    assert data["label_source_version"] == 1
    assert next(unit["label"] for unit in data["units"] if unit["code"] == "MM") == name
    assert all(unit["label_status"] == "provided" for unit in data["units"])
    assert [(unit["code"], unit["symbol"]) for unit in data["units"]] == [
        (unit.code.value, unit.symbol) for unit in list_construction_units()
    ]
    assert snapshot(c) == before and c.commits == 0
    c.units.localizer.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_language_outside_bundled_dictionary_is_translated_cached_and_never_replaced_with_ru(machine_units):
    c = machine_units
    before = snapshot(c)
    first = await call(c)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["language_code"] == "fi"
    assert all(unit["label_status"] == "translated" for unit in first.json()["data"]["units"])
    assert c.provider.translate.await_count == 16
    assert cache_count(c) == 16 and c.commits == 1
    rows = c.sync.execute(select(TranslationCache)).scalars().all()
    assert all(row.target_language == "fi" and row.source_language == "en" for row in rows)
    assert all(len(row.source_text_hash) == 64 for row in rows)
    second = await call(c)
    assert second.status_code == 200, second.text
    assert all(unit["label_status"] == "cached" for unit in second.json()["data"]["units"])
    assert [unit["label"] for unit in second.json()["data"]["units"]] == [
        unit["label"] for unit in first.json()["data"]["units"]
    ]
    assert c.provider.translate.await_count == 16 and cache_count(c) == 16
    assert c.commits == 1 and snapshot(c) == before


@pytest.mark.asyncio
async def test_source_version_changes_create_new_cache_without_rewriting_previous_rows(machine_units, monkeypatch):
    c = machine_units
    assert (await call(c)).status_code == 200
    old = c.sync.execute(select(TranslationCache.__table__)).all()
    version, source, supplied = unit_label_source()
    monkeypatch.setattr(service_module, "unit_label_source", lambda: (version + 1, source, supplied))
    response = await call(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["label_source_version"] == 2
    assert cache_count(c) == 32 and c.provider.translate.await_count == 32
    current = {row.id: row for row in c.sync.execute(select(TranslationCache.__table__)).all()}
    assert all(current[row.id] == row for row in old)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [("provider", 503), ("commit", 500), ("membership", 403)])
async def test_provider_write_commit_and_late_membership_failures_do_not_leave_partial_cache(machine_units, failure, status):
    c = machine_units
    if failure == "provider":
        c.provider.translate.side_effect = RuntimeError("private provider detail")
    elif failure == "commit":
        c.fail = "commit"
    else:
        c.deny_at = 3
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == status, response.text
    assert "private provider detail" not in response.text and "data" not in response.json()
    assert cache_count(c) == 0 and c.commits == 0
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_language_change_during_translation_returns_conflict_without_cache(machine_units):
    c = machine_units
    original = c.identity.require_actor.side_effect
    async def refresh(**kwargs):
        actor = await original(**kwargs)
        return replace(actor, language_code="it") if c.calls == 2 else actor
    c.identity.require_actor.side_effect = refresh
    response = await call(c)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LANGUAGE_CONTEXT_CHANGED"
    assert cache_count(c) == 0 and c.commits == 0


@pytest.mark.asyncio
async def test_inactive_profile_language_is_not_replaced_with_another_language(units_http):
    c = units_http
    set_language(c, "fr", active=False)
    response = await call(c)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LANGUAGE_CONFIGURATION_REQUIRED"
    assert c.commits == 0


def test_localized_api_documents_required_labels_and_language_errors(units_http):
    schema = units_http.app.openapi()
    option = schema["components"]["schemas"]["UnitOptionResponse"]
    assert {"label", "label_status"} <= set(option["required"])
    data = schema["components"]["schemas"]["UnitCatalogData"]
    assert {"language_code", "label_source_version"} <= set(data["required"])
    operation = schema["paths"]["/api/v1/construction/settings/units"]["get"]
    assert {"409", "503"} <= set(operation["responses"])
