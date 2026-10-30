"""Explicit operator email requests through the common HTTP API only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_client_link_api_client import ClientLinkQueuedReceipt
from services.construction.construction_client_link_email import normalize_client_link_email
from ui.construction_client_link_operator import tr
from ui.construction_client_card import tr as card_tr
from ui.construction_client_catalog import tr as catalog_tr
from ui.texts import t

router = Router(name="construction_client_link_operator")
logger = logging.getLogger(__name__)
FORM = "construction_client_link_operator_form"
CARD = "construction_client_card_view"
CONTEXT = "construction_suite_context"
LIST = "construction_client_catalog_view"
KEYS = (FORM, CARD, CONTEXT, LIST, "last_menu_message_id")
SAFE_RETRY_SECONDS = 86340

def button(text, value):
    return InlineKeyboardButton(text=text, callback_data=value)

def cb(form, action):
    return f'CLO:{action}:{form["nonce"]}'

def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})

def same(data, before):
    return all(data.get(key) == value for key, value in before.items())

def source(data, sender, menu):
    card, context = data.get(CARD), data.get(CONTEXT)
    if (
        not isinstance(card, dict) or not isinstance(context, dict)
        or card.get("telegram_user_id") != sender
        or context.get("telegram_user_id") != sender
        or card.get("tenant_id") != context.get("tenant_id")
        or card.get("menu_id") != menu
        or data.get("last_menu_message_id") != menu
        or not isinstance(card.get("nonce"), str)
    ):
        return None
    for name in ("tenant_id", "client_id", "actor_user_id"):
        value = card.get(name)
        if not isinstance(value, str) or str(UUID(value)) != value:
            return None
    return card

def bound(data, form, sender):
    if (
        not isinstance(form, dict) or not form
        or data.get(FORM) != form
        or form.get("telegram_user_id") != sender
        or form.get("menu_id") is None
    ):
        return False
    card = source(data, sender, form["menu_id"])
    return (
        card is not None and card["nonce"] == form.get("card_nonce")
        and all(
            form.get(name) == card.get(name)
            for name in ("tenant_id", "client_id", "actor_user_id")
        )
    )

def unresolved(form):
    return (
        isinstance(form, dict)
        and form.get("attempted_at") is not None
        and form.get("stage") not in ("queued", "cancelled")
    )

def retry_allowed(form):
    attempted = form.get("attempted_at")
    return (
        type(attempted) in (int, float)
        and 0 <= time() - attempted < SAFE_RETRY_SECONDS
    )

async def navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith("CLO:")
    ):
        saved = (await state.get_data()).get(FORM)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{
                FORM: {**saved, "menu_id": None} if unresolved(saved) else {},
            })
    return await handler(event, data)

async def display(event, state, form):
    data = await state.get_data()
    if not bound(data, form, event.from_user.id):
        return
    before = snapshot(data)
    card = data[CARD]
    updated = {**form, "nonce": uuid4().hex}
    language, stage = form["language"], form["stage"]
    lines = [tr("title", language)]
    rows = []
    if stage == "input":
        lines.append(tr("enter", language))
    elif stage == "review":
        lines.extend([tr("review", language), form["email"]])
        if form["previous_user_id"] is not None:
            lines.append(tr("replacement", language))
        lines.append(tr("lifetime", language))
        rows.extend([
            [button(
                tr("send_replacement" if form["previous_user_id"] is not None else "send", language),
                cb(updated, "submit"),
            )],
            [button(tr("edit", language), cb(updated, "edit"))],
        ])
    elif stage in ("not_found", "invalid"):
        lines.append(tr(stage, language))
        rows.append([button(tr("edit", language), cb(updated, "edit"))])
    elif stage == "pending":
        lines.append(tr("pending", language))
        if retry_allowed(form):
            rows.append([button(tr("retry", language), cb(updated, "submit"))])
        else:
            updated["stage"] = "expired"
            lines.append(tr("expired", language))
            rows.append([button(tr("new", language), cb(updated, "new"))])
    elif stage == "expired":
        lines.append(tr("expired", language))
        rows.append([button(tr("new", language), cb(updated, "new"))])
    elif stage == "queued":
        lines.append(tr("queued", language))
        expiry = datetime.fromisoformat(form["receipt"]["expires_at"])
        lines.append(
            tr("expires", language) + ": "
            + expiry.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        )
    elif stage in ("blocked", "conflict", "cancelled"):
        lines.append(tr(stage, language))
    else:
        raise ValueError("Invalid operator-link stage.")
    lines.append(tr("boundary", language))
    if form.get("attempted_at") is None and stage in (
        "input", "review", "not_found", "invalid",
    ):
        rows.append([button(tr("cancel", language), cb(updated, "cancel"))])
    rows.append([button(
        card_tr("summary", language), "CC:summary:" + card["nonce"],
    )])
    rows.append([button(
        catalog_tr("back" if card.get("catalog_nonce") else "title", language),
        "CL:back:" + card["catalog_nonce"] if card.get("catalog_nonce") else "CL:open",
    )])
    rows.append([button(t("construction_bot_home_btn", language), "CS_HOME")])
    text = "\n\n".join(lines)
    parent = getattr(event, "message", None) or event
    sent = await parent.answer(
        text, parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not same(await state.get_data(), before):
        return
    from handlers.construction.construction_client_catalog import sync_origin
    values = sync_origin(data, card, sent.message_id)
    values.update({
        FORM: {} if stage == "cancelled" else {
            **updated, "menu_id": sent.message_id,
        },
        CARD: {**card, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })
    await state.update_data(**values)

async def start(callback, state, *, token, bot, event_update, discard_expired=False):
    data = await state.get_data()
    card = source(data, callback.from_user.id, callback.message.message_id)
    if card is None or token != card["nonce"]:
        raise ValueError("Client link source is stale.")
    previous = deepcopy(data.get(FORM))
    matches = isinstance(previous, dict) and all(
        previous.get(name) == card.get(name)
        for name in ("telegram_user_id", "tenant_id", "client_id", "actor_user_id")
    )
    if unresolved(previous) and matches and not discard_expired:
        resumed = {
            **previous, "card_nonce": card["nonce"],
            "menu_id": callback.message.message_id,
            "stage": "pending" if retry_allowed(previous) else "expired",
        }
        await state.update_data(**{
            FORM: resumed, "construction_client_edit_form": {},
        })
        await display(callback, state, resumed)
        return
    if discard_expired and (
        not matches or not unresolved(previous) or retry_allowed(previous)
    ):
        raise ValueError("A new request requires explicit expired-state review.")
    if (
        unresolved(previous) and not matches and retry_allowed(previous)
        and previous.get("actor_user_id") == card["actor_user_id"]
    ):
        await callback.message.answer(
            tr("other", callback.from_user.language_code), parse_mode=None,
        )
        return

    from handlers.construction.construction_team import suspend_forms
    from handlers.construction.construction_area_create import suspend_area_form
    await suspend_forms(state)
    await suspend_area_form(state)
    await state.update_data(construction_client_edit_form={})
    data = await state.get_data()
    current_card = source(data, callback.from_user.id, callback.message.message_id)
    if current_card != card:
        return
    before = snapshot(data)
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.client_card(
            telegram_user_id=callback.from_user.id,
            tenant_id=UUID(card["tenant_id"]), client_id=UUID(card["client_id"]),
            expected_user_id=UUID(card["actor_user_id"]),
        )
    if not same(await state.get_data(), before):
        return
    item = result["item"]
    if (
        result["actor_user_id"] != card["actor_user_id"]
        or item["id"] != card["client_id"] or item["tenant_id"] != card["tenant_id"]
        or type(item["row_version"]) is not int
        or not 1 <= item["row_version"] <= 2**63 - 1
    ):
        raise ValueError("Client link context differs.")
    linked = item["linked_user_id"]
    if linked is not None:
        if not isinstance(linked, str) or str(UUID(linked)) != linked:
            raise ValueError("Previous identity is invalid.")
    form = {
        "telegram_user_id": callback.from_user.id,
        "tenant_id": card["tenant_id"], "client_id": card["client_id"],
        "actor_user_id": card["actor_user_id"], "card_nonce": card["nonce"],
        "expected_row_version": item["row_version"], "previous_user_id": linked,
        "language": result.get("language") or callback.from_user.language_code or "ru",
        "nonce": uuid4().hex, "menu_id": callback.message.message_id,
        "stage": "input", "email": None,
        "attempted_at": None, "key": None, "payload": None,
    }
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

async def submit(callback, state, form, *, bot, event_update):
    if form["stage"] not in ("review", "pending"):
        raise ValueError("Explicit request review is required.")
    if form["attempted_at"] is not None and not retry_allowed(form):
        expired = {**form, "stage": "expired"}
        await state.update_data(**{FORM: expired})
        await display(callback, state, expired)
        return
    writing = deepcopy(form)
    if writing["attempted_at"] is None:
        email = normalize_client_link_email(writing["email"])
        payload = {
            "target_email": email,
            "expected_row_version": writing["expected_row_version"],
        }
        if writing["previous_user_id"] is not None:
            payload["previous_user_id"] = writing["previous_user_id"]
        writing.update(
            email=email, payload=payload,
            key=str(uuid4()), attempted_at=time(),
        )
    payload = writing["payload"]
    expected_keys = {"target_email", "expected_row_version"}
    if writing["previous_user_id"] is not None:
        expected_keys.add("previous_user_id")
    if (
        not isinstance(payload, dict) or set(payload) != expected_keys
        or payload["target_email"] != writing["email"]
        or payload["expected_row_version"] != writing["expected_row_version"]
        or payload.get("previous_user_id") != writing["previous_user_id"]
        or not isinstance(writing["key"], str) or not writing["key"]
    ):
        raise ValueError("Frozen client-link command differs.")
    writing.update(stage="sending", nonce=uuid4().hex)
    await state.update_data(**{FORM: writing})
    before = snapshot(await state.get_data())
    result_form = {**writing, "stage": "pending"}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.request_client_user_link(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(writing["tenant_id"]),
                client_id=UUID(writing["client_id"]),
                expected_user_id=UUID(writing["actor_user_id"]),
                target_email=payload["target_email"],
                expected_row_version=payload["expected_row_version"],
                previous_user_id=UUID(payload["previous_user_id"])
                    if "previous_user_id" in payload else None,
                idempotency_key=writing["key"],
            )
        receipt = result["request"]
        if (
            result["actor_user_id"] != writing["actor_user_id"]
            or not isinstance(receipt, ClientLinkQueuedReceipt)
            or receipt.tenant_id != UUID(writing["tenant_id"])
            or receipt.client_id != UUID(writing["client_id"])
            or receipt.target_user_id == UUID(writing["actor_user_id"])
            or receipt.row_version != 1 or receipt.delivery_status != "queued"
            or receipt.expires_at - receipt.issued_at != timedelta(hours=24)
        ):
            raise ValueError("Client-link queue receipt differs.")
        result_form.update(
            stage="queued", email=None, payload=None, key=None, attempted_at=None,
            receipt={
                "request_id": str(receipt.request_id),
                "expires_at": receipt.expires_at.isoformat(),
            },
        )
    except Exception as error:
        logger.warning(
            "construction_bot_client_link_operator_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        if status == 404 and code == "PLATFORM_USER_NOT_FOUND":
            result_form.update(
                stage="not_found", attempted_at=None, key=None, payload=None,
            )
        elif status == 422:
            result_form.update(
                stage="invalid", attempted_at=None, key=None, payload=None,
            )
        elif status == 409 and code == "ROW_VERSION_CONFLICT":
            result_form.update(
                stage="conflict", attempted_at=None, key=None, payload=None,
            )
        elif status in (400, 401, 403, 404) or (
            status == 409 and code != "idempotency_in_progress"
        ):
            result_form["stage"] = "blocked"
        # Other failures retain the exact command/key for explicit recovery.
    if not same(await state.get_data(), before):
        return
    await state.update_data(**{FORM: result_form})
    await display(callback, state, result_form)

@router.callback_query(F.data.startswith("CLO:"))
async def operator_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            parts = callback.data.split(":")
            if len(parts) != 3:
                raise ValueError("Invalid operator-link command.")
            if parts[1] == "open":
                await start(
                    callback, state, token=parts[2],
                    bot=bot, event_update=event_update,
                )
                return
            data = await state.get_data()
            form = deepcopy(data.get(FORM))
            if (
                not bound(data, form, callback.from_user.id)
                or parts[2] != form["nonce"]
                or callback.message.message_id != form["menu_id"]
            ):
                raise ValueError("Operator-link form is stale.")
            action = parts[1]
            if action == "submit":
                await submit(callback, state, form, bot=bot, event_update=event_update)
                return
            if action == "new" and form["stage"] == "expired":
                await start(
                    callback, state, token=data[CARD]["nonce"],
                    bot=bot, event_update=event_update, discard_expired=True,
                )
                return
            if form["attempted_at"] is not None:
                raise ValueError("An unresolved request cannot be edited.")
            if action == "edit" and form["stage"] in ("review", "not_found", "invalid"):
                form.update(stage="input", email=None, payload=None, key=None)
            elif action == "cancel" and form["stage"] in (
                "input", "review", "not_found", "invalid",
            ):
                form.update(stage="cancelled", email=None, payload=None, key=None)
            else:
                raise ValueError("Operator-link action is unavailable.")
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            logger.warning(
                "construction_bot_client_link_operator_denied error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(
                t("construction_lead_form_stale", language), parse_mode=None,
            )

class OperatorEmailInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        return (
            bound(data, form, message.from_user.id)
            and form["stage"] == "input" and form["attempted_at"] is None
        )

@router.message(OperatorEmailInput())
async def operator_email(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await OperatorEmailInput()(message, state):
            return
        form = deepcopy((await state.get_data())[FORM])
        try:
            # Local format validation only. Identity resolution is server-only.
            email = normalize_client_link_email(message.text)
        except ValueError:
            await message.answer(tr("invalid", form["language"]), parse_mode=None)
            return
        form.update(email=email, stage="review")
        await state.update_data(**{FORM: form})
        await display(message, state, form)

# client_link_operator_bot_v1
