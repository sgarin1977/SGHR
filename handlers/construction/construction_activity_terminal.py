"""Complete/cancel the selected next activity through approved API routes."""
import logging
from copy import deepcopy
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import (
    lock_for, owns, private, suspend_lead_form,
)
from handlers.construction.construction_activity_plan import suspend_activity_form
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_api_client import ConstructionApiClientError
from ui.construction_activity import activity_label, display_deadline
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_activity_terminal")
logger = logging.getLogger(__name__)
FORM = "construction_activity_terminal_form"
TARGET = "construction_activity_terminal_target"
CONTEXT = "construction_suite_context"


def tr(key, language):
    return t("construction_activity_terminal_" + key, language)


def common(key, language):
    return t("construction_lead_form_" + key, language)


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(form, action):
    return f'AT:{action}:{form["id"]}:{form["phase"]}'


async def next_action_buttons(state, result, *, telegram_user_id, tenant_id):
    item = result["item"]
    action = item.get("next_action")
    if action is None:
        await state.update_data(**{TARGET: {}})
        return []
    lead_id = UUID(item["id"])
    activity_id = UUID(action["id"])
    version = action["row_version"]
    if (
        action["tenant_id"] != str(tenant_id)
        or action["lead_id"] != str(lead_id)
        or action["status"] != "planned"
        or type(version) is not int
        or not 0 < version < 2**63
    ):
        raise ValueError("Invalid next activity projection.")
    language = result.get("language") or "ru"
    target = {
        "id": uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "tenant_id": str(tenant_id), "lead_id": str(lead_id),
        "activity_id": str(activity_id), "version": version,
        "type": action["type"], "due_at": action["due_at"],
        "timezone": action["timezone"],
        "lead_name": item["name"], "organization": result["organization"],
    }
    await state.update_data(**{TARGET: target})
    return [
        [button(tr("complete", language), "AT:start:" + target["id"] + ":complete")],
        [button(tr("cancel", language), "AT:start:" + target["id"] + ":cancel")],
    ]


async def suspend_terminal_form(state):
    data = await state.get_data()
    form = data.get(FORM)
    if isinstance(form, dict):
        keep = form.get("phase") in {"pending", "done"}
        await state.update_data(**{
            FORM: {**form, "menu_id": None} if keep else {},
        })


async def show(event, state, form, text, rows):
    if isinstance(form, dict) and form.get("kind") == "project":
        from handlers.construction.construction_project_activity_terminal import render
        return await render(event, state, form, text, rows)
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
    if form is not None:
        form["menu_id"] = sent.message_id
        await state.update_data(**{FORM: form})
    await state.update_data(last_menu_message_id=sent.message_id)


def back_row(language, lead_id):
    return [button(common("open", language), "CS_LEAD:" + UUID(lead_id).hex)]


async def display(event, state, form, notice=None):
    language = form["language"]
    phase = form["phase"]
    rows = []
    if phase == "done":
        text = tr("completed" if form["operation"] == "complete" else "cancelled", language)
    elif phase == "conflict":
        text = tr("conflict", language)
    elif form["attempted_at"] is not None:
        text = tr("pending", language)
        if 0 <= time() - form["attempted_at"] < 86340:
            rows.append([button(common("retry", language), cb(form, "submit"))])
        else:
            text = t("construction_activity_form_expired", language)
    elif phase == "choose":
        from ui.construction_client_card import tr as card_tr
        text = card_tr("activity_choose", language)
        rows = [
            [button(tr("complete", language), cb(form, "complete"))],
            [button(tr("cancel", language), cb(form, "cancel"))],
        ]
    elif phase == "result":
        text = tr("result", language)
        rows.append([button(common("skip", language), cb(form, "skip"))])
    elif phase == "review":
        text = (
            tr("review_complete" if form["operation"] == "complete" else "review_cancel", language)
            + "\n\n"
            + (
                form["result"]
                if form["result_provided"]
                else tr("keep_result", language)
            )
        )
        rows = [
            [button(tr("confirm", language), cb(form, "submit"))],
            [button(common("edit", language), cb(form, "edit"))],
        ]
    else:
        raise ValueError("Invalid terminal form phase.")

    if form["attempted_at"] is None:
        rows.append([button(tr("abort", language), cb(form, "abort"))])
    rows.append(terminal_parent_row(language, form))
    title = "\n".join([
        terminal_heading(form, language),
        form["organization"],
        str(form.get("subject_name") or form.get("lead_name", ""))[:350],
        activity_label(form["type"], language),
        display_deadline(form["due_at"], form["timezone"])
            + " · " + form["timezone"],
    ])
    if notice:
        text = notice + "\n\n" + text
    await show(event, state, form, title + "\n\n" + text, rows)


async def failure(event, state, form, language, error):
    logger.warning(
        "construction_bot_activity_terminal_failed error_type=%s status=%s code=%s",
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


@router.callback_query(F.data.startswith("AT:"))
async def terminal_callback(callback, state, bot=None, event_update=None):
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

            if len(parts) == 4 and parts[1] == "project":
                from handlers.construction.construction_project_activity_terminal import select
                await select(
                    callback, state, data, parts[2], parts[3], bot, event_update,
                )
                return
            if len(parts) == 3 and parts[1] == "project-resume":
                from handlers.construction.construction_project_activity_terminal import resume
                await resume(callback, state, data, parts[2])
                return

            if len(parts) == 4 and parts[1] == "client":
                await select_client_activity(
                    callback, state, data, parts[2], parts[3], bot, event_update,
                )
                return
            if len(parts) == 3 and parts[1] == "resume":
                await resume_client_terminal(callback, state, data, parts[2])
                return

            if len(parts) == 4 and parts[1] == "start":
                operation = parts[3]
                if operation not in {"complete", "cancel"}:
                    raise ValueError("Invalid operation.")

                if owns(existing, callback.from_user.id):
                    if existing.get("phase") == "pending":
                        form = dict(existing)
                        await state.update_data(**{
                            CONTEXT: {**context, "tenant_id": form["tenant_id"]},
                        })
                        await display(callback, state, form)
                        return
                    if (
                        existing.get("phase") == "done"
                        and existing.get("source_id") == parts[2]
                        and existing.get("operation") == operation
                    ):
                        await display(callback, state, dict(existing))
                        return

                target = data.get(TARGET)
                if (
                    not owns(target, callback.from_user.id)
                    or target.get("id") != parts[2]
                    or target.get("tenant_id") != context.get("tenant_id")
                ):
                    raise ValueError("Selected activity unavailable.")

                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    prepared = await service.activity_terminal_context(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(target["tenant_id"]),
                        lead_id=UUID(target["lead_id"]),
                    )
                current = prepared["next_action"]
                if (
                    current is None
                    or current["id"] != target["activity_id"]
                    or current["row_version"] != target["version"]
                    or current["status"] != "planned"
                ):
                    await show(callback, state, None, tr("conflict", language), [
                        back_row(language, target["lead_id"]),
                    ])
                    return

                form = {
                    **target,
                    "source_id": target["id"],
                    "id": uuid4().hex,
                    "actor_user_id": prepared["actor_user_id"],
                    "language": prepared["language"] or language,
                    "operation": operation, "phase": "result",
                    "result_provided": False, "result": None,
                    "attempted_at": None, "menu_id": None,
                }
                await suspend_lead_form(state)
                await suspend_activity_form(state)
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
                return

            if not owns(existing, callback.from_user.id):
                raise ValueError("Terminal form unavailable.")
            form = dict(existing)
            language = form["language"]
            if form.get("kind") == "project":
                from handlers.construction.construction_project_activity_terminal import valid
                if not valid(data, form, callback):
                    return
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
            if phase in {"done", "conflict"}:
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
                    command = {"expected_row_version": form["version"]}
                    if form["result_provided"]:
                        command["result"] = form["result"]
                    form["command"] = command
                    form["attempted_at"] = time()
                form["phase"] = "pending"
                # Freeze identity, activity, operation, version and body before POST.
                await state.update_data(**{FORM: form})
                try:
                    async with construction_bot_api_entry(
                        bot=bot, event_update=event_update,
                    ) as service:
                        if form.get("kind") == "project":
                            execute = service.terminal_project_activity
                            target = {
                                "project_id": UUID(form["project_id"]),
                                "expected_links": dict(form["expected_links"]),
                            }
                        elif form.get("kind") == "client":
                            execute = service.terminal_client_activity
                            target = {
                                "client_id": UUID(form["client_id"]),
                                "expected_links": dict(form["expected_links"]),
                            }
                        else:
                            execute = service.terminal_lead_activity
                            target = {"lead_id": UUID(form["lead_id"])}
                        response = await execute(
                            telegram_user_id=callback.from_user.id,
                            tenant_id=UUID(form["tenant_id"]),
                            activity_id=UUID(form["activity_id"]),
                            expected_user_id=UUID(form["actor_user_id"]),
                            operation=form["operation"],
                            payload=dict(form["command"]),
                            idempotency_key="tg-activity-terminal:" + form["id"],
                            **target,
                        )
                except ConstructionApiClientError as error:
                    if error.status_code == 409 and error.code in {
                        "ROW_VERSION_CONFLICT", "conflict",
                    }:
                        form["phase"] = "conflict"
                        await state.update_data(**{FORM: form})
                        await display(callback, state, form)
                        return
                    raise

                if form.get("kind") == "project":
                    from handlers.construction.construction_project_activity_terminal import valid
                    if not valid(await state.get_data(), form, callback):
                        return
                item = response["item"]
                expected_status = (
                    "completed" if form["operation"] == "complete" else "cancelled"
                )
                if item["id"] != form["activity_id"] or item["status"] != expected_status:
                    raise ValueError("Terminal response differs.")
                form = {
                    **form, "phase": "done",
                    "result": None, "command": {},
                    "receipt_version": item["row_version"],
                }
            elif form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            elif action in ("complete", "cancel") and phase == "choose":
                form.update(operation=action, phase="result")
            elif action == "abort":
                if form.get("kind") == "project":
                    await show(callback, state, form, tr("unchanged", language), [
                        terminal_parent_row(language, form),
                    ])
                    await state.update_data(**{FORM: {}})
                else:
                    await state.update_data(**{FORM: {}})
                    await show(callback, state, None, tr("unchanged", language), [
                        terminal_parent_row(language, form),
                    ])
                return
            elif action == "skip" and phase == "result":
                form.update(
                    phase="review", result_provided=False, result=None,
                )
            elif action == "edit" and phase == "review":
                form["phase"] = "result"
            else:
                await callback.message.answer(common("stale", language))
                return
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            await failure(callback, state, form, language, error)


class ResultInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message)
            or (message.text or "").startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form, context = data.get(FORM), data.get(CONTEXT, {})
        if isinstance(form, dict) and form.get("kind") == "project":
            from handlers.construction.construction_project_activity_terminal import valid
            if not valid(data, form, message):
                return False
        return (
            owns(form, message.from_user.id)
            and isinstance(context, dict)
            and context.get("telegram_user_id") == message.from_user.id
            and context.get("tenant_id") == form.get("tenant_id")
            and form.get("phase") == "result"
            and form.get("menu_id") is not None
            and form["menu_id"] == data.get("last_menu_message_id")
        )


@router.message(ResultInput())
async def terminal_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ResultInput()(message, state):
            return
        form = dict((await state.get_data())[FORM])
        value = message.text
        if not isinstance(value, str) or not value.strip():
            await display(
                message, state, form,
                notice=common("invalid", form["language"]),
            )
            return
        form.update(
            phase="review", result_provided=True, result=value.strip(),
        )
        await state.update_data(**{FORM: form})
        await display(message, state, form)

# Client planned activity terminal controls v1.
def terminal_heading(form, language):
    if form["phase"] == "choose":
        from ui.construction_client_card import tr as card_tr
        return card_tr("activity_action", language)
    return tr("complete" if form["operation"] == "complete" else "cancel", language)


def terminal_parent_row(language, form):
    if form.get("kind") == "project":
        from ui.construction_project_activities import tr as project_tr
        return [button(
            project_tr("title", language),
            "PAV:refresh:" + form["project_page_nonce"],
        )]
    if form.get("kind") == "client":
        from ui.construction_client_card import tr as card_tr
        return [button(
            card_tr("summary", language), "CC:open:" + UUID(form["client_id"]).hex,
        )]
    return back_row(language, form["lead_id"])


def pending_client_terminal(form, view):
    return (
        isinstance(form, dict) and form.get("kind") == "client"
        and form.get("phase") == "pending"
        and form.get("attempted_at") is not None
        and all(form.get(key) == view.get(key) for key in (
            "telegram_user_id", "tenant_id", "client_id", "actor_user_id",
        ))
    )


def terminal_card_source(data, callback, token):
    from handlers.construction.construction_client_card import VIEW as CARD
    view, context = data.get(CARD), data.get(CONTEXT)
    if (
        not isinstance(view, dict) or not isinstance(context, dict)
        or view.get("section") != "activities" or view.get("nonce") != token
        or view.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or view.get("tenant_id") != context.get("tenant_id")
        or view.get("menu_id") != callback.message.message_id
        or view.get("menu_id") != data.get("last_menu_message_id")
    ):
        raise ValueError("Client activity page is stale.")
    return view


async def resume_client_terminal(callback, state, data, token):
    view = terminal_card_source(data, callback, token)
    form = data.get(FORM)
    if not pending_client_terminal(form, view):
        raise ValueError("Pending client activity context differs.")
    await display(callback, state, deepcopy(form))


async def select_client_activity(callback, state, data, token, selector, bot, event_update):
    from handlers.construction.construction_client_card import VIEW as CARD
    view = terminal_card_source(data, callback, token)
    ids = view.get("activity_ids", [])
    index = int(selector)
    if str(index) != selector or not 0 <= index < len(ids):
        raise ValueError("Invalid client activity selector.")
    activity_id = UUID(ids[index])
    previous = data.get(FORM)
    if isinstance(previous, dict) and previous.get("phase") == "pending":
        if (
            pending_client_terminal(previous, view)
            and previous.get("activity_id") == str(activity_id)
        ):
            await display(callback, state, deepcopy(previous))
            return
        raise ValueError("Another terminal activity request is unresolved.")
    saved = deepcopy({
        key: data.get(key) for key in (FORM, CARD, CONTEXT, "last_menu_message_id")
    })
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        prepared = await service.client_activity_terminal_context(
            telegram_user_id=callback.from_user.id,
            tenant_id=UUID(view["tenant_id"]), client_id=UUID(view["client_id"]),
            activity_id=activity_id, expected_user_id=UUID(view["actor_user_id"]),
            cursors=dict(view["positions"]),
        )
    current = await state.get_data()
    action = prepared["activity"]
    if (
        any(current.get(key) != value for key, value in saved.items())
        or prepared["actor_user_id"] != view["actor_user_id"]
        or prepared["client_id"] != view["client_id"]
        or action["id"] != str(activity_id)
        or action["tenant_id"] != view["tenant_id"]
        or action["status"] != "planned"
        or type(action["row_version"]) is not int
        or not 0 < action["row_version"] < 2**63
    ):
        raise ValueError("Client activity selection changed.")
    links = {
        key: str(UUID(action[key])) if action[key] is not None else None
        for key in ("lead_id", "client_id", "project_id")
    }
    if not any(value is not None for value in links.values()):
        raise ValueError("Activity relations are missing.")
    form = {
        "id": uuid4().hex, "source_id": uuid4().hex, "kind": "client",
        "telegram_user_id": callback.from_user.id,
        "tenant_id": view["tenant_id"], "client_id": view["client_id"],
        "actor_user_id": view["actor_user_id"],
        "activity_id": str(activity_id), "version": action["row_version"],
        "expected_links": links, "type": action["type"],
        "due_at": action["due_at"], "timezone": action["timezone"],
        "organization": prepared["organization"],
        "subject_name": prepared["subject_name"],
        "language": prepared["language"] or callback.from_user.language_code or "ru",
        "operation": None, "phase": "choose",
        "result_provided": False, "result": None,
        "attempted_at": None, "menu_id": None,
    }
    await suspend_lead_form(state)
    await suspend_activity_form(state)
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

# project_activity_terminal_v1
