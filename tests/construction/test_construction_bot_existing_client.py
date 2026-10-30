from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_convert as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_lead_convert import case, click


@pytest.mark.asyncio
async def test_search_and_summary_use_only_existing_read_routes():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    client_id = uuid4()
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "X-Tenant-ID" not in request.headers
        if request.url.path.endswith("/address-suggestions"):
            assert dict(request.url.params) == {"q": "Saved address", "limit": "10"}
            data = [{
                "entity_type": "client", "entity_id": str(client_id),
                "label": "Existing client", "address_raw": "Saved address",
                "match_kind": "exact", "score": 1000,
            }]
        else:
            assert request.url.path == f"/api/v1/construction/clients/{client_id}"
            assert dict(request.url.params) == {
                "projects_limit": "1", "leads_limit": "1",
                "activities_limit": "1", "history_limit": "1",
            }
            data = {
                "id": str(client_id), "tenant_id": str(session.tenant_id),
                "display_name": "Existing client", "client_type": "person",
                "phone": "+123", "email": None, "row_version": 1,
            }
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "selection-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        matches = await api.address_suggestions(session=session, query="Saved address")
        selected = await api.client_summary(session=session, client_id=client_id)
        assert matches.data[0]["entity_id"] == selected.data["id"]
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_project_match_resolves_current_client_via_api():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    project_id, client_id = uuid4(), uuid4()
    api = NS(
        project=AsyncMock(return_value=NS(data={"client_id": str(client_id)})),
        client_summary=AsyncMock(return_value=NS(data={
            "id": str(client_id), "tenant_id": str(session.tenant_id),
            "display_name": "Existing client",
        })),
    )
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "name": "Organization"},
    ))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id,
        candidate={"entity_type": "project", "entity_id": str(project_id)},
    )
    result = await entry.select_conversion_client(**options)
    assert result["client"]["id"] == str(client_id)
    api.project.assert_awaited_once_with(session=session, project_id=project_id)
    api.client_summary.assert_awaited_once_with(session=session, client_id=client_id)
    api.client_summary.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.select_conversion_client(**{
            **options, "expected_user_id": uuid4(),
        })
    api.client_summary.assert_not_awaited()


@pytest.fixture
def selection(case):
    c = case
    client_id = c.receipt["client_id"]
    c.service.conversion_search = AsyncMock(return_value={
        "items": [{
            "entity_type": "client", "entity_id": client_id,
            "label": "Existing client", "address_raw": "Saved address",
            "match_kind": "exact", "score": 1000,
        }],
    })
    c.service.select_conversion_client = AsyncMock(return_value={
        "client": {
            "id": client_id, "tenant_id": c.source["tenant_id"],
            "display_name": "Existing client", "client_type": "person",
            "phone": "+999", "email": None, "row_version": 1,
        },
    })
    return c


async def search(c):
    await handler.conversion_callback(c.callback, c.state)
    assert c.data[handler.FORM]["phase"] == "mode"
    await click(c, "existing")
    c.message.text = "Saved address"
    await handler.conversion_message(c.message, c.state)
    assert c.data[handler.FORM]["phase"] == "matches"


async def selected_review(c):
    await search(c)
    await click(c, "pick0")
    assert c.data[handler.FORM]["phase"] == "clientconfirm"
    c.service.convert_lead.assert_not_awaited()
    await click(c, "keepclient")
    c.message.text = "Project for existing client"
    await handler.conversion_message(c.message, c.state)
    await click(c, "use")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_client_is_explicitly_confirmed_and_only_new_project_is_requested(selection):
    c = selection
    await selected_review(c)
    await click(c, "submit")
    payload = c.service.convert_lead.await_args.kwargs["payload"]
    assert payload == {
        "expected_row_version": 4,
        "client_id": c.receipt["client_id"], "project_id": None,
        "client_type": None,
        "project_name": "Project for existing client",
        "project_address_raw": "Saved address",
    }
    assert c.data[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_old_result_button_cannot_select_from_new_search_results(selection):
    c = selection
    await search(c)
    old_callback = handler.cb(c.data[handler.FORM], "pick0")
    await click(c, "research")
    c.message.text = "Another address"
    await handler.conversion_message(c.message, c.state)
    c.callback.data = old_callback
    await handler.conversion_callback(c.callback, c.state)
    c.service.select_conversion_client.assert_not_awaited()
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_existing_client_conversion_keeps_selection_and_key(selection):
    c = selection
    await selected_review(c)
    c.service.convert_lead.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": c.receipt},
    ]
    await click(c, "submit")
    first = c.service.convert_lead.await_args.kwargs
    await click(c, "edit")
    assert c.service.convert_lead.await_count == 1
    await click(c, "submit")
    assert c.service.convert_lead.await_args.kwargs == first
    assert c.service.select_conversion_client.await_count == 1


@pytest.mark.asyncio
async def test_denied_search_never_falls_back_to_creating_a_client(selection):
    c = selection
    await handler.conversion_callback(c.callback, c.state)
    await click(c, "existing")
    c.service.conversion_search.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    c.message.text = "Saved address"
    await handler.conversion_message(c.message, c.state)
    assert c.data[handler.FORM]["phase"] == "clientsearch"
    c.service.convert_lead.assert_not_awaited()
