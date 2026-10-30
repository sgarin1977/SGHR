from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_create as parent
from handlers.construction import construction_lead_responsible as picker
from services.construction.construction_api_client import (
    ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry


@pytest.fixture
def case(monkeypatch):
    actor, tenant, lead = uuid4(), uuid4(), uuid4()
    form = {
        "id": uuid4().hex, "telegram_user_id": 123,
        "tenant_id": str(tenant), "actor_user_id": str(actor),
        "organization": "Organization", "language": "uk",
        "phase": "input", "step": len(parent.FIELDS),
        "payload": {
            "name": "Lead", "phone": "+123",
            "source": "test", "work_type_description": "Repair",
        },
        "assign_self": False, "attempted_at": None, "menu_id": 1,
        "can_choose_responsible": True,
    }
    data = {
        parent.FORM: form,
        parent.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(tenant)},
        "last_menu_message_id": 1,
    }

    async def update(**values):
        data.update(deepcopy(values))

    state = NS(
        get_data=AsyncMock(side_effect=lambda: data),
        get_state=AsyncMock(return_value=None),
        update_data=AsyncMock(side_effect=update),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    callback = NS(
        from_user=sender, answer=AsyncMock(),
        message=NS(
            chat=NS(id=123, type="private"), message_id=1,
            answer=AsyncMock(),
        ),
    )
    members = [
        {
            "user_id": str(UUID(int=index + 100)),
            "display_name": "Colleague " + str(index),
            "email": "private-email@example.test",
        }
        for index in range(7)
    ]
    service = NS(
        team_members=AsyncMock(return_value={
            "actor_user_id": str(actor), "items": members,
            "organization": "Organization", "language": "uk",
        }),
        create_lead=AsyncMock(return_value={"item": {"id": str(lead)}}),
    )

    @asynccontextmanager
    async def entry(**kwargs):
        yield service

    shown = []

    async def show(event, current_state, current_form, text, rows, **kwargs):
        saved = kwargs.get("expected_snapshot")
        if saved is not None and not picker.same(data, saved):
            return
        shown.append((text, rows))
        if current_form is not None:
            current_form["menu_id"] = 1
            await current_state.update_data(**{parent.FORM: current_form})
        await current_state.update_data(last_menu_message_id=1)

    monkeypatch.setattr(parent, "construction_bot_api_entry", entry)
    monkeypatch.setattr(parent, "show", show)
    return NS(
        actor=actor, tenant=tenant, lead=lead, data=data,
        callback=callback, state=state, service=service,
        members=members, shown=shown,
    )


async def click(case, action):
    case.callback.data = parent.cb(case.data[parent.FORM], action)
    await parent.lead_create_callback(case.callback, case.state)


@pytest.mark.asyncio
async def test_selection_rechecks_api_and_submits_exact_selected_identity(case):
    c = case
    await click(c, "r_open")
    assert len(c.data[parent.FORM]["responsible_ids"]) == 5
    assert "private-email@example.test" not in repr(c.data)
    assert "Colleague 0" not in repr(c.data)
    selected = c.data[parent.FORM]["responsible_ids"][0]
    await click(c, "r_pick0")
    assert c.service.team_members.await_count == 2
    assert c.data[parent.FORM]["phase"] == "review"
    assert c.data[parent.FORM]["responsible_user_id"] == selected
    c.service.create_lead.assert_not_awaited()
    await click(c, "submit")
    sent = c.service.create_lead.await_args.kwargs
    assert sent["responsible_user_id"] == UUID(selected)
    assert sent["assign_self"] is False
    assert "responsible_user_id" not in sent["payload"]
    assert c.data[parent.FORM]["phase"] == "done"
    assert c.data[parent.FORM]["responsible_name"] is None


@pytest.mark.asyncio
async def test_old_page_button_cannot_select_a_different_member(case):
    c = case
    await click(c, "r_open")
    old_button = parent.cb(c.data[parent.FORM], "r_pick0")
    await click(c, "r_next")
    assert len(c.data[parent.FORM]["responsible_ids"]) == 2
    count = c.service.team_members.await_count
    c.callback.data = old_button
    await parent.lead_create_callback(c.callback, c.state)
    assert c.service.team_members.await_count == count
    assert c.data[parent.FORM]["phase"] == "responsibles"
    assert not c.data[parent.FORM].get("responsible_user_id")


@pytest.mark.asyncio
async def test_removed_candidate_is_not_selected_from_cached_page(case):
    c = case
    await click(c, "r_open")
    selected = c.data[parent.FORM]["responsible_ids"][0]
    c.service.team_members.return_value["items"] = [
        item for item in c.members if item["user_id"] != selected
    ]
    await click(c, "r_pick0")
    assert c.data[parent.FORM]["phase"] == "responsibles"
    assert not c.data[parent.FORM].get("responsible_user_id")
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_team_access_exposes_no_cached_choices(case):
    c = case
    await click(c, "r_open")
    c.service.team_members.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "r_refresh")
    assert c.data[parent.FORM]["responsible_ids"] == []
    assert all("Colleague" not in row[0].text for row in c.shown[-1][1])
    await click(c, "r_back")
    await click(c, "none")
    assert c.data[parent.FORM]["phase"] == "review"
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_team_response_does_not_restore_another_organization(case):
    c = case

    async def changed(**kwargs):
        c.data[parent.CONTEXT]["tenant_id"] = str(uuid4())
        return {"actor_user_id": str(c.actor), "items": c.members}

    c.service.team_members.side_effect = changed
    before = deepcopy(c.data[parent.FORM])
    await click(c, "r_open")
    assert c.data[parent.FORM] == before
    assert c.shown == []


@pytest.mark.asyncio
async def test_picker_without_existing_capability_does_not_read_team(case):
    c = case
    c.data[parent.FORM]["can_choose_responsible"] = False
    await click(c, "r_open")
    c.service.team_members.assert_not_awaited()
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_create_keeps_selected_user_payload_and_key(case):
    c = case
    await click(c, "r_open")
    await click(c, "r_pick0")
    c.service.create_lead.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": {"id": str(c.lead)}},
    ]
    await click(c, "submit")
    first = deepcopy(c.service.create_lead.await_args.kwargs)
    assert c.data[parent.FORM]["phase"] == "pending"
    reads = c.service.team_members.await_count
    await click(c, "r_open")
    assert c.service.team_members.await_count == reads
    await click(c, "submit")
    assert c.service.create_lead.await_args.kwargs == first
    assert c.service.team_members.await_count == reads


@pytest.mark.asyncio
async def test_adapter_preserves_target_and_rejects_changed_actor():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test"))
    post = AsyncMock(return_value=NS(data={"id": str(uuid4())}))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(create_lead=post)),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "name": "Organization"},
    ))
    target = uuid4()
    command = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id,
        payload={"name": "Lead", "phone": "+123",
                 "source": "test", "work_type_description": "Repair"},
        assign_self=False, responsible_user_id=target,
        idempotency_key="stable",
    )
    await entry.create_lead(**command)
    sent = post.await_args.kwargs
    assert sent["session"] is session
    assert sent["payload"] == {
        **command["payload"], "responsible_user_id": str(target),
    }
    assert sent["idempotency_key"] == "stable"
    post.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.create_lead(**{**command, "expected_user_id": uuid4()})
    post.assert_not_awaited()
    with pytest.raises(ValueError):
        await entry.create_lead(**{**command, "assign_self": True})
    with pytest.raises(ValueError):
        await entry.create_lead(**{**command, "responsible_user_id": str(target)})
    with pytest.raises(ValueError):
        await entry.create_lead(**{
            **command, "payload": {**command["payload"], "responsible_user_id": target},
        })
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_picker_capability_comes_from_existing_navigation(allowed):
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test"))
    entry = ConstructionBotApiEntry(api=None, context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        session,
        {"name": "Organization", "language": "uk", "can_manage_team": allowed},
    ))
    result = await entry.lead_form(telegram_user_id=123, tenant_id=session.tenant_id)
    assert result["can_choose_responsible"] is allowed
    assert result["actor_user_id"] == str(session.user_id)


def test_seven_languages_and_bounded_callbacks():
    assert all(len(values) == 7 for values in picker.LABELS.values())
    form = {"id": "a" * 32, "step": len(parent.FIELDS)}
    for action in ("r_open", "r_pick4", "r_next", "r_prev", "r_refresh", "r_back"):
        assert len(parent.cb(form, action).encode("utf-8")) <= 64
