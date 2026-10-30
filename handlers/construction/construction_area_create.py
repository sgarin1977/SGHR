"""Private area wizard. Domain writes go through the shared API only."""
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_project_areas as areas
from handlers.construction.construction_lead_create import lock_for, private, owns
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_area_create import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_area_create")
logger = logging.getLogger(__name__)
FORM = "construction_area_create_form"
TYPES = ("ROOM", "PROJECT_GENERAL", "EXTERIOR", "OTHER")
OTHER_FORMS = (
    "construction_lead_create_form", "construction_activity_plan_form",
    "construction_activity_terminal_form", "construction_lead_convert_form",
    "construction_client_create_form", "construction_project_create_form",
)
SAFE_RETRY_SECONDS = 86340


def cb(form, action):
    return f'AC:{action}:{form["id"]}:{form["revision"]}'


async def suspend_area_form(state):
    form = (await state.get_data()).get(FORM)
    if isinstance(form, dict) and form:
        keep = form.get("attempted_at") is not None or form.get("phase") == "done"
        await state.update_data(**{
            FORM: {**form, "menu_id": None} if keep else {},
        })


def selected_for(data, sender, menu):
    view = data.get(areas.VIEW)
    if not isinstance(view, dict) or view.get("menu_id") != menu:
        return None
    selected = areas.binding(
        data, key=view.get("source_key"), token=view.get("source_token"),
        sender=sender, menu=menu,
    )
    if selected is None or any(
        view.get(key) != selected[key]
        for key in ("telegram_user_id", "tenant_id", "project_id", "actor_user_id")
    ):
        return None
    return selected


def live(form, data, sender):
    if (
        not owns(form, sender) or not form
        or form.get("menu_id") != data.get("last_menu_message_id")
        or form.get("menu_id") is None
    ):
        return False
    view = data.get(areas.VIEW, {})
    selected = selected_for(data, sender, form["menu_id"])
    if selected is None or view.get("nonce") != form.get("area_nonce"):
        return False
    if any(form.get(key) != selected[key] for key in (
        "telegram_user_id", "tenant_id", "project_id", "actor_user_id",
    )):
        return False
    return not any(
        isinstance(other := data.get(key), dict)
        and other.get("menu_id") == form["menu_id"]
        and other.get("phase") not in ("done", "cancelled")
        for key in OTHER_FORMS
    )


async def suspend_others(state, selected):
    from handlers.construction.construction_lead_create import suspend_lead_form
    from handlers.construction.construction_activity_plan import suspend_activity_form
    from handlers.construction.construction_activity_terminal import suspend_terminal_form
    from handlers.construction.construction_lead_convert import suspend_conversion_form
    from handlers.construction.construction_client_create import suspend_client_form
    from handlers.construction.construction_project_create import suspend_project_form

    for suspend in (
        suspend_lead_form, suspend_activity_form, suspend_terminal_form,
        suspend_conversion_form, suspend_client_form,
    ):
        await suspend(state)
    if selected["source_key"] != areas.FORM:
        await suspend_project_form(state)


async def display(event, state, form, *, notice=None, persist=True):
    data = await state.get_data()
    if not live(form, data, event.from_user.id):
        raise ValueError("Area form is no longer current.")
    selected = selected_for(data, event.from_user.id, form["menu_id"])
    updated = {**form, "revision": form["revision"] + 1}
    view = {**data[areas.VIEW], "nonce": uuid4().hex}
    updated["area_nonce"] = view["nonce"]
    language = updated["language"]
    lines = [
        tr("title", language), updated["organization"][:120],
        updated["project_name"][:180],
    ]
    rows = []
    phase = updated["phase"]
    if notice:
        lines.append(notice)
    if phase == "done":
        lines.append(tr("created", language))
        rows.append([areas.button(tr("create", language), "AC:new:" + view["nonce"])])
    elif phase == "cancelled":
        lines.append(tr("cancelled", language))
    elif updated.get("attempted_at") is not None:
        if time() - updated["attempted_at"] < SAFE_RETRY_SECONDS:
            lines.append(tr("uncertain", language))
            rows.append([areas.button(tr("retry", language), cb(updated, "submit"))])
        else:
            lines.append(tr("expired", language))
    elif phase == "type":
        lines.append(tr("type", language))
        rows.extend([[areas.button(tr(kind, language), cb(updated, kind))] for kind in TYPES])
        rows.append([areas.button(tr("cancel", language), cb(updated, "cancel"))])
    elif phase == "input":
        lines.extend([tr(updated["payload"]["area_type"], language), tr("name", language)])
        rows.append([areas.button(tr("cancel", language), cb(updated, "cancel"))])
    elif phase == "review":
        lines.extend([
            tr("review", language),
            tr(updated["payload"]["area_type"], language),
            updated["payload"]["name"],
        ])
        rows.extend([
            [areas.button(tr("create", language), cb(updated, "submit"))],
            [areas.button(tr("edit", language), cb(updated, "edit"))],
            [areas.button(tr("cancel", language), cb(updated, "cancel"))],
        ])
    else:
        raise ValueError("Invalid area form phase.")
    rows.extend([
        [areas.button(tr("list", language), f'PA:page:{view["nonce"]}:0')],
        [areas.button(t("construction_bot_home_btn", language), "CS_HOME")],
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n\n".join(lines)
    chunks = [text[index:index + 1800] for index in range(0, len(text), 1800)]
    if getattr(event, "message", None) is not None and len(chunks) == 1:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=keyboard,
        )
    else:
        message = getattr(event, "message", None) or event
        for index, chunk in enumerate(chunks):
            sent = await message.answer(
                chunk, parse_mode=None,
                reply_markup=keyboard if index == len(chunks) - 1 else None,
            )
    current = await state.get_data()
    if not live(form, current, event.from_user.id):
        return
    values = {
        FORM: {**updated, "menu_id": sent.message_id} if persist else {},
        areas.VIEW: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
        selected["source_key"]: {
            **current[selected["source_key"]], "menu_id": sent.message_id,
        },
    }
    if selected["source_key"] == areas.PROJECT:
        values[areas.CARD] = {**current[areas.CARD], "menu_id": sent.message_id}
        from handlers.construction.construction_client_catalog import sync_origin
        values.update(sync_origin(current, current[areas.CARD], sent.message_id))
    if selected["source_key"] == areas.CATALOG:
        values["construction_project_catalog_view"] = {
            **current["construction_project_catalog_view"], "menu_id": sent.message_id,
        }
    await state.update_data(**values)


async def fail(callback, state, form, error, language):
    logger.warning(
        "construction_bot_area_create_failed error_type=%s status=%s code=%s",
        type(error).__name__, getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    status = getattr(error, "status_code", None)
    key = (
        "construction_bot_unavailable" if status in (401, 403, 404)
        else "construction_bot_rate_limited" if status == 429
        else "construction_bot_error"
    )
    data = await state.get_data()
    if form and status not in (401, 403, 404) and live(form, data, callback.from_user.id):
        await display(callback, state, form, notice=t(key, language))
        return
    if form:
        await state.update_data(**{FORM: {**form, "menu_id": None}})
    await areas.show(callback, state, t(key, language), areas.back_rows(language))


@router.callback_query(F.data.startswith("AC:"))
async def area_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        form = None
        try:
            if await state.get_state() is not None:
                await callback.message.answer(tr("stale", language))
                return
            data = await state.get_data()
            parts = callback.data.split(":")
            if len(parts) == 3 and parts[1] == "new":
                view = data.get(areas.VIEW, {})
                selected = selected_for(data, callback.from_user.id, callback.message.message_id)
                if selected is None or view.get("nonce") != parts[2]:
                    await callback.message.answer(tr("stale", language))
                    return
                previous = data.get(FORM, {})
                pending = (
                    owns(previous, callback.from_user.id)
                    and previous.get("phase") != "done"
                    and previous.get("attempted_at") is not None
                )
                same = (
                    owns(previous, callback.from_user.id)
                    and all(previous.get(key) == selected[key] for key in (
                        "tenant_id", "project_id", "actor_user_id",
                    ))
                    and previous.get("phase") not in ("done", "cancelled")
                )
                if pending and not same:
                    await callback.message.answer(tr("pending_other", language))
                    return
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.project_areas(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(selected["tenant_id"]),
                        project_id=UUID(selected["project_id"]),
                        expected_user_id=UUID(selected["actor_user_id"]),
                        client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
                    )
                if (
                    result["actor_user_id"] != selected["actor_user_id"]
                    or result["project_id"] != selected["project_id"]
                    or selected_for(
                        await state.get_data(), callback.from_user.id,
                        callback.message.message_id,
                    ) != selected
                ):
                    raise ValueError("Area creation context changed.")
                if not pending and result.get("can_create_area") is not True:
                    raise ConstructionApiClientError(status_code=403, code="forbidden")
                language = result["language"] or language
                form = dict(previous) if same else {
                    "id": uuid4().hex, "revision": 0, "phase": "type",
                    "payload": {}, "attempted_at": None,
                    **{key: selected[key] for key in (
                        "telegram_user_id", "tenant_id", "project_id", "actor_user_id",
                    )},
                }
                form.update(
                    language=language, organization=result["organization"],
                    project_name=result["project_name"], area_nonce=view["nonce"],
                    menu_id=callback.message.message_id,
                )
                await suspend_others(state, selected)
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
                return

            form = data.get(FORM)
            if (
                len(parts) != 4 or not live(form, data, callback.from_user.id)
                or parts[2] != form["id"] or parts[3] != str(form["revision"])
                or callback.message.message_id != form["menu_id"]
            ):
                await callback.message.answer(tr("stale", language))
                return
            language = form["language"]
            action = parts[1]
            if form["phase"] == "done":
                await display(callback, state, form)
                return
            if action == "submit" and form["phase"] in ("review", "pending"):
                if (
                    form["attempted_at"] is not None
                    and time() - form["attempted_at"] >= SAFE_RETRY_SECONDS
                ):
                    await display(callback, state, form)
                    return
                form = {
                    **form, "phase": "pending",
                    "attempted_at": form["attempted_at"] if form["attempted_at"] is not None else time(),
                }
                await state.update_data(**{FORM: form})
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.create_project_area(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        project_id=UUID(form["project_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        payload=dict(form["payload"]),
                        idempotency_key="tg-area:" + form["id"],
                    )
                UUID(result["item"]["id"])
                form = {**form, "phase": "done", "area_id": result["item"]["id"], "payload": {}}
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
                return
            if form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            if action == "cancel":
                form = {**form, "phase": "cancelled"}
                await state.update_data(**{FORM: form})
                await display(callback, state, form, persist=False)
            elif action in TYPES and form["phase"] == "type":
                form = {
                    **form, "phase": "input",
                    "payload": {"area_type": action, "name": "", "sort_order": None},
                }
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
            elif action == "edit" and form["phase"] == "review":
                form = {**form, "phase": "type", "payload": {}}
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
            else:
                await callback.message.answer(tr("stale", language))
        except Exception as error:
            await fail(callback, state, form, error, language)


class AreaNameInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        return (
            isinstance(form, dict) and form.get("phase") == "input"
            and form.get("attempted_at") is None
            and live(form, data, message.from_user.id)
        )


@router.message(AreaNameInput())
async def area_name(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await AreaNameInput()(message, state):
            return
        data = await state.get_data()
        form = data[FORM]
        value = message.text.strip()
        if not value:
            await display(message, state, form, notice=tr("invalid", form["language"]))
            return
        form = {
            **form, "phase": "review",
            "payload": {**form["payload"], "name": value},
        }
        await state.update_data(**{FORM: form})
        await display(message, state, form)
