"""Project activity selection and navigation for the shared terminal form."""
from copy import deepcopy
from uuid import UUID, uuid4

from handlers.construction import construction_project_activities as pages
from handlers.construction import construction_project_team as team
from handlers.construction import construction_activity_terminal as terminal
from handlers.construction.construction_activity_plan import suspend_activity_form
from handlers.construction.construction_lead_create import suspend_lead_form
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_client_card import tr as card_tr

FIELDS = ("telegram_user_id", "tenant_id", "project_id", "actor_user_id")


def matches(form, selected):
    return (
        isinstance(form, dict) and form.get("kind") == "project"
        and all(form.get(key) == selected.get(key) for key in FIELDS)
    )


def pending(form, selected):
    return (
        matches(form, selected) and form.get("phase") == "pending"
        and form.get("attempted_at") is not None
    )


def controls(view, selected, language, form=None):
    result = []
    if pending(form, selected):
        result.append([terminal.button(
            card_tr("activity_resume", language),
            "AT:project-resume:" + view["nonce"],
        )])
    if view.get("phase") == "project_activities":
        for index, activity_id in enumerate(view.get("activity_ids", [])):
            UUID(activity_id)
            result.append([terminal.button(
                f'{index + 1}. ' + card_tr("activity_action", language),
                f'AT:project:{view["nonce"]}:{index}',
            )])
    return result


def page_source(data, event, token):
    view = data.get(team.VIEW)
    if (
        not isinstance(view, dict) or view.get("nonce") != token
        or view.get("phase") not in ("project_activities", "project_activities_error")
    ):
        raise ValueError("Project activity page is stale.")
    selected = team.selected_from(data, view, event)
    if selected is None:
        raise ValueError("Project activity origin changed.")
    return view, selected


def origin(data, form, event):
    if not isinstance(form, dict) or form.get("kind") != "project":
        return None
    view = data.get(team.VIEW)
    if (
        not isinstance(view, dict)
        or view.get("nonce") != form.get("project_page_nonce")
        or view.get("phase") not in (
            "project_activities", "project_activities_error",
            "project_activity_terminal",
        )
    ):
        return None
    selected = team.selected_from(data, form.get("project_origin"), event)
    return selected if selected is not None and matches(form, selected) else None


def valid(data, form, event):
    current = data.get(terminal.FORM)
    return (
        isinstance(current, dict)
        and current.get("id") == form.get("id")
        and current.get("phase") == form.get("phase")
        and current.get("menu_id") is not None
        and current.get("menu_id") == data.get("last_menu_message_id")
        and origin(data, form, event) is not None
    )


async def render(event, state, form, text, rows):
    data = await state.get_data()
    selected = origin(data, form, event)
    current = data.get(terminal.FORM)
    if (
        selected is None or not isinstance(current, dict)
        or current.get("id") != form.get("id")
    ):
        return
    saved = pages.snapshot(data)
    view = {**data[team.VIEW], "phase": "project_activity_terminal"}
    menu = await team.show(
        event, state, selected, view, text, rows, expected_snapshot=saved,
    )
    if menu is None:
        return
    after = await state.get_data()
    if after.get(terminal.FORM) != saved[terminal.FORM]:
        return
    form = {**form, "menu_id": menu}
    await state.update_data(**{terminal.FORM: form})


async def resume(event, state, data, token):
    view, selected = page_source(data, event, token)
    previous = data.get(terminal.FORM)
    if not pending(previous, selected):
        raise ValueError("Pending project activity context differs.")
    # Only navigation changes; the original command and key remain frozen.
    form = {
        **deepcopy(previous), "project_origin": selected,
        "project_page_nonce": view["nonce"], "menu_id": None,
    }
    await state.update_data(**{terminal.FORM: form})
    await terminal.display(event, state, form)


async def select(event, state, data, token, selector, bot, event_update):
    view, selected = page_source(data, event, token)
    ids = view.get("activity_ids", [])
    if view["phase"] != "project_activities" or not isinstance(ids, list):
        raise ValueError("Project activity selection unavailable.")
    index = int(selector)
    if str(index) != selector or not 0 <= index < len(ids) <= 5:
        raise ValueError("Invalid project activity selector.")
    activity_id = UUID(ids[index])
    previous = data.get(terminal.FORM)
    if isinstance(previous, dict) and previous.get("phase") == "pending":
        if pending(previous, selected) and previous.get("activity_id") == str(activity_id):
            await resume(event, state, data, token)
            return
        raise ValueError("Another activity request is unresolved.")
    saved = pages.snapshot(data)
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        prepared = await service.project_activity_terminal_context(
            telegram_user_id=event.from_user.id,
            tenant_id=UUID(selected["tenant_id"]),
            project_id=UUID(selected["project_id"]), activity_id=activity_id,
            expected_user_id=UUID(selected["actor_user_id"]),
            client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
            cursor=view.get("cursor"),
        )
    if not pages.same(await state.get_data(), saved):
        return
    action = prepared["activity"]
    if (
        prepared["actor_user_id"] != selected["actor_user_id"]
        or prepared["project_id"] != selected["project_id"]
        or action["id"] != str(activity_id)
        or action["tenant_id"] != selected["tenant_id"]
        or action["project_id"] != selected["project_id"]
        or action["status"] != "planned"
        or type(action["row_version"]) is not int
        or not 0 < action["row_version"] < 2**63
    ):
        raise ValueError("Project activity selection changed.")
    links = {
        key: str(UUID(action[key])) if action[key] is not None else None
        for key in ("lead_id", "client_id", "project_id")
    }
    form = {
        **{key: selected[key] for key in FIELDS},
        "id": uuid4().hex, "source_id": uuid4().hex, "kind": "project",
        "project_origin": selected, "project_page_nonce": view["nonce"],
        "activity_id": str(activity_id), "version": action["row_version"],
        "expected_links": links, "type": action["type"],
        "due_at": action["due_at"], "timezone": action["timezone"],
        "organization": prepared["organization"],
        "subject_name": prepared["subject_name"],
        "language": prepared["language"] or event.from_user.language_code or "ru",
        "operation": None, "phase": "choose",
        "result_provided": False, "result": None,
        "attempted_at": None, "menu_id": None,
    }
    await suspend_lead_form(state)
    await suspend_activity_form(state)
    await state.update_data(**{terminal.FORM: form})
    await terminal.display(event, state, form)
