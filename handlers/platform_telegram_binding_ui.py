"""Own Platform Telegram binding UI; secrets remain transient."""
from copy import deepcopy
from datetime import datetime, timezone
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.platform_telegram_binding import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="platform_telegram_binding_ui")
logger = logging.getLogger(__name__)
VIEW = "platform_telegram_binding_view"
ATTEMPT = "platform_telegram_binding_attempt"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, ATTEMPT, CONTEXT, "last_menu_message_id")

def button(text, command):
    return InlineKeyboardButton(text=text, callback_data=command)

def cb(view, action):
    return "TGB:" + action + ":" + view["nonce"]

def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}

def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)

def owned(view, attempt):
    return (
        isinstance(attempt, dict)
        and all(attempt.get(key) == view.get(key) for key in (
            "telegram_user_id", "actor_user_id", "tenant_id",
        ))
    )

def retryable(attempt):
    return (
        isinstance(attempt, dict)
        and type(attempt.get("started_at")) in (int, float)
        and 0 <= time() - attempt["started_at"] < 86340
    )

def live_receipt(attempt):
    receipt = attempt.get("receipt") if isinstance(attempt, dict) else None
    if not isinstance(receipt, dict) or "expires_at" not in receipt:
        return False
    instant = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
    return instant > datetime.now(timezone.utc)

def bound(data, view, callback):
    context = data.get(CONTEXT)
    return (
        isinstance(view, dict) and isinstance(context, dict)
        and view.get("telegram_user_id") == callback.from_user.id
        and context.get("telegram_user_id") == callback.from_user.id
        and view.get("tenant_id") == context.get("tenant_id")
        and view.get("menu_id") == callback.message.message_id
        and view.get("menu_id") == data.get("last_menu_message_id")
    )

async def telegram_binding_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("TGB:"):
        view = (await state.get_data()).get(VIEW)
        if isinstance(view, dict) and view.get("telegram_user_id") == event.from_user.id:
            # Keep immutable request metadata for recovery; it contains no secret.
            await state.update_data(**{VIEW: {}})
    if state is not None and private(event) and not (event.data or "").startswith("TGB:"):
        from handlers.platform_telegram_account_unlink_ui import VIEW as UNLINK_VIEW
        unlink_view = (await state.get_data()).get(UNLINK_VIEW)
        if isinstance(unlink_view, dict) and unlink_view.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{UNLINK_VIEW: {}})
    return await handler(event, data)

async def show(callback, state, view, text, rows):
    data = await state.get_data()
    if not bound(data, view, callback):
        return
    saved = snapshot(data)
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })

async def display(callback, state, view, *, items=None, binding=None, notice=None):
    data = await state.get_data()
    attempt = data.get(ATTEMPT)
    attempt = attempt if owned(view, attempt) else None
    language, mode = view["language"], view["mode"]
    lines, rows = [tr("title", language)], []
    if mode == "accounts":
        if items:
            lines.extend(
                "Telegram ID: " + item["telegram_user_id"] + " · " + tr(item["status"], language)
                for item in items
            )
        else:
            lines.append(tr("empty", language))
        if view.get("next_cursor"):
            rows.append([button(t("construction_bot_next_btn", language), cb(view, "next"))])
    if mode == "accounts":
        from handlers.platform_telegram_account_unlink_ui import account_buttons
        rows.extend(account_buttons(view, items or [], data, language))
    if mode == "review":
        lines.append(tr("instructions", language))
        rows.extend([
            [button(tr("confirm", language), cb(view, "create"))],
            [button(tr("cancel", language), cb(view, "accounts"))],
        ])
    elif mode == "revoke_review":
        lines.append(tr("revoke_review", language))
        rows.extend([
            [button(tr("confirm", language), cb(view, "revoke_confirm"))],
            [button(tr("cancel", language), cb(view, "accounts"))],
        ])
    elif attempt and attempt.get("phase") == "pending":
        lines.append(tr("pending", language))
        if retryable(attempt):
            rows.append([button(tr("restore", language), cb(view, "restore"))])
        else:
            rows.append([button(tr("new", language), cb(view, "new"))])
    elif attempt and attempt.get("phase") == "issued":
        receipt = attempt["receipt"]
        if live_receipt(attempt):
            expires = datetime.fromisoformat(
                receipt["expires_at"].replace("Z", "+00:00")
            ).astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            lines.extend([tr("instructions", language), tr("expires", language) + ": " + expires])
            if binding is not None:
                rows.append([InlineKeyboardButton(text=tr("open", language), url=binding.deep_link)])
            rows.extend([
                [button(tr("restore", language), cb(view, "restore"))],
                [button(tr("resend", language), cb(view, "resend"))],
                [button(tr("revoke", language), cb(view, "revoke"))],
            ])
        else:
            lines.append(tr("expired", language))
            rows.append([button(tr("new", language), cb(view, "new"))])
    else:
        if attempt and attempt.get("phase") == "revoked":
            lines.append(tr("revoked", language))
        rows.append([button(tr("new", language), cb(view, "new"))])
    if mode != "accounts":
        rows.append([button(tr("accounts", language), cb(view, "accounts"))])
    rows.append([button(t("construction_bot_home_btn", language), "CS_HOME")])
    if notice:
        lines.append(notice)
    await show(callback, state, view, "\n\n".join(lines), rows)

async def read_accounts(callback, state, *, old=None, cursor=None, bot=None, event_update=None):
    data = await state.get_data()
    saved = snapshot(data)
    context = data.get(CONTEXT)
    if not isinstance(context, dict) or context.get("telegram_user_id") != callback.from_user.id:
        raise ValueError("Platform context unavailable.")
    tenant = UUID(context["tenant_id"])
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.telegram_accounts(
            telegram_user_id=callback.from_user.id, tenant_id=tenant,
            cursor=cursor,
            expected_user_id=UUID(old["actor_user_id"]) if old else None,
        )
    if not same(await state.get_data(), saved):
        return
    actor = str(UUID(result["actor_user_id"]))
    if old and actor != old["actor_user_id"]:
        raise ValueError("Platform identity changed.")
    view = {
        "telegram_user_id": callback.from_user.id, "tenant_id": str(tenant),
        "actor_user_id": actor, "language": result["language"],
        "mode": "accounts", "nonce": uuid4().hex,
        "next_cursor": result["next_cursor"], "menu_id": callback.message.message_id,
        "cursor": str(cursor) if cursor is not None else None,
        "account_ids": [item["account_id"] for item in result["items"]],
        "account_versions": [item["row_version"] for item in result["items"]],
        "account_statuses": [item["status"] for item in result["items"]],
    }
    await state.update_data(**{VIEW: view})
    await display(callback, state, view, items=result["items"])

async def execute(callback, state, view, *, operation=None, previous=None, bot=None, event_update=None):
    if previous is not None:
        if not owned(view, previous) or not retryable(previous):
            raise ValueError("Binding replay is unavailable.")
        attempt = deepcopy(previous)
    else:
        attempt = {
            key: view[key] for key in ("telegram_user_id", "tenant_id", "actor_user_id")
        }
        attempt.update(
            operation=operation, idempotency_key="tgb:" + uuid4().hex,
            started_at=time(), binding_id=None, expected_row_version=None,
        )
        if operation in ("resend", "revoke"):
            current = (await state.get_data()).get(ATTEMPT)
            if not owned(view, current) or current.get("phase") != "issued":
                raise ValueError("Binding receipt unavailable.")
            receipt = current["receipt"]
            attempt.update(
                binding_id=receipt["binding_id"],
                expected_row_version=receipt["row_version"],
            )
    attempt["phase"] = "pending"
    view = {**view, "mode": "binding", "nonce": uuid4().hex}
    # MemoryStorage copies only the outer dictionary. Keep the in-flight
    # request independent from the stored snapshot until response validation.
    await state.update_data(**{VIEW: deepcopy(view), ATTEMPT: deepcopy(attempt)})
    saved = snapshot(await state.get_data())
    binding = None
    notice = None
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.telegram_binding(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                operation=attempt["operation"],
                idempotency_key=attempt["idempotency_key"],
                binding_id=UUID(attempt["binding_id"]) if attempt["binding_id"] else None,
                expected_row_version=attempt["expected_row_version"],
            )
        if result["actor_user_id"] != view["actor_user_id"]:
            raise ValueError("Binding actor changed.")
        binding = result["binding"]
        # Persist only receipt metadata. The secret-bearing model stays local.
        attempt["receipt"] = binding.model_dump(
            mode="json", exclude={"code", "deep_link"},
        )
        attempt["phase"] = "revoked" if attempt["operation"] == "revoke" else "issued"
    except Exception as error:
        logger.warning(
            "platform_telegram_binding_ui_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        if status in (400, 401, 403, 404, 409, 422) and code != "idempotency_in_progress":
            attempt["phase"] = "failed"
        notice = t("platform_telegram_binding_unavailable", view["language"])
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{ATTEMPT: attempt})
    await display(callback, state, view, binding=binding, notice=notice)

@router.callback_query(F.data.startswith("TGB:"))
async def telegram_binding_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("platform_telegram_binding_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            from handlers.platform_telegram_account_unlink_ui import (
                dispatch as dispatch_unlink, VIEW as UNLINK_VIEW,
            )
            if callback.data.startswith((
                "TGB:unlink:", "TGB:unlink_resume:",
                "TGB:unlink_confirm:", "TGB:unlink_retry:",
            )):
                await dispatch_unlink(callback, state, bot=bot, event_update=event_update)
                return
            unlink_view = (await state.get_data()).get(UNLINK_VIEW)
            if isinstance(unlink_view, dict) and unlink_view.get("telegram_user_id") == callback.from_user.id:
                await state.update_data(**{UNLINK_VIEW: {}})
            if callback.data == "TGB:open":
                await read_accounts(callback, state, bot=bot, event_update=event_update)
                return
            data = await state.get_data()
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) != 3 or not bound(data, view, callback)
                or parts[2] != view.get("nonce")
            ):
                raise ValueError("Telegram binding menu is stale.")
            language = view["language"]
            attempt = data.get(ATTEMPT)
            attempt = attempt if owned(view, attempt) else None
            action = parts[1]
            if action in ("accounts", "next"):
                cursor = UUID(view["next_cursor"]) if action == "next" and view.get("next_cursor") else None
                if action == "next" and cursor is None:
                    raise ValueError("No account continuation.")
                await read_accounts(callback, state, old=view, cursor=cursor, bot=bot, event_update=event_update)
                return
            if action == "restore":
                await execute(callback, state, view, previous=attempt, bot=bot, event_update=event_update)
                return
            if action == "new":
                if attempt and (
                    attempt.get("phase") == "pending" and retryable(attempt)
                    or attempt.get("phase") == "issued" and live_receipt(attempt)
                ):
                    raise ValueError("Recover or replace the existing request.")
                view.update(mode="review", nonce=uuid4().hex)
            elif action == "create" and view["mode"] == "review":
                await execute(callback, state, view, operation="initiate", bot=bot, event_update=event_update)
                return
            elif action == "resend" and attempt and attempt.get("phase") == "issued":
                await execute(callback, state, view, operation="resend", bot=bot, event_update=event_update)
                return
            elif action == "revoke" and attempt and attempt.get("phase") == "issued":
                view.update(
                    mode="revoke_review", nonce=uuid4().hex,
                    revoke_binding_id=attempt["receipt"]["binding_id"],
                    revoke_version=attempt["receipt"]["row_version"],
                )
            elif action == "revoke_confirm" and view["mode"] == "revoke_review":
                if (
                    not attempt or attempt.get("phase") != "issued"
                    or view.get("revoke_binding_id") != attempt["receipt"]["binding_id"]
                    or view.get("revoke_version") != attempt["receipt"]["row_version"]
                ):
                    raise ValueError("Binding revocation review changed.")
                await execute(callback, state, view, operation="revoke", bot=bot, event_update=event_update)
                return
            else:
                raise ValueError("Binding action unavailable.")
            await state.update_data(**{VIEW: view})
            await display(callback, state, view)
        except Exception as error:
            logger.warning(
                "platform_telegram_binding_ui_denied error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("platform_telegram_binding_unavailable", language))
