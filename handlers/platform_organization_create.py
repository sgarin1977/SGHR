"""Organization onboarding through the shared Platform API only."""
import logging
import re
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, owns, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.platform_organization_create import language_name, tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="platform_organization_create")
logger = logging.getLogger(__name__)
FORM = "platform_organization_create_form"
CONTEXT = "construction_suite_context"
SAFE_RETRY_SECONDS = 86340


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(form, action):
    return f'OC:{action}:{form["id"]}:{form["revision"]}'


def back_rows(language):
    return [[button(tr("organizations", language), "CS_ORGS")]]


async def organization_entry_button(state, *, telegram_user_id, language):
    data = await state.get_data()
    context = data.get(CONTEXT)
    if (
        not isinstance(context, dict)
        or context.get("telegram_user_id") != telegram_user_id
        or "tenant_id" in context
    ):
        raise ValueError("Organization selector is unavailable.")
    nonce = uuid4().hex
    await state.update_data(**{
        CONTEXT: {**context, "organization_creation_nonce": nonce},
    })
    return [button(tr("button", language), "OC:new:" + nonce)]


async def suspend_organization_form(state, *, telegram_user_id=None):
    data = await state.get_data()
    values = {}
    form = data.get(FORM)
    if (
        isinstance(form, dict) and form
        and (telegram_user_id is None or owns(form, telegram_user_id))
    ):
        keep = form.get("attempted_at") is not None or form.get("phase") == "done"
        values[FORM] = {**form, "menu_id": None} if keep else {}
    context = data.get(CONTEXT)
    if (
        isinstance(context, dict)
        and (
            telegram_user_id is None
            or context.get("telegram_user_id") == telegram_user_id
        )
    ):
        cleaned = {
            key: value for key, value in context.items()
            if key not in ("organization_form_id", "organization_creation_nonce")
        }
        if cleaned != context:
            values[CONTEXT] = cleaned
    if values:
        await state.update_data(**values)


async def retain_organization_attempt(state, previous_data, telegram_user_id):
    form = previous_data.get(FORM)
    if (
        owns(form, telegram_user_id)
        and form.get("attempted_at") is not None
        and form.get("phase") != "done"
    ):
        await state.update_data(**{FORM: {**form, "menu_id": None}})


async def organization_navigation_middleware(handler, event, data):
    value = getattr(event, "data", None)
    state = data.get("state")
    sender = getattr(event, "from_user", None)
    if (
        state is not None and sender is not None
        and not (isinstance(value, str) and value.startswith("OC:"))
    ):
        # Only this new form's state is touched; authorization is unchanged.
        await suspend_organization_form(state, telegram_user_id=sender.id)
    return await handler(event, data)


def live(form, data, sender):
    context = data.get(CONTEXT)
    current = data.get(FORM)
    return (
        owns(form, sender)
        and isinstance(current, dict)
        and current.get("id") == form.get("id")
        and current.get("revision") == form.get("revision")
        and type(form.get("revision")) is int
        and isinstance(context, dict)
        and context.get("telegram_user_id") == sender
        and context.get("organization_form_id") == form.get("id")
        and "tenant_id" not in context
        and form.get("menu_id") is not None
        and form["menu_id"] == data.get("last_menu_message_id")
    )


async def draw(event, text, rows):
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    chunks = [text[index:index + 1800] for index in range(0, len(text), 1800)] or [text]
    message = getattr(event, "message", None)
    if message is not None and len(chunks) == 1:
        return await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=keyboard,
        )
    for index, chunk in enumerate(chunks):
        sent = await (message or event).answer(
            chunk, parse_mode=None,
            reply_markup=keyboard if index == len(chunks) - 1 else None,
        )
    return sent


async def plain(event, state, text, language):
    sent = await draw(event, text, back_rows(language))
    await state.update_data(last_menu_message_id=sent.message_id)


async def display(event, state, form, *, notice=None):
    if not live(form, await state.get_data(), event.from_user.id):
        return
    shown = {**form, "revision": form["revision"] + 1}
    language = form["language"]
    lines = [tr("title", language)]
    rows = []
    if notice:
        lines.append(notice)
    phase = form["phase"]
    if phase == "done":
        lines.extend([tr("created", language), form["created_name"]])
        rows.append([button(
            tr("open", language), "CS_ORG:" + UUID(form["tenant_id"]).hex,
        )])
    elif phase == "rejected":
        lines.append(tr("rejected", language))
        rows.extend([
            [button(tr("edit", language), cb(shown, "edit"))],
            [button(tr("cancel", language), cb(shown, "cancel"))],
        ])
    elif form.get("attempted_at") is not None:
        if time() - form["attempted_at"] < SAFE_RETRY_SECONDS:
            lines.append(tr("uncertain", language))
            rows.append([button(tr("retry", language), cb(shown, "submit"))])
        else:
            lines.append(tr("expired", language))
            rows.append([button(tr("finish", language), cb(shown, "finish"))])
    elif phase == "name":
        lines.append(tr("name", language))
    elif phase == "language":
        lines.append(tr("language", language))
        offset = form["language_offset"]
        codes = form["available_languages"]
        rows.extend([
            [button(language_name(code), cb(shown, "lang_" + code))]
            for code in codes[offset:offset + 8]
        ])
        if offset:
            rows.append([button(tr("previous", language), cb(shown, "langprev"))])
        if offset + 8 < len(codes):
            rows.append([button(tr("next", language), cb(shown, "langnext"))])
    elif phase == "currency":
        lines.append(tr("currency", language))
    elif phase == "review":
        lines.extend([
            tr("review", language),
            tr("name_label", language) + ": " + form["payload"]["name"],
            tr("language_label", language) + ": "
                + language_name(form["payload"]["language"]),
            tr("currency_label", language) + ": " + form["payload"]["currency"],
        ])
        rows.extend([
            [button(tr("button", language), cb(shown, "submit"))],
            [button(tr("edit", language), cb(shown, "edit"))],
        ])
    else:
        raise ValueError("Organization form phase is invalid.")
    if form.get("attempted_at") is None and phase != "done":
        rows.append([button(tr("cancel", language), cb(shown, "cancel"))])
    rows.extend(back_rows(language))
    sent = await draw(event, "\n\n".join(lines), rows)
    if live(form, await state.get_data(), event.from_user.id):
        await state.update_data(**{
            FORM: {**shown, "menu_id": sent.message_id},
            "last_menu_message_id": sent.message_id,
        })


async def begin(callback, state, existing, *, bot, event_update, force_new=False):
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        prepared = await service.organization_form(
            telegram_user_id=callback.from_user.id,
        )
    actor = str(UUID(prepared["actor_user_id"]))
    keep = (
        owns(existing, callback.from_user.id)
        and existing.get("phase") != "done"
        and not force_new
    )
    if (
        keep and existing.get("actor_user_id") != actor
        and existing.get("attempted_at") is not None
    ):
        raise ConstructionApiClientError(status_code=403, code="account_unavailable")
    keep = keep and existing.get("actor_user_id") == actor
    if not prepared["languages"] and not (
        keep and existing.get("attempted_at") is not None
    ):
        raise ConstructionApiClientError(status_code=503, code="service_unavailable")
    form = dict(existing) if keep else {
        "id": uuid4().hex, "revision": 0, "phase": "name",
        "payload": {}, "attempted_at": None,
        "telegram_user_id": callback.from_user.id, "actor_user_id": actor,
        "language_offset": 0,
    }
    form.update(
        language=prepared["language"] or callback.from_user.language_code or "ru",
        available_languages=list(prepared["languages"]),
        menu_id=(await state.get_data()).get("last_menu_message_id"),
    )
    if form.get("attempted_at") is None:
        form["language_offset"] = 0
        if (
            "language" in form["payload"]
            and form["payload"]["language"] not in form["available_languages"]
        ):
            form["payload"] = {"name": form["payload"]["name"]}
            form["phase"] = "language"

    from handlers.construction.construction_team import suspend_forms
    from handlers.construction.construction_area_create import suspend_area_form
    await suspend_forms(state)
    await suspend_area_form(state)
    await state.update_data(**{
        FORM: form,
        CONTEXT: {
            "telegram_user_id": callback.from_user.id,
            "organization_form_id": form["id"],
        },
        "construction_client_card_view": {},
        "construction_project_card_view": {},
        "construction_project_areas_view": {},
        "construction_team_view": {},
    })
    await display(callback, state, form)
    return form


async def close_form(callback, state, form, *, expired=False):
    if not live(form, await state.get_data(), callback.from_user.id):
        return
    language = form["language"]
    await state.update_data(**{
        FORM: {}, CONTEXT: {"telegram_user_id": callback.from_user.id},
    })
    await plain(
        callback, state,
        tr("expired" if expired else "cancelled", language), language,
    )


async def failure(callback, state, form, language, error):
    logger.warning(
        "platform_bot_organization_create_failed error_type=%s status=%s code=%s",
        type(error).__name__, getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    if form and form.get("phase") == "done":
        return
    status = getattr(error, "status_code", None)
    if (
        form and status == 422
        and getattr(error, "code", None) == "validation_error"
        and form.get("attempted_at") is not None
        and live(form, await state.get_data(), callback.from_user.id)
    ):
        form = {**form, "phase": "rejected"}
        await state.update_data(**{FORM: form})
        await display(callback, state, form)
        return
    key = (
        "construction_bot_unavailable" if status in (401, 403, 404)
        else "construction_bot_rate_limited" if status == 429
        else "construction_bot_error"
    )
    if (
        form and status not in (401, 403, 404)
        and live(form, await state.get_data(), callback.from_user.id)
    ):
        await display(callback, state, form, notice=t(key, language))
        return
    await suspend_organization_form(state, telegram_user_id=callback.from_user.id)
    await plain(callback, state, t(key, language), language)


@router.callback_query(F.data.startswith("OC:"))
async def organization_callback(callback, state, bot=None, event_update=None):
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
            context = data.get(CONTEXT, {})
            parts = callback.data.split(":")
            if len(parts) == 3 and parts[1] == "new":
                if (
                    not isinstance(context, dict)
                    or context.get("telegram_user_id") != callback.from_user.id
                    or context.get("organization_creation_nonce") != parts[2]
                    or "tenant_id" in context
                    or callback.message.message_id != data.get("last_menu_message_id")
                ):
                    await callback.message.answer(tr("stale", language))
                    return
                form = data.get(FORM)
                form = await begin(
                    callback, state, form, bot=bot, event_update=event_update,
                )
                return

            form = data.get(FORM)
            if (
                len(parts) != 4
                or not live(form, data, callback.from_user.id)
                or parts[2] != form["id"]
                or parts[3] != str(form["revision"])
                or callback.message.message_id != form["menu_id"]
            ):
                await callback.message.answer(tr("stale", language))
                return
            language = form["language"]
            action = parts[1]
            if form["phase"] == "done":
                await display(callback, state, form)
                return
            if form["phase"] == "rejected":
                if action == "edit":
                    form = await begin(
                        callback, state, form, bot=bot,
                        event_update=event_update, force_new=True,
                    )
                elif action == "cancel":
                    await close_form(callback, state, form)
                else:
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
                    "attempted_at": (
                        form["attempted_at"]
                        if form["attempted_at"] is not None else time()
                    ),
                }
                await state.update_data(**{FORM: form})
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.create_organization(
                        telegram_user_id=callback.from_user.id,
                        expected_user_id=UUID(form["actor_user_id"]),
                        payload=dict(form["payload"]),
                        idempotency_key="tg-organization:" + form["id"],
                    )
                item = result["item"]
                tenant_id = str(UUID(item["id"]))
                current = await state.get_data()
                if current.get(FORM, {}).get("id") != form["id"]:
                    return
                visible = live(form, current, callback.from_user.id)
                form = {
                    **form, "phase": "done", "payload": {},
                    "tenant_id": tenant_id, "created_name": item["name"],
                    "menu_id": form["menu_id"] if visible else None,
                }
                await state.update_data(**{FORM: form})
                if visible:
                    await display(callback, state, form)
                return
            if form["attempted_at"] is not None:
                if (
                    action == "finish"
                    and time() - form["attempted_at"] >= SAFE_RETRY_SECONDS
                ):
                    await close_form(callback, state, form, expired=True)
                else:
                    await display(callback, state, form)
                return
            if action == "cancel":
                await close_form(callback, state, form)
                return
            if action == "edit" and form["phase"] == "review":
                form = {**form, "phase": "name", "payload": {}, "language_offset": 0}
            elif action.startswith("lang_") and form["phase"] == "language":
                code = action[5:]
                if code not in form["available_languages"]:
                    await callback.message.answer(tr("stale", language))
                    return
                form = {
                    **form, "phase": "currency",
                    "payload": {**form["payload"], "language": code},
                }
            elif action in ("langnext", "langprev") and form["phase"] == "language":
                offset = form["language_offset"] + (8 if action == "langnext" else -8)
                if not 0 <= offset < len(form["available_languages"]):
                    await callback.message.answer(tr("stale", language))
                    return
                form = {**form, "language_offset": offset}
            else:
                await callback.message.answer(tr("stale", language))
                return
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            await failure(callback, state, form, language, error)


class OrganizationFormInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        return (
            isinstance(form, dict) and form.get("phase") in ("name", "currency")
            and form.get("attempted_at") is None
            and live(form, data, message.from_user.id)
        )


@router.message(OrganizationFormInput())
async def organization_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await OrganizationFormInput()(message, state):
            return
        form = dict((await state.get_data())[FORM])
        value = message.text.strip()
        if not value:
            await display(message, state, form, notice=tr("invalid", form["language"]))
            return
        if form["phase"] == "name":
            form = {
                **form, "phase": "language",
                "payload": {**form["payload"], "name": value},
            }
        else:
            value = value.upper()
            if re.fullmatch(r"[A-Z]{3}", value) is None:
                await display(message, state, form, notice=tr("currency", form["language"]))
                return
            form = {
                **form, "phase": "review",
                "payload": {**form["payload"], "currency": value},
            }
        await state.update_data(**{FORM: form})
        await display(message, state, form)
