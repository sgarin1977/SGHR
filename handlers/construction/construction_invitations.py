"""Scoped invitation sender UI; mutations use existing keyed APIs."""
from copy import deepcopy
from datetime import datetime
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from email_validator import validate_email, EmailNotValidError

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_invitations import tr, detail, parse_grant_expiry
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_invitations")
logger = logging.getLogger(__name__)
VIEW = "construction_invitations_view"
ATTEMPT = "construction_invitations_attempt"
CONTEXT = "construction_suite_context"
ORG_SOURCE = "construction_team_view"
PROJECT_SOURCE = "construction_project_team_view"
KEYS = (VIEW, ATTEMPT, CONTEXT, "last_menu_message_id")
# Shared API receipts are retained for 24 hours; keep a one-minute margin.
RETRY_WINDOW = 24 * 60 * 60 - 60


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(view, action, index=None):
    result = "IV:" + action + ":" + view["nonce"]
    return result if index is None else result + ":" + str(index)


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


def same_owner(attempt, view):
    return (
        isinstance(attempt, dict) and attempt.get("status") == "pending"
        and all(attempt.get(key) == view.get(key) for key in (
            "telegram_user_id", "tenant_id", "actor_user_id", "project_id",
        ))
    )


def can_retry(attempt):
    started = attempt.get("attempted_at") if isinstance(attempt, dict) else None
    return (
        type(started) in (int, float)
        and 0 <= time() - started < RETRY_WINDOW
    )


async def invitation_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("IV:"):
        view = (await state.get_data()).get(VIEW)
        if isinstance(view, dict) and view.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def show(event, state, view, text, rows):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, event.from_user.id):
        return
    if len(text) > 3900:
        raise ValueError("Invitation presentation is too large.")
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
    language = view["language"]
    rows = [[button(tr("list", language), cb(view, "refresh"))]]
    rows.append([button(
        t("construction_bot_home_btn", language), "CS_HOME",
    )])
    return rows


async def fetch(state, view, bot, event_update):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, view["telegram_user_id"]):
        raise ValueError("Invitation context is no longer active.")
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.invitation_context(
            telegram_user_id=view["telegram_user_id"],
            tenant_id=UUID(view["tenant_id"]),
            expected_user_id=UUID(view["actor_user_id"]),
            project_id=UUID(view["project_id"]) if view["project_id"] else None,
        )
    if not same(await state.get_data(), snapshot) or result["actor_user_id"] != view["actor_user_id"]:
        raise ValueError("Invitation context changed.")
    return result


async def list_screen(event, state, view, result):
    items = sorted(result["items"], key=lambda item: (
        item["status"] != "pending",
        datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")).timestamp(),
        item["id"],
    ))
    offset = view.get("offset", 0)
    offset = min(max(offset, 0), ((len(items) - 1) // 5) * 5) if items else 0
    page = items[offset:offset + 5]
    view = {
        key: value for key, value in view.items()
        if key not in ("email", "role", "target_id", "expected_row_version", "operation", "notice", "grant_expires_at")
    }
    view.update(
        phase="list", nonce=uuid4().hex, offset=offset,
        ids=[item["id"] for item in page],
        language=result["language"] or view["language"],
    )
    language = view["language"]
    lines = [
        tr("button", language), str(result["organization"])[:120],
        tr("project" if view["project_id"] else "tenant", language),
    ]
    rows = []
    for index, item in enumerate(page):
        lines.append(
            str(index + 1) + ". " + item["email"][:150]
            + "\n" + tr(item["role"], language) + " · " + tr(item["status"], language)
        )
        rows.append([button(
            str(index + 1) + ". " + item["email"][:55], cb(view, "item", index),
        )])
    if not page:
        lines.append(tr("empty", language))
    attempt = (await state.get_data()).get(ATTEMPT)
    if same_owner(attempt, view):
        rows.append([button(tr("retry", language), cb(view, "resume"))])
    elif result["roles"]:
        rows.append([button(tr("create", language), cb(view, "new"))])
    if offset:
        rows.append([button(tr("previous", language), cb(view, "previous"))])
    if offset + 5 < len(items):
        rows.append([button(tr("next", language), cb(view, "next"))])
    rows.append([button(tr("refresh", language), cb(view, "refresh"))])
    rows.append([button(t("construction_bot_home_btn", language), "CS_HOME")])
    await state.update_data(**{VIEW: view})
    await show(event, state, view, "\n\n".join(lines), rows)


async def draft(event, state, view, notice=None):
    language, phase = view["language"], view["phase"]
    rows = []
    if phase == "email":
        text = tr("email", language)
    elif phase == "roles":
        text = tr("role", language)
        rows = [
            [button(tr(role, language), cb(view, "role", index))]
            for index, role in enumerate(view["roles"])
        ]
    elif phase == "review":
        if view["operation"] == "create":
            text = "\n\n".join([
                tr("review", language), view["email"], tr(view["role"], language),
                tr("scope", language) + ": " + tr("project" if view["project_id"] else "tenant", language),
                tr("grant_expires", language) + ": " + (
                    view.get("grant_expires_at", "").replace("T", " ").replace("Z", " UTC")
                    if view.get("grant_expires_at") else tr("unlimited", language)
                ),
                tr("before_accept", language),
            ])
        else:
            text = tr("resend_note" if view["operation"] == "resend" else "revoke_note", language)
        if view["operation"] == "create":
            rows.append([button(tr("grant_date", language), cb(view, "grant"))])
        rows.append([button(tr("save", language), cb(view, "submit"))])
    elif phase == "grant_expiry":
        text = tr("grant_prompt", language)
        rows.append([button(tr("grant_none", language), cb(view, "grant_none"))])
        rows.append([button(tr("grant_back", language), cb(view, "grant_back"))])
    elif phase == "attempt":
        attempt = (await state.get_data()).get(ATTEMPT)
        if not same_owner(attempt, view):
            raise ValueError("Invitation attempt differs.")
        text = tr("uncertain" if can_retry(attempt) else "window_expired", language)
        if can_retry(attempt):
            rows.append([button(tr("retry", language), cb(view, "retry"))])
        rows.append([button(tr("finish", language), cb(view, "finish"))])
    elif phase == "finish":
        text = tr("finish_note", language)
        rows.append([button(tr("finish", language), cb(view, "forget"))])
    elif phase == "done":
        text = tr({
            "create": "created", "resend": "resent", "revoke": "revoked_notice",
        }[view["operation"]], language)
    elif phase == "blocked":
        text = view.get("notice") or t("construction_bot_unavailable", language)
    else:
        raise ValueError("Unknown invitation phase.")
    if notice:
        text = notice + "\n\n" + text
    if phase in ("email", "roles", "review", "grant_expiry"):
        rows.append([button(tr("cancel", language), cb(view, "refresh"))])
    else:
        rows.extend(back_rows(view))
    await show(event, state, view, text, rows)


async def open_screen(callback, state, bot, event_update):
    data = await state.get_data()
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in ("org", "project"):
        raise ValueError("Unknown invitation source.")
    source_key = ORG_SOURCE if parts[1] == "org" else PROJECT_SOURCE
    source, context = data.get(source_key), data.get(CONTEXT)
    if (
        not isinstance(source, dict) or not isinstance(context, dict)
        or source.get("nonce") != parts[2]
        or source.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or source.get("tenant_id") != context.get("tenant_id")
        or source.get("menu_id") != callback.message.message_id
        or data.get("last_menu_message_id") != callback.message.message_id
        or parts[1] == "project" and source.get("phase") != "list"
    ):
        raise ValueError("Stale invitation source.")
    project_id = str(UUID(source["project_id"])) if parts[1] == "project" else None
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
        "project_id": project_id, "menu_id": callback.message.message_id,
        "language": callback.from_user.language_code or "ru",
        "offset": 0, "phase": "list", "ids": [],
    }
    await state.update_data(**{VIEW: view})
    result = await fetch(state, view, bot, event_update)
    await list_screen(callback, state, view, result)


async def run_attempt(callback, state, view, attempt, bot, event_update):
    if not same_owner(attempt, view) or not can_retry(attempt):
        view.update(phase="attempt", nonce=uuid4().hex)
        await state.update_data(**{VIEW: view})
        await draft(callback, state, view)
        return
    view = {key: value for key, value in view.items() if key not in ("email", "role", "grant_expires_at")}
    view.update(phase="writing", nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.invitation_write(
                telegram_user_id=view["telegram_user_id"],
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                project_id=UUID(view["project_id"]) if view["project_id"] else None,
                operation=attempt["operation"], payload=deepcopy(attempt["payload"]),
                idempotency_key=attempt["idempotency_key"],
                invitation_id=UUID(attempt["invitation_id"]) if attempt["invitation_id"] else None,
            )
        if result["actor_user_id"] != view["actor_user_id"]:
            raise ValueError("Invitation receipt actor differs.")
        if not same(await state.get_data(), snapshot):
            return
        view.update(
            phase="done", operation=attempt["operation"],
            invitation_id=result["item"]["id"], nonce=uuid4().hex,
        )
        await state.update_data(**{VIEW: view, ATTEMPT: {}})
    except Exception as error:
        logger.warning(
            "construction_bot_invitation_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), snapshot):
            return
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        if status in (400, 401, 403, 404, 409, 422, 429) and code != "idempotency_in_progress":
            view.update(
                phase="blocked", nonce=uuid4().hex,
                notice=t("construction_bot_unavailable", view["language"]),
            )
            await state.update_data(**{VIEW: view, ATTEMPT: {}})
        else:
            view.update(phase="attempt", nonce=uuid4().hex)
            await state.update_data(**{VIEW: view})
    await draft(callback, state, view)


async def submit(callback, state, view, bot, event_update):
    data = await state.get_data()
    previous = data.get(ATTEMPT)
    if isinstance(previous, dict) and previous.get("status") == "pending":
        if not same_owner(previous, view):
            raise ValueError("Another invitation attempt is pending.")
        view.update(phase="attempt", nonce=uuid4().hex)
        await state.update_data(**{VIEW: view})
        await draft(callback, state, view)
        return
    if view["phase"] != "review":
        raise ValueError("Invitation is not ready.")
    operation = view["operation"]
    if operation == "create":
        deadline = view.get("grant_expires_at")
        if deadline is not None:
            try:
                deadline = parse_grant_expiry(deadline)
            except ValueError:
                view.update(nonce=uuid4().hex)
                await state.update_data(**{VIEW: view})
                await draft(callback, state, view, tr("grant_invalid", view["language"]))
                return
        payload = {
            "email": view["email"], "role": view["role"],
            "scopes": [{
                "scope_type": "project" if view["project_id"] else "tenant",
                "scope_id": view["project_id"] or view["tenant_id"],
            }],
        }
        if deadline is not None:
            payload["grant_expires_at"] = deadline
        invitation_id = None
    elif operation in ("resend", "revoke"):
        payload = {"expected_row_version": view["expected_row_version"]}
        invitation_id = view["target_id"]
    else:
        raise ValueError("Unknown invitation operation.")
    attempt = {
        key: view.get(key) for key in (
            "telegram_user_id", "tenant_id", "actor_user_id", "project_id",
        )
    }
    attempt.update(
        status="pending", operation=operation, payload=payload,
        invitation_id=invitation_id, idempotency_key="bot-invitation-" + uuid4().hex,
        attempted_at=time(),
    )
    await state.update_data(**{ATTEMPT: attempt})
    await run_attempt(callback, state, view, attempt, bot, event_update)


@router.callback_query(F.data.startswith("IV:"))
async def invitation_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith(("IV:org:", "IV:project:")):
                await open_screen(callback, state, bot, event_update)
                return
            data = await state.get_data()
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4) or not bound(data, view, callback.from_user.id)
                or view.get("nonce") != parts[2]
                or view.get("menu_id") != callback.message.message_id
            ):
                raise ValueError("Stale invitation selection.")
            language, action = view["language"], parts[1]
            if action == "submit" and len(parts) == 3:
                await submit(callback, state, view, bot, event_update)
                return
            if action in ("resume", "retry", "finish", "forget") and len(parts) == 3:
                attempt = data.get(ATTEMPT)
                if not same_owner(attempt, view):
                    raise ValueError("Invitation attempt differs.")
                if action == "retry":
                    if view["phase"] != "attempt":
                        raise ValueError("Open the pending attempt first.")
                    await run_attempt(callback, state, view, attempt, bot, event_update)
                    return
                if action == "forget":
                    if view["phase"] != "finish":
                        raise ValueError("Confirm finishing the local attempt.")
                    await state.update_data(**{ATTEMPT: {}})
                    result = await fetch(state, view, bot, event_update)
                    await list_screen(callback, state, view, result)
                    return
                view.update(
                    phase="finish" if action == "finish" else "attempt",
                    nonce=uuid4().hex,
                )
                await state.update_data(**{VIEW: view})
                await draft(callback, state, view)
                return

            if action in ("grant", "grant_none", "grant_back"):
                required_phase = "review" if action == "grant" else "grant_expiry"
                if (
                    len(parts) != 3
                    or view["phase"] != required_phase
                    or view.get("operation") != "create"
                ):
                    raise ValueError("Access expiry selection is no longer active.")
                result = await fetch(state, view, bot, event_update)
                if view.get("role") not in result["roles"]:
                    raise ValueError("Role is no longer delegable.")
                if action == "grant_none":
                    view["grant_expires_at"] = None
                view.update(
                    phase="grant_expiry" if action == "grant" else "review",
                    nonce=uuid4().hex,
                )
                await state.update_data(**{VIEW: view})
                await draft(callback, state, view)
                return

            if action not in ("refresh", "next", "previous", "item", "new", "role", "resend", "revoke"):
                raise ValueError("Unknown invitation action.")
            if action in ("item", "role"):
                if len(parts) != 4:
                    raise ValueError("Selector is required.")
            elif len(parts) != 3:
                raise ValueError("Invitation callback shape differs.")
            if action in ("next", "previous", "item", "new") and view["phase"] != "list":
                raise ValueError("Invitation list is no longer active.")
            if action in ("resend", "revoke") and view["phase"] != "card":
                raise ValueError("Invitation card is no longer active.")
            if action == "role" and view["phase"] != "roles":
                raise ValueError("Choose the email first.")
            result = await fetch(state, view, bot, event_update)
            if action in ("refresh", "next", "previous"):
                if action == "next":
                    view["offset"] += 5
                elif action == "previous":
                    view["offset"] = max(0, view["offset"] - 5)
                await list_screen(callback, state, view, result)
                return
            if action == "new":
                attempt = (await state.get_data()).get(ATTEMPT)
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if same_owner(attempt, view):
                        view.update(phase="attempt", nonce=uuid4().hex)
                    else:
                        await callback.message.answer(tr("other_pending", language))
                        return
                else:
                    if not result["roles"]:
                        raise ValueError("No delegable invitation roles.")
                    view.update(
                        phase="email", roles=result["roles"], operation="create",
                        grant_expires_at=None, nonce=uuid4().hex,
                    )
            elif action == "role":
                if parts[3] not in {str(index) for index in range(len(view["roles"]))}:
                    raise ValueError("Invalid role selector.")
                role = view["roles"][int(parts[3])]
                if role not in result["roles"]:
                    raise ValueError("Role is no longer delegable.")
                view.update(
                    role=role, phase="review", grant_expires_at=None, nonce=uuid4().hex,
                )
            else:
                if action == "item":
                    if parts[3] not in {str(index) for index in range(len(view["ids"]))}:
                        raise ValueError("Invalid invitation selector.")
                    target = view["ids"][int(parts[3])]
                else:
                    target = view.get("target_id")
                item = next((item for item in result["items"] if item["id"] == target), None)
                if item is None:
                    raise ValueError("Invitation is no longer visible.")
                if action == "item":
                    view.update(
                        phase="card", target_id=target,
                        expected_row_version=item["row_version"], nonce=uuid4().hex,
                    )
                    rows = []
                    if item["status"] == "pending":
                        rows = [
                            [button(tr("resend", language), cb(view, "resend"))],
                            [button(tr("revoke", language), cb(view, "revoke"))],
                        ]
                    rows.extend(back_rows(view))
                    await state.update_data(**{VIEW: view})
                    await show(callback, state, view, detail(item, language), rows)
                    return
                attempt = (await state.get_data()).get(ATTEMPT)
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if not same_owner(attempt, view):
                        await callback.message.answer(tr("other_pending", language))
                        return
                    view.update(phase="attempt", nonce=uuid4().hex)
                else:
                    if item["status"] != "pending":
                        raise ValueError("Invitation is terminal.")
                    view.update(
                        phase="review", operation=action, target_id=target,
                        expected_row_version=item["row_version"], nonce=uuid4().hex,
                    )
            await state.update_data(**{VIEW: view})
            await draft(callback, state, view)
        except Exception as error:
            logger.warning(
                "construction_bot_invitations_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("construction_lead_form_stale", language))


class InvitationEmailInput(BaseFilter):
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
            and view.get("phase") in ("email", "grant_expiry")
        )


@router.message(InvitationEmailInput())
async def invitation_email_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await InvitationEmailInput()(message, state):
            return
        view = deepcopy((await state.get_data())[VIEW])
        if view["phase"] == "grant_expiry":
            try:
                deadline = parse_grant_expiry(message.text)
            except ValueError:
                await draft(message, state, view, tr("grant_invalid", view["language"]))
                return
            view.update(
                grant_expires_at=deadline, phase="review", nonce=uuid4().hex,
            )
            await state.update_data(**{VIEW: view})
            await draft(message, state, view)
            return
        try:
            if len(message.text.strip()) > 320:
                raise ValueError("Email is too long.")
            email = validate_email(message.text.strip(), check_deliverability=False).normalized
        except (ValueError, EmailNotValidError):
            await draft(message, state, view, tr("invalid_input", view["language"]))
            return
        view.update(email=email, phase="roles", nonce=uuid4().hex)
        await state.update_data(**{VIEW: view})
        await draft(message, state, view)
