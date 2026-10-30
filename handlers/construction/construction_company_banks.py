"""Company bank account UI backed exclusively by Platform API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_company_banks import (
    tr, account_text, value_text, EDIT_FIELDS, TEXT_FIELDS, FLAGS,
)
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_company_banks")
logger = logging.getLogger(__name__)
VIEW = "construction_company_banks_view"
PARENT = "construction_company_profile_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")
LIMITS = {
    "label": 100, "account_holder_name": 255, "bank_name": 255,
    "country_code": 2, "iban": 128, "bic_swift": 64,
    "account_number": 100, "currency": 3,
}


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(view, action, value=None):
    result = "CBK:" + action + ":" + view["nonce"]
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


async def suspend_bank_view(state):
    view = (await state.get_data()).get(VIEW)
    if isinstance(view, dict) and view:
        if view.get("attempted"):
            view = {key: value for key, value in view.items() if key != "payload"}
            view["menu_id"] = None
        else:
            view = {}
        await state.update_data(**{VIEW: view})


async def bank_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("CBK:"):
        await suspend_bank_view(state)
    return await handler(event, data)


async def show(event, state, view, text, rows):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, event.from_user.id):
        return
    if len(text) > 3900:
        raise ValueError("Bank account presentation is too large.")
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(event, "message", None) is not None:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=markup,
        )
    else:
        sent = await event.answer(text, reply_markup=markup)
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
    if data.get(VIEW) != view or not bound(data, view, view["telegram_user_id"]):
        raise ValueError("Bank account context is no longer active.")
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.company_bank_accounts(
            telegram_user_id=view["telegram_user_id"],
            tenant_id=UUID(view["tenant_id"]),
            expected_user_id=UUID(view["actor_user_id"]),
        )
    if not same(await state.get_data(), snapshot):
        raise ValueError("Bank account context changed.")
    if result["actor_user_id"] != view["actor_user_id"]:
        raise ValueError("Bank account actor changed.")
    return result


async def list_screen(event, state, view, result):
    items = sorted(result["items"], key=lambda item: (
        not item["is_active"], item["currency"], item["id"],
    ))
    offset = view.get("offset", 0)
    offset = min(max(offset, 0), ((len(items) - 1) // 5) * 5) if items else 0
    page = items[offset:offset + 5]
    view = {
        key: value for key, value in view.items()
        if key not in (
            "payload", "target_id", "operation", "expected_row_version",
            "field", "notice",
        )
    }
    view.update(
        phase="list", nonce=uuid4().hex, offset=offset, attempted=False,
        ids=[item["id"] for item in page],
        language=result["language"] or view["language"],
    )
    language = view["language"]
    lines = [tr("title", language), str(result["organization"])[:120]]
    rows = []
    for index, item in enumerate(page):
        title = (item["label"] or item["account_holder_name"])[:80]
        lines.append(
            str(index + 1) + ". " + title + " · " + item["currency"]
            + "\n" + tr("active" if item["is_active"] else "inactive", language)
        )
        rows.append([button(
            str(index + 1) + ". " + title[:40], cb(view, "item", index),
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
    language, phase = view["language"], view["phase"]
    rows = []
    if phase == "input":
        text = tr(view["field"], language) + "\n\n" + tr("enter_value", language)
        if view["operation"] == "create":
            text += "\n\n" + tr("create_note", language)
        elif view["field"] not in ("account_holder_name", "currency"):
            rows.append([button(tr("clear_field", language), cb(view, "clear"))])
    elif phase == "kind":
        text = tr("kind", language)
        rows = [
            [button(tr("iban", language), cb(view, "kind", 0))],
            [button(tr("account_number", language), cb(view, "kind", 1))],
        ]
    elif phase in ("primary", "documents", "boolean"):
        field = (
            "is_primary" if phase == "primary"
            else "show_in_documents" if phase == "documents" else view["field"]
        )
        text = tr(field, language)
        rows = [
            [button(tr("yes", language), cb(view, "flag", 1))],
            [button(tr("no", language), cb(view, "flag", 0))],
        ]
    elif phase == "review":
        lines = [tr("review", language)]
        for field, value in view["payload"].items():
            lines.append(tr(field, language) + ":\n" + value_text(value, language))
        text = "\n\n".join(lines)
        rows.append([button(tr("save", language), cb(view, "commit"))])
    elif phase == "done":
        text = tr("saved", language)
    elif phase == "uncertain":
        text = tr("uncertain_write", language)
    elif phase == "blocked":
        text = tr(view.get("notice", "invalid_write"), language)
    else:
        raise ValueError("Unknown bank account draft phase.")
    if phase in ("input", "kind", "primary", "documents", "boolean", "review"):
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
        raise ValueError("Stale company bank source.")
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
    operation = view.get("operation")
    if (
        view["phase"] not in ("review", "deactivate")
        or view.get("attempted")
        or operation not in ("create", "update", "deactivate")
        or (operation == "deactivate") != (view["phase"] == "deactivate")
    ):
        raise ValueError("Bank account operation is not ready.")
    payload = deepcopy(view.get("payload", {}))
    if operation != "create":
        if type(view.get("expected_row_version")) is not int or view["expected_row_version"] <= 0:
            raise ValueError("Bank account base version differs.")
        payload["expected_row_version"] = view["expected_row_version"]
    account_id = UUID(view["target_id"]) if view.get("target_id") else None
    view = {key: value for key, value in view.items() if key != "payload"}
    view.update(phase="sending", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            receipt = await service.mutate_company_bank_account(
                telegram_user_id=view["telegram_user_id"],
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                operation=operation, payload=payload, account_id=account_id,
            )
        if (
            receipt["actor_user_id"] != view["actor_user_id"]
            or (account_id is not None and receipt["item"]["id"] != str(account_id))
        ):
            raise ValueError("Bank account receipt context differs.")
        if not same(await state.get_data(), snapshot):
            return
        view["phase"] = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_company_bank_write_failed error_type=%s status=%s code=%s",
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


@router.callback_query(F.data.startswith("CBK:"))
async def bank_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith("CBK:open:"):
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
                raise ValueError("Stale bank account selection.")
            language, action = view["language"], parts[1]
            if action == "commit" and len(parts) == 3:
                await commit(callback, state, view, bot, event_update)
                return

            if action == "field" and len(parts) == 4:
                if (
                    view["phase"] != "choose"
                    or parts[3] not in {str(index) for index in range(len(EDIT_FIELDS))}
                ):
                    raise ValueError("Invalid bank field selector.")
                field = EDIT_FIELDS[int(parts[3])]
                view.update(
                    field=field, payload={},
                    phase="boolean" if field in FLAGS else "input",
                    nonce=uuid4().hex,
                )
            elif action == "kind" and len(parts) == 4:
                if view["phase"] != "kind" or parts[3] not in ("0", "1"):
                    raise ValueError("Invalid bank account format.")
                view.update(
                    phase="input",
                    field="iban" if parts[3] == "0" else "account_number",
                    nonce=uuid4().hex,
                )
            elif action == "flag" and len(parts) == 4:
                if view["phase"] not in ("primary", "documents", "boolean") or parts[3] not in ("0", "1"):
                    raise ValueError("Invalid bank account flag.")
                field = (
                    "is_primary" if view["phase"] == "primary"
                    else "show_in_documents" if view["phase"] == "documents"
                    else view["field"]
                )
                if field not in FLAGS:
                    raise ValueError("Unknown bank account flag.")
                view["payload"][field] = parts[3] == "1"
                view.update(
                    phase="documents" if view["phase"] == "primary" else "review",
                    nonce=uuid4().hex,
                )
            elif action == "clear" and len(parts) == 3:
                if (
                    view["phase"] != "input" or view["operation"] != "update"
                    or view["field"] not in set(TEXT_FIELDS) - {"account_holder_name", "currency"}
                ):
                    raise ValueError("This bank field cannot be cleared.")
                view["payload"] = {view["field"]: None}
                view.update(phase="review", nonce=uuid4().hex)
            else:
                if action not in ("refresh", "next", "previous", "item", "add", "edit", "deactivate"):
                    raise ValueError("Unknown bank account action.")
                if (action == "item" and len(parts) != 4) or (action != "item" and len(parts) != 3):
                    raise ValueError("Bank callback shape differs.")
                if action in ("next", "previous", "item", "add") and view["phase"] != "list":
                    raise ValueError("Bank account list is no longer active.")
                if action in ("edit", "deactivate") and view["phase"] != "card":
                    raise ValueError("Bank account card is no longer active.")
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
                        raise ValueError("Bank list slot differs.")
                    target = view["ids"][int(parts[3])]
                elif action in ("edit", "deactivate"):
                    target = view.get("target_id")
                item = next((item for item in result["items"] if item["id"] == target), None)
                if target is not None and item is None:
                    raise ValueError("Bank account is no longer available.")
                if action == "item":
                    view.update(
                        phase="card", target_id=target,
                        expected_row_version=item["row_version"], nonce=uuid4().hex,
                    )
                    rows = []
                    if result["can_manage"] is True:
                        rows.append([button(tr("edit", language), cb(view, "edit"))])
                        if item["is_active"]:
                            rows.append([button(tr("deactivate", language), cb(view, "deactivate"))])
                    rows.extend(back_rows(view))
                    await state.update_data(**{VIEW: view})
                    await show(callback, state, view, account_text(item, language), rows)
                    return
                if result["can_manage"] is not True or result["profile_id"] is None:
                    raise ValueError("Bank account management is unavailable.")
                view.update(
                    target_id=target, attempted=False, payload={},
                    nonce=uuid4().hex,
                )
                if action == "add":
                    view.update(
                        operation="create", phase="input",
                        field="account_holder_name",
                    )
                    view.pop("expected_row_version", None)
                elif action == "edit":
                    view.update(
                        operation="update", phase="choose",
                        expected_row_version=item["row_version"],
                    )
                    rows = [
                        [button(tr(field, language), cb(view, "field", index))]
                        for index, field in enumerate(EDIT_FIELDS)
                    ]
                    rows.append([button(tr("cancel", language), cb(view, "refresh"))])
                    await state.update_data(**{VIEW: view})
                    await show(
                        callback, state, view,
                        account_text(item, language) + "\n\n" + tr("choose", language),
                        rows,
                    )
                    return
                else:
                    if item["is_active"] is not True:
                        raise ValueError("Bank account is already inactive.")
                    view.update(
                        operation="deactivate", phase="deactivate",
                        expected_row_version=item["row_version"],
                    )
                    await state.update_data(**{VIEW: view})
                    await show(
                        callback, state, view,
                        tr("confirm_deactivate", language) + "\n\n" + account_text(item, language),
                        [[button(tr("deactivate", language), cb(view, "commit"))],
                         [button(tr("cancel", language), cb(view, "refresh"))]],
                    )
                    return
            await state.update_data(**{VIEW: view})
            await draft_screen(callback, state, view)
        except Exception as error:
            logger.warning(
                "construction_bot_company_bank_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("construction_lead_form_stale", language))


class BankInput(BaseFilter):
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
            and view.get("field") in TEXT_FIELDS
        )


@router.message(BankInput())
async def bank_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await BankInput()(message, state):
            return
        view = deepcopy((await state.get_data())[VIEW])
        field, value = view["field"], message.text.strip()
        valid = (
            bool(value) and len(value) <= LIMITS[field]
            and not any(ord(char) < 32 for char in value)
        )
        if field in ("country_code", "currency"):
            valid = (
                valid and len(value) == (2 if field == "country_code" else 3)
                and value.isascii() and value.isalpha()
            )
        if not valid:
            await draft_screen(message, state, view, tr("invalid_input", view["language"]))
            return
        view["payload"][field] = value
        if view["operation"] == "update":
            view["phase"] = "review"
        elif field == "account_holder_name":
            view.update(phase="input", field="currency")
        elif field == "currency":
            view["phase"] = "kind"
        elif field in ("iban", "account_number"):
            view["phase"] = "primary"
        else:
            raise ValueError("Unknown bank account creation step.")
        view["nonce"] = uuid4().hex
        await state.update_data(**{VIEW: view})
        await draft_screen(message, state, view)
