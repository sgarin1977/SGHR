"""Lead conversion with explicit existing-project selection through API."""
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import (
    lock_for, owns, private, suspend_lead_form,
)
from handlers.construction.construction_activity_plan import suspend_activity_form
from handlers.construction.construction_activity_terminal import suspend_terminal_form
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_lead_convert")
logger = logging.getLogger(__name__)
FORM = "construction_lead_convert_form"
CONTEXT = "construction_suite_context"


def tr(key, language):
    return t("construction_lead_convert_" + key, language)


def common(key, language):
    return t("construction_lead_form_" + key, language)


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(form, action):
    return f'CV:{action}:{form["id"]}:{form["phase"]}'


def back_row(language, lead_id):
    return [button(common("open", language), "CS_LEAD:" + UUID(lead_id).hex)]


async def suspend_conversion_form(state):
    form = (await state.get_data()).get(FORM)
    if isinstance(form, dict):
        keep = form.get("phase") in {"pending", "done"}
        await state.update_data(**{
            FORM: {**form, "menu_id": None} if keep else {},
        })


async def show(event, state, form, text, rows):
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



def conversion_review(form):
    language, source = form["language"], form["source"]
    if form.get("project_id") is not None:
        client, project = form["client"], form["project"]
        return "\n\n".join([
            tr("review_project", language),
            tr("selected", language) + "\n" + client["display_name"],
            tr("object_name", language) + "\n" + project["name"],
            common("address_raw", language) + ":\n" + project["address_raw"],
        ])
    existing = form.get("client_id") is not None
    contact = form["client"] if existing else {
        "display_name": source["name"],
        "phone": source.get("phone"),
        "email": source.get("email"),
    }
    return "\n\n".join([
        tr("review_existing" if existing else "review", language),
        (
            tr("selected", language) if existing
            else tr(form["client_type"], language)
        ),
        contact["display_name"],
        common("phone", language).split("\n", 1)[0]
            + ":\n" + (contact.get("phone") or "—"),
        common("email", language) + ":\n" + (contact.get("email") or "—"),
        tr("name", language) + "\n" + form["project_name"],
        common("address_raw", language) + ":\n" + form["project_address_raw"],
        common("work_type_description", language)
            + ":\n" + source["work_type_description"],
        common("comment", language) + ":\n" + (source.get("comment") or "—"),
        tr("transfer_existing" if existing else "transfer", language),
    ])


async def search_clients(event, state, form, query, *, bot, event_update):
    try:
        async with construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.conversion_search(
                telegram_user_id=event.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                query=query,
            )
        # Old result buttons must not select an entry from a newer result list.
        form.update(
            id=uuid4().hex, phase="matches",
            matches=[dict(item) for item in result["items"]],
            candidate_client=None,
        )
        await state.update_data(**{FORM: form})
        await display(event, state, form)
    except Exception as error:
        await failure(event, state, form, form["language"], error)


async def load_projects(event, state, form, *, bot, event_update, cursor=None):
    async with construction_bot_api_entry(
        bot=bot, event_update=event_update,
    ) as service:
        result = await service.conversion_projects(
            telegram_user_id=event.from_user.id,
            tenant_id=UUID(form["tenant_id"]),
            expected_user_id=UUID(form["actor_user_id"]),
            client_id=UUID(form["client_id"]), cursor=cursor,
        )
    # A new nonce prevents old indexed buttons selecting a different page.
    form.update(
        id=uuid4().hex, phase="projects",
        project_id=None, project=None,
        project_items=[dict(item) for item in result["items"]],
        project_next_cursor=result["next_cursor"],
    )


async def display(event, state, form, notice=None):
    language, phase = form["language"], form["phase"]
    rows = []
    if phase == "done":
        text = tr("done", language)
        rows.append([button(
            t("construction_client_card_open", language),
            "CC:open:" + UUID(form["client_id"]).hex,
        )])
    elif phase == "conflict":
        text = tr("conflict", language)
    elif form["attempted_at"] is not None:
        text = tr("pending", language)
        if 0 <= time() - form["attempted_at"] < 86340:
            rows.append([button(common("retry", language), cb(form, "submit"))])
        else:
            text = tr("expired", language)
    elif phase == "mode":
        text = tr("mode", language)
        rows = [
            [button(tr("new_client", language), cb(form, "newclient"))],
            [button(tr("existing_client", language), cb(form, "existing"))],
        ]
    elif phase == "clientsearch":
        text = tr("search_prompt", language)
        if form["source"].get("address_raw"):
            rows.append([button(tr("search_saved", language), cb(form, "searchsaved"))])
        rows.append([button(tr("new_client", language), cb(form, "newclient"))])
    elif phase == "matches":
        text = tr("matches" if form["matches"] else "no_matches", language)
        for index, item in enumerate(form["matches"]):
            label = tr("match_" + item["entity_type"], language)
            caption = (label + " " + item["label"] + " · " + item["address_raw"])[:100]
            rows.append([button(caption, cb(form, "pick" + str(index)))])
        rows.extend([
            [button(tr("search_again", language), cb(form, "research"))],
            [button(tr("new_client", language), cb(form, "newclient"))],
        ])
    elif phase == "clientconfirm":
        client = form["candidate_client"]
        text = "\n\n".join([
            tr("selected", language),
            client["display_name"],
            common("phone", language).split("\n", 1)[0]
                + ":\n" + (client.get("phone") or "—"),
            common("email", language) + ":\n" + (client.get("email") or "—"),
            tr("client_unchanged", language),
        ])
        rows = [
            [button(tr("use_client", language), cb(form, "keepclient"))],
            [button(tr("existing_project", language), cb(form, "objects"))],
            [button(tr("search_again", language), cb(form, "research"))],
        ]
    elif phase == "projects":
        text = (
            tr("projects_prompt" if form["project_items"] else "projects_empty", language)
            + "\n\n" + form["client"]["display_name"]
        )
        for index, item in enumerate(form["project_items"]):
            caption = (item["name"] + " · " + item["address_raw"])[:100]
            rows.append([button(caption, cb(form, "proj" + str(index)))])
        if form.get("project_next_cursor") is not None:
            rows.append([button(tr("projects_next", language), cb(form, "nextprojects"))])
        rows.extend([
            [button(tr("projects_first", language), cb(form, "firstprojects"))],
            [button(tr("new_project", language), cb(form, "newproject"))],
        ])
    elif phase == "type":
        text = tr("type", language)
        rows = [
            [button(tr("person", language), cb(form, "person"))],
            [button(tr("company", language), cb(form, "company"))],
        ]
    elif phase == "name":
        text = tr("name", language)
    elif phase == "address":
        text = tr("address_saved", language) + "\n\n" + form["source"]["address_raw"]
        rows = [
            [button(tr("use_address", language), cb(form, "use"))],
            [button(tr("change_address", language), cb(form, "address"))],
        ]
    elif phase == "addressinput":
        text = tr("address_input", language)
    elif phase == "review":
        text = conversion_review(form)
        rows = [
            [button(tr("confirm", language), cb(form, "submit"))],
            [button(common("edit", language), cb(form, "edit"))],
        ]
    else:
        raise ValueError("Unknown conversion form phase.")

    if form["attempted_at"] is None and phase not in {"done", "conflict"}:
        rows.append([button(common("cancel", language), cb(form, "abort"))])
    rows.append(back_row(language, form["lead_id"]))
    title = (
        tr("title", language) + "\n" + form["organization"]
        + "\n" + str(form["lead_name"])[:350]
    )
    if notice:
        text = notice + "\n\n" + text
    await show(event, state, form, title + "\n\n" + text, rows)


async def failure(event, state, form, language, error):
    logger.warning(
        "construction_bot_lead_convert_failed error_type=%s status=%s code=%s",
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
            [button(t("construction_bot_leads_btn", language), "CS_LEADS")],
        ])


@router.callback_query(F.data.startswith("CV:"))
async def conversion_callback(callback, state, bot=None, event_update=None):
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

            if len(parts) == 3 and parts[1] == "new":
                tenant_id, lead_id = UUID(context["tenant_id"]), UUID(parts[2])
                if owns(existing, callback.from_user.id):
                    same = (
                        existing.get("tenant_id") == str(tenant_id)
                        and existing.get("lead_id") == str(lead_id)
                    )
                    if (
                        existing.get("phase") == "pending"
                        or (same and existing.get("phase") != "conflict")
                    ):
                        form = dict(existing)
                        await state.update_data(**{
                            CONTEXT: {**context, "tenant_id": form["tenant_id"]},
                        })
                        await display(callback, state, form)
                        return

                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    prepared = await service.conversion_form(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=tenant_id, lead_id=lead_id,
                    )
                source = prepared["item"]
                language = prepared["language"] or language
                if (
                    source["id"] != str(lead_id)
                    or source["tenant_id"] != str(tenant_id)
                    or source["status"] not in CONSTRUCTION_LEAD_STATUSES
                    or type(source["row_version"]) is not int
                    or not 0 < source["row_version"] < 2**63
                ):
                    raise ValueError("Invalid conversion source.")
                if source["status"] in {"won", "lost"}:
                    await show(callback, state, None, tr("closed", language), [
                        back_row(language, str(lead_id)),
                    ])
                    return
                if (
                    source["converted_client_id"] is not None
                    or source["converted_project_id"] is not None
                ):
                    raise ValueError("Unexpected existing conversion links.")

                form = {
                    "id": uuid4().hex,
                    "telegram_user_id": callback.from_user.id,
                    "tenant_id": str(tenant_id), "lead_id": str(lead_id),
                    "actor_user_id": prepared["actor_user_id"],
                    "organization": prepared["organization"],
                    "language": language, "lead_name": source["name"],
                    "source": dict(source), "version": source["row_version"],
                    "phase": "mode", "attempted_at": None, "menu_id": None,
                }
                await suspend_lead_form(state)
                await suspend_activity_form(state)
                await suspend_terminal_form(state)
                await state.update_data(**{FORM: form})
                await display(callback, state, form)
                return

            if not owns(existing, callback.from_user.id):
                raise ValueError("Conversion form unavailable.")
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
                    form["command"] = {
                        "expected_row_version": form["version"],
                        "client_id": form.get("client_id"),
                        "project_id": form.get("project_id"),
                        "client_type": (
                            None if form.get("client_id") else form["client_type"]
                        ),
                        "project_name": (
                            None if form.get("project_id") else form["project_name"]
                        ),
                        "project_address_raw": (
                            None if form.get("project_id") else form["project_address_raw"]
                        ),
                    }
                    form["attempted_at"] = time()
                form["phase"] = "pending"
                await state.update_data(**{FORM: form})

                try:
                    async with construction_bot_api_entry(
                        bot=bot, event_update=event_update,
                    ) as service:
                        response = await service.convert_lead(
                            telegram_user_id=callback.from_user.id,
                            tenant_id=UUID(form["tenant_id"]),
                            lead_id=UUID(form["lead_id"]),
                            expected_user_id=UUID(form["actor_user_id"]),
                            payload=dict(form["command"]),
                            idempotency_key="tg-lead-convert:" + form["id"],
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

                item = response["item"]
                if item["lead_id"] != form["lead_id"] or item["status"] != "won":
                    raise ValueError("Conversion receipt differs.")
                form = {
                    **form, "phase": "done",
                    "source": {}, "command": {},
                    "client": None, "candidate_client": None, "matches": [],
                    "project": None, "project_items": [], "project_next_cursor": None,
                    "client_id": str(UUID(item["client_id"])),
                    "project_id": str(UUID(item["project_id"])),
                    "receipt_version": item["row_version"],
                }
            elif form["attempted_at"] is not None:
                await display(callback, state, form)
                return
            elif action == "abort":
                await state.update_data(**{FORM: {}})
                await show(callback, state, None, common("cancelled", language), [
                    back_row(language, form["lead_id"]),
                ])
                return
            elif action == "newclient" and phase in {
                "mode", "matches", "clientconfirm", "clientsearch",
            }:
                form.update(
                    phase="type", client_id=None, client=None,
                    candidate_client=None, matches=[],
                    project_id=None, project=None,
                )
            elif action == "existing" and phase == "mode":
                form.update(
                    phase="clientsearch", client_id=None, client=None, matches=[],
                    project_id=None, project=None,
                )
            elif action == "searchsaved" and phase == "clientsearch":
                query = form["source"].get("address_raw")
                if not isinstance(query, str) or not query.strip():
                    raise ValueError("Saved address unavailable.")
                await search_clients(
                    callback, state, form, query,
                    bot=bot, event_update=event_update,
                )
                return
            elif action == "research" and phase in {"matches", "clientconfirm"}:
                form.update(
                    phase="clientsearch", client_id=None, client=None,
                    candidate_client=None, matches=[],
                    project_id=None, project=None,
                )
            elif action.startswith("pick") and phase == "matches":
                index = int(action[4:])
                if not 0 <= index < len(form["matches"]) or str(index) != action[4:]:
                    raise ValueError("Invalid candidate index.")
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    selected = await service.select_conversion_client(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        candidate=dict(form["matches"][index]),
                    )
                form.update(
                    phase="clientconfirm", candidate_client=dict(selected["client"]),
                )
            elif action == "keepclient" and phase == "clientconfirm":
                client = form["candidate_client"]
                if client["tenant_id"] != form["tenant_id"]:
                    raise ValueError("Client context differs.")
                form.update(
                    phase="name", client_id=str(UUID(client["id"])),
                    client=dict(client), client_type=None,
                    project_id=None, project=None,
                )
            elif action == "objects" and phase == "clientconfirm":
                client = form["candidate_client"]
                if client["tenant_id"] != form["tenant_id"]:
                    raise ValueError("Client context differs.")
                form.update(
                    client_id=str(UUID(client["id"])), client=dict(client),
                    client_type=None, project_id=None, project=None,
                )
                await load_projects(
                    callback, state, form, bot=bot, event_update=event_update,
                )
            elif action in {"nextprojects", "firstprojects"} and phase == "projects":
                cursor = (
                    form.get("project_next_cursor") if action == "nextprojects"
                    else None
                )
                if action == "nextprojects" and cursor is None:
                    raise ValueError("No next project page.")
                await load_projects(
                    callback, state, form, bot=bot, event_update=event_update,
                    cursor=cursor,
                )
            elif action == "newproject" and phase == "projects":
                form.update(phase="name", project_id=None, project=None)
            elif action.startswith("proj") and phase == "projects":
                index = int(action[4:])
                items = form["project_items"]
                if not 0 <= index < len(items) or str(index) != action[4:]:
                    raise ValueError("Invalid project index.")
                selected_id = UUID(items[index]["id"])
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.select_conversion_project(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(form["tenant_id"]),
                        expected_user_id=UUID(form["actor_user_id"]),
                        client_id=UUID(form["client_id"]),
                        project_id=selected_id,
                    )
                project = result["project"]
                if (
                    project["id"] != str(selected_id)
                    or project["client_id"] != form["client_id"]
                    or project["tenant_id"] != form["tenant_id"]
                ):
                    raise ValueError("Selected project context differs.")
                form.update(
                    phase="review", project_id=str(selected_id),
                    project=dict(project),
                )
            elif action in {"person", "company"} and phase in {"type", "mode"}:
                # Accept the prior form's valid new-client selection as well.
                form.update(
                    client_id=None, client=None,
                    client_type=action, phase="name",
                    project_id=None, project=None,
                )
            elif action == "use" and phase == "address":
                form.update(
                    project_address_raw=form["source"]["address_raw"],
                    phase="review",
                )
            elif action == "address" and phase == "address":
                form["phase"] = "addressinput"
            elif action == "edit" and phase == "review":
                form.update(
                    phase="mode", project_id=None, project=None,
                    project_items=[], project_next_cursor=None,
                )
            else:
                await callback.message.answer(common("stale", language))
                return
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            await failure(callback, state, form, language, error)


class ConversionInput(BaseFilter):
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
            and form.get("phase") in {"name", "addressinput", "clientsearch"}
            and form.get("menu_id") is not None
            and form["menu_id"] == data.get("last_menu_message_id")
        )


@router.message(ConversionInput())
async def conversion_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ConversionInput()(message, state):
            return
        form = dict((await state.get_data())[FORM])
        value = message.text
        if not isinstance(value, str) or not value.strip():
            await display(
                message, state, form,
                notice=common("invalid", form["language"]),
            )
            return
        if form["phase"] == "clientsearch":
            await search_clients(
                message, state, form, value.strip(),
                bot=bot, event_update=event_update,
            )
            return
        if form["phase"] == "name":
            form["project_name"] = value.strip()
            form["phase"] = (
                "address" if form["source"].get("address_raw")
                else "addressinput"
            )
        else:
            form.update(project_address_raw=value.strip(), phase="review")
        await state.update_data(**{FORM: form})
        await display(message, state, form)
