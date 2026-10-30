"""Read-only client card through the normal authenticated API."""
import logging
from copy import deepcopy
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private, suspend_lead_form
from handlers.construction.construction_activity_plan import suspend_activity_form
from handlers.construction.construction_activity_terminal import suspend_terminal_form
from handlers.construction.construction_lead_convert import suspend_conversion_form
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_client_card import SECTIONS, card_text, tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_client_card")
logger = logging.getLogger(__name__)
VIEW = "construction_client_card_view"
CONTEXT = "construction_suite_context"

def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)

def cb(view, action):
    return "CC:" + action + ":" + view["nonce"]

def back_rows(language):
    return [
        [button(t("construction_bot_leads_btn", language), "CS_LEADS")],
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
        [button(t("all_services_suites_btn", language), "M_SUITES")],
    ]

async def show(callback, state, text, rows, view=None):
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if view is not None:
        view["menu_id"] = sent.message_id
    values = {
        VIEW: view or {},
        "last_menu_message_id": sent.message_id,
    }
    from handlers.construction.construction_client_catalog import sync_origin
    values.update(sync_origin(await state.get_data(), view, sent.message_id))
    await state.update_data(**values)

def rows_for(view, result, language):
    rows = [
        [button(tr(section, language), cb(view, section))]
        for section in SECTIONS
    ]
    section = view["section"]
    if section in ("summary", "projects") and result.get("can_create_project") is True:
        rows.append([button(
            t("construction_project_form_button", language),
            "PF:client:" + view["nonce"],
        )])
    if section != "summary":
        if view["next_cursors"].get(section) is not None:
            rows.append([button(
                t("construction_bot_next_btn", language), cb(view, "next"),
            )])
        rows.append([button(tr("first", language), cb(view, "first"))])
        rows.append([button(tr("summary", language), cb(view, "summary"))])
    if section == "projects":
        for index, item in enumerate(result["item"]["projects"]):
            rows.append([button(
                str(item["name"])[:70],
                "PC:open:" + view["nonce"] + ":" + str(index),
            )])
    if section == "activities":
        from ui.construction_activity import activity_label, display_deadline
        if result.get("pending_client_terminal") is True:
            rows.append([button(
                tr("activity_resume", language), "AT:resume:" + view["nonce"],
            )])
        for index, item in enumerate(result["item"]["activities"]):
            title = (
                str(index + 1) + ". " + activity_label(item["type"], language)
                + " · " + display_deadline(item["due_at"], item["timezone"])
            )
            rows.append([button(
                title[:90], "AT:client:" + view["nonce"] + ":" + str(index),
            )])
    if section == "leads":
        for item in result["item"]["leads"]:
            rows.append([button(
                str(item["name"])[:70],
                "CS_LEAD:" + UUID(item["id"]).hex,
            )])
    if section == "summary":
        from ui.construction_client_edit import tr as edit_tr
        rows.append([button(edit_tr("edit", language), "CDE:open:" + view["nonce"])])
        rows.append([button(edit_tr("archive", language), "CDE:archive:" + view["nonce"])])
        rows.append([button(tr("identity", language), cb(view, "identity"))])
        from ui.construction_client_link_operator import tr as operator_tr
        rows.append([button(
            operator_tr("open", language), "CLO:open:" + view["nonce"],
        )])
    if section in ("summary", "activities"):
        rows.append([button(tr("plan_activity", language), "AP:client:" + view["nonce"])])
    rows.append([button(tr("refresh", language), cb(view, "refresh"))])
    from ui.construction_client_catalog import tr as catalog_tr
    rows.append([button(
        catalog_tr("back" if view.get("catalog_nonce") else "title", language),
        "CL:back:" + view["catalog_nonce"] if view.get("catalog_nonce") else "CL:open",
    )])
    rows.extend(back_rows(language))
    return rows

@router.callback_query(F.data.startswith("CC:"))
async def card_callback(callback, state, bot=None, event_update=None):
    language = (callback.from_user.language_code or "ru").lower()
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            data = await state.get_data()
            context = data.get(CONTEXT, {})
            if (
                not isinstance(context, dict)
                or context.get("telegram_user_id") != callback.from_user.id
            ):
                raise ValueError("Construction context unavailable.")
            tenant_id = UUID(context["tenant_id"])
            parts = callback.data.split(":")
            if len(parts) not in (3, 4):
                raise ValueError("Invalid card command.")
            if parts[1] in ("open", "catalog"):
                origin = None
                if parts[1] == "catalog":
                    if len(parts) != 4:
                        raise ValueError("Invalid client catalog selection.")
                    from handlers.construction.construction_client_catalog import select_client
                    origin = select_client(
                        data, token=parts[2], index=parts[3],
                        sender=callback.from_user.id, menu=callback.message.message_id,
                    )
                    client_id = UUID(origin["client_id"])
                    expected_user_id = UUID(origin["actor_user_id"])
                else:
                    if len(parts) != 3:
                        raise ValueError("Invalid client card selection.")
                    client_id = UUID(parts[2])
                    expected_user_id = None
                view = {
                    "telegram_user_id": callback.from_user.id,
                    "tenant_id": str(tenant_id), "client_id": str(client_id),
                    "positions": {section: None for section in SECTIONS},
                    "section": "summary",
                }
                if origin is not None:
                    view["catalog_nonce"] = origin["catalog_nonce"]
                await suspend_lead_form(state)
                await suspend_activity_form(state)
                await suspend_terminal_form(state)
                await suspend_conversion_form(state)
                from handlers.construction.construction_project_create import suspend_project_form
                await suspend_project_form(state)
                from handlers.construction.construction_client_create import suspend_client_form
                await suspend_client_form(state)
            else:
                if len(parts) != 3:
                    raise ValueError("Invalid client card command.")
                stored = data.get(VIEW)
                if (
                    not isinstance(stored, dict)
                    or stored.get("telegram_user_id") != callback.from_user.id
                    or stored.get("tenant_id") != str(tenant_id)
                    or stored.get("nonce") != parts[2]
                    or stored.get("menu_id") != data.get("last_menu_message_id")
                    or stored.get("menu_id") != callback.message.message_id
                ):
                    await callback.message.answer(t("construction_lead_form_stale", language))
                    return
                view = {
                    **stored,
                    "positions": dict(stored["positions"]),
                }
                client_id = UUID(view["client_id"])
                expected_user_id = UUID(view["actor_user_id"])
                action = parts[1]
                if action in ("identity_unlink", "identity_unlink_submit", "identity_own"):
                    await client_unlink_action(
                        callback, state, view, action, bot, event_update,
                    )
                    return
                if action == "identity_submit":
                    await submit_self_identity(
                        callback, state, view, bot, event_update,
                    )
                    return
                if action in (*SECTIONS, "summary", "identity", "identity_review"):
                    view["section"] = action
                elif action == "refresh":
                    view["positions"] = {section: None for section in SECTIONS}
                    if view["section"].startswith("identity"):
                        view["section"] = "identity"
                elif action in ("next", "first") and view["section"] in SECTIONS:
                    section = view["section"]
                    cursor = view["next_cursors"].get(section) if action == "next" else None
                    if action == "next" and cursor is None:
                        raise ValueError("Next card page unavailable.")
                    view["positions"][section] = cursor
                else:
                    raise ValueError("Unknown card action.")

            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await service.client_card(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, client_id=client_id,
                    cursors=dict(view["positions"]),
                    expected_user_id=expected_user_id,
                )
            current = (await state.get_data()).get(CONTEXT, {})
            if (
                not isinstance(current, dict)
                or current.get("telegram_user_id") != callback.from_user.id
                or current.get("tenant_id") != str(tenant_id)
                or result["item"]["id"] != str(client_id)
                or result["item"]["tenant_id"] != str(tenant_id)
                or (
                    expected_user_id is not None
                    and result["actor_user_id"] != str(expected_user_id)
                )
            ):
                raise ValueError("Client card context changed.")
            if view.get("catalog_nonce"):
                from handlers.construction.construction_client_catalog import sync_origin
                sync_origin(
                    await state.get_data(),
                    {**view, "actor_user_id": result["actor_user_id"]},
                    callback.message.message_id,
                )
            language = result["language"] or language
            # Store navigation metadata only, never card or history payloads.
            view.update(
                actor_user_id=str(UUID(result["actor_user_id"])),
                nonce=uuid4().hex,
                next_cursors=dict(result["next_cursors"]),
                project_ids=(
                    [item["id"] for item in result["item"]["projects"]]
                    if view["section"] == "projects" else []
                ),
                activity_ids=(
                    [item["id"] for item in result["item"]["activities"]]
                    if view["section"] == "activities" else []
                ),
            )
            if view["section"] == "activities":
                from handlers.construction.construction_activity_terminal import (
                    FORM as TERMINAL_FORM, pending_client_terminal,
                )
                result = {
                    **result,
                    "pending_client_terminal": pending_client_terminal(
                        (await state.get_data()).get(TERMINAL_FORM), view,
                    ),
                }
            if view["section"] in ("identity", "identity_review"):
                await identity_screen(callback, state, view, result, language)
                return
            view.pop("identity_version", None)
            view.pop("identity_previous", None)
            await show(
                callback, state,
                card_text(result, view["section"], language),
                rows_for(view, result, language), view,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_client_card_failed error_type=%s status=%s code=%s",
                type(error).__name__,
                getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if isinstance(error, ConstructionApiClientError) and error.status_code == 429:
                key = "construction_bot_rate_limited"
            elif isinstance(error, ConstructionApiClientError) and error.status_code in (401, 403, 404, 422):
                key = "construction_bot_unavailable"
            else:
                key = "construction_bot_error"
            await show(callback, state, t(key, language), back_rows(language))

# Client self identity controls v1.
def identity_rows(view, language, *, confirm=False, replacement=False, offer=False):
    rows = []
    if view.get("section") == "identity" and view.get("identity_has_link") is True:
        rows.append([button(tr("identity_unlink", language), cb(view, "identity_unlink"))])
    if confirm or offer:
        rows.append([button(
            tr("identity_replace" if replacement else "identity_link", language),
            cb(view, "identity_submit" if confirm else "identity_review"),
        )])
    rows.extend([
        [button(tr("refresh", language), cb(view, "identity"))],
        [button(tr("summary", language), cb(view, "summary"))],
    ])
    from ui.construction_client_catalog import tr as catalog_tr
    rows.append([button(
        catalog_tr("back" if view.get("catalog_nonce") else "title", language),
        "CL:back:" + view["catalog_nonce"] if view.get("catalog_nonce") else "CL:open",
    )])
    rows.extend(back_rows(language))
    return rows


async def identity_screen(callback, state, view, result, language):
    item = result["item"]
    actor = str(UUID(result["actor_user_id"]))
    previous = item["linked_user_id"]
    previous = str(UUID(previous)) if previous is not None else None
    own = previous == actor
    view.update(identity_link_owner=own, identity_has_link=previous is not None)
    review = view["section"] == "identity_review" and not own
    view.pop("identity_version", None)
    view.pop("identity_previous", None)
    if review:
        version = item["row_version"]
        if type(version) is not int or not 0 < version < 2**63:
            raise ValueError("Invalid reviewed client version.")
        view.update(identity_version=version, identity_previous=previous)
    else:
        view["section"] = "identity"
    status = "identity_mine" if own else "identity_other" if previous else "identity_none"
    lines = [
        tr("identity", language), str(result["organization"])[:100],
        str(item["display_name"])[:180], tr(status, language),
    ]
    if review:
        lines.append(tr(
            "identity_replace_review" if previous else "identity_review", language,
        ))
    lines.append(tr("identity_boundary", language))
    await show(
        callback, state, "\n\n".join(lines),
        identity_rows(
            view, language, confirm=review,
            replacement=previous is not None, offer=not own and not review,
        ),
        view,
    )


async def submit_self_identity(callback, state, view, bot, event_update):
    if view.get("section") != "identity_review":
        raise ValueError("Explicit client identity review is required.")
    version = view.get("identity_version")
    if type(version) is not int or not 0 < version < 2**63:
        raise ValueError("Reviewed client version is missing.")
    previous = view.get("identity_previous")
    previous = UUID(previous) if previous is not None else None
    actor_id = UUID(view["actor_user_id"])
    if previous == actor_id:
        raise ValueError("The client is already linked to this account.")

    data = await state.get_data()
    stored = data.get(VIEW)
    if not isinstance(stored, dict) or stored.get("nonce") != view["nonce"]:
        raise ValueError("Client identity confirmation is stale.")

    view = deepcopy(view)
    view.update(section="identity_writing", nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    snapshot = deepcopy({
        VIEW: view, CONTEXT: data.get(CONTEXT),
        "last_menu_message_id": data.get("last_menu_message_id"),
    })
    language = callback.from_user.language_code or "ru"
    message_key = "identity_done"
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.self_link_client(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(view["tenant_id"]), client_id=UUID(view["client_id"]),
                expected_user_id=actor_id, expected_row_version=version,
                previous_user_id=previous,
            )
        item = result["item"]
        if (
            result["actor_user_id"] != view["actor_user_id"]
            or item["client_id"] != view["client_id"]
            or item["tenant_id"] != view["tenant_id"]
            or item["linked_user_id"] != view["actor_user_id"]
            or type(item["changed"]) is not bool
            or type(item["row_version"]) is not int
            or item["row_version"] != version + int(item["changed"])
        ):
            raise ValueError("Client self-link receipt differs.")
        language = result["language"] or language
    except Exception as error:
        logger.warning(
            "construction_bot_client_self_link_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status = getattr(error, "status_code", None)
        if status == 409:
            message_key = "identity_conflict"
        elif status in (400, 401, 403, 404, 422):
            message_key = "construction_bot_unavailable"
        elif status == 429:
            message_key = "construction_bot_rate_limited"
        else:
            message_key = "identity_uncertain"

    current = await state.get_data()
    if any(current.get(key) != value for key, value in snapshot.items()):
        return
    view.pop("identity_version", None)
    view.pop("identity_previous", None)
    view.update(section="identity_result", nonce=uuid4().hex)
    text = (
        t(message_key, language) if message_key.startswith("construction_bot_")
        else tr(message_key, language)
    )
    await show(
        callback, state,
        text + "\n\n" + tr("identity_boundary", language),
        identity_rows(view, language), view,
    )

# Client unlink bot controls v1.
def unlink_rows(view, language, *, confirm=False):
    rows = []
    if confirm:
        rows.append([button(
            tr("identity_unlink_confirm", language), cb(view, "identity_unlink_submit"),
        )])
    if view.get("unlink_owner") is True:
        rows.append([button(
            tr("identity_own_refresh", language), cb(view, "identity_own"),
        )])
    else:
        rows.append([button(tr("refresh", language), cb(view, "identity"))])
    rows.append([button(tr("summary", language), cb(view, "summary"))])
    rows.extend(back_rows(language))
    return rows


def unlink_snapshot(data):
    return deepcopy({
        key: data.get(key) for key in (VIEW, CONTEXT, "last_menu_message_id")
    })


def unlink_same(data, snapshot):
    return all(data.get(key) == value for key, value in snapshot.items())


async def client_unlink_action(callback, state, view, action, bot, event_update):
    language = callback.from_user.language_code or "ru"
    if action == "identity_unlink_submit":
        await submit_client_unlink(callback, state, view, bot, event_update)
        return
    if action == "identity_unlink":
        if view.get("section") != "identity" or view.get("identity_has_link") is not True:
            raise ValueError("Open the existing client link first.")
        owner = view.get("identity_link_owner") is True
    else:
        if view.get("unlink_owner") is not True:
            raise ValueError("Own-link inspection context is unavailable.")
        owner = True

    view = deepcopy(view)
    before = unlink_snapshot(await state.get_data())
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            common = dict(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(view["tenant_id"]), client_id=UUID(view["client_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
            )
            if owner:
                result = await service.own_client_link_state(**common)
                item = result["item"]
                if (
                    result["actor_user_id"] != view["actor_user_id"]
                    or item["client_id"] != view["client_id"]
                    or item["linked_user_id"] != view["actor_user_id"]
                ):
                    raise ValueError("Own-link context differs.")
                previous = view["actor_user_id"]
            else:
                result = await service.client_card(
                    **common, cursors=dict(view["positions"]),
                )
                item = result["item"]
                if (
                    result["actor_user_id"] != view["actor_user_id"]
                    or item["id"] != view["client_id"]
                    or item["tenant_id"] != view["tenant_id"]
                ):
                    raise ValueError("Editor unlink context differs.")
                previous = item["linked_user_id"]
                if previous is not None:
                    previous = str(UUID(previous))
                owner = previous == view["actor_user_id"]
        if not unlink_same(await state.get_data(), before):
            return
        version = item["row_version"]
        if type(version) is not int or not 0 < version < 2**63:
            raise ValueError("Client unlink version is invalid.")
        language = result["language"] or language
        view.update(
            section="identity_unlink_review", unlink_version=version,
            unlink_owner=owner, nonce=uuid4().hex,
        )
        if previous is None:
            view["section"] = "identity_unlink_result"
            view.pop("unlink_version", None)
            text = tr("identity_none", language)
        else:
            text = tr(
                "identity_unlink_own_review" if owner else "identity_unlink_editor_review",
                language,
            )
        await show(
            callback, state,
            text + "\n\n" + tr("identity_unlink_boundary", language),
            unlink_rows(view, language, confirm=previous is not None), view,
        )
    except Exception as error:
        logger.warning(
            "construction_bot_client_unlink_read_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not unlink_same(await state.get_data(), before):
            return
        view.update(
            section="identity_unlink_result", unlink_owner=owner, nonce=uuid4().hex,
        )
        view.pop("unlink_version", None)
        status = getattr(error, "status_code", None)
        text = (
            tr("identity_own_unavailable", language) if owner and status == 404
            else t("construction_bot_unavailable", language)
        )
        await show(callback, state, text, unlink_rows(view, language), view)


async def submit_client_unlink(callback, state, view, bot, event_update):
    version = view.get("unlink_version")
    if (
        view.get("section") != "identity_unlink_review"
        or type(version) is not int or not 0 < version < 2**63
        or type(view.get("unlink_owner")) is not bool
    ):
        raise ValueError("Explicit unlink review is required.")
    view = deepcopy(view)
    view.update(section="identity_unlink_writing", nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    before = unlink_snapshot(await state.get_data())
    language = callback.from_user.language_code or "ru"
    key = "identity_unlink_done"
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.unlink_client(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(view["tenant_id"]), client_id=UUID(view["client_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                expected_row_version=version,
                as_owner=view["unlink_owner"],
            )
        item = result["item"]
        if (
            result["actor_user_id"] != view["actor_user_id"]
            or item["client_id"] != view["client_id"]
            or item["linked_user_id"] is not None
            or type(item["changed"]) is not bool
            or type(item["row_version"]) is not int
            or item["row_version"] != version + int(item["changed"])
            or (view["unlink_owner"] and not item["changed"])
        ):
            raise ValueError("Client unlink receipt differs.")
        language = result["language"] or language
    except Exception as error:
        logger.warning(
            "construction_bot_client_unlink_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status = getattr(error, "status_code", None)
        if status == 409:
            key = "identity_conflict"
        elif status in (400, 401, 403, 404, 422):
            key = "construction_bot_unavailable"
        elif status == 429:
            key = "construction_bot_rate_limited"
        else:
            key = "identity_uncertain"
    if not unlink_same(await state.get_data(), before):
        return
    view.pop("unlink_version", None)
    view.update(section="identity_unlink_result", nonce=uuid4().hex)
    text = t(key, language) if key.startswith("construction_bot_") else tr(key, language)
    await show(
        callback, state,
        text + "\n\n" + tr("identity_unlink_boundary", language),
        unlink_rows(view, language), view,
    )

# client_link_operator_bot_v1
