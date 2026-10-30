from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_activity_plan as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_activity import local_deadlines


def test_local_time_is_converted_to_utc_without_rejecting_overdue_dates():
    assert local_deadlines("22.10.2020 15:30", "Europe/Berlin") == (
        "2020-10-22T13:30+00:00",
    )


def test_missing_dst_time_is_rejected_and_ambiguous_time_requires_choice():
    with pytest.raises(ValueError):
        local_deadlines("29.03.2026 02:30", "Europe/Berlin")
    assert local_deadlines("25.10.2026 02:30", "Europe/Berlin") == (
        "2026-10-25T00:30+00:00", "2026-10-25T01:30+00:00",
    )


@pytest.mark.asyncio
async def test_transport_retries_only_on_explicit_call_with_same_key_and_payload():
    import json
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead_id, activity_id = uuid4(), uuid4()
    payload = {
        "type": "call", "due_at": "2026-10-23T12:30+00:00",
        "timezone": "Europe/Kyiv", "comment": None,
    }
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/construction/crm/activities"
        assert request.headers["Idempotency-Key"] == "stable-activity"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body == {
            **payload, "lead_id": str(lead_id), "client_id": None,
            "project_id": None, "responsible_user_id": str(session.user_id),
        }
        if len(seen) == 1:
            raise httpx.ReadTimeout("uncertain", request=request)
        data = {
            **body, "id": str(activity_id), "tenant_id": str(session.tenant_id),
            "status": "planned", "result": None, "completed_at": None,
            "cancelled_at": None, "created_by_user_id": str(session.user_id),
            "created_at": "2026-10-22T12:00:00Z",
            "updated_at": "2026-10-22T12:00:00Z", "row_version": 1,
        }
        return httpx.Response(201, json={
            "data": data, "meta": {}, "request_id": "activity-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        options = dict(
            session=session, lead_id=lead_id, payload=payload,
            responsible_user_id=session.user_id, idempotency_key="stable-activity",
        )
        with pytest.raises(ConstructionApiClientError):
            await client.create_lead_activity(**options)
        assert len(seen) == 1
        result = await client.create_lead_activity(**options)
        assert result.data["id"] == str(activity_id)
    assert seen[0].content == seen[1].content


@pytest.mark.asyncio
async def test_adapter_rejects_changed_canonical_identity_before_mutation():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    create = AsyncMock()
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(create_lead_activity=create)),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.create_lead_activity(
            telegram_user_id=123, tenant_id=session.tenant_id,
            lead_id=uuid4(), expected_user_id=uuid4(),
            payload={}, assign_self=True, idempotency_key="stable",
        )
    create.assert_not_awaited()


@pytest.fixture
def form(monkeypatch):
    tenant, lead, user, activity = uuid4(), uuid4(), uuid4(), uuid4()
    data = {handler.CONTEXT: {
        "telegram_user_id": 123, "tenant_id": str(tenant),
    }}

    async def update(**values):
        data.update(values)

    state = NS(
        get_data=AsyncMock(side_effect=lambda: data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    message = NS(
        chat=NS(id=123, type="private"), from_user=sender,
        answer=AsyncMock(), text=None,
    )
    callback = NS(
        from_user=sender, message=message, answer=AsyncMock(),
        data="AP:new:" + lead.hex,
    )
    service = NS(
        activity_form=AsyncMock(return_value={
            "actor_user_id": str(user), "organization": "Organization",
            "language": "uk", "timezone": "UTC", "lead_name": "Contact",
        }),
        create_lead_activity=AsyncMock(return_value={
            "item": {"id": str(activity)},
        }),
    )

    @asynccontextmanager
    async def entry(**kwargs):
        yield service

    async def show(event, state, form, text, rows):
        if form is not None:
            form["menu_id"] = 1
            await state.update_data(**{handler.FORM: form})
        await state.update_data(last_menu_message_id=1)

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "suspend_lead_form", AsyncMock())
    monkeypatch.setattr(handler, "show", show)
    return NS(
        data=data, state=state, callback=callback, message=message,
        service=service, activity=activity,
    )


async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    await handler.activity_callback(c.callback, c.state)


async def review(c):
    await handler.activity_callback(c.callback, c.state)
    await click(c, "type0")
    await click(c, "use")
    c.message.text = "23.10.2026 15:30"
    await handler.activity_message(c.message, c.state)
    await click(c, "mine")
    await click(c, "skip")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.create_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_is_required_and_cancel_creates_nothing(form):
    c = form
    await review(c)
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.create_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_outcome_freezes_command_and_reuses_key(form):
    c = form
    await review(c)
    c.service.create_lead_activity.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": {"id": str(c.activity)}},
    ]
    await click(c, "submit")
    first = c.service.create_lead_activity.await_args.kwargs
    assert c.data[handler.FORM]["phase"] == "pending"
    await click(c, "edit")
    assert c.service.create_lead_activity.await_count == 1
    await click(c, "submit")
    assert c.service.create_lead_activity.await_args.kwargs == first
    assert c.data[handler.FORM]["phase"] == "done"
    await click(c, "submit")
    assert c.service.create_lead_activity.await_count == 2


@pytest.mark.asyncio
async def test_input_does_not_capture_other_menus_or_legacy_fsm(form):
    c = form
    await handler.activity_callback(c.callback, c.state)
    await click(c, "type0")
    await click(c, "use")
    check = handler.ActivityInput()
    assert await check(c.message, c.state)
    c.data["last_menu_message_id"] = 99
    assert not await check(c.message, c.state)
    c.data["last_menu_message_id"] = 1
    c.state.get_state.return_value = "Support:message"
    assert not await check(c.message, c.state)

# Direct client activity regression v1.
@pytest.fixture
def client_form(form):
    c = form
    c.client = uuid4()
    c.actor = c.service.activity_form.return_value["actor_user_id"]
    c.callback.message.message_id = 1
    c.data["last_menu_message_id"] = 1
    c.data["construction_client_card_view"] = {
        "nonce": "client-origin", "telegram_user_id": 123,
        "tenant_id": c.data[handler.CONTEXT]["tenant_id"],
        "client_id": str(c.client), "actor_user_id": c.actor,
        "menu_id": 1, "positions": {}, "section": "summary",
    }
    c.callback.data = "AP:client:client-origin"
    c.service.client_activity_form = AsyncMock(return_value={
        "client_id": str(c.client), "actor_user_id": c.actor,
        "organization": "Organization", "subject_name": "Client",
        "language": "uk", "timezone": "UTC",
    })
    c.service.create_client_activity = AsyncMock(return_value={
        "item": {"id": str(c.activity)}, "language": "uk",
    })
    return c


@pytest.mark.asyncio
async def test_direct_client_activity_requires_review_without_creating_lead(client_form):
    c = client_form
    await review(c)
    assert c.data[handler.FORM]["kind"] == "client"
    assert c.data[handler.FORM]["lead_id"] is None
    c.service.create_client_activity.assert_not_awaited()
    await click(c, "submit")
    args = c.service.create_client_activity.await_args.kwargs
    assert args["client_id"] == c.client
    assert "lead_id" not in args and "project_id" not in args
    assert c.data[handler.FORM]["phase"] == "done"
    c.service.activity_form.assert_not_awaited()
    c.service.create_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_client_activity_cancel_writes_nothing(client_form):
    c = client_form
    await review(c)
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.create_client_activity.assert_not_awaited()
    c.service.create_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_client_retry_keeps_client_payload_and_key(client_form):
    from copy import deepcopy
    c = client_form
    await review(c)
    c.service.create_client_activity.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"),
        {"item": {"id": str(c.activity)}, "language": "uk"},
    ]
    await click(c, "submit")
    frozen = deepcopy(c.data[handler.FORM])
    await click(c, "edit")
    c.service.create_client_activity.assert_awaited_once()
    await click(c, "submit")
    first, second = c.service.create_client_activity.await_args_list
    assert first.kwargs == second.kwargs
    assert second.kwargs["client_id"] == c.client
    assert second.kwargs["idempotency_key"] == "tg-activity:" + frozen["id"]
    assert c.data[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "nonce"])
async def test_direct_client_start_rejects_stale_source(client_form, failure):
    c = client_form
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.callback.message.message_id = 99
    else:
        c.callback.data = "AP:client:obsolete"
    await handler.activity_callback(c.callback, c.state)
    c.service.client_activity_form.assert_not_awaited()
    c.service.create_client_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_resume_preserves_pending_command_and_does_not_reissue_form(client_form):
    from copy import deepcopy
    c = client_form
    await review(c)
    c.service.create_client_activity.side_effect = RuntimeError("uncertain")
    await click(c, "submit")
    original = deepcopy(c.data[handler.FORM])
    c.callback.data = "AP:client:client-origin"
    await handler.activity_callback(c.callback, c.state)
    assert c.service.client_activity_form.await_args.kwargs["resume"] is True
    assert c.data[handler.FORM]["id"] == original["id"]
    assert c.data[handler.FORM]["payload"] == original["payload"]
    assert c.data[handler.FORM]["attempted_at"] == original["attempted_at"]


@pytest.mark.asyncio
async def test_client_resume_context_does_not_read_mutable_card():
    user, tenant, client_id = uuid4(), uuid4(), uuid4()
    api = NS(client_card=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"name": "Organization", "language": "uk", "timezone": "UTC"},
    ))
    result = await entry.client_activity_form(
        telegram_user_id=123, tenant_id=tenant, client_id=client_id,
        expected_user_id=user, resume=True,
    )
    assert result["client_id"] == str(client_id)
    api.client_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_activity_changed_actor_fails_before_mutation():
    user, tenant = uuid4(), uuid4()
    api = NS(create_client_activity=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.create_client_activity(
            telegram_user_id=123, tenant_id=tenant, client_id=uuid4(),
            expected_user_id=uuid4(), payload={}, assign_self=False,
            idempotency_key="local",
        )
    api.create_client_activity.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "client", "lead", "responsible"])
async def test_client_activity_transport_uses_exact_client_relation(failure):
    import json
    user, tenant, client_id, activity_id = uuid4(), uuid4(), uuid4(), uuid4()
    payload = {
        "type": "call", "due_at": "2026-10-28T12:30:00Z",
        "timezone": "Europe/Kyiv", "comment": None,
    }
    calls = []
    def transport(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body == {
            **payload, "client_id": str(client_id),
            "lead_id": None, "project_id": None, "responsible_user_id": str(user),
        }
        data = {
            **body, "id": str(activity_id), "tenant_id": str(tenant),
            "status": "planned", "result": None, "completed_at": None,
            "cancelled_at": None, "created_by_user_id": str(user),
            "created_at": "2026-10-27T12:00:00Z",
            "updated_at": "2026-10-27T12:00:00Z", "row_version": 1,
        }
        if failure == "client":
            data["client_id"] = str(uuid4())
        elif failure == "lead":
            data["lead_id"] = str(uuid4())
        elif failure == "responsible":
            data["responsible_user_id"] = str(uuid4())
        return httpx.Response(201, json={"data": data, "meta": {}, "request_id": "local"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        async def call():
            return await ConstructionApiClient(http=http).create_client_activity(
                session=PlatformApiClientSession(user, tenant, SecretStr("local")),
                client_id=client_id, payload=payload, responsible_user_id=user,
                idempotency_key="direct-client-activity",
            )
        if failure:
            with pytest.raises(ConstructionApiClientError):
                await call()
        else:
            assert (await call()).data["client_id"] == str(client_id)
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/api/v1/construction/crm/activities"
    assert calls[0].headers["Idempotency-Key"] == "direct-client-activity"


def test_client_activity_button_translations_and_callback_limit():
    from ui.construction_client_card import LABELS
    assert len(LABELS["plan_activity"]) == 7
    assert len(("AP:client:" + "a" * 32).encode("utf-8")) <= 64

# Direct project activity regression v1.
@pytest.fixture(params=["standalone", "client", "catalog"])
def project_form(form, request):
    from handlers.construction import construction_project_areas as areas
    c = form
    c.project = uuid4()
    c.actor = c.service.activity_form.return_value["actor_user_id"]
    c.callback.message.message_id = 1
    c.data["last_menu_message_id"] = 1
    common = {
        "telegram_user_id": 123,
        "tenant_id": c.data[handler.CONTEXT]["tenant_id"],
        "actor_user_id": c.actor, "menu_id": 1,
    }
    if request.param == "standalone":
        c.data[areas.FORM] = {
            **common, "id": "project-source", "phase": "done",
            "step": 4, "payload": {}, "project_id": str(c.project),
        }
    elif request.param == "client":
        client_id = str(uuid4())
        c.data[areas.CARD] = {
            **common, "nonce": "client-page", "client_id": client_id,
            "section": "projects", "project_ids": [str(c.project)],
        }
        c.data[areas.PROJECT] = {
            **common, "nonce": "project-source", "source_nonce": "client-page",
            "client_id": client_id, "project_id": str(c.project),
        }
    else:
        c.data["construction_project_catalog_view"] = {
            **common, "nonce": "project-list", "screen": "card",
            "project_ids": [str(c.project)],
        }
        c.data[areas.CATALOG] = {
            **common, "nonce": "project-source", "source_nonce": "project-list",
            "project_id": str(c.project),
        }
    c.callback.data = "AP:project:project-source"
    c.service.project_activity_form = AsyncMock(return_value={
        "project_id": str(c.project), "actor_user_id": c.actor,
        "organization": "Organization", "subject_name": "Project",
        "language": "uk", "timezone": "UTC",
    })
    c.service.create_project_activity = AsyncMock(return_value={
        "item": {"id": str(c.activity)}, "language": "uk",
    })
    return c


@pytest.mark.asyncio
async def test_project_activity_opens_from_all_three_sources_and_requires_review(project_form):
    c = project_form
    await review(c)
    assert c.data[handler.FORM]["kind"] == "project"
    assert c.data[handler.FORM]["client_id"] is None
    assert c.data[handler.FORM]["lead_id"] is None
    c.service.create_project_activity.assert_not_awaited()
    await click(c, "submit")
    args = c.service.create_project_activity.await_args.kwargs
    assert args["project_id"] == c.project
    assert "client_id" not in args and "lead_id" not in args
    assert c.data[handler.FORM]["phase"] == "done"
    c.service.create_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_activity_cancel_creates_nothing(project_form):
    c = project_form
    await review(c)
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.create_project_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_activity_unknown_result_retries_original_scope_and_key(project_form):
    from copy import deepcopy
    c = project_form
    await review(c)
    c.service.create_project_activity.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"),
        {"item": {"id": str(c.activity)}, "language": "uk"},
    ]
    await click(c, "submit")
    frozen = deepcopy(c.data[handler.FORM])
    await click(c, "edit")
    c.service.create_project_activity.assert_awaited_once()
    await click(c, "submit")
    first, second = c.service.create_project_activity.await_args_list
    assert first.kwargs == second.kwargs
    assert second.kwargs["project_id"] == c.project
    assert second.kwargs["idempotency_key"] == "tg-activity:" + frozen["id"]
    assert c.data[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "nonce"])
async def test_project_activity_start_rejects_stale_origin(project_form, failure):
    c = project_form
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.callback.message.message_id = 99
    else:
        c.callback.data = "AP:project:obsolete"
    await handler.activity_callback(c.callback, c.state)
    c.service.project_activity_form.assert_not_awaited()
    c.service.create_project_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_activity_resume_skips_mutable_project_read():
    user, tenant, project_id = uuid4(), uuid4(), uuid4()
    api = NS(project_details=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"name": "Organization", "language": "uk", "timezone": "UTC"},
    ))
    result = await entry.project_activity_form(
        telegram_user_id=123, tenant_id=tenant, project_id=project_id,
        expected_user_id=user, resume=True,
    )
    assert result["project_id"] == str(project_id)
    api.project_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_activity_changed_actor_is_denied_before_mutation():
    user, tenant = uuid4(), uuid4()
    api = NS(create_project_activity=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.create_project_activity(
            telegram_user_id=123, tenant_id=tenant, project_id=uuid4(),
            expected_user_id=uuid4(), payload={}, assign_self=False,
            idempotency_key="local",
        )
    api.create_project_activity.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "project", "client", "lead"])
async def test_project_activity_transport_does_not_add_other_relations(failure):
    import json
    user, tenant, project_id, activity_id = uuid4(), uuid4(), uuid4(), uuid4()
    payload = {
        "type": "site_visit", "due_at": "2026-10-28T12:30:00Z",
        "timezone": "Europe/Kyiv", "comment": None,
    }
    calls = []
    def transport(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body == {
            **payload, "project_id": str(project_id), "client_id": None,
            "lead_id": None, "responsible_user_id": str(user),
        }
        data = {
            **body, "id": str(activity_id), "tenant_id": str(tenant),
            "status": "planned", "result": None, "completed_at": None,
            "cancelled_at": None, "created_by_user_id": str(user),
            "created_at": "2026-10-27T12:00:00Z",
            "updated_at": "2026-10-27T12:00:00Z", "row_version": 1,
        }
        if failure == "project":
            data["project_id"] = str(uuid4())
        elif failure == "client":
            data["client_id"] = str(uuid4())
        elif failure == "lead":
            data["lead_id"] = str(uuid4())
        return httpx.Response(201, json={"data": data, "meta": {}, "request_id": "local"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        async def call():
            return await ConstructionApiClient(http=http).create_project_activity(
                session=PlatformApiClientSession(user, tenant, SecretStr("local")),
                project_id=project_id, payload=payload, responsible_user_id=user,
                idempotency_key="direct-project-activity",
            )
        if failure:
            with pytest.raises(ConstructionApiClientError):
                await call()
        else:
            assert (await call()).data["project_id"] == str(project_id)
    assert calls[0].url.path == "/api/v1/construction/crm/activities"
    assert calls[0].headers["Idempotency-Key"] == "direct-project-activity"


def test_project_activity_callback_is_bounded():
    assert len(("AP:project:" + "a" * 32).encode("utf-8")) <= 64
