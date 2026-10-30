"""Activity responsible selection through the existing scoped team API."""
from copy import deepcopy
from uuid import UUID, uuid4

from handlers.construction import construction_activity_plan as parent
from handlers.construction.construction_lead_responsible import tr, members_from, member_name
from ui.construction_team import tr as team_tr
from ui.texts import t

PAGE_SIZE = 5


def snapshot(data):
    return deepcopy({
        key: data.get(key)
        for key in (parent.FORM, parent.CONTEXT, "last_menu_message_id")
    })


def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())


def bound(data, form, event):
    context = data.get(parent.CONTEXT)
    return (
        data.get(parent.FORM) == form
        and isinstance(context, dict)
        and form.get("telegram_user_id") == event.from_user.id
        and context.get("telegram_user_id") == event.from_user.id
        and context.get("tenant_id") == form.get("tenant_id")
        and (
            form.get("can_choose_responsible") is True
            or form.get("kind") == "project"
        )
        and form.get("attempted_at") is None
        and form.get("phase") in ("who", "responsibles")
        and form.get("menu_id") is not None
        and form["menu_id"] == data.get("last_menu_message_id")
        and event.message.message_id == form["menu_id"]
    )


async def render(
    event, state, form, *, members=None, offset=0,
    notice=None, expected_snapshot=None,
):
    data = await state.get_data()
    saved = expected_snapshot if expected_snapshot is not None else snapshot(data)
    if await state.get_state() is not None or not same(data, saved):
        return
    form = deepcopy(form)
    language = form["language"]
    form.update(
        id=uuid4().hex, phase="responsibles",
        responsible_ids=[], responsible_offset=offset,
        responsible_has_next=False,
    )
    lines = [
        parent.common("responsible", language),
        str(form["organization"])[:120], tr("note", language),
    ]
    rows = []
    if members is None:
        lines.append(t("construction_bot_unavailable", language))
    else:
        offset = min(offset, ((len(members) - 1) // PAGE_SIZE) * PAGE_SIZE) if members else 0
        page = members[offset:offset + PAGE_SIZE]
        form["responsible_offset"] = offset
        form["responsible_ids"] = [item["user_id"] for item in page]
        form["responsible_has_next"] = offset + PAGE_SIZE < len(members)
        lines.append(
            f"{offset + 1}–{offset + len(page)} / {len(members)}"
            if page else tr("empty", language)
        )
        for index, item in enumerate(page):
            rows.append([parent.button(
                member_name(item, offset + index, language),
                parent.cb(form, "r_pick" + str(index)),
            )])
        if offset:
            rows.append([parent.button(
                team_tr("previous", language), parent.cb(form, "r_prev"),
            )])
        if form["responsible_has_next"]:
            rows.append([parent.button(
                team_tr("next", language), parent.cb(form, "r_next"),
            )])
    if notice:
        lines.append(notice)
    rows.extend([
        [parent.button(team_tr("refresh", language), parent.cb(form, "r_refresh"))],
        [parent.button(tr("back", language), parent.cb(form, "r_back"))],
        [parent.button(parent.common("cancel", language), parent.cb(form, "cancel"))],
        [parent.activity_parent_button(form, language)],
    ])
    await parent.show(
        event, state, form, "\n\n".join(lines), rows,
        expected_snapshot=saved,
    )


async def dispatch(event, state, form, action, *, bot, event_update):
    data = await state.get_data()
    if await state.get_state() is not None or not bound(data, form, event):
        return
    saved = snapshot(data)
    if action == "r_back":
        form = deepcopy(form)
        form.update(id=uuid4().hex, phase="who", responsible_ids=[])
        await state.update_data(**{parent.FORM: form})
        await parent.display(event, state, form)
        return
    phase = form["phase"]
    if action == "r_open" and phase == "who":
        offset = 0
    elif phase == "responsibles" and action in ("r_next", "r_prev", "r_refresh"):
        offset = form.get("responsible_offset", 0)
        if type(offset) is not int or offset < 0 or offset % PAGE_SIZE:
            return
        if action == "r_next":
            if form.get("responsible_has_next") is not True:
                return
            offset += PAGE_SIZE
        elif action == "r_prev":
            if not offset:
                return
            offset -= PAGE_SIZE
    elif phase == "responsibles" and action.startswith("r_pick"):
        index = action.removeprefix("r_pick")
        ids = form.get("responsible_ids", [])
        if (
            index not in tuple(str(value) for value in range(PAGE_SIZE))
            or not isinstance(ids, list) or int(index) >= len(ids)
        ):
            return
        selected_id = str(UUID(ids[int(index)]))
        offset = form.get("responsible_offset", 0)
    else:
        return
    try:
        scope = (
            {"project_id": UUID(form["project_id"])}
            if form.get("kind") == "project" else {}
        )
        async with parent.construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.team_members(
                telegram_user_id=event.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                **scope,
            )
        members = members_from(result, form)
        if await state.get_state() is not None or not same(await state.get_data(), saved):
            return
        notice = None
        if action.startswith("r_pick"):
            selected = next(
                (item for item in members if item["user_id"] == selected_id),
                None,
            )
            if selected is not None:
                form = deepcopy(form)
                form.update(
                    id=uuid4().hex, phase="comment", assign_self=False,
                    responsible_user_id=selected_id,
                    responsible_name=member_name(
                        selected, members.index(selected), form["language"],
                    ),
                    responsible_ids=[],
                )
                await state.update_data(**{parent.FORM: form})
                await parent.display(event, state, form)
                return
            notice = parent.common("stale", form["language"])
        await render(
            event, state, form, members=members, offset=offset,
            notice=notice, expected_snapshot=saved,
        )
    except Exception as error:
        parent.logger.warning(
            "construction_activity_responsible_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if await state.get_state() is None and same(await state.get_data(), saved):
            await render(event, state, form, expected_snapshot=saved)
