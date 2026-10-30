from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_create as parent
from handlers.construction import construction_lead_create as leads
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_project_create import wizard, click
from test_construction_bot_project_from_client import client_wizard


@pytest.fixture(params=("standalone", "client"))
def case(request, client_wizard):
    c = client_wizard
    c.kind = request.param
    c.service.project_form.return_value["can_choose_responsible"] = True
    c.service.client_project_form.return_value["can_choose_responsible"] = True
    c.members = [
        {
            "user_id": str(UUID(int=index + 100)),
            "display_name": "Colleague " + str(index),
            "email": "private@example.test",
        }
        for index in range(7)
    ]
    c.service.team_members = AsyncMock(return_value={
        "actor_user_id": str(c.actor_id), "items": c.members,
    })
    if c.kind == "standalone":
        c.callback.data = "PF:new"
    return c


async def responsible_step(c):
    await parent.project_create_callback(c.callback, c.state)
    for value in ("Project", "Address"):
        c.message.text = value
        await parent.project_create_message(c.message, c.state)
    await click(c, "address_continue")
    await click(c, "skip")
    assert c.data[parent.FORM]["step"] == len(parent.FIELDS)
    assert c.data[parent.FORM]["can_choose_responsible"] is True


@pytest.mark.asyncio
async def test_selected_colleague_is_reviewed_and_client_binding_is_preserved(case):
    c = case
    await responsible_step(c)
    c.data[leads.FORM] = {"separate_lead_marker": True}
    before = deepcopy(c.data[leads.FORM])
    await click(c, "r_open")
    assert len(c.data[parent.FORM]["responsible_ids"]) == 5
    assert "private@example.test" not in repr(c.data[parent.FORM])
    selected = c.data[parent.FORM]["responsible_ids"][0]
    await click(c, "r_pick0")
    assert c.data[parent.FORM]["phase"] == "review"
    assert c.data[leads.FORM] == before
    assert "Colleague 0" in c.shown[-1][0]
    c.service.create_project.assert_not_awaited()
    await click(c, "submit")
    sent = c.service.create_project.await_args.kwargs
    assert sent["responsible_user_id"] == UUID(selected)
    assert sent["assign_self"] is False
    assert "responsible_user_id" not in sent["payload"]
    if c.kind == "client":
        assert sent["client_id"] == c.client_id
    else:
        assert "client_id" not in sent
    assert c.data[parent.FORM]["phase"] == "done"
    assert c.data[parent.FORM]["responsible_name"] is None


@pytest.mark.asyncio
async def test_old_page_and_removed_member_cannot_be_selected(case):
    c = case
    await responsible_step(c)
    await click(c, "r_open")
    old = parent.cb(c.data[parent.FORM], "r_pick0")
    await click(c, "r_next")
    count = c.service.team_members.await_count
    c.callback.data = old
    await parent.project_create_callback(c.callback, c.state)
    assert c.service.team_members.await_count == count
    selected = c.data[parent.FORM]["responsible_ids"][0]
    c.service.team_members.return_value["items"] = [
        item for item in c.members if item["user_id"] != selected
    ]
    await click(c, "r_pick0")
    assert c.data[parent.FORM]["phase"] == "responsibles"
    assert not c.data[parent.FORM].get("responsible_user_id")
    c.service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_team_access_exposes_no_cached_members(case):
    c = case
    await responsible_step(c)
    await click(c, "r_open")
    c.service.team_members.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "r_refresh")
    assert c.data[parent.FORM]["responsible_ids"] == []
    assert "Colleague" not in repr(c.shown[-1])
    await click(c, "r_back")
    await click(c, "none")
    assert c.data[parent.FORM]["phase"] == "review"
    assert not c.data[parent.FORM].get("responsible_user_id")


@pytest.mark.asyncio
async def test_late_response_does_not_restore_another_organization(case):
    c = case
    await responsible_step(c)
    before = deepcopy(c.data[parent.FORM])

    async def changed(**kwargs):
        c.data[parent.CONTEXT]["tenant_id"] = str(uuid4())
        return {"actor_user_id": str(c.actor_id), "items": c.members}

    c.service.team_members.side_effect = changed
    shown = len(c.shown)
    await click(c, "r_open")
    assert c.data[parent.FORM] == before
    assert len(c.shown) == shown


@pytest.mark.asyncio
async def test_uncertain_create_reuses_original_responsible_client_and_key(case):
    c = case
    await responsible_step(c)
    await click(c, "r_open")
    await click(c, "r_pick0")
    receipt = c.service.create_project.return_value
    c.service.create_project.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        receipt,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.create_project.await_args.kwargs)
    reads = c.service.team_members.await_count
    await click(c, "r_open")
    assert c.service.team_members.await_count == reads
    await click(c, "submit")
    assert c.service.create_project.await_args.kwargs == first
    assert c.data[parent.FORM]["phase"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", (False, True))
async def test_adapter_sends_selected_uuid_and_rejects_ambiguous_assignment(linked):
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test"))
    post = AsyncMock(return_value=NS(data={"id": str(uuid4())}))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(create_project=post)), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Organization", "language": "uk", "can_create_project": True},
    ))
    target = uuid4()
    client_id = uuid4() if linked else None
    values = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id,
        payload={"name": "Project", "address_raw": "Address", "comment": None},
        assign_self=False, idempotency_key="stable",
        client_id=client_id, responsible_user_id=target,
    )
    await entry.create_project(**values)
    sent = post.await_args.kwargs
    assert sent["payload"] == {
        **values["payload"],
        "client_id": str(client_id) if client_id else None,
        "responsible_user_id": str(target),
    }
    assert sent["idempotency_key"] == "stable"
    post.reset_mock()
    with pytest.raises(ValueError):
        await entry.create_project(**{**values, "assign_self": True})
    with pytest.raises(ValueError):
        await entry.create_project(**{**values, "responsible_user_id": str(target)})
    with pytest.raises(ConstructionApiClientError):
        await entry.create_project(**{**values, "expected_user_id": uuid4()})
    post.assert_not_awaited()
