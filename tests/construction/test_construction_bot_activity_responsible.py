from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from handlers.construction import construction_activity_plan as parent
from handlers.construction import construction_activity_responsible as picker
from services.construction.construction_api_client import (
    ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry


@pytest.fixture(params=("lead", "client", "project"))
def case(request, monkeypatch):
    kind = request.param
    actor, tenant, resource, activity = uuid4(), uuid4(), uuid4(), uuid4()
    form = {
        "id": uuid4().hex, "kind": kind, "telegram_user_id": 123,
        "tenant_id": str(tenant), "actor_user_id": str(actor),
        kind + "_id": str(resource),
        "organization": "Organization", "language": "uk",
        "phase": "who", "timezone": "UTC",
        "payload": {
            "type": "call", "due_at": "2026-10-30T12:00+00:00",
            "timezone": "UTC",
        },
        "assign_self": False, "attempted_at": None, "menu_id": 1,
        "can_choose_responsible": kind != "project",
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
            chat=NS(id=123, type="private"),
            message_id=1, answer=AsyncMock(),
        ),
    )
    members = [
        {"user_id": str(UUID(int=index + 100)),
         "display_name": "Colleague " + str(index),
         "email": "private@example.test"}
        for index in range(7)
    ]
    service = NS(
        team_members=AsyncMock(return_value={
            "actor_user_id": str(actor), "items": members,
        }),
    )
    create = AsyncMock(return_value={"item": {"id": str(activity)}})
    setattr(service, "create_" + kind + "_activity", create)

    @asynccontextmanager
    async def entry(**kwargs):
        yield service

    shown = []

    async def show(event, state, form, text, rows, **kwargs):
        saved = kwargs.get("expected_snapshot")
        if saved is not None and not picker.same(data, saved):
            return
        shown.append((text, rows))
        if form is not None:
            form["menu_id"] = 1
            await state.update_data(**{parent.FORM: form})
        await state.update_data(last_menu_message_id=1)

    monkeypatch.setattr(parent, "construction_bot_api_entry", entry)
    monkeypatch.setattr(parent, "show", show)
    return NS(
        kind=kind, actor=actor, tenant=tenant, resource=resource,
        activity=activity, data=data, state=state, callback=callback,
        members=members, service=service, create=create, shown=shown,
    )


async def click(c, action):
    c.callback.data = parent.cb(c.data[parent.FORM], action)
    await parent.activity_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_selection_uses_exact_scope_and_requires_review(case):
    c = case
    await click(c, "r_open")
    query = c.service.team_members.await_args.kwargs
    assert query["tenant_id"] == c.tenant
    assert query["expected_user_id"] == c.actor
    if c.kind == "project":
        assert query["project_id"] == c.resource
    else:
        assert "project_id" not in query
    assert len(c.data[parent.FORM]["responsible_ids"]) == 5
    assert "private@example.test" not in repr(c.data)
    selected = c.data[parent.FORM]["responsible_ids"][0]
    await click(c, "r_pick0")
    assert c.service.team_members.await_count == 2
    assert c.data[parent.FORM]["phase"] == "comment"
    c.create.assert_not_awaited()
    await click(c, "skip")
    assert c.data[parent.FORM]["phase"] == "review"
    assert "Colleague 0" in c.shown[-1][0]
    c.create.assert_not_awaited()
    await click(c, "submit")
    command = c.create.await_args.kwargs
    assert command["responsible_user_id"] == UUID(selected)
    assert command[c.kind + "_id"] == c.resource
    assert command["assign_self"] is False
    assert "responsible_user_id" not in command["payload"]
    assert c.data[parent.FORM]["phase"] == "done"
    assert c.data[parent.FORM]["responsible_name"] is None


@pytest.mark.asyncio
async def test_stale_page_and_removed_member_are_not_selected(case):
    c = case
    await click(c, "r_open")
    stale = parent.cb(c.data[parent.FORM], "r_pick0")
    await click(c, "r_next")
    count = c.service.team_members.await_count
    c.callback.data = stale
    await parent.activity_callback(c.callback, c.state)
    assert c.service.team_members.await_count == count
    selected = c.data[parent.FORM]["responsible_ids"][0]
    c.service.team_members.return_value["items"] = [
        item for item in c.members if item["user_id"] != selected
    ]
    await click(c, "r_pick0")
    assert c.data[parent.FORM]["phase"] == "responsibles"
    assert not c.data[parent.FORM].get("responsible_user_id")
    c.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_access_does_not_reuse_cached_choices(case):
    c = case
    await click(c, "r_open")
    c.service.team_members.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "r_refresh")
    assert c.data[parent.FORM]["responsible_ids"] == []
    assert "Colleague" not in repr(c.shown[-1])
    await click(c, "r_back")
    await click(c, "mine")
    assert c.data[parent.FORM]["phase"] == "comment"
    assert not c.data[parent.FORM].get("responsible_user_id")


@pytest.mark.asyncio
async def test_late_response_does_not_restore_departed_context(case):
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
async def test_pending_retry_keeps_target_body_and_key(case):
    c = case
    await click(c, "r_open")
    await click(c, "r_pick0")
    await click(c, "skip")
    c.create.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": {"id": str(c.activity)}},
    ]
    await click(c, "submit")
    first = deepcopy(c.create.await_args.kwargs)
    reads = c.service.team_members.await_count
    await click(c, "r_open")
    assert c.service.team_members.await_count == reads
    await click(c, "submit")
    assert c.create.await_args.kwargs == first
    assert c.data[parent.FORM]["phase"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("lead", "client", "project"))
async def test_adapter_preserves_responsible_and_rejects_identity_change(kind):
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test"))
    post = AsyncMock(return_value=NS(data={"id": str(uuid4())}))
    api = NS()
    setattr(api, "create_" + kind + "_activity", post)
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Organization", "language": "uk"},
    ))
    target, resource = uuid4(), uuid4()
    command = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id, assign_self=False,
        responsible_user_id=target, idempotency_key="stable",
        payload={
            "type": "call", "due_at": "2026-10-30T12:00+00:00",
            "timezone": "UTC", "comment": None,
        },
        **{kind + "_id": resource},
    )
    create = getattr(entry, "create_" + kind + "_activity")
    await create(**command)
    sent = post.await_args.kwargs
    assert sent["session"] is session
    assert sent["responsible_user_id"] == target
    assert sent[kind + "_id"] == resource
    assert sent["payload"] == command["payload"]
    assert sent["idempotency_key"] == "stable"
    post.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await create(**{**command, "expected_user_id": uuid4()})
    with pytest.raises(ValueError):
        await create(**{**command, "assign_self": True})
    with pytest.raises(ValueError):
        await create(**{**command, "responsible_user_id": str(target)})
    post.assert_not_awaited()


def test_callbacks_fit_telegram_limit():
    form = {"id": "a" * 32, "phase": "responsibles"}
    for action in ("r_pick4", "r_refresh", "r_next", "r_prev", "r_back"):
        assert len(parent.cb(form, action).encode("utf-8")) <= 64
