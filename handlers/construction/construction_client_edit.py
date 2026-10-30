"""Single-field client editing through the versioned authenticated API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_client_edit import tr
from ui.texts import t

router = Router(name="construction_client_edit")
logger = logging.getLogger(__name__)
FORM = "construction_client_edit_form"
CARD = "construction_client_card_view"
CONTEXT = "construction_suite_context"
LIST = "construction_client_catalog_view"
KEYS = (FORM, CARD, CONTEXT, LIST, "last_menu_message_id")
FIELDS = ("display_name", "client_type", "phone", "email", "notes")
NULLABLE = {"phone", "email", "notes"}

def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)

def cb(form, action, selector=None):
    value = "CDE:" + action + ":" + form["nonce"]
    return value if selector is None else value + ":" + str(selector)

def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}

def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)

def bound(data, form, sender):
    context, card = data.get(CONTEXT), data.get(CARD)
    return (
        isinstance(form, dict) and isinstance(context, dict)
        and isinstance(card, dict)
        and form.get("telegram_user_id") == sender
        and context.get("telegram_user_id") == sender
        and card.get("telegram_user_id") == sender
        and form.get("tenant_id") == context.get("tenant_id") == card.get("tenant_id")
        and form.get("client_id") == card.get("client_id")
        and form.get("actor_user_id") == card.get("actor_user_id")
        and form.get("menu_id") is not None
        and form.get("menu_id") == card.get("menu_id") == data.get("last_menu_message_id")
    )

def valid_value(field, value):
    if field not in FIELDS:
        return False
    if value is None:
        return field in NULLABLE
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    return field != "client_type" or value in ("person", "company")

async def client_edit_navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith("CDE:")
    ):
        saved = (await state.get_data()).get(FORM)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{FORM: {}})
    return await handler(event, data)

async def render(event, state, form, text, rows):
    data = await state.get_data()
    if data.get(FORM) != form or not bound(data, form, event.from_user.id):
        return
    saved = snapshot(data)
    parent = getattr(event, "message", None) or event
    chunks = [text[index:index + 3000] for index in range(0, len(text), 3000)] or [text]
    sent = None
    for index, chunk in enumerate(chunks):
        if not same(await state.get_data(), saved):
            return
        markup = (
            InlineKeyboardMarkup(inline_keyboard=rows)
            if index == len(chunks) - 1 else None
        )
        sent = await parent.answer(chunk, reply_markup=markup, parse_mode=None)
    if not same(await state.get_data(), saved):
        return
    from handlers.construction.construction_client_catalog import sync_origin
    card = {**data[CARD], "menu_id": sent.message_id}
    values = sync_origin(data, data[CARD], sent.message_id)
    values.update({
        FORM: {**form, "menu_id": sent.message_id},
        CARD: card, "last_menu_message_id": sent.message_id,
    })
    await state.update_data(**values)

async def display(event, state, form, notice=None):
    language, stage = form["language"], form["stage"]
    data = await state.get_data()
    rows = []
    if stage == "choose":
        text = tr("edit", language) + "\n\n" + tr("choose", language)
        rows = [
            [button(tr(field, language), cb(form, "field", index))]
            for index, field in enumerate(FIELDS)
        ]
    elif stage == "input":
        text = tr(form["field"], language) + "\n\n" + tr("enter", language)
        if form["field"] == "client_type":
            rows.extend([
                [button(tr(kind, language), cb(form, "type", kind))]
                for kind in ("person", "company")
            ])
        elif form["field"] in NULLABLE:
            rows.append([button(tr("clear", language), cb(form, "clear"))])
    elif stage == "archive_review":
        text = tr("archive_review", language)
        rows.append([button(tr("archive_confirm", language), cb(form, "archive_confirm"))])
    elif stage == "review":
        value = form["value"]
        if form["field"] == "client_type":
            value = tr(value, language)
        text = (
            tr("review", language) + "\n\n" + tr(form["field"], language)
            + ":\n" + (value if value is not None else "—")
        )
        rows.append([button(tr("save", language), cb(form, "save"))])
    elif stage in ("saved", "conflict", "uncertain", "blocked", "cancelled", "archived"):
        text = tr(stage, language)
    else:
        raise ValueError("Unknown client edit stage.")
    if stage in ("choose", "input", "review", "archive_review"):
        rows.append([button(tr("cancel", language), cb(form, "cancel"))])
    if stage != "archived":
        rows.append([button(
            tr("summary", language), "CC:summary:" + data[CARD]["nonce"],
        )])
    from ui.construction_client_catalog import tr as catalog_tr
    rows.append([button(catalog_tr("title", language), "CL:open")])
    if notice:
        text = notice + "\n\n" + text
    await render(event, state, form, text, rows)

async def start(callback, state, bot, event_update):
    data = await state.get_data()
    saved = snapshot(data)
    card, context = data.get(CARD), data.get(CONTEXT)
    parts = callback.data.split(":")
    if (
        len(parts) != 3 or parts[1] not in ("open", "archive")
        or not isinstance(card, dict) or not isinstance(context, dict)
        or card.get("nonce") != parts[2]
        or card.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or card.get("tenant_id") != context.get("tenant_id")
        or card.get("menu_id") != callback.message.message_id
        or data.get("last_menu_message_id") != callback.message.message_id
    ):
        raise ValueError("Client edit source is stale.")
    tenant, actor, client_id = (
        UUID(card["tenant_id"]), UUID(card["actor_user_id"]), UUID(card["client_id"]),
    )
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.client_card(
            telegram_user_id=callback.from_user.id, tenant_id=tenant,
            client_id=client_id, expected_user_id=actor,
        )
    item = result["item"]
    if (
        not same(await state.get_data(), saved)
        or result["actor_user_id"] != str(actor)
        or item["id"] != str(client_id) or item["tenant_id"] != str(tenant)
        or type(item["row_version"]) is not int
        or not 1 <= item["row_version"] <= 2**63-1
    ):
        raise ValueError("Client edit context changed.")
    form = {
        "telegram_user_id": callback.from_user.id,
        "tenant_id": str(tenant), "actor_user_id": str(actor),
        "client_id": str(client_id), "expected_row_version": item["row_version"],
        "language": result.get("language") or callback.from_user.language_code or "ru",
        "stage": "archive_review" if parts[1] == "archive" else "choose",
        "nonce": uuid4().hex,
        "menu_id": callback.message.message_id, "attempted": False,
    }
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

async def submit(callback, state, form, bot, event_update):
    if (
        form["stage"] != "review" or form["attempted"]
        or not valid_value(form.get("field"), form.get("value"))
    ):
        raise ValueError("Client change is not ready.")
    field, value = form["field"], form["value"]
    payload = {
        "expected_row_version": form["expected_row_version"], field: value,
    }
    form = {key: item for key, item in form.items() if key != "value"}
    form.update(stage="sending", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{FORM: form})
    saved = snapshot(await state.get_data())
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.patch_client(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                client_id=UUID(form["client_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                payload=payload,
            )
        item = result["item"]
        if (
            result["actor_user_id"] != form["actor_user_id"]
            or item["id"] != form["client_id"]
            or item["tenant_id"] != form["tenant_id"]
            or type(item["row_version"]) is not int
            or item["row_version"] not in (
                form["expected_row_version"], form["expected_row_version"] + 1,
            )
            or item[field] != value
        ):
            raise ValueError("Client edit response differs.")
        form["stage"] = "saved"
    except Exception as error:
        logger.warning(
            "construction_bot_client_edit_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status = getattr(error, "status_code", None)
        form["stage"] = (
            "conflict" if status == 409 else
            "blocked" if status in (400, 401, 403, 404, 422, 429) else "uncertain"
        )
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

async def submit_archive(callback, state, form, bot, event_update):
    if form["stage"] != "archive_review" or form["attempted"]:
        raise ValueError("Client archive is not ready.")
    form = {key: value for key, value in form.items() if key != "value"}
    form.update(stage="sending", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{FORM: form})
    saved = snapshot(await state.get_data())
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.archive_client(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                client_id=UUID(form["client_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                expected_row_version=form["expected_row_version"],
            )
        item = result["item"]
        if (
            result["actor_user_id"] != form["actor_user_id"]
            or item["id"] != form["client_id"]
            or item["tenant_id"] != form["tenant_id"]
            or type(item["row_version"]) is not int
            or item["row_version"] != form["expected_row_version"] + 1
            or not isinstance(item.get("deleted_at"), str)
        ):
            raise ValueError("Client archive response differs.")
        form["stage"] = "archived"
    except Exception as error:
        logger.warning(
            "construction_bot_client_archive_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status = getattr(error, "status_code", None)
        form["stage"] = (
            "conflict" if status == 409 else
            "blocked" if status in (400, 401, 403, 404, 422, 429) else "uncertain"
        )
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

@router.callback_query(F.data.startswith("CDE:"))
async def client_edit_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith(("CDE:open:", "CDE:archive:")):
                await start(callback, state, bot, event_update)
                return
            data = await state.get_data()
            form = deepcopy(data.get(FORM))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or not bound(data, form, callback.from_user.id)
                or parts[2] != form.get("nonce")
                or callback.message.message_id != form.get("menu_id")
            ):
                raise ValueError("Client editor is stale.")
            language = form["language"]
            action = parts[1]
            if (
                action == "field" and len(parts) == 4
                and form["stage"] == "choose"
                and parts[3] in {str(index) for index in range(len(FIELDS))}
            ):
                form.update(stage="input", field=FIELDS[int(parts[3])])
            elif (
                action == "type" and len(parts) == 4
                and form["stage"] == "input" and form["field"] == "client_type"
                and parts[3] in ("person", "company")
            ):
                form.update(stage="review", value=parts[3])
            elif (
                action == "clear" and len(parts) == 3
                and form["stage"] == "input" and form["field"] in NULLABLE
            ):
                form.update(stage="review", value=None)
            elif action == "cancel" and len(parts) == 3 and not form["attempted"]:
                form.pop("value", None)
                form["stage"] = "cancelled"
            elif action == "archive_confirm" and len(parts) == 3:
                await submit_archive(callback, state, form, bot, event_update)
                return
            elif action == "save" and len(parts) == 3:
                await submit(callback, state, form, bot, event_update)
                return
            else:
                raise ValueError("Client edit action is unavailable.")
            form["nonce"] = uuid4().hex
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            logger.warning(
                "construction_bot_client_edit_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(
                t("construction_lead_form_stale", language), parse_mode=None,
            )

class ClientFieldInput(BaseFilter):
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
            and form.get("field") != "client_type"
        )

@router.message(ClientFieldInput())
async def client_edit_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ClientFieldInput()(message, state):
            return
        form = deepcopy((await state.get_data())[FORM])
        value = message.text.strip()
        if not valid_value(form["field"], value):
            await display(message, state, form, tr("invalid", form["language"]))
            return
        form.update(stage="review", value=value, nonce=uuid4().hex)
        await state.update_data(**{FORM: form})
        await display(message, state, form)
