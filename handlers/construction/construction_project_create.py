"""Standalone Stage 1 project creation through the existing API."""
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, owns, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_card import project_text, tr as project_tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_project_create")
logger = logging.getLogger(__name__)
FORM = "construction_project_create_form"
CONTEXT = "construction_suite_context"
FIELDS = ("name", "address_raw", "comment")
OTHER_FORMS = (
    "construction_lead_create_form", "construction_activity_plan_form",
    "construction_activity_terminal_form", "construction_lead_convert_form",
    "construction_client_create_form",
)

def tr(key, language):
    return t("construction_project_form_" + key, language)

def common(key, language):
    return t("construction_lead_form_" + key, language)

def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)

def cb(form, action):
    return f'PF:{action}:{form["id"]}:{form["step"]}'

async def suspend_project_form(state):
    form = (await state.get_data()).get(FORM)
    if isinstance(form, dict):
        keep = form.get("attempted_at") is not None or form.get("phase") == "done"
        await state.update_data(**{
            FORM: {**form, "menu_id": None} if keep else {},
        })

async def show(event, state, form, text, rows, *, expected_snapshot=None):
    if expected_snapshot is not None:
        current = await state.get_data()
        if any(current.get(key) != value for key, value in expected_snapshot.items()):
            return
    message = getattr(event, "message", None)
    chunks = [text[index:index + 1800] for index in range(0, len(text), 1800)] or [text]
    sent = None
    for index, chunk in enumerate(chunks):
        markup = (
            InlineKeyboardMarkup(inline_keyboard=rows)
            if index == len(chunks) - 1 else None
        )
        if message is not None and len(chunks) == 1:
            sent = await edit_or_replace_menu_message(
                callback=event, text=chunk, reply_markup=markup,
            )
        else:
            sent = await (message or event).answer(chunk, reply_markup=markup)
    if expected_snapshot is not None:
        current = await state.get_data()
        if any(current.get(key) != value for key, value in expected_snapshot.items()):
            return
    if form is not None:
        form["menu_id"] = sent.message_id
        await state.update_data(**{FORM: form})
    await state.update_data(last_menu_message_id=sent.message_id)

def back_rows(language):
    return [[button(t("construction_bot_home_btn", language), "CS_HOME")]]

async def display(event, state, form, notice=None):
    if form.get("phase") == "responsibles":
        from handlers.construction.construction_project_responsible import render
        return await render(event, state, form, notice=notice)
    from handlers.construction.construction_project_from_client import caption, back_rows as client_back_rows
    if form.get("phase") == "address_suggestions":
        from handlers.construction.construction_project_address_suggestions import render
        return await render(event, state, form, notice=notice)
    language = form["language"]
    rows = []
    if form["phase"] == "done":
        text = tr("created", language)
        rows.append([button(tr("open", language), cb(form, "open"))])
    elif form["attempted_at"] is not None:
        text = common("uncertain", language)
        if 0 <= time() - form["attempted_at"] < 86340:
            rows.append([button(common("retry", language), cb(form, "submit"))])
        else:
            text = common("expired", language)
    elif form["step"] < len(FIELDS):
        text = tr(FIELDS[form["step"]], language)
        if form["step"] == 2:
            rows.append([button(common("skip", language), cb(form, "skip"))])
        rows.append([button(common("cancel", language), cb(form, "cancel"))])
    elif form["step"] == len(FIELDS):
        text = common("responsible", language)
        rows = [
            [button(common("mine", language), cb(form, "mine"))],
            [button(common("none", language), cb(form, "none"))],
            [button(common("cancel", language), cb(form, "cancel"))],
        ]
        if form.get("can_choose_responsible") is True:
            from handlers.construction.construction_lead_responsible import tr as responsible_tr
            rows.insert(1, [
                button(responsible_tr("choose", language), cb(form, "r_open")),
            ])
    else:
        text = "\n\n".join([
            tr("review", language),
            tr("name", language) + "\n" + form["payload"]["name"],
            tr("address_raw", language) + "\n" + form["payload"]["address_raw"],
            tr("comment", language) + "\n" + (form["payload"]["comment"] or "—"),
            common("responsible", language) + "\n"
                + (
                    form.get("responsible_name")
                    if form.get("responsible_user_id")
                    else common("mine" if form["assign_self"] else "none", language)
                ),
            caption(form, language),
        ])
        rows = [
            [button(tr("button", language), cb(form, "submit"))],
            [button(common("edit", language), cb(form, "edit"))],
            [button(common("cancel", language), cb(form, "cancel"))],
        ]
    if notice:
        text = notice + "\n\n" + text
    rows.extend(client_back_rows(form, language))
    rows.extend(back_rows(language))
    await show(
        event, state, form,
        tr("title", language) + "\n" + form["organization"] + "\n\n" + text,
        rows,
    )

async def failure(event, state, form, language, error):
    logger.warning(
        "construction_bot_project_create_failed error_type=%s status=%s code=%s",
        type(error).__name__,
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    if isinstance(error, ConstructionApiClientError) and error.status_code in (401, 403, 404):
        await show(
            event, state, None,
            t("construction_bot_unavailable", language), back_rows(language),
        )
        return
    key = (
        "construction_bot_rate_limited"
        if getattr(error, "status_code", None) == 429
        else "construction_bot_error"
    )
    if form is not None:
        await display(event, state, form, t(key, language))
    else:
        await show(event, state, None, t(key, language), back_rows(language))

@router.callback_query(F.data.startswith("PF:"))
async def project_create_callback(callback, state, bot=None, event_update=None):
    language = (callback.from_user.language_code or "ru").lower()
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        form = None
        try:
            data = await state.get_data()
            context = data.get(CONTEXT, {})
            if (
                not isinstance(context, dict)
                or context.get("telegram_user_id") != callback.from_user.id
                or await state.get_state() is not None
            ):
                raise ValueError("Construction form context unavailable.")
            existing = data.get(FORM)
            if callback.data.startswith("PF:client:"):
                from handlers.construction.construction_project_from_client import start
                await start(callback, state, data, bot=bot, event_update=event_update)
                return
            if callback.data == "PF:new":
                tenant_id = UUID(context["tenant_id"])
                if owns(existing, callback.from_user.id) and existing.get("phase") != "done":
                    if (
                        existing.get("attempted_at") is not None
                        or (
                            existing.get("tenant_id") == str(tenant_id)
                            and existing.get("client_id") is None
                        )
                    ):
                        form = dict(existing)
                        tenant_id = UUID(form["tenant_id"])
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    prepared = await service.project_form(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=tenant_id,
                    )
                if form is None:
                    form = {
                        "id": uuid4().hex,
                        "telegram_user_id": callback.from_user.id,
                        "tenant_id": str(tenant_id),
                        "actor_user_id": prepared["actor_user_id"],
                        "organization": prepared["organization"],
                        "language": prepared["language"] or language,
                        "phase": "input", "step": 0,
                        "payload": {}, "assign_self": False,
                        "attempted_at": None, "menu_id": None,
                    }
                elif form["actor_user_id"] != prepared["actor_user_id"]:
                    raise ValueError("Canonical identity changed.")

                if form.get("attempted_at") is None:
                    form["can_choose_responsible"] = (
                        prepared.get("can_choose_responsible") is True
                    )
                from handlers.construction.construction_lead_create import suspend_lead_form
                from handlers.construction.construction_activity_plan import suspend_activity_form
                from handlers.construction.construction_activity_terminal import suspend_terminal_form
                from handlers.construction.construction_lead_convert import suspend_conversion_form
                from handlers.construction.construction_client_create import suspend_client_form
                for suspend in (
                    suspend_lead_form, suspend_activity_form,
                    suspend_terminal_form, suspend_conversion_form,
                    suspend_client_form,
                ):
                    await suspend(state)
                await state.update_data(**{
                    CONTEXT: {
                        "telegram_user_id": callback.from_user.id,
                        "tenant_id": str(tenant_id),
                    },
                    FORM: form,
                    "construction_client_card_view": {},
                    "construction_project_card_view": {},
                })
                await display(callback, state, form)
                return

            if not owns(existing, callback.from_user.id):
                raise ValueError("Project form unavailable.")
            form = dict(existing)
            language = form["language"]
            parts = callback.data.split(":")
            if (
                len(parts) != 4
                or parts[2] != form["id"]
                or parts[3] != str(form["step"])
                or form["tenant_id"] != context.get("tenant_id")
                or form.get("menu_id") != data.get("last_menu_message_id")
            ):
                await callback.message.answer(common("stale", language))
                return
            action = parts[1]
            if form.get("phase") == "address_suggestions":
                from handlers.construction.construction_project_address_suggestions import dispatch
                if await dispatch(
                    callback, state, form, action, bot=bot, event_update=event_update,
                ):
                    return
            if form["phase"] == "done":
                if action == "open":
                    async with construction_bot_api_entry(
                        bot=bot, event_update=event_update,
                    ) as service:
                        result = await service.read_created_project(
                            telegram_user_id=callback.from_user.id,
                            tenant_id=UUID(form["tenant_id"]),
                            project_id=UUID(form["project_id"]),
                            expected_user_id=UUID(form["actor_user_id"]),
                        )
                    rows = [[button(
                        project_tr("refresh", language), cb(form, "open"),
                    )]]
                    if result.get("can_create_project") is True:
                        rows.append([button(tr("button", language), "PF:new")])
                    from ui.construction_project_areas import tr as area_tr
                    rows.insert(1, [button(
                        area_tr("title", language), "PA:open:" + form["id"],
                    )])
                    from ui.construction_project_activities import tr as planned_tr
                    rows.append([button(planned_tr("title", language), "PAV:open:" + form["id"])])
                    from ui.construction_client_card import tr as activity_tr
                    if result.get("can_plan_activity") is True:
                        rows.append([button(activity_tr("plan_activity", language), "AP:project:" + form["id"])])
                    from ui.construction_project_edit import tr as edit_tr
                    if result.get("can_edit_project") is True:
                        rows.append([button(edit_tr("edit", language), "PDE:open:" + form["id"])])
                    from ui.construction_project_team import tr as project_team_tr
                    if result.get("can_open_team") is True:
                        rows.insert(0, [button(
                            project_team_tr("title", language), "PT:open:" + form["id"],
                        )])
                    from handlers.construction.construction_project_from_client import back_rows as client_back_rows
                    rows.extend(client_back_rows(form, language))
                    rows.extend(back_rows(language))
                    await show(
                        callback, state, form,
                        project_text(result, result["language"] or language), rows,
                    )
                else:
                    await display(callback, state, form)
                return

            if action == "submit" and form["step"] == len(FIELDS) + 1:
                if (
                    form["attempted_at"] is not None
                    and not 0 <= time() - form["attempted_at"] < 86340
                ):
                    await display(callback, state, form)
                    return
                if form["attempted_at"] is None:
                    form["attempted_at"] = time()
                form["phase"] = "pending"
                await state.update_data(**{FORM: form})
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.create_project(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        payload=dict(form["payload"]),
                        assign_self=form["assign_self"],
                        idempotency_key="tg-project:" + form["id"],
                        **(
                            {"responsible_user_id": UUID(form["responsible_user_id"])}
                            if form.get("responsible_user_id") else {}
                        ),
                        **(
                            {"client_id": UUID(form["client_id"])}
                            if form.get("client_id") else {}
                        ),
                    )
                form.update(
                    phase="done", project_id=str(UUID(result["item"]["id"])),
                    payload={}, responsible_user_id=None,
                    responsible_name=None, responsible_ids=[],
                )
                await state.update_data(**{FORM: form})
            elif form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            elif action == "cancel":
                await state.update_data(**{FORM: {}})
                await show(
                    callback, state, None,
                    common("cancelled", language), back_rows(language),
                )
                return
            elif action.startswith("r_") or form.get("phase") == "responsibles":
                from handlers.construction.construction_project_responsible import dispatch
                await dispatch(
                    callback, state, form, action,
                    bot=bot, event_update=event_update,
                )
                return
            elif action == "skip" and form["step"] == 2:
                form["payload"] = {**form["payload"], "comment": None}
                form["step"] = 3
            elif action in ("mine", "none") and form["step"] == 3:
                form.update(
                    assign_self=action == "mine", step=4, phase="review",
                    responsible_user_id=None, responsible_name=None,
                    responsible_ids=[],
                )
            elif action == "edit" and form["step"] == 4:
                form.update(
                    step=0, phase="input", payload={}, assign_self=False,
                    responsible_user_id=None, responsible_name=None,
                    responsible_ids=[],
                )
            else:
                await callback.message.answer(common("stale", language))
                return
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            await failure(callback, state, form, language, error)

class ProjectFormInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message)
            or (message.text or "").startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form, context = data.get(FORM), data.get(CONTEXT, {})
        if not (
            owns(form, message.from_user.id)
            and isinstance(context, dict)
            and context.get("telegram_user_id") == message.from_user.id
            and context.get("tenant_id") == form.get("tenant_id")
            and form.get("phase") == "input"
            and type(form.get("step")) is int
            and 0 <= form["step"] < len(FIELDS)
            and form.get("menu_id") is not None
            and form["menu_id"] == data.get("last_menu_message_id")
        ):
            return False
        return not any(
            isinstance(data.get(key), dict)
            and data[key].get("menu_id") is not None
            and data[key]["menu_id"] == data.get("last_menu_message_id")
            for key in OTHER_FORMS
        )

@router.message(ProjectFormInput())
async def project_create_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ProjectFormInput()(message, state):
            return
        form = dict((await state.get_data())[FORM])
        value = message.text
        if not isinstance(value, str) or not value.strip():
            await display(
                message, state, form,
                common("invalid", form["language"]),
            )
            return
        form["payload"] = {
            **form["payload"], FIELDS[form["step"]]: value.strip(),
        }
        form["step"] += 1
        await state.update_data(**{FORM: form})
        if form["step"] == 2:
            from handlers.construction.construction_project_address_suggestions import lookup
            await lookup(message, state, form, bot=bot, event_update=event_update)
            return
        await display(message, state, form)

# standalone_project_address_suggestions_v1
