"""Private company profile editor using the existing Platform API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_company_profile import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_company_edit")
logger = logging.getLogger(__name__)
FORM = "construction_company_edit_form"
VIEW = "construction_company_profile_view"
CONTEXT = "construction_suite_context"
KEYS = (FORM, CONTEXT, "last_menu_message_id")
FIELDS = (
    "legal_name", "commercial_name",
    "registered_address_raw", "operational_address_raw",
    "phone", "email", "website",
    "signatory_name", "signatory_position",
)
LIMITS = {
    "legal_name": 255, "commercial_name": 255,
    "registered_address_raw": 4000, "operational_address_raw": 4000,
    "phone": 50, "email": 320, "website": 2048,
    "signatory_name": 255, "signatory_position": 255,
}


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(form, action, field=None):
    value = "CE:" + action + ":" + form["nonce"]
    return value if field is None else value + ":" + str(FIELDS.index(field))


def profile_rows(language):
    return [
        [button(tr("open_profile", language), "CP:open")],
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
    ]


def same(data, snapshot):
    return all(data.get(key) == snapshot.get(key) for key in KEYS)


def bound(data, form, sender):
    context = data.get(CONTEXT)
    return (
        isinstance(form, dict)
        and form.get("telegram_user_id") == sender
        and isinstance(context, dict)
        and context.get("telegram_user_id") == sender
        and context.get("tenant_id") == form.get("tenant_id")
        and form.get("menu_id") is not None
        and form.get("menu_id") == data.get("last_menu_message_id")
    )


async def suspend_company_form(state):
    form = (await state.get_data()).get(FORM)
    if isinstance(form, dict) and form:
        # Preserve only attempt metadata when leaving an uncertain operation.
        if form.get("attempted"):
            form = {
                key: value for key, value in form.items()
                if key != "value"
            }
            form["menu_id"] = None
        else:
            form = {}
        await state.update_data(**{FORM: form})


async def render(event, state, form, text, rows):
    data = await state.get_data()
    if data.get(FORM) != form or not bound(data, form, event.from_user.id):
        return
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    chunks = [text[i:i + 3000] for i in range(0, len(text), 3000)] or [text]
    sent = None
    for index, chunk in enumerate(chunks):
        if not same(await state.get_data(), snapshot):
            return
        markup = (
            InlineKeyboardMarkup(inline_keyboard=rows)
            if index == len(chunks) - 1 else None
        )
        message = getattr(event, "message", None)
        if message is not None and len(chunks) == 1:
            sent = await edit_or_replace_menu_message(
                callback=event, text=chunk, reply_markup=markup,
            )
        else:
            sent = await (message or event).answer(chunk, reply_markup=markup)
    if not same(await state.get_data(), snapshot):
        return
    await state.update_data(**{
        FORM: {**form, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })


async def display(event, state, form, notice=None):
    language, stage = form["language"], form["stage"]
    rows = []
    if stage == "choose":
        text = tr("edit", language) + "\n\n" + tr("choose_field", language)
        rows = [
            [button(tr(field, language), cb(form, "field", field))]
            for field in FIELDS
        ]
    elif stage == "input":
        text = (
            tr("create" if form["operation"] == "create" else "edit", language)
            + "\n\n" + tr(form["field"], language)
            + "\n\n" + tr("enter_value", language)
        )
        if form["field"] != "legal_name":
            rows.append([button(tr("clear_field", language), cb(form, "clear"))])
    elif stage == "review":
        value = form["value"]
        text = (
            tr("review", language) + "\n\n"
            + tr(form["field"], language) + ":\n"
            + (value if value is not None else tr("cleared_value", language))
        )
        rows.append([button(tr("save", language), cb(form, "save"))])
    elif stage == "done":
        text = tr("saved", language)
    elif stage == "uncertain":
        text = tr("uncertain_write", language)
    elif stage == "blocked":
        text = tr(form.get("notice", "invalid_write"), language)
    else:
        raise ValueError("Unknown company editor stage.")
    if stage in ("choose", "input", "review"):
        rows.append([button(tr("cancel", language), cb(form, "cancel"))])
    else:
        rows.extend(profile_rows(language))
    if notice:
        text = notice + "\n\n" + text
    await render(event, state, form, text, rows)


def valid_value(field, value):
    return (
        field in LIMITS and isinstance(value, str)
        and bool(value.strip()) and len(value.strip()) <= LIMITS[field]
        and not any(ord(char) < 32 and char not in "\n\r\t" for char in value)
    )


async def start(callback, state, bot, event_update):
    data = await state.get_data()
    source = data.get(VIEW)
    context = data.get(CONTEXT)
    parts = callback.data.split(":")
    if (
        len(parts) != 3 or parts[1] != "open"
        or not isinstance(source, dict)
        or not isinstance(context, dict)
        or source.get("nonce") != parts[2]
        or source.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or source.get("tenant_id") != context.get("tenant_id")
        or source.get("menu_id") != callback.message.message_id
        or data.get("last_menu_message_id") != callback.message.message_id
    ):
        raise ValueError("Stale company profile source.")
    tenant, actor = UUID(source["tenant_id"]), UUID(source["actor_user_id"])
    original = deepcopy(data)
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.company_profile(
            telegram_user_id=callback.from_user.id, tenant_id=tenant,
            expected_user_id=actor,
        )
    data = await state.get_data()
    if (
        any(data.get(key) != original.get(key) for key in
            (VIEW, FORM, CONTEXT, "last_menu_message_id"))
        or result.get("actor_user_id") != str(actor)
        or result.get("can_manage_profile") is not True
    ):
        raise ValueError("Company editor context or permission changed.")
    item = result["item"]
    if item is not None and (
        item["tenant_id"] != str(tenant)
        or type(item["row_version"]) is not int or item["row_version"] <= 0
    ):
        raise ValueError("Company profile base differs.")
    from handlers.construction.construction_team import suspend_forms
    from handlers.construction.construction_area_create import suspend_area_form
    await suspend_forms(state)
    await suspend_area_form(state)
    form = {
        "nonce": uuid4().hex, "telegram_user_id": callback.from_user.id,
        "tenant_id": str(tenant), "actor_user_id": str(actor),
        "language": result["language"] or callback.from_user.language_code or "ru",
        "operation": "create" if item is None else "update",
        "profile_id": item["id"] if item else None,
        "expected_row_version": item["row_version"] if item else None,
        "stage": "input" if item is None else "choose",
        "field": "legal_name" if item is None else None,
        "attempted": False, "menu_id": callback.message.message_id,
    }
    await state.update_data(**{FORM: form, VIEW: {}})
    await display(callback, state, form)


async def submit(callback, state, form, bot, event_update):
    value = form.get("value")
    if (
        form["stage"] != "review" or form["attempted"]
        or form["field"] not in FIELDS
        or (value is None and form["field"] == "legal_name")
        or (value is not None and not valid_value(form["field"], value))
    ):
        raise ValueError("Company edit is not ready.")
    payload = {form["field"]: value}
    if form["operation"] == "update":
        payload["expected_row_version"] = form["expected_row_version"]
    elif form["operation"] != "create" or form["field"] != "legal_name":
        raise ValueError("Company creation base differs.")

    # Freeze the attempted operation before network I/O; never rebase or retry it.
    form = {key: value for key, value in form.items() if key != "value"}
    form.update(stage="sending", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{FORM: form})
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.save_company_profile(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                operation=form["operation"], payload=payload,
                expected_profile_id=(
                    UUID(form["profile_id"]) if form["profile_id"] else None
                ),
            )
        item = result["item"]
        if (
            result["actor_user_id"] != form["actor_user_id"]
            or item["tenant_id"] != form["tenant_id"]
            or (form["profile_id"] is not None and item["id"] != form["profile_id"])
        ):
            raise ValueError("Company write response context differs.")
        if not same(await state.get_data(), snapshot):
            return
        form.update(
            stage="done", profile_id=item["id"],
            expected_row_version=item["row_version"],
        )
    except Exception as error:
        logger.warning(
            "construction_bot_company_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), snapshot):
            return
        status = getattr(error, "status_code", None)
        if status in (400, 401, 403, 404, 409, 422, 429):
            form.update(
                stage="blocked",
                notice="conflict_write" if status == 409 else "invalid_write",
            )
        else:
            form["stage"] = "uncertain"
    await state.update_data(**{FORM: form})
    await display(callback, state, form)


@router.callback_query(F.data.startswith("CE:"))
async def company_edit_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith("CE:open:"):
                await start(callback, state, bot, event_update)
                return
            data = await state.get_data()
            form = deepcopy(data.get(FORM))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or not bound(data, form, callback.from_user.id)
                or form.get("nonce") != parts[2]
                or form.get("menu_id") != callback.message.message_id
            ):
                raise ValueError("Stale company editor.")
            language = form["language"]
            action = parts[1]
            if action == "cancel" and len(parts) == 3 and not form["attempted"]:
                form = {key: value for key, value in form.items() if key != "value"}
                form.update(stage="blocked", notice="cancel", nonce=uuid4().hex)
            elif (
                action == "field" and len(parts) == 4
                and form["stage"] == "choose"
                and parts[3] in {str(index) for index in range(len(FIELDS))}
            ):
                form.update(
                    stage="input", field=FIELDS[int(parts[3])], nonce=uuid4().hex,
                )
            elif (
                action == "clear" and len(parts) == 3
                and form["stage"] == "input"
                and form["field"] in FIELDS and form["field"] != "legal_name"
            ):
                form.update(stage="review", value=None, nonce=uuid4().hex)
            elif action == "save" and len(parts) == 3:
                await submit(callback, state, form, bot, event_update)
                return
            else:
                raise ValueError("Company editor action is unavailable.")
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            logger.warning(
                "construction_bot_company_edit_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("construction_lead_form_stale", language))


class CompanyFieldInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        return (
            bound(data, form, message.from_user.id)
            and form.get("stage") == "input" and not form.get("attempted")
            and form.get("field") in FIELDS
        )


@router.message(CompanyFieldInput())
async def company_edit_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await CompanyFieldInput()(message, state):
            return
        form = deepcopy((await state.get_data())[FORM])
        if not valid_value(form["field"], message.text):
            await display(message, state, form, tr("invalid_input", form["language"]))
            return
        form.update(
            stage="review", value=message.text.strip(), nonce=uuid4().hex,
        )
        await state.update_data(**{FORM: form})
        await display(message, state, form)
