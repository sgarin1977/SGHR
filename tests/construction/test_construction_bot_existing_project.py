from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_convert as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_lead_convert import case, click
from test_construction_bot_existing_client import selection, search


def project_data(tenant, client_id, project_id):
    return {
        "id": str(project_id), "tenant_id": str(tenant),
        "client_id": str(client_id), "name": "Existing project",
        "address_raw": "Existing project address", "row_version": 3,
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_project_pages_and_selection_use_existing_bearer_routes():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    client_id, project_id = uuid4(), uuid4()
    items = [
        project_data(session.tenant_id, client_id, uuid4())
        for _ in range(10)
    ]
    items[0]["id"] = str(project_id)
    for index, item in enumerate(items):
        item["status"] = (
            "draft", "planning", "active", "completed", "cancelled"
        )[index % 5]
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "X-Tenant-ID" not in request.headers
        if request.url.path == f"/api/v1/construction/projects/{project_id}":
            data, meta = items[0], {}
        else:
            assert request.url.path == f"/api/v1/construction/clients/{client_id}"
            params = dict(request.url.params)
            assert params.pop("projects_cursor", None) == (
                None if len(seen) == 1 else "opaque-project-page"
            )
            assert params == {
                "projects_limit": "10", "leads_limit": "1",
                "activities_limit": "1", "history_limit": "1",
            }
            data = {
                "id": str(client_id), "tenant_id": str(session.tenant_id),
                "projects": items if len(seen) == 1 else [],
            }
            meta = {"pagination": {
                "projects_next_cursor": (
                    "opaque-project-page" if len(seen) == 1 else None
                ),
            }}
        return httpx.Response(200, json={
            "data": data, "meta": meta, "request_id": "project-selection",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        first = await api.conversion_projects(
            session=session, client_id=client_id,
        )
        assert [item["id"] for item in first.data] == [item["id"] for item in items]
        second = await api.conversion_projects(
            session=session, client_id=client_id,
            cursor=first.meta["pagination"]["next_cursor"],
        )
        assert second.data == []
        assert second.meta["pagination"]["next_cursor"] is None
        selected = await api.conversion_project(
            session=session, client_id=client_id, project_id=project_id,
        )
        assert selected.data["id"] == str(project_id)
    assert len(seen) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "tenant", "client", "duplicate", "version", "cursor",
])
async def test_invalid_project_page_never_becomes_a_selection(failure):
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    client_id = uuid4()
    item = project_data(session.tenant_id, client_id, uuid4())
    cursor = None
    if failure == "tenant":
        item["tenant_id"] = str(uuid4())
    elif failure == "client":
        item["client_id"] = str(uuid4())
    elif failure == "version":
        item["row_version"] = True
    elif failure == "cursor":
        cursor = ""
    items = [item, dict(item)] if failure == "duplicate" else [item]

    def transport(request):
        return httpx.Response(200, json={
            "data": {
                "id": str(client_id), "tenant_id": str(session.tenant_id),
                "projects": items,
            },
            "meta": {"pagination": {"projects_next_cursor": cursor}},
            "request_id": "invalid-project-page",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(transport),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).conversion_projects(
                session=session, client_id=client_id,
            )


@pytest.mark.asyncio
async def test_adapter_rechecks_subject_and_project_client_relationship():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    client_id, project_id = uuid4(), uuid4()
    item = project_data(session.tenant_id, client_id, project_id)
    api = NS(conversion_project=AsyncMock(return_value=NS(data=item)))
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(session, {"language": "uk"}))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id,
        client_id=client_id, project_id=project_id,
    )
    result = await entry.select_conversion_project(**options)
    assert result["project"]["id"] == str(project_id)
    api.conversion_project.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.select_conversion_project(**{
            **options, "expected_user_id": uuid4(),
        })
    api.conversion_project.assert_not_awaited()
    api.conversion_project.return_value = NS(data={
        **item, "client_id": str(uuid4()),
    })
    with pytest.raises(ConstructionApiClientError):
        await entry.select_conversion_project(**options)


@pytest.fixture
def projects(selection):
    c = selection
    item = project_data(
        c.source["tenant_id"], c.receipt["client_id"],
        c.receipt["project_id"],
    )
    c.project_item = item
    c.service.conversion_projects = AsyncMock(return_value={
        "items": [item], "next_cursor": "opaque-project-page",
    })
    c.service.select_conversion_project = AsyncMock(
        return_value={"project": dict(item)},
    )
    return c


async def open_projects(c):
    await search(c)
    await click(c, "pick0")
    await click(c, "objects")
    assert c.data[handler.FORM]["phase"] == "projects"
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_project_requires_review_and_sends_no_creation_fields(projects):
    c = projects
    await open_projects(c)
    await click(c, "proj0")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.convert_lead.assert_not_awaited()
    text = handler.conversion_review(c.data[handler.FORM])
    assert c.project_item["name"] in text
    assert c.project_item["address_raw"] in text
    assert c.source["work_type_description"] not in text
    await click(c, "submit")
    assert c.service.convert_lead.await_args.kwargs["payload"] == {
        "expected_row_version": 4,
        "client_id": c.receipt["client_id"],
        "project_id": c.receipt["project_id"],
        "client_type": None, "project_name": None,
        "project_address_raw": None,
    }
    assert c.data[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_old_project_page_button_cannot_select_new_page_index(projects):
    c = projects
    await open_projects(c)
    old_button = handler.cb(c.data[handler.FORM], "proj0")
    c.service.conversion_projects.return_value = {
        "items": [dict(c.project_item, id=str(uuid4()))],
        "next_cursor": None,
    }
    await click(c, "nextprojects")
    assert c.service.conversion_projects.await_args.kwargs["cursor"] == "opaque-project-page"
    c.callback.data = old_button
    await handler.conversion_callback(c.callback, c.state)
    c.service.select_conversion_project.assert_not_awaited()
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_project_client_does_not_fall_back_to_creation(projects):
    c = projects
    await open_projects(c)
    c.service.select_conversion_project.return_value = {
        "project": dict(c.project_item, client_id=str(uuid4())),
    }
    await click(c, "proj0")
    assert c.data[handler.FORM]["phase"] == "projects"
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_conversion_retries_original_project_without_reselection(projects):
    c = projects
    await open_projects(c)
    await click(c, "proj0")
    c.service.convert_lead.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": c.receipt},
    ]
    await click(c, "submit")
    first = c.service.convert_lead.await_args.kwargs
    await click(c, "newproject")
    assert c.service.convert_lead.await_count == 1
    await click(c, "submit")
    assert c.service.convert_lead.await_args.kwargs == first
    assert c.service.select_conversion_project.await_count == 1
    assert c.data[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_empty_page_requires_explicit_new_project_choice(projects):
    c = projects
    c.service.conversion_projects.return_value = {
        "items": [], "next_cursor": None,
    }
    await open_projects(c)
    c.service.convert_lead.assert_not_awaited()
    await click(c, "newproject")
    assert c.data[handler.FORM]["phase"] == "name"
    assert c.data[handler.FORM]["client_id"] == c.receipt["client_id"]
    assert c.data[handler.FORM].get("project_id") is None


@pytest.mark.asyncio
async def test_editing_selection_clears_old_project_for_new_client(projects):
    c = projects
    await open_projects(c)
    await click(c, "proj0")
    await click(c, "edit")
    await click(c, "newclient")
    await click(c, "person")
    c.message.text = "Another new project"
    await handler.conversion_message(c.message, c.state)
    await click(c, "use")
    await click(c, "submit")
    payload = c.service.convert_lead.await_args.kwargs["payload"]
    assert payload["client_id"] is None
    assert payload["project_id"] is None
    assert payload["client_type"] == "person"
    assert payload["project_name"] == "Another new project"
