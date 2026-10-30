"""Plan a lead activity using the existing authenticated activity API."""
import logging
from copy import deepcopy
from time import time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import (
    lock_for, owns, private, suspend_lead_form,
)
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_activity import (
    ACTIVITY_ORDER, activity_label, local_deadlines, display_deadline,
)
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_activity_plan")
logger = logging.getLogger(__name__)
FORM = "construction_activity_plan_form"
CONTEXT = "construction_suite_context"


def tr(key, language):
    return t("construction_activity_form_" + key, language)


def common(key, language):
    return t("construction_lead_form_" + key, language)


def cb(form, action):
    return f'AP:{action}:{form["id"]}:{form["phase"]}'


def button(text, value):
    return InlineKeyboardButton(text=text, callback_data=value)


async def suspend_activity_form(state):
    data = await state.get_data()
    form = data.get(FORM)
    if isinstance(form, dict):
        keep = form.get("attempted_at") is not None or form.get("phase") == "done"
        await state.update_data(**{
            FORM: {**form, "menu_id": None} if keep else {},
        })


async def show(event, state, form, text, rows, *, expected_snapshot=None):
    if expected_snapshot is not None:
        current = await state.get_data()
        if (
            await state.get_state() is not None
            or any(current.get(key) != value for key, value in expected_snapshot.items())
        ):
            return
    message = getattr(event, "message", None)
    chunks = [text[i:i + 1800] for i in range(0, len(text), 1800)] or [text]
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
        from handlers.construction.construction_activity_responsible import render
        return await render(event, state, form, notice=notice)
    language, phase = form["language"], form["phase"]
    rows = []
    if phase == "done":
        text = tr("created", language)
    elif form["attempted_at"] is not None:
        text = tr("pending", language)
        if 0 <= time() - form["attempted_at"] < 86340:
            rows.append([button(common("retry", language), cb(form, "submit"))])
        else:
            text = tr("expired", language)
    elif phase == "type":
        text = tr("type", language)
        rows = [
            [button(activity_label(value, language), cb(form, "type" + str(index)))]
            for index, value in enumerate(ACTIVITY_ORDER)
        ]
    elif phase == "zone":
        text = tr("zone_current", language).format(zone=form["timezone"])
        rows = [
            [button(tr("use_zone", language), cb(form, "use"))],
            [button(tr("change_zone", language), cb(form, "zone"))],
        ]
    elif phase == "zoneinput":
        text = tr("zone_input", language)
    elif phase == "due":
        text = tr("due", language).format(zone=form["timezone"])
    elif phase == "fold":
        text = tr("ambiguous", language)
        rows = [
            [button(
                display_deadline(value, form["timezone"]),
                cb(form, "fold" + str(index)),
            )]
            for index, value in enumerate(form["choices"])
        ]
    elif phase == "who":
        text = common("responsible", language)
        rows = [
            [button(common("mine", language), cb(form, "mine"))],
            [button(common("none", language), cb(form, "none"))],
        ]
        if form.get("can_choose_responsible") is True or form.get("kind") == "project":
            from handlers.construction.construction_lead_responsible import tr as responsible_tr
            rows.insert(1, [
                button(responsible_tr("choose", language), cb(form, "r_open")),
            ])
    elif phase == "comment":
        text = common("comment", language)
        rows = [[button(common("skip", language), cb(form, "skip"))]]
    elif phase == "review":
        payload = form["payload"]
        text = "\n\n".join([
            tr("review", language),
            activity_label(payload["type"], language),
            display_deadline(payload["due_at"], payload["timezone"])
                + " · " + payload["timezone"],
            common("responsible", language) + ": "
                + (
                    form.get("responsible_name")
                    if form.get("responsible_user_id")
                    else common("mine" if form["assign_self"] else "none", language)
                ),
            common("comment", language) + ":\n" + (payload["comment"] or "—"),
        ])
        rows = [
            [button(tr("save", language), cb(form, "submit"))],
            [button(common("edit", language), cb(form, "edit"))],
        ]
    else:
        raise ValueError("Unknown activity form step.")

    if form["attempted_at"] is None and phase != "done":
        rows.append([button(common("cancel", language), cb(form, "cancel"))])
    rows.append([activity_parent_button(form, language)])
    title = (
        tr("title", language) + "\n" + form["organization"]
        + "\n" + str(form.get("subject_name") or form.get("lead_name", ""))[:350]
    )
    if notice:
        text = notice + "\n\n" + text
    await show(event, state, form, title + "\n\n" + text, rows)


async def failure(event, state, form, language, error):
    logger.warning(
        "construction_bot_activity_plan_failed error_type=%s status=%s code=%s",
        type(error).__name__,
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    key = (
        "construction_bot_rate_limited"
        if getattr(error, "status_code", None) == 429
        else "construction_bot_error"
    )
    if form is not None:
        await display(event, state, form, notice=t(key, language))
    else:
        await show(event, state, None, t("construction_bot_unavailable", language), [
            [button(t("construction_bot_home_btn", language), "CS_HOME")],
        ])


@router.callback_query(F.data.startswith("AP:"))
async def activity_callback(callback, state, bot=None, event_update=None):
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
            parts = callback.data.split(":")
            existing = data.get(FORM)
            if len(parts) == 3 and parts[1] == "project":
                await start_project_activity(
                    callback, state, data, parts[2], bot, event_update,
                )
                return
            if len(parts) == 3 and parts[1] == "client":
                await start_client_activity(
                    callback, state, data, parts[2], bot, event_update,
                )
                return
            if len(parts) == 3 and parts[1] == "new":
                if (
                    owns(existing, callback.from_user.id)
                    and existing.get("kind") in ("client", "project")
                    and existing.get("attempted_at") is not None
                    and existing.get("phase") != "done"
                ):
                    raise ValueError("Resolve the pending client activity in its original context.")
                tenant_id, lead_id = UUID(context["tenant_id"]), UUID(parts[2])
                if owns(existing, callback.from_user.id) and existing.get("phase") != "done":
                    if (
                        existing.get("attempted_at") is not None
                        or (
                            existing.get("tenant_id") == str(tenant_id)
                            and existing.get("lead_id") == str(lead_id)
                        )
                    ):
                        form = dict(existing)
                        tenant_id = UUID(form["tenant_id"])
                        lead_id = UUID(form["lead_id"])
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    prepared = await service.activity_form(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=tenant_id, lead_id=lead_id,
                    )
                if form is None:
                    zone = prepared.get("timezone")
                    try:
                        ZoneInfo(zone)
                    except (TypeError, ValueError, KeyError):
                        zone = None
                    form = {
                        "id": uuid4().hex,
                        "telegram_user_id": callback.from_user.id,
                        "tenant_id": str(tenant_id), "lead_id": str(lead_id),
                        "actor_user_id": prepared["actor_user_id"],
                        "organization": prepared["organization"],
                        "lead_name": prepared["lead_name"],
                        "language": prepared["language"] or language,
                        "timezone": zone, "phase": "type",
                        "payload": {}, "assign_self": False,
                        "attempted_at": None, "menu_id": None,
                    }
                elif form["actor_user_id"] != prepared["actor_user_id"]:
                    raise ValueError("Canonical identity changed.")
                if form.get("attempted_at") is None:
                    form["can_choose_responsible"] = (
                        prepared.get("can_choose_responsible") is True
                    )
                await suspend_lead_form(state)
                await state.update_data(**{
                    CONTEXT: {**context, "tenant_id": str(tenant_id)},
                    FORM: form,
                })
                await display(callback, state, form)
                return

            if not owns(existing, callback.from_user.id):
                raise ValueError("Activity form unavailable.")
            form = dict(existing)
            language = form["language"]
            if (
                len(parts) != 4
                or parts[2] != form["id"]
                or parts[3] != form["phase"]
                or form["menu_id"] != data.get("last_menu_message_id")
                or form["tenant_id"] != context.get("tenant_id")
            ):
                await callback.message.answer(common("stale", language))
                return
            action, phase = parts[1], form["phase"]
            if phase == "done":
                await display(callback, state, form)
                return
            if action == "submit" and phase in {"review", "pending"}:
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
                    if form.get("kind") == "client":
                        create = service.create_client_activity
                        target = {"client_id": UUID(form["client_id"])}
                    elif form.get("kind") == "project":
                        create = service.create_project_activity
                        target = {"project_id": UUID(form["project_id"])}
                    else:
                        create = service.create_lead_activity
                        target = {"lead_id": UUID(form["lead_id"])}
                    result = await create(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        payload=dict(form["payload"]),
                        assign_self=form["assign_self"],
                        idempotency_key="tg-activity:" + form["id"],
                        **target,
                        **(
                            {"responsible_user_id": UUID(form["responsible_user_id"])}
                            if form.get("responsible_user_id") else {}
                        ),
                    )
                form = {
                    **form, "phase": "done", "payload": {},
                    "activity_id": str(UUID(result["item"]["id"])),
                    "responsible_user_id": None, "responsible_name": None,
                    "responsible_ids": [],
                }
            elif form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            elif action == "cancel":
                await state.update_data(**{FORM: {}})
                await show(callback, state, None, common("cancelled", language), [
                    [activity_parent_button(form, language)],
                ])
                return
            elif action.startswith("r_") or phase == "responsibles":
                from handlers.construction.construction_activity_responsible import dispatch
                await dispatch(
                    callback, state, form, action,
                    bot=bot, event_update=event_update,
                )
                return
            elif action.startswith("type") and phase == "type":
                index = int(action[4:])
                if not 0 <= index < len(ACTIVITY_ORDER):
                    raise ValueError("Invalid activity type.")
                form["payload"] = {"type": ACTIVITY_ORDER[index]}
                form["phase"] = "zone" if form["timezone"] else "zoneinput"
            elif action == "use" and phase == "zone":
                form["phase"] = "due"
            elif action == "zone" and phase == "zone":
                form["phase"] = "zoneinput"
            elif action.startswith("fold") and phase == "fold":
                index = int(action[4:])
                if index not in (0, 1):
                    raise ValueError("Invalid time choice.")
                form["payload"] = {
                    **form["payload"], "due_at": form["choices"][index],
                    "timezone": form["timezone"],
                }
                form["phase"] = "who"
            elif action in {"mine", "none"} and phase == "who":
                form["assign_self"] = action == "mine"
                form["responsible_user_id"] = None
                form["responsible_name"] = None
                form["responsible_ids"] = []
                form["phase"] = "comment"
            elif action == "skip" and phase == "comment":
                form["payload"] = {**form["payload"], "comment": None}
                form["phase"] = "review"
            elif action == "edit" and phase == "review":
                form.update(
                    phase="type", payload={}, assign_self=False,
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


class ActivityInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message)
            or (message.text or "").startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form, context = data.get(FORM), data.get(CONTEXT, {})
        return (
            owns(form, message.from_user.id)
            and isinstance(context, dict)
            and context.get("telegram_user_id") == message.from_user.id
            and context.get("tenant_id") == form.get("tenant_id")
            and form.get("phase") in {"zoneinput", "due", "comment"}
            and form.get("menu_id") is not None
            and form["menu_id"] == data.get("last_menu_message_id")
        )


@router.message(ActivityInput())
async def activity_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ActivityInput()(message, state):
            return
        form = dict((await state.get_data())[FORM])
        language = form["language"]
        value = message.text
        if not isinstance(value, str) or not value.strip():
            await display(message, state, form, common("invalid", language))
            return
        value = value.strip()
        if form["phase"] == "zoneinput":
            try:
                ZoneInfo(value)
            except (KeyError, ValueError, TypeError):
                await display(message, state, form, tr("bad_zone", language))
                return
            form.update(timezone=value, phase="due")
        elif form["phase"] == "due":
            try:
                choices = local_deadlines(value, form["timezone"])
            except (ValueError, KeyError):
                await display(message, state, form, tr("bad_due", language))
                return
            if len(choices) == 2:
                form.update(choices=list(choices), phase="fold")
            else:
                form["payload"] = {
                    **form["payload"], "due_at": choices[0],
                    "timezone": form["timezone"],
                }
                form["phase"] = "who"
        else:
            form["payload"] = {**form["payload"], "comment": value}
            form["phase"] = "review"
        await state.update_data(**{FORM: form})
        await display(message, state, form)

# Direct client activity planning v1.
def activity_parent_button(form, language):
    if form.get("kind") == "project":
        from ui.construction_project_catalog import tr as catalog_tr
        return button(catalog_tr("title", language), "PL:open")
    if form.get("kind") == "client":
        from ui.construction_client_card import tr as card_tr
        return button(
            card_tr("summary", language), "CC:open:" + UUID(form["client_id"]).hex,
        )
    return button(common("open", language), "CS_LEAD:" + UUID(form["lead_id"]).hex)


async def start_client_activity(callback, state, data, token, bot, event_update):
    from handlers.construction.construction_client_card import VIEW as CARD
    context, source = data.get(CONTEXT), data.get(CARD)
    if (
        not isinstance(context, dict) or not isinstance(source, dict)
        or source.get("nonce") != token
        or source.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or source.get("tenant_id") != context.get("tenant_id")
        or source.get("menu_id") != callback.message.message_id
        or source.get("menu_id") != data.get("last_menu_message_id")
    ):
        raise ValueError("Client activity origin is stale.")
    tenant_id = UUID(source["tenant_id"])
    client_id = UUID(source["client_id"])
    actor_id = UUID(source["actor_user_id"])
    previous = data.get(FORM)
    resume = (
        isinstance(previous, dict)
        and previous.get("attempted_at") is not None
        and previous.get("phase") != "done"
    )
    if resume and (
        not owns(previous, callback.from_user.id)
        or previous.get("kind") != "client"
        or previous.get("tenant_id") != str(tenant_id)
        or previous.get("client_id") != str(client_id)
        or previous.get("actor_user_id") != str(actor_id)
    ):
        raise ValueError("Another activity request is unresolved.")
    saved = deepcopy({
        key: data.get(key) for key in (FORM, CONTEXT, CARD, "last_menu_message_id")
    })
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        prepared = await service.client_activity_form(
            telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
            client_id=client_id, expected_user_id=actor_id, resume=resume,
        )
    current = await state.get_data()
    if (
        any(current.get(key) != value for key, value in saved.items())
        or prepared["actor_user_id"] != str(actor_id)
        or prepared["client_id"] != str(client_id)
    ):
        raise ValueError("Client activity context changed.")
    if resume:
        form = deepcopy(previous)
    else:
        zone = prepared.get("timezone")
        try:
            ZoneInfo(zone)
        except (TypeError, ValueError, KeyError):
            zone = None
        form = {
            "id": uuid4().hex, "kind": "client",
            "telegram_user_id": callback.from_user.id,
            "tenant_id": str(tenant_id), "client_id": str(client_id),
            "lead_id": None, "actor_user_id": str(actor_id),
            "organization": prepared["organization"],
            "subject_name": prepared["subject_name"],
            "language": prepared["language"] or callback.from_user.language_code or "ru",
            "timezone": zone, "phase": "type", "payload": {},
            "assign_self": False, "attempted_at": None, "menu_id": None,
        }
    if form.get("attempted_at") is None:
        form["can_choose_responsible"] = prepared.get("can_choose_responsible") is True
    await suspend_lead_form(state)
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

# Direct project activity planning v1.
async def start_project_activity(callback, state, data, token, bot, event_update):
    from handlers.construction.construction_project_areas import (
        binding, PROJECT, FORM as PROJECT_FORM, CARD, CATALOG,
    )
    choices = [
        selected for key in (PROJECT, PROJECT_FORM, CATALOG)
        if (selected := binding(
            data, key=key, token=token,
            sender=callback.from_user.id, menu=callback.message.message_id,
        )) is not None
    ]
    if len(choices) != 1:
        raise ValueError("Project activity origin is stale or ambiguous.")
    selected = choices[0]
    tenant_id = UUID(selected["tenant_id"])
    project_id = UUID(selected["project_id"])
    actor_id = UUID(selected["actor_user_id"])
    previous = data.get(FORM)
    resume = (
        isinstance(previous, dict)
        and previous.get("attempted_at") is not None
        and previous.get("phase") != "done"
    )
    if resume and (
        not owns(previous, callback.from_user.id)
        or previous.get("kind") != "project"
        or previous.get("tenant_id") != str(tenant_id)
        or previous.get("project_id") != str(project_id)
        or previous.get("actor_user_id") != str(actor_id)
    ):
        raise ValueError("Another activity request is unresolved.")
    keys = (
        FORM, CONTEXT, PROJECT, PROJECT_FORM, CARD, CATALOG,
        "construction_project_catalog_view",
        "construction_client_catalog_view", "last_menu_message_id",
    )
    saved = deepcopy({key: data.get(key) for key in keys})
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        prepared = await service.project_activity_form(
            telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
            project_id=project_id, expected_user_id=actor_id, resume=resume,
        )
    current = await state.get_data()
    if (
        any(current.get(key) != value for key, value in saved.items())
        or prepared["actor_user_id"] != str(actor_id)
        or prepared["project_id"] != str(project_id)
    ):
        raise ValueError("Project activity context changed.")
    if resume:
        form = deepcopy(previous)
    else:
        zone = prepared.get("timezone")
        try:
            ZoneInfo(zone)
        except (TypeError, ValueError, KeyError):
            zone = None
        form = {
            "id": uuid4().hex, "kind": "project",
            "telegram_user_id": callback.from_user.id,
            "tenant_id": str(tenant_id), "project_id": str(project_id),
            "client_id": None, "lead_id": None,
            "actor_user_id": str(actor_id),
            "organization": prepared["organization"],
            "subject_name": prepared["subject_name"],
            "language": prepared["language"] or callback.from_user.language_code or "ru",
            "timezone": zone, "phase": "type", "payload": {},
            "assign_self": False, "attempted_at": None, "menu_id": None,
        }
    if form.get("attempted_at") is None:
        form["can_choose_responsible"] = prepared.get("can_choose_responsible") is True
    await suspend_lead_form(state)
    await state.update_data(**{FORM: form})
    await display(callback, state, form)
