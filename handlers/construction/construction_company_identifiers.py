"""Company identifier list/create/replace/delete through Platform API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_company_identifiers import tr, item_text
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_company_identifiers")
logger = logging.getLogger(__name__)
VIEW = "construction_company_identifiers_view"
PARENT = "construction_company_profile_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")
FIELDS = ("country_code", "identifier_type", "identifier_value")


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(view, action, value=None):
    result = "CI:" + action + ":" + view["nonce"]
    return result if value is None else result + ":" + str(value)


def same(data, snapshot):
    return all(data.get(key) == snapshot.get(key) for key in KEYS)


def bound(data, view, sender):
    context = data.get(CONTEXT)
    return (
        isinstance(view, dict) and view.get("telegram_user_id") == sender
        and isinstance(context, dict) and context.get("telegram_user_id") == sender
        and context.get("tenant_id") == view.get("tenant_id")
        and view.get("menu_id") is not None
        and view.get("menu_id") == data.get("last_menu_message_id")
    )


async def suspend_identifier_view(state):
    view = (await state.get_data()).get(VIEW)
    if isinstance(view, dict) and view:
        if view.get("attempted"):
            view = {key: value for key, value in view.items() if key != "payload"}
            view["menu_id"] = None
        else:
            view = {}
        await state.update_data(**{VIEW: view})


async def identifier_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("CI:"):
        await suspend_identifier_view(state)
    return await handler(event, data)


async def show(event, state, view, text, rows):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, event.from_user.id):
        return
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    if len(text) > 3900:
        raise ValueError("Identifier presentation is too large.")
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(event, "message", None) is not None:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=keyboard,
        )
    else:
        sent = await event.answer(text, reply_markup=keyboard)
    if not same(await state.get_data(), snapshot):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })


def back_rows(view):
    return [
        [button(tr("list", view["language"]), cb(view, "refresh"))],
        [button(tr("open_profile", view["language"]), "CP:open")],
    ]


async def fetch(state, view, bot, event_update):
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.company_identifiers(
            telegram_user_id=view["telegram_user_id"],
            tenant_id=UUID(view["tenant_id"]),
            expected_user_id=UUID(view["actor_user_id"]),
        )
    if not same(await state.get_data(), snapshot):
        raise ValueError("Identifier context changed.")
    if result["actor_user_id"] != view["actor_user_id"]:
        raise ValueError("Identifier actor changed.")
    return result


async def list_screen(event, state, view, result):
    items = sorted(result["items"], key=lambda item: (
        item["country_code"], item["identifier_type"], item["id"],
    ))
    offset = view.get("offset", 0)
    offset = min(max(offset, 0), ((len(items) - 1) // 5) * 5) if items else 0
    page = items[offset:offset + 5]
    view = {
        key: value for key, value in view.items()
        if key not in ("payload", "target_id", "operation", "step", "notice")
    }
    view.update(
        phase="list", nonce=uuid4().hex, offset=offset,
        ids=[item["id"] for item in page], attempted=False,
        language=result["language"] or view["language"],
    )
    language = view["language"]
    lines = [tr("title", language), str(result["organization"])[:120]]
    rows = []
    for index, item in enumerate(page):
        lines.append(str(index + 1) + ". " + item_text(item, language))
        rows.append([button(
            str(index + 1) + ". " + item["country_code"] + " · " + item["identifier_type"],
            cb(view, "item", index),
        )])
    if not page:
        lines.append(tr("empty", language))
    if result["can_manage"] is True and result["profile_id"] is not None:
        rows.append([button(tr("add", language), cb(view, "add"))])
    if offset:
        rows.append([button(tr("previous", language), cb(view, "previous"))])
    if offset + 5 < len(items):
        rows.append([button(tr("next", language), cb(view, "next"))])
    rows.extend(back_rows(view))
    await state.update_data(**{VIEW: view})
    await show(event, state, view, "\n\n".join(lines), rows)


async def draft_screen(event, state, view, notice=None):
    language = view["language"]
    phase = view["phase"]
    rows = []
    if phase == "input":
        text = tr("replace" if view["operation"] == "replace" else "add", language)
        text += "\n\n" + tr(FIELDS[view["step"]], language)
    elif phase == "primary":
        text = tr("primary", language)
        rows = [
            [button(tr("yes", language), cb(view, "primary", 1))],
            [button(tr("no", language), cb(view, "primary", 0))],
        ]
    elif phase == "review":
        payload = view["payload"]
        text = tr("review", language) + "\n\n" + item_text(payload, language)
        rows.append([button(tr("save", language), cb(view, "commit"))])
    elif phase == "delete":
        text = tr("confirm_delete", language)
        rows.append([button(tr("delete", language), cb(view, "commit"))])
    elif phase == "done":
        text = tr("saved", language)
    elif phase == "uncertain":
        text = tr("uncertain_write", language)
    elif phase == "blocked":
        text = tr(view.get("notice", "invalid_write"), language)
    else:
        raise ValueError("Unknown identifier phase.")
    if phase in ("input", "primary", "review", "delete"):
        rows.append([button(tr("cancel", language), cb(view, "refresh"))])
    else:
        rows.extend(back_rows(view))
    if notice:
        text = notice + "\n\n" + text
    await show(event, state, view, text, rows)


async def open_screen(callback, state, bot, event_update):
    data = await state.get_data()
    source, context = data.get(PARENT), data.get(CONTEXT)
    parts = callback.data.split(":")
    if (
        len(parts) != 3 or parts[1] != "open"
        or not isinstance(source, dict) or not isinstance(context, dict)
        or source.get("nonce") != parts[2]
        or source.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or source.get("tenant_id") != context.get("tenant_id")
        or source.get("menu_id") != callback.message.message_id
        or data.get("last_menu_message_id") != callback.message.message_id
    ):
        raise ValueError("Stale company identifier source.")
    from handlers.construction.construction_team import suspend_forms
    from handlers.construction.construction_area_create import suspend_area_form
    from handlers.construction.construction_company_edit import suspend_company_form
    await suspend_forms(state)
    await suspend_area_form(state)
    await suspend_company_form(state)
    view = {
        "nonce": uuid4().hex, "telegram_user_id": callback.from_user.id,
        "tenant_id": str(UUID(source["tenant_id"])),
        "actor_user_id": str(UUID(source["actor_user_id"])),
        "menu_id": callback.message.message_id,
        "language": callback.from_user.language_code or "ru",
        "offset": 0, "phase": "list", "ids": [], "attempted": False,
    }
    await state.update_data(**{VIEW: view, PARENT: {}})
    result = await fetch(state, view, bot, event_update)
    await list_screen(callback, state, view, result)


async def commit(callback, state, view, bot, event_update):
    if (
        view["phase"] not in ("review", "delete") or view.get("attempted")
        or view.get("operation") not in ("create", "replace", "delete")
    ):
        raise ValueError("Identifier operation is not ready.")
    payload = deepcopy(view.get("payload")) if view["operation"] != "delete" else None
    target = UUID(view["target_id"]) if view.get("target_id") else None
    if view["operation"] != "delete" and (
        not isinstance(payload, dict)
        or set(payload) != set(FIELDS) | {"is_primary"}
        or type(payload["is_primary"]) is not bool
    ):
        raise ValueError("Identifier draft differs.")
    view = {key: value for key, value in view.items() if key != "payload"}
    view.update(phase="sending", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            receipt = await service.mutate_company_identifier(
                telegram_user_id=view["telegram_user_id"],
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                operation=view["operation"], identifier_id=target, payload=payload,
            )
        if receipt["actor_user_id"] != view["actor_user_id"]:
            raise ValueError("Identifier receipt actor differs.")
        if not same(await state.get_data(), snapshot):
            return
        view["phase"] = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_company_identifier_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), snapshot):
            return
        status = getattr(error, "status_code", None)
        if status in (400, 401, 403, 404, 409, 422, 429):
            view.update(
                phase="blocked",
                notice="conflict_write" if status == 409 else "invalid_write",
            )
        else:
            view["phase"] = "uncertain"
    await state.update_data(**{VIEW: view})
    await draft_screen(callback, state, view)


@router.callback_query(F.data.startswith("CI:"))
async def identifier_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith("CI:open:"):
                await open_screen(callback, state, bot, event_update)
                return
            data = await state.get_data()
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or not bound(data, view, callback.from_user.id)
                or view.get("nonce") != parts[2]
                or view.get("menu_id") != callback.message.message_id
            ):
                raise ValueError("Stale identifier selection.")
            language = view["language"]
            action = parts[1]
            if action == "commit" and len(parts) == 3:
                await commit(callback, state, view, bot, event_update)
                return
            if action == "primary" and len(parts) == 4:
                if view["phase"] != "primary" or parts[3] not in ("0", "1"):
                    raise ValueError("Invalid primary selection.")
                view["payload"]["is_primary"] = parts[3] == "1"
                view.update(phase="review", nonce=uuid4().hex)
                await state.update_data(**{VIEW: view})
                await draft_screen(callback, state, view)
                return
            if action not in ("refresh", "next", "previous", "item", "add", "replace", "delete"):
                raise ValueError("Unknown identifier action.")
            if (action == "item" and len(parts) != 4) or (action != "item" and len(parts) != 3):
                raise ValueError("Identifier callback shape differs.")
            if action in ("next", "previous", "item", "add") and view["phase"] != "list":
                raise ValueError("Identifier list is no longer active.")
            if action in ("replace", "delete") and view["phase"] != "card":
                raise ValueError("Identifier card is no longer active.")
            result = await fetch(state, view, bot, event_update)
            if action in ("refresh", "next", "previous"):
                if action == "next":
                    view["offset"] += 5
                elif action == "previous":
                    view["offset"] = max(0, view["offset"] - 5)
                await list_screen(callback, state, view, result)
                return

            target = None
            if action == "item":
                if parts[3] not in {str(index) for index in range(len(view["ids"]))}:
                    raise ValueError("Identifier list slot differs.")
                target = view["ids"][int(parts[3])]
            elif action in ("replace", "delete"):
                target = view.get("target_id")
            item = next((item for item in result["items"] if item["id"] == target), None)
            if target is not None and item is None:
                raise ValueError("Identifier is no longer available.")
            if action == "item":
                view.update(phase="card", target_id=target, nonce=uuid4().hex)
                rows = []
                if result["can_manage"] is True:
                    rows = [
                        [button(tr("replace", language), cb(view, "replace"))],
                        [button(tr("delete", language), cb(view, "delete"))],
                    ]
                rows.extend(back_rows(view))
                await state.update_data(**{VIEW: view})
                await show(callback, state, view, item_text(item, language), rows)
                return
            if result["can_manage"] is not True or result["profile_id"] is None:
                raise ValueError("Identifier management is unavailable.")
            view.update(
                operation="create" if action == "add" else action,
                target_id=target, nonce=uuid4().hex, attempted=False,
            )
            if action == "delete":
                view["phase"] = "delete"
                await state.update_data(**{VIEW: view})
                await show(
                    callback, state, view,
                    tr("confirm_delete", language) + "\n\n" + item_text(item, language),
                    [[button(tr("delete", language), cb(view, "commit"))],
                     [button(tr("cancel", language), cb(view, "refresh"))]],
                )
            else:
                view.update(phase="input", step=0, payload={})
                await state.update_data(**{VIEW: view})
                await draft_screen(callback, state, view)
        except Exception as error:
            logger.warning(
                "construction_bot_company_identifiers_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("construction_lead_form_stale", language))


class IdentifierInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(VIEW)
        return (
            bound(data, view, message.from_user.id)
            and view.get("phase") == "input" and not view.get("attempted")
            and type(view.get("step")) is int and 0 <= view["step"] < len(FIELDS)
        )


@router.message(IdentifierInput())
async def identifier_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await IdentifierInput()(message, state):
            return
        view = deepcopy((await state.get_data())[VIEW])
        field = FIELDS[view["step"]]
        value = message.text if field == "identifier_value" else message.text.strip()
        valid = (
            bool(value.strip())
            and len(value) <= {"country_code": 2, "identifier_type": 50, "identifier_value": 100}[field]
            and not any(ord(char) < 32 for char in value)
        )
        if field == "country_code":
            valid = valid and len(value) == 2 and value.isascii() and value.isalpha()
        if not valid:
            await draft_screen(message, state, view, tr("invalid_input", view["language"]))
            return
        view["payload"][field] = value
        view["step"] += 1
        view.update(
            phase="primary" if view["step"] == len(FIELDS) else "input",
            nonce=uuid4().hex,
        )
        await state.update_data(**{VIEW: view})
        await draft_screen(message, state, view)
