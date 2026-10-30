"""Telegram lead form. Mutations use the existing authenticated API only."""
import asyncio
import logging
from time import time
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_lead_create")
logger = logging.getLogger(__name__)
FORM = "construction_lead_create_form"
CONTEXT = "construction_suite_context"
FIELDS = (
    "name", "phone", "email", "work_type_description",
    "source", "address_raw", "comment",
)
LOCKS = WeakValueDictionary()


def tr(key, language):
    return t("construction_lead_form_" + key, language)


def lock_for(bot, user_id):
    key = (getattr(bot, "id", None), user_id)
    lock = LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        LOCKS[key] = lock
    return lock


def private(event):
    message = getattr(event, "message", None) or event
    user = getattr(event, "from_user", None)
    return (
        user is not None and not user.is_bot
        and message.chat.type == "private"
        and message.chat.id == user.id
    )


def owns(form, user_id):
    return isinstance(form, dict) and form.get("telegram_user_id") == user_id


def optional(form):
    field = FIELDS[form["step"]]
    return (
        field in {"phone", "address_raw", "comment"}
        or (field == "email" and bool(form["payload"].get("phone")))
    )


def cb(form, action):
    return f'LC:{action}:{form["id"]}:{form["step"]}'


def btn(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


async def suspend_lead_form(state):
    data = await state.get_data()
    form = data.get(FORM)
    if isinstance(form, dict):
        if form.get("attempted_at") is not None or form.get("phase") == "done":
            await state.update_data(**{FORM: {**form, "menu_id": None}})
        else:
            await state.update_data(**{FORM: {}})


async def show(event, state, form, text, rows, *, expected_snapshot=None):
    if expected_snapshot is not None:
        current = await state.get_data()
        if (
            await state.get_state() is not None
            or any(current.get(key) != value for key, value in expected_snapshot.items())
        ):
            return
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    chunks = [text[i:i + 1800] for i in range(0, len(text), 1800)] or [text]
    callback_message = getattr(event, "message", None)
    sent = None
    for index, chunk in enumerate(chunks):
        markup = keyboard if index == len(chunks) - 1 else None
        if callback_message is not None and len(chunks) == 1:
            sent = await edit_or_replace_menu_message(
                callback=event, text=chunk, reply_markup=markup,
            )
        else:
            message = callback_message or event
            sent = await message.answer(chunk, reply_markup=markup)
    if expected_snapshot is not None:
        current = await state.get_data()
        if (
            await state.get_state() is not None
            or any(current.get(key) != value for key, value in expected_snapshot.items())
        ):
            return
    if form is not None:
        form["menu_id"] = sent.message_id
        await state.update_data(**{FORM: form})
    await state.update_data(last_menu_message_id=sent.message_id)


async def display(event, state, form, notice=None):
    if form.get("phase") == "responsibles":
        from handlers.construction.construction_lead_responsible import render
        return await render(event, state, form, notice=notice)
    if form.get("phase") == "address_suggestions":
        from handlers.construction.construction_lead_address_suggestions import (
            render as render_address_suggestions,
        )
        return await render_address_suggestions(event, state, form, notice=notice)
    lang = form["language"]
    rows = []
    if form["phase"] == "done":
        text = tr("created", lang)
        rows = [[btn(tr("open", lang), "CS_LEAD:" + UUID(form["lead_id"]).hex)]]
    elif form["attempted_at"] is not None:
        text = tr("uncertain", lang)
        if 0 <= time() - form["attempted_at"] < 86340:
            rows = [[btn(tr("retry", lang), cb(form, "submit"))]]
        else:
            text = tr("expired", lang)
    elif form["step"] < len(FIELDS):
        text = tr(FIELDS[form["step"]], lang)
        if optional(form):
            rows.append([btn(tr("skip", lang), cb(form, "skip"))])
        rows.append([btn(tr("cancel", lang), cb(form, "cancel"))])
    elif form["step"] == len(FIELDS):
        text = tr("responsible", lang)
        rows = [
            [btn(tr("mine", lang), cb(form, "mine"))],
            [btn(tr("none", lang), cb(form, "none"))],
            [btn(tr("cancel", lang), cb(form, "cancel"))],
        ]
        if form.get("can_choose_responsible") is True:
            from handlers.construction.construction_lead_responsible import tr as responsible_tr
            rows.insert(1, [
                btn(responsible_tr("choose", lang), cb(form, "r_open")),
            ])
    else:
        lines = [tr("review", lang)]
        for field in FIELDS:
            lines.append(
                tr(field, lang).split("\n", 1)[0] + "\n"
                + (form["payload"].get(field) or "—")
            )
        lines.append(
            tr("responsible", lang) + "\n"
            + (
                form.get("responsible_name")
                if form.get("responsible_user_id")
                else tr("mine" if form["assign_self"] else "none", lang)
            )
        )
        text = "\n\n".join(lines)
        rows = [
            [btn(tr("save", lang), cb(form, "submit"))],
            [btn(tr("edit", lang), cb(form, "edit"))],
            [btn(tr("cancel", lang), cb(form, "cancel"))],
        ]
    rows.append([btn(t("construction_bot_leads_btn", lang), "CS_LEADS")])
    title = tr("title", lang) + "\n" + form["organization"]
    if notice:
        text = notice + "\n\n" + text
    await show(event, state, form, title + "\n\n" + text, rows)


async def error_screen(event, state, form, language, error):
    # Never log input, credentials, full response bodies or exception messages.
    logger.warning(
        "construction_bot_lead_create_failed error_type=%s status=%s code=%s",
        type(error).__name__,
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    if form:
        key = (
            "construction_bot_rate_limited"
            if getattr(error, "status_code", None) == 429
            else "construction_bot_error"
        )
        await display(event, state, form, notice=t(key, language))
    else:
        await show(event, state, None, t("construction_bot_unavailable", language), [
            [btn(t("construction_bot_change_org_btn", language), "CS_ORGS")],
            [btn(t("all_services_suites_btn", language), "M_SUITES")],
        ])


@router.callback_query(F.data.startswith("LC:"))
async def lead_create_callback(callback, state, bot=None, event_update=None):
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
            if callback.data == "LC:new":
                tenant_id = UUID(context["tenant_id"])
                if owns(existing, callback.from_user.id) and existing.get("phase") != "done":
                    if (
                        existing.get("attempted_at") is not None
                        or existing.get("tenant_id") == str(tenant_id)
                    ):
                        form = dict(existing)
                        tenant_id = UUID(form["tenant_id"])
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    prepared = await service.lead_form(
                        telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
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
                    raise ValueError("Canonical account changed.")
                await state.update_data(**{
                    CONTEXT: {
                        **context, "tenant_id": str(tenant_id),
                        "telegram_user_id": callback.from_user.id,
                    },
                    FORM: form,
                })
                if form.get("attempted_at") is None:
                    form["can_choose_responsible"] = (
                        prepared.get("can_choose_responsible") is True
                    )
                    await state.update_data(**{FORM: form})
                await display(callback, state, form)
                return

            if not owns(existing, callback.from_user.id):
                raise ValueError("Form unavailable.")
            form = dict(existing)
            language = form["language"]
            if callback.data.count(":") != 3:
                raise ValueError("Invalid form callback.")
            _, action, nonce, step = callback.data.split(":")
            if nonce != form["id"] or step != str(form["step"]):
                await callback.message.answer(tr("stale", language))
                return
            if form["tenant_id"] != context.get("tenant_id"):
                raise ValueError("Organization changed.")
            if form["phase"] == "done":
                await display(callback, state, form)
                return

            if form.get("phase") == "address_suggestions":
                from handlers.construction.construction_lead_address_suggestions import dispatch
                if await dispatch(
                    callback, state, form, action, bot=bot, event_update=event_update,
                ):
                    return

            if action == "submit":
                if form["step"] != len(FIELDS) + 1:
                    raise ValueError("Review is required.")
                if not form["payload"].get("phone") and not form["payload"].get("email"):
                    raise ValueError("Contact is required.")
                attempted = form["attempted_at"]
                if attempted is not None and not 0 <= time() - attempted < 86340:
                    await display(callback, state, form)
                    return
                # Persist the immutable command and stable key BEFORE the API call.
                if attempted is None:
                    form["attempted_at"] = time()
                form["phase"] = "pending"
                await state.update_data(**{FORM: form})
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.create_lead(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        payload=dict(form["payload"]),
                        assign_self=form["assign_self"],
                        idempotency_key="tg-lead:" + form["id"],
                        **(
                            {"responsible_user_id": UUID(form["responsible_user_id"])}
                            if form.get("responsible_user_id") else {}
                        ),
                    )
                lead_id = str(UUID(result["item"]["id"]))
                form = {
                    **form, "phase": "done", "lead_id": lead_id,
                    "payload": {},
                    "responsible_user_id": None,
                    "responsible_name": None,
                    "responsible_ids": [],
                }
                # Save success before replying; an old button cannot create again.
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
                return

            if form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            if action == "cancel":
                await state.update_data(**{FORM: {}})
                await show(callback, state, None, tr("cancelled", language), [
                    [btn(t("construction_bot_leads_btn", language), "CS_LEADS")],
                ])
                return
            if action.startswith("r_") or form.get("phase") == "responsibles":
                from handlers.construction.construction_lead_responsible import dispatch
                if await dispatch(
                    callback, state, form, action,
                    bot=bot, event_update=event_update,
                ):
                    return

            if action == "edit" and form["step"] == len(FIELDS) + 1:
                form.update(
                    step=0, phase="input", payload={}, assign_self=False,
                    responsible_user_id=None, responsible_name=None,
                    responsible_ids=[],
                )
            elif action == "skip" and form["step"] < len(FIELDS) and optional(form):
                form["payload"] = {**form["payload"], FIELDS[form["step"]]: None}
                form["step"] += 1
            elif action in {"mine", "none"} and form["step"] == len(FIELDS):
                form["assign_self"] = action == "mine"
                form["responsible_user_id"] = None
                form["responsible_name"] = None
                form["responsible_ids"] = []
                form["step"] += 1
                form["phase"] = "review"
            else:
                await callback.message.answer(tr("stale", language))
                return
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            await error_screen(callback, state, form, language, error)


class LeadFormInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message)
            or (message.text or "").startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        context = data.get(CONTEXT, {})
        return (
            owns(form, message.from_user.id)
            and isinstance(context, dict)
            and context.get("telegram_user_id") == message.from_user.id
            and context.get("tenant_id") == form.get("tenant_id")
            and form.get("phase") == "input"
            and isinstance(form.get("step"), int)
            and 0 <= form["step"] < len(FIELDS)
            and form.get("menu_id") is not None
            and form["menu_id"] == data.get("last_menu_message_id")
        )


@router.message(LeadFormInput())
async def lead_create_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        # Recheck after waiting for another message/callback.
        if not await LeadFormInput()(message, state):
            return
        data = await state.get_data()
        form = dict(data[FORM])
        language = form["language"]
        value = message.text
        if not isinstance(value, str) or not value.strip():
            await display(message, state, form, notice=tr("invalid", language))
            return
        form["payload"] = {
            **form["payload"], FIELDS[form["step"]]: value.strip(),
        }
        form["step"] += 1
        await state.update_data(**{FORM: form})
        if form["step"] == FIELDS.index("address_raw") + 1:
            from handlers.construction.construction_lead_address_suggestions import lookup
            await lookup(message, state, form, bot=bot, event_update=event_update)
            return
        await display(message, state, form)

# lead_address_suggestions_v1
