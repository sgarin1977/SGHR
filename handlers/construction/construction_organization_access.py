"""Explicit tenant role assignment through existing grant APIs."""
from copy import deepcopy
from datetime import datetime
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_team as team
from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_invitations import parse_grant_expiry, tr as invitation_tr
from ui.construction_organization_access import tr
from ui.texts import t
from ui.construction_team import tr as team_tr
from utils.telegram_cleanup import edit_or_replace_menu_message

date_router = Router(name="construction_organization_access_date")
logger = logging.getLogger(__name__)
ATTEMPT = "construction_organization_access_attempt"
IDENTITY = ("telegram_user_id", "tenant_id", "actor_user_id")
KEYS = (team.VIEW, team.CONTEXT, ATTEMPT, "last_menu_message_id")

def cb(view, action, value=None):
    text = "TP:ta:" + view["nonce"] + ":" + action
    return text if value is None else text + ":" + str(value)

def owned(attempt, view):
    return (
        isinstance(attempt, dict)
        and all(attempt.get(key) == view.get(key) for key in IDENTITY)
    )

def safe(attempt):
    started = attempt.get("started_at")
    return type(started) in (int, float) and 0 <= time() - started < 86340

def pending(data):
    value = data.get(ATTEMPT)
    return isinstance(value, dict) and value.get("status") == "pending"

def recovery_rows(view, data, language):
    attempt = data.get(ATTEMPT)
    if isinstance(attempt, dict) and attempt.get("operation") == "revoke":
        from handlers.construction.construction_organization_revoke import recovery_rows as revoke_recovery_rows
        return revoke_recovery_rows(view, data, language)
    if not pending(data) or not owned(attempt, view):
        return []
    return [[team.button(tr("retry", language), cb(view, "resume"))]]

def bound(data, view, event):
    context = data.get(team.CONTEXT)
    if not isinstance(view, dict) or not isinstance(context, dict):
        return False
    try:
        UUID(view["tenant_id"])
        UUID(view["actor_user_id"])
        message = getattr(event, "message", None)
        return (
            view["telegram_user_id"] == event.from_user.id
            == context.get("telegram_user_id")
            and view["tenant_id"] == context.get("tenant_id")
            and view["menu_id"] == data.get("last_menu_message_id")
            and (message is None or message.message_id == view["menu_id"])
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        return False

def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})

def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())

def phase(view):
    value = view.get("phase", "")
    prefix = "rights_assign_"
    return value[len(prefix):] if value.startswith(prefix) else ""

def original_of(value):
    from services.construction.construction_team_transport import Grant
    raw = value.get("original_grant")
    if raw is None:
        return None
    item = Grant.model_validate(raw)
    if (
        item.role not in ("OWNER", "ADMIN") or item.scope_type != "tenant"
        or str(item.scope_id) != value["tenant_id"]
        or (
            item.expires_at is not None
            and (item.expires_at.tzinfo is None or item.expires_at.utcoffset() is None)
        )
    ):
        raise ValueError("Original organization grant differs.")
    return item

def no_change(view):
    original = original_of(view)
    if original is None:
        return False
    deadline = view.get("assign_expires_at")
    deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00")) if deadline else None
    return view.get("assign_role") == original.role and deadline == original.expires_at

def receipt(result, attempt):
    from api.routes.construction_access_mutations import GrantMutationResponse
    item = GrantMutationResponse.model_validate(result["item"])
    original = original_of(attempt)
    deadline = attempt["expires_at"]
    deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00")) if deadline else None
    if (
        result["actor_user_id"] != attempt["actor_user_id"]
        or str(item.user_id) != attempt["target_user_id"]
        or str(item.scope_id) != attempt["tenant_id"]
        or item.scope_type != "tenant" or item.role != attempt["role"]
        or item.status != "active" or item.expires_at != deadline
        or (original is not None and item.id == original.id)
        or (item.expires_at is not None and item.expires_at <= item.granted_at)
    ):
        raise ValueError("Organization grant receipt differs.")
    return str(item.id)

async def display(event, state, old, mode, language, *, name=None, **values):
    data = await state.get_data()
    if data.get(team.VIEW) != old or not bound(data, old, event):
        return
    saved = snapshot(data)
    view = {
        key: value for key, value in old.items()
        if key not in ("user_ids", "rights_grant_ids", "rights_grant_id")
    }
    view.update(values)
    view.update(phase="rights_assign_" + mode, nonce=uuid4().hex)
    original = original_of(view)
    lines = [tr("replace" if original is not None else "assign", language)]
    if name:
        lines.append(str(name)[:100])
    if original is not None:
        lines.extend((
            tr("old_role", language) + ": " + tr(original.role, language),
            tr("old_expiry", language) + ": "
            + (view["original_grant"]["expires_at"] or tr("no_expiry", language)),
        ))
    rows = []
    if mode == "roles":
        roles = view["assign_roles"]
        lines.append(tr("role" if roles else "empty", language))
        rows.extend([team.button(tr(role, language), cb(view, "role", index))]
                    for index, role in enumerate(roles))
    elif mode in ("expiry", "date"):
        lines.extend((tr(view["assign_role"], language),
                      tr("date_prompt" if mode == "date" else "expiry", language)))
        rows.append([team.button(tr("no_expiry", language), cb(view, "none"))])
        if original is not None:
            rows.append([team.button(tr("keep_expiry", language), cb(view, "keep"))])
        if mode == "expiry":
            rows.append([team.button(tr("date", language), cb(view, "date"))])
    elif mode == "review":
        if original is not None:
            lines.extend((
                tr("new_role", language) + ": " + tr(view["assign_role"], language),
                tr("new_expiry", language) + ": "
                + (view["assign_expires_at"] or tr("no_expiry", language)),
                tr("review_access", language),
            ))
        else:
            lines.extend((
                tr(view["assign_role"], language),
                tr("until", language) + ": "
                + (view["assign_expires_at"] or tr("no_expiry", language)),
                tr("review", language),
            ))
        rows.append([team.button(tr("date", language), cb(view, "date"))])
        if no_change(view):
            lines.append(tr("unchanged", language))
        else:
            rows.append([team.button(
                tr("confirm_replace" if original is not None else "confirm", language),
                cb(view, "submit"),
            )])
    elif mode == "pending":
        attempt = data.get(ATTEMPT, {})
        if safe(attempt):
            lines.extend((
                tr(attempt["role"], language),
                tr("until", language) + ": "
                + (attempt["expires_at"] or tr("no_expiry", language)),
                tr("pending", language),
            ))
            rows.append([team.button(tr("retry", language), cb(view, "submit"))])
        else:
            lines.append(tr("expired", language))
    else:
        key = "replace_" + mode if original is not None and mode in ("done", "denied") else mode
        lines.append(tr(key, language))
    rows.append([team.button(team_tr("refresh", language), "CT:open")])
    rows.extend(team.back_rows(language))
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(event, "message", None) is None:
        sent = await event.answer("\n\n".join(lines), reply_markup=markup)
    else:
        sent = await edit_or_replace_menu_message(
            callback=event, text="\n\n".join(lines), reply_markup=markup,
        )
    if same(await state.get_data(), saved):
        await state.update_data(**{
            team.VIEW: {**view, "menu_id": sent.message_id},
            "last_menu_message_id": sent.message_id,
        })

async def show_pending(event, state, old, language):
    data = await state.get_data()
    attempt = data.get(ATTEMPT)
    if isinstance(attempt, dict) and attempt.get("operation") == "revoke":
        from handlers.construction.construction_organization_revoke import show_pending as show_revoke_pending
        await show_revoke_pending(event, state, old, language)
        return
    if not pending(data) or not owned(attempt, old):
        await display(event, state, old, "other_pending", language)
        return
    await display(
        event, state, old, "pending", language,
        rights_user_id=attempt["target_user_id"],
        original_grant=deepcopy(attempt.get("original_grant")),
        assign_role=attempt["role"], assign_expires_at=attempt["expires_at"],
    )

async def fetch(event, old, *, bot, event_update, grant_id=None):
    expected = original_of(old) if phase(old) else None
    selected_id = grant_id if grant_id is not None else expected.id if expected else None
    arguments = {
        "telegram_user_id": event.from_user.id,
        "tenant_id": UUID(old["tenant_id"]),
        "expected_user_id": UUID(old["actor_user_id"]),
        "target_user_id": UUID(old["rights_user_id"]),
    }
    if selected_id is not None:
        arguments["grant_id"] = selected_id
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.organization_access_context(**arguments)
    if (
        result["actor_user_id"] != old["actor_user_id"]
        or not isinstance(result["roles"], list)
        or len(set(result["roles"])) != len(result["roles"])
        or any(role not in ("OWNER", "ADMIN") for role in result["roles"])
    ):
        raise ValueError("Organization role context differs.")
    current = original_of({
        "tenant_id": old["tenant_id"],
        "original_grant": result.get("original_grant"),
    })
    if selected_id is not None:
        if current is None or current.id != selected_id or (expected is not None and current != expected):
            raise ValueError("The original organization grant changed.")
        result["original_grant"] = current.model_dump(mode="json")
    elif current is not None:
        raise ValueError("Unexpected original grant.")
    return result

async def submit(callback, state, old, language, *, bot, event_update):
    data = await state.get_data()
    if pending(data) and data[ATTEMPT].get("operation") == "revoke":
        # Another action must never execute a pending revocation as creation
        # or replacement. Only its dedicated retry command performs the RPC.
        await show_pending(callback, state, old, language)
        return
    replay = phase(old) == "pending"
    if replay:
        attempt = deepcopy(data.get(ATTEMPT))
        if (
            not pending(data) or not owned(attempt, old)
            or attempt.get("target_user_id") != old.get("rights_user_id")
            or original_of(attempt) != original_of(old)
        ):
            return
        if not safe(attempt):
            await display(callback, state, old, "pending", language)
            return
    else:
        if phase(old) != "review":
            return
        if pending(data):
            await show_pending(callback, state, old, language)
            return
        if no_change(old):
            await display(callback, state, old, "review", language)
            return
        deadline = old["assign_expires_at"]
        if deadline is not None:
            deadline = parse_grant_expiry(deadline)
        attempt = {
            **{key: old[key] for key in IDENTITY},
            "target_user_id": old["rights_user_id"],
            "role": old["assign_role"], "expires_at": deadline,
            "idempotency_key": "org-grant:" + uuid4().hex,
            "started_at": time(), "status": "pending",
        }
    if not replay and original_of(old) is not None:
        attempt["original_grant"] = deepcopy(old["original_grant"])
    inflight = {**old, "phase": "rights_assign_pending"}
    await state.update_data(**{
        team.VIEW: deepcopy(inflight), ATTEMPT: deepcopy(attempt),
    })
    saved = snapshot(await state.get_data())
    mode = "pending"
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            original = original_of(attempt)
            write = (
                service.replace_organization_access
                if original is not None else service.create_organization_access
            )
            result = await write(
                telegram_user_id=attempt["telegram_user_id"],
                tenant_id=UUID(attempt["tenant_id"]),
                expected_user_id=UUID(attempt["actor_user_id"]),
                target_user_id=UUID(attempt["target_user_id"]),
                role=attempt["role"], expires_at=attempt["expires_at"],
                idempotency_key=attempt["idempotency_key"],
                **({"original_grant": deepcopy(attempt["original_grant"])}
                   if original is not None else {}),
            )
        identifier = receipt(result, attempt)
        attempt.update(status="done", grant_id=identifier)
        language = result.get("language") or language
        mode = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_organization_access_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if (
            not replay and isinstance(error, ConstructionApiClientError)
            and error.status_code in (400, 401, 403, 404, 409, 422)
            and error.code != "idempotency_in_progress"
        ):
            attempt["status"] = "denied"
            mode = "denied"
    if same(await state.get_data(), saved):
        await state.update_data(**{ATTEMPT: deepcopy(attempt)})
        await display(callback, state, inflight, mode, language)

async def dispatch(callback, state, *, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    old = None
    try:
        parts = callback.data.split(":")
        if (
            len(parts) not in (4, 5) or parts[:2] != ["TP", "ta"]
            or await state.get_state() is not None
        ):
            return
        action = parts[3]
        if action not in ("open", "replace", "resume", "role", "date", "none", "keep", "submit"):
            return
        if len(parts) != (5 if action == "role" else 4):
            return
        data = await state.get_data()
        old = deepcopy(data.get(team.VIEW))
        if not bound(data, old, callback) or old.get("nonce") != parts[2]:
            return
        if action in ("open", "replace", "resume") and pending(data):
            await show_pending(callback, state, old, language)
            return
        if action == "resume":
            return
        if action == "submit":
            await submit(callback, state, old, language, bot=bot, event_update=event_update)
            return
        if (
            action == "open" and old.get("phase") != "rights_scopes"
            or action == "replace" and old.get("phase") != "rights_permissions"
            or action == "role" and phase(old) != "roles"
            or action == "date" and phase(old) not in ("expiry", "review")
            or action in ("none", "keep") and phase(old) not in ("expiry", "date", "review")
        ):
            return
        if action == "keep" and original_of(old) is None:
            return
        selected_id = UUID(old["rights_grant_id"]) if action == "replace" else None
        saved = snapshot(data)
        result = await fetch(
            callback, old, bot=bot, event_update=event_update, grant_id=selected_id,
        )
        if not same(await state.get_data(), saved):
            return
        language = result.get("language") or language
        if action in ("open", "replace"):
            await display(
                callback, state, old, "roles", language,
                name=result.get("member_name"), assign_roles=result["roles"],
                original_grant=result.get("original_grant") if action == "replace" else None,
            )
            return
        if action == "role":
            index = int(parts[4])
            if str(index) != parts[4] or not 0 <= index < len(old["assign_roles"]):
                return
            role = old["assign_roles"][index]
        else:
            role = old["assign_role"]
        if role not in result["roles"]:
            raise ValueError("The role is no longer available.")
        original = original_of(old)
        if action == "none":
            deadline = None
        elif action in ("role", "keep"):
            deadline = old["original_grant"]["expires_at"] if original is not None else None
        else:
            deadline = old.get("assign_expires_at")
        mode = "expiry" if action == "role" else "date" if action == "date" else "review"
        await display(
            callback, state, old, mode, language,
            name=result.get("member_name"), assign_role=role, assign_expires_at=deadline,
        )
    except Exception as error:
        logger.warning(
            "construction_bot_organization_access_view_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        data = await state.get_data()
        if isinstance(old, dict) and data.get(team.VIEW) == old and bound(data, old, callback):
            await display(callback, state, old, "denied", language)

class OrganizationAccessDateInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(team.VIEW)
        return isinstance(view, dict) and phase(view) == "date" and bound(data, view, message)

@date_router.message(OrganizationAccessDateInput())
async def date_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await OrganizationAccessDateInput()(message, state):
            return
        old = deepcopy((await state.get_data())[team.VIEW])
        language = message.from_user.language_code or "ru"
        try:
            deadline = parse_grant_expiry(message.text)
        except ValueError:
            await message.answer(invitation_tr("grant_invalid", language))
            return
        saved = snapshot(await state.get_data())
        try:
            result = await fetch(message, old, bot=bot, event_update=event_update)
            if not same(await state.get_data(), saved):
                return
            if old["assign_role"] not in result["roles"]:
                raise ValueError("The role is no longer available.")
            await display(
                message, state, old, "review", result.get("language") or language,
                name=result.get("member_name"), assign_expires_at=deadline,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_organization_access_date_failed error_type=%s",
                type(error).__name__,
            )
            if same(await state.get_data(), saved):
                await display(message, state, old, "denied", language)
