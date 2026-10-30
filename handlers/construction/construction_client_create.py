"""Standalone client form; all domain operations use the shared API."""
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, owns, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_client_create")
logger = logging.getLogger(__name__)
FORM = "construction_client_create_form"
CONTEXT = "construction_suite_context"
FIELDS = ("display_name", "phone", "email", "notes")
OTHER_FORMS = (
    "construction_lead_create_form", "construction_activity_plan_form",
    "construction_activity_terminal_form", "construction_lead_convert_form",
    "construction_project_create_form",
)

def tr(key, language):
    return t("construction_client_form_" + key, language)

def common(key, language):
    return t("construction_lead_form_" + key, language)

def type_label(value, language):
    return t("construction_lead_convert_" + value, language)

def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)

def cb(form, action):
    return f'CF:{action}:{form["id"]}:{form["phase"]}:{form["step"]}'

async def suspend_client_form(state):
    form = (await state.get_data()).get(FORM)
    if isinstance(form, dict):
        keep = form.get("attempted_at") is not None or form.get("phase") == "done"
        await state.update_data(**{
            FORM: {**form, "menu_id": None} if keep else {},
        })

async def show(event, state, form, text, rows):
    message = getattr(event, "message", None)
    chunks = [text[index:index + 1800] for index in range(0, len(text), 1800)] or [text]
    sent = None
    for index, chunk in enumerate(chunks):
        markup = InlineKeyboardMarkup(inline_keyboard=rows) if index == len(chunks) - 1 else None
        if message is not None and len(chunks) == 1:
            sent = await edit_or_replace_menu_message(
                callback=event, text=chunk, reply_markup=markup,
            )
        else:
            sent = await (message or event).answer(chunk, reply_markup=markup)
    if form is not None:
        form["menu_id"] = sent.message_id
        await state.update_data(**{FORM: form})
    await state.update_data(last_menu_message_id=sent.message_id)

def back_rows(language):
    return [[button(t("construction_bot_home_btn", language), "CS_HOME")]]

async def display(event, state, form, notice=None):
    language, phase = form["language"], form["phase"]
    rows = []
    if phase == "done":
        text = tr("created", language)
        rows = [
            [button(
                t("construction_client_card_open", language),
                "CC:open:" + UUID(form["client_id"]).hex,
            )],
            [button(tr("button", language), "CF:new")],
        ]
    elif form["attempted_at"] is not None:
        text = common("uncertain", language)
        if time() - form["attempted_at"] < 86340:
            rows.append([button(common("retry", language), cb(form, "submit"))])
        else:
            text = common("expired", language)
    elif phase == "type":
        text = tr("type", language)
        rows = [
            [button(type_label(value, language), cb(form, value))]
            for value in ("person", "company")
        ]
        rows.append([button(common("cancel", language), cb(form, "cancel"))])
    elif phase == "input":
        text = tr(FIELDS[form["step"]], language)
        if form["step"] > 0:
            rows.append([button(common("skip", language), cb(form, "skip"))])
        rows.append([button(common("cancel", language), cb(form, "cancel"))])
    elif phase == "review":
        lines = [tr("review", language), type_label(form["payload"]["client_type"], language)]
        for field in FIELDS:
            lines.append(tr(field, language) + "\n" + (form["payload"][field] or "—"))
        text = "\n\n".join(lines)
        rows = [
            [button(tr("button", language), cb(form, "submit"))],
            [button(common("edit", language), cb(form, "edit"))],
            [button(common("cancel", language), cb(form, "cancel"))],
        ]
    else:
        raise ValueError("Unknown client form phase.")
    if notice:
        text = notice + "\n\n" + text
    rows.extend(back_rows(language))
    await show(
        event, state, form,
        tr("title", language) + "\n" + form["organization"] + "\n\n" + text,
        rows,
    )

async def failure(event, state, form, language, error):
    logger.warning(
        "construction_bot_client_create_failed error_type=%s status=%s code=%s",
        type(error).__name__, getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    if isinstance(error, ConstructionApiClientError) and error.status_code in (401, 403, 404):
        await show(event, state, None, t("construction_bot_unavailable", language), back_rows(language))
        return
    key = "construction_bot_rate_limited" if getattr(error, "status_code", None) == 429 else "construction_bot_error"
    if form is not None:
        await display(event, state, form, t(key, language))
    else:
        await show(event, state, None, t(key, language), back_rows(language))

@router.callback_query(F.data.startswith("CF:"))
async def client_create_callback(callback, state, bot=None, event_update=None):
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
                raise ValueError("Construction context unavailable.")
            existing = data.get(FORM)
            if callback.data == "CF:new":
                tenant_id = UUID(context["tenant_id"])
                if owns(existing, callback.from_user.id) and existing.get("phase") != "done":
                    if existing.get("attempted_at") is not None or existing.get("tenant_id") == str(tenant_id):
                        form = dict(existing)
                        tenant_id = UUID(form["tenant_id"])
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    prepared = await service.client_form(
                        telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
                    )
                if form is None:
                    form = {
                        "id": uuid4().hex, "telegram_user_id": callback.from_user.id,
                        "tenant_id": str(tenant_id),
                        "actor_user_id": prepared["actor_user_id"],
                        "organization": prepared["organization"],
                        "language": prepared["language"] or language,
                        "phase": "type", "step": 0, "payload": {},
                        "attempted_at": None, "menu_id": None,
                    }
                elif form["actor_user_id"] != prepared["actor_user_id"]:
                    raise ValueError("Canonical identity changed.")

                from handlers.construction.construction_lead_create import suspend_lead_form
                from handlers.construction.construction_activity_plan import suspend_activity_form
                from handlers.construction.construction_activity_terminal import suspend_terminal_form
                from handlers.construction.construction_lead_convert import suspend_conversion_form
                from handlers.construction.construction_project_create import suspend_project_form
                for suspend in (
                    suspend_lead_form, suspend_activity_form, suspend_terminal_form,
                    suspend_conversion_form, suspend_project_form,
                ):
                    await suspend(state)
                await state.update_data(**{
                    FORM: form,
                    CONTEXT: {"telegram_user_id": callback.from_user.id, "tenant_id": str(tenant_id)},
                    "construction_client_card_view": {},
                    "construction_project_card_view": {},
                })
                await display(callback, state, form)
                return

            if not owns(existing, callback.from_user.id):
                raise ValueError("Client form unavailable.")
            form = dict(existing)
            language = form["language"]
            parts = callback.data.split(":")
            if (
                len(parts) != 5 or parts[2] != form["id"]
                or parts[3] != form["phase"] or parts[4] != str(form["step"])
                or form["tenant_id"] != context.get("tenant_id")
                or form.get("menu_id") != data.get("last_menu_message_id")
            ):
                await callback.message.answer(common("stale", language))
                return
            action = parts[1]
            if form["phase"] == "done":
                await display(callback, state, form)
                return
            if action == "submit" and form["phase"] in ("review", "pending") and form["step"] == len(FIELDS):
                if form["attempted_at"] is not None and time() - form["attempted_at"] >= 86340:
                    await display(callback, state, form)
                    return
                if form["attempted_at"] is None:
                    form["attempted_at"] = time()
                form["phase"] = "pending"
                await state.update_data(**{FORM: form})
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    result = await service.create_client(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        payload=dict(form["payload"]),
                        idempotency_key="tg-client:" + form["id"],
                    )
                form.update(
                    phase="done", client_id=str(UUID(result["item"]["id"])), payload={},
                )
                await state.update_data(**{FORM: form})
            elif form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            elif action == "cancel":
                await state.update_data(**{FORM: {}})
                await show(callback, state, None, common("cancelled", language), back_rows(language))
                return
            elif action in ("person", "company") and form["phase"] == "type":
                form.update(phase="input", step=0, payload={"client_type": action})
            elif action == "skip" and form["phase"] == "input" and 0 < form["step"] < len(FIELDS):
                form["payload"] = {**form["payload"], FIELDS[form["step"]]: None}
                form["step"] += 1
                if form["step"] == len(FIELDS):
                    form["phase"] = "review"
            elif action == "edit" and form["phase"] == "review":
                form.update(phase="type", step=0, payload={})
            else:
                await callback.message.answer(common("stale", language))
                return
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            await failure(callback, state, form, language, error)

class ClientFormInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or (message.text or "").startswith("/")
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
            isinstance(data.get(key), dict) and data[key].get("menu_id") is not None
            and data[key]["menu_id"] == data.get("last_menu_message_id")
            for key in OTHER_FORMS
        )

@router.message(ClientFormInput())
async def client_create_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ClientFormInput()(message, state):
            return
        form = dict((await state.get_data())[FORM])
        value = message.text
        if not isinstance(value, str) or not value.strip():
            await display(message, state, form, common("invalid", form["language"]))
            return
        form["payload"] = {**form["payload"], FIELDS[form["step"]]: value.strip()}
        form["step"] += 1
        if form["step"] == len(FIELDS):
            form["phase"] = "review"
        await state.update_data(**{FORM: form})
        await display(message, state, form)
