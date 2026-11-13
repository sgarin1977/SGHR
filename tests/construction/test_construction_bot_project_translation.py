"""Existing localized GET, original-preserving display and explicit outage handling."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_project_presentation import ProjectPresentationResult
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_card import project_text
from ui.construction_project_translation import LABELS, LANGUAGES, tr


@pytest.fixture
def data():
    return {
        "id": str(uuid4()), "tenant_id": str(uuid4()), "client_id": str(uuid4()),
        "name": "Original project", "status": "draft", "currency": "EUR",
        "responsible_user_id": None, "comment": "Original comment",
        "address_raw": "Original address", "address_formatted": None,
        "country_code": None, "region": None, "city": None, "postal_code": None,
        "latitude": None, "longitude": None, "address_provider": None,
        "provider_place_id": None, "address_verification_status": "pending",
        "created_by": str(uuid4()), "created_at": "2026-11-12T10:00:00Z",
        "updated_at": "2026-11-12T10:00:00Z", "row_version": 4,
    }


def translated(original, display):
    return {
        "original_text": original, "display_text": display,
        "source_language": "en", "translation_status": "AUTO_TRANSLATED",
        "translated_at": "2026-11-12T10:01:00+00:00", "reviewed_by": None,
        "cache_row_version": 1, "cache_written": False,
    }


def metadata(data):
    return {
        "capabilities": {
            "can_edit_project": False, "can_plan_activity": False, "can_open_team": True,
        },
        "translations": {
            "target_language": "uk", "source_version": data["row_version"],
            "name": translated(data["name"], "Перекладена назва"),
            "comment": translated(data["comment"], "Перекладений коментар"),
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "ok", "language", "version", "original", "tenant", "stale",
    "unavailable", "forbidden", "internal",
])
async def test_localized_get_uses_only_existing_selector_and_validates_source(data, case):
    from uuid import UUID
    user = uuid4()
    meta = metadata(data)
    body = deepcopy(data)
    if case == "language":
        meta["translations"]["target_language"] = "pl"
    elif case == "version":
        meta["translations"]["source_version"] = 5
    elif case == "original":
        meta["translations"]["name"]["original_text"] = "Different original"
    elif case == "tenant":
        body["tenant_id"] = str(uuid4())
    elif case == "stale":
        meta["translations"]["name"]["translation_status"] = "STALE"
    calls = []

    def transport(request):
        calls.append(request)
        localized = request.url.params.get("include_translations") == "true"
        if localized and case in ("unavailable", "forbidden", "internal"):
            status, code = {
                "unavailable": (503, "TRANSLATION_UNAVAILABLE"),
                "forbidden": (403, "forbidden"),
                "internal": (500, "internal_error"),
            }[case]
            return httpx.Response(status, json={"error": {
                "code": code, "message": "Private server detail", "request_id": "local",
            }})
        return httpx.Response(200, json={
            "data": body,
            "meta": meta if localized else {"capabilities": meta["capabilities"]},
            "request_id": "local",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        session = PlatformApiClientSession(user, UUID(data["tenant_id"]), SecretStr("local-only"))
        call = client.project_details(
            session=session, project_id=UUID(data["id"]),
            include_translations=True, expected_language="uk",
        )
        if case not in ("ok", "unavailable"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            expected = 409 if case == "language" else 403 if case == "forbidden" else 500 if case == "internal" else 502
            assert caught.value.status_code == expected
            assert len(calls) == 1
        else:
            result = await call
            assert result.data == data
            assert result.translation_unavailable is (case == "unavailable")
            assert len(calls) == (2 if case == "unavailable" else 1)
            if case == "unavailable":
                assert "translations" not in result.meta
                assert not calls[1].url.query
        assert calls[0].method == "GET"
        assert calls[0].url.path == "/api/v1/construction/projects/" + data["id"]
        assert list(calls[0].url.params.multi_items()) == [("include_translations", "true")]
        assert "X-Tenant-ID" not in calls[0].headers


@pytest.mark.asyncio
async def test_localized_card_preserves_exact_client_relationship(data):
    from uuid import UUID
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": data, "meta": metadata(data), "request_id": "local",
        })),
    ) as http:
        client = ConstructionApiClient(http=http)
        with pytest.raises(ConstructionApiClientError) as caught:
            await client.project_card(
                session=PlatformApiClientSession(uuid4(), UUID(data["tenant_id"]), SecretStr("local-only")),
                project_id=UUID(data["id"]), client_id=uuid4(),
                include_translations=True, expected_language="uk",
            )
        assert caught.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["project_catalog_card", "project_card", "read_created_project"])
async def test_all_card_entries_request_profile_language_and_keep_original_item(data, method):
    from uuid import UUID
    user, tenant = uuid4(), UUID(data["tenant_id"])
    response = ProjectPresentationResult(data, metadata(data), "local")
    reader = AsyncMock(return_value=response)
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(project_details=reader, project_card=reader)),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-only")),
        {"name": "Organization", "language": "uk"},
    ))
    kwargs = dict(
        telegram_user_id=123, tenant_id=tenant,
        project_id=UUID(data["id"]), expected_user_id=user,
    )
    if method == "project_card":
        kwargs["client_id"] = UUID(data["client_id"])
    result = await getattr(entry, method)(**kwargs)
    assert reader.await_args.kwargs["include_translations"] is True
    assert reader.await_args.kwargs["expected_language"] == "uk"
    assert result["item"] == data
    assert result["translations"] == response.meta["translations"]
    assert result["can_edit_project"] is False


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("status", ["AUTO_TRANSLATED", "VERIFIED"])
def test_display_retains_original_and_does_not_translate_address(data, language, status):
    meta = metadata(data)["translations"]
    meta["target_language"] = language
    for field in ("name", "comment"):
        meta[field]["translation_status"] = status
    result = {
        "item": data, "actor_user_id": str(uuid4()), "organization": "Organization",
        "language": language, "translations": meta, "translation_unavailable": False,
    }
    before = deepcopy(result)
    text = project_text(result, language)
    assert "Перекладена назва" in text and "Перекладений коментар" in text
    assert data["name"] in text and data["comment"] in text
    assert data["address_raw"] in text
    assert tr(status, language) in text and tr("original", language) in text
    assert result == before


def test_provider_outage_is_explicit_and_preserves_original(data):
    result = {
        "item": data, "actor_user_id": str(uuid4()), "organization": "Organization",
        "language": "uk", "translations": None, "translation_unavailable": True,
    }
    text = project_text(result, "uk")
    assert tr("unavailable", "uk") in text
    assert data["name"] in text and data["comment"] in text
    assert "Автоматичний переклад" not in text


def test_translation_labels_cover_all_languages():
    assert all(len(values) == len(LANGUAGES) for values in LABELS.values())
