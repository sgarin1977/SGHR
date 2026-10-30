"""Explicit own-account unlink, dispatched under the existing binding UI lock."""
from copy import deepcopy
from time import time
from uuid import UUID, uuid4
import logging

from aiogram.types import InlineKeyboardMarkup

from handlers import platform_telegram_binding_ui as parent
from services.construction.construction_api_client import ConstructionApiClientError
from ui.platform_telegram_binding import tr
from ui.texts import t

VIEW = "platform_telegram_account_unlink_view"
ATTEMPT = "platform_telegram_account_unlink_attempt"
KEYS = (VIEW, ATTEMPT, parent.VIEW, parent.CONTEXT, "last_menu_message_id")
logger = logging.getLogger(__name__)
IDENTITY = ("telegram_user_id", "tenant_id", "actor_user_id")
TARGET = ("account_id", "expected_row_version", "telegram_account_id", "is_current")

def pending_for(view, data):
    attempt = data.get(ATTEMPT)
    return (
        parent.owned(view, attempt) and attempt.get("phase") == "pending"
        and parent.retryable(attempt)
    )

def account_buttons(view, items, data, language):
    rows = []
    for index, item in enumerate(items):
        if item["status"] == "active":
            rows.append([parent.button(
                tr("unlink_account", language) + " · " + item["telegram_user_id"],
                "TGB:unlink:" + view["nonce"] + ":" + str(index),
            )])
    if pending_for(view, data):
        rows.append([parent.button(
            tr("restore", language), "TGB:unlink_resume:" + view["nonce"],
        )])
    return rows

def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}

def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)

def command(view, action):
    return "TGB:unlink_" + action + ":" + view["nonce"]

async def show(callback, state, view):
    data = await state.get_data()
    if data.get(VIEW) != view or not parent.bound(data, view, callback):
        return
    saved = snapshot(data)
    language, phase = view["language"], view["phase"]
    lines = [tr("unlink_account", language)]
    if view.get("telegram_account_id"):
        lines.append("Telegram ID: " + view["telegram_account_id"])
    rows = []
    if phase == "review":
        lines.append(tr("unlink_review", language))
        if view["is_current"]:
            lines.append(tr("unlink_current", language))
        rows.append([parent.button(tr("confirm", language), command(view, "confirm"))])
    elif phase == "pending":
        lines.append(tr("unlink_pending", language))
        attempt = data.get(ATTEMPT)
        if parent.owned(view, attempt) and parent.retryable(attempt):
            rows.append([parent.button(tr("restore", language), command(view, "retry"))])
        else:
            lines.append(tr("unlink_expired", language))
    elif phase == "done":
        lines.append(tr("unlink_done", language))
    elif phase == "conflict":
        lines.append(tr("unlink_conflict", language))
    elif phase == "blocked":
        lines.append(t("platform_telegram_binding_unavailable", language))
    else:
        raise ValueError("Unknown unlink phase.")
    rows.append([parent.button(tr("accounts", language), "TGB:open")])
    sent = await parent.edit_or_replace_menu_message(
        callback=callback, text="\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{
        VIEW: {**deepcopy(view), "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })

async def set_view(callback, state, view):
    await state.update_data(**{
        VIEW: deepcopy(view), parent.VIEW: {},
    })
    await show(callback, state, view)

async def review(callback, state, source, index, bot, event_update):
    data = await state.get_data()
    if pending_for(source, data):
        attempt = data[ATTEMPT]
        view = {
            **deepcopy(attempt), "language": source["language"],
            "phase": "pending", "nonce": uuid4().hex,
            "menu_id": callback.message.message_id,
        }
        await set_view(callback, state, view)
        return

    ids = source.get("account_ids")
    versions = source.get("account_versions")
    statuses = source.get("account_statuses")
    if (
        not isinstance(ids, list) or not isinstance(versions, list)
        or not isinstance(statuses, list)
        or not len(ids) == len(versions) == len(statuses)
        or len(ids) > 5 or not 0 <= index < len(ids)
        or statuses[index] != "active"
        or type(versions[index]) is not int
    ):
        raise ValueError("Account selector is unavailable.")
    account_id = UUID(ids[index])
    expected = versions[index]
    saved = snapshot(data)
    async with parent.construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.telegram_accounts(
            telegram_user_id=callback.from_user.id,
            tenant_id=UUID(source["tenant_id"]),
            expected_user_id=UUID(source["actor_user_id"]),
            cursor=UUID(source["cursor"]) if source.get("cursor") else None,
        )
    if not same(await state.get_data(), saved):
        return
    if result["actor_user_id"] != source["actor_user_id"]:
        raise ValueError("Platform identity changed.")
    item = next(
        (item for item in result["items"] if item["account_id"] == str(account_id)),
        None,
    )
    view = {
        **{key: source[key] for key in IDENTITY},
        "account_id": str(account_id), "expected_row_version": expected,
        "language": result["language"] or source["language"],
        "nonce": uuid4().hex, "menu_id": callback.message.message_id,
        "telegram_account_id": item["telegram_user_id"] if item else None,
        "is_current": bool(item and item["telegram_user_id"] == str(callback.from_user.id)),
        "phase": "review",
    }
    if item is None or item["status"] != "active" or item["row_version"] != expected:
        view["phase"] = "conflict"
    await set_view(callback, state, view)

async def execute(callback, state, view, *, replay, bot, event_update):
    data = await state.get_data()
    if replay:
        attempt = data.get(ATTEMPT)
        if (
            view["phase"] != "pending" or not parent.owned(view, attempt)
            or not parent.retryable(attempt)
            or any(view.get(key) != attempt.get(key) for key in TARGET)
            or view.get("idempotency_key") != attempt.get("idempotency_key")
        ):
            raise ValueError("Unlink replay unavailable.")
        attempt = deepcopy(attempt)
    else:
        if view["phase"] != "review":
            raise ValueError("Unlink confirmation unavailable.")
        attempt = {
            key: deepcopy(view[key]) for key in (*IDENTITY, *TARGET)
        }
        attempt.update(
            idempotency_key="tg-unlink:" + uuid4().hex,
            started_at=time(), phase="pending",
        )
    view = {
        **deepcopy(view), "phase": "pending", "nonce": uuid4().hex,
        "idempotency_key": attempt["idempotency_key"],
    }
    await state.update_data(**{
        VIEW: deepcopy(view), ATTEMPT: deepcopy(attempt), parent.VIEW: {},
    })
    saved = snapshot(await state.get_data())
    try:
        async with parent.construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.unlink_telegram_account(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                account_id=UUID(attempt["account_id"]),
                expected_row_version=attempt["expected_row_version"],
                idempotency_key=attempt["idempotency_key"],
            )
        item = result["account"]
        if (
            result["actor_user_id"] != view["actor_user_id"]
            or item.account_id != UUID(attempt["account_id"])
            or item.status != "unlinked"
            or type(item.changed) is not bool
            or item.row_version != attempt["expected_row_version"] + int(item.changed)
        ):
            raise ValueError("Unlink receipt differs.")
        attempt["receipt"] = item.model_dump(mode="json")
        attempt["phase"] = view["phase"] = "done"
    except Exception as error:
        logger.warning(
            "platform_telegram_account_unlink_ui_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        phase = "pending"
        if status == 409 and code == "ROW_VERSION_CONFLICT":
            phase = "conflict"
        elif status in (400, 401, 403, 404, 409, 422) and code != "idempotency_in_progress":
            phase = "blocked"
        attempt["phase"] = view["phase"] = phase
    if not same(await state.get_data(), saved):
        return
    view["nonce"] = uuid4().hex
    await state.update_data(**{VIEW: deepcopy(view), ATTEMPT: deepcopy(attempt)})
    await show(callback, state, view)

async def dispatch(callback, state, *, bot=None, event_update=None):
    data = await state.get_data()
    parts = callback.data.split(":")
    action = parts[1] if len(parts) >= 2 else ""
    if action in ("unlink", "unlink_resume"):
        source = data.get(parent.VIEW)
        if (
            len(parts) != (4 if action == "unlink" else 3)
            or not parent.bound(data, source, callback)
            or source.get("mode") != "accounts"
            or parts[2] != source.get("nonce")
        ):
            raise ValueError("Account page is stale.")
        if action == "unlink":
            index = int(parts[3])
            if str(index) != parts[3]:
                raise ValueError("Account selector differs.")
            await review(callback, state, source, index, bot, event_update)
            return
        if not pending_for(source, data):
            raise ValueError("No pending unlink request.")
        attempt = data[ATTEMPT]
        view = {
            **deepcopy(attempt), "language": source["language"],
            "nonce": uuid4().hex, "menu_id": callback.message.message_id,
            "phase": "pending",
        }
        await state.update_data(**{VIEW: deepcopy(view), parent.VIEW: {}})
        await execute(callback, state, view, replay=True, bot=bot, event_update=event_update)
        return
    view = deepcopy(data.get(VIEW))
    if (
        len(parts) != 3 or not parent.bound(data, view, callback)
        or parts[2] != view.get("nonce")
    ):
        raise ValueError("Unlink review is stale.")
    if action == "unlink_confirm":
        await execute(callback, state, view, replay=False, bot=bot, event_update=event_update)
    elif action == "unlink_retry":
        await execute(callback, state, view, replay=True, bot=bot, event_update=event_update)
    else:
        raise ValueError("Unknown account unlink action.")
