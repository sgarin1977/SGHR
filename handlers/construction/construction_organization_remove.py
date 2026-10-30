"""Confirmed organization removal through the existing Platform API."""
from copy import deepcopy
import logging
from time import time
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_team as team
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_organization_remove import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

logger = logging.getLogger(__name__)
ATTEMPT = "construction_organization_remove_attempt"
IDENTITY = ("telegram_user_id", "tenant_id", "actor_user_id")
KEYS = (team.VIEW, team.CONTEXT, "last_menu_message_id", ATTEMPT)

def cb(view, action):
    return "TP:rm:" + view["nonce"] + ":" + action

def owned(attempt, view):
    return (
        isinstance(attempt, dict)
        and all(attempt.get(key) == view.get(key) for key in IDENTITY)
    )

def retryable(attempt):
    started = attempt.get("started_at")
    return (
        type(started) in (float, int)
        and 0 <= time() - started < 86340
    )

def pending(data, view):
    attempt = data.get(ATTEMPT)
    return owned(attempt, view) and attempt.get("status") == "pending"

def recovery_rows(view, data, language):
    if not pending(data, view):
        return []
    return [[team.button(tr("retry", language), cb(view, "resume"))]]

def bound(data, view, callback):
    context = data.get(team.CONTEXT)
    if not isinstance(view, dict) or not isinstance(context, dict):
        return False
    try:
        UUID(view["tenant_id"])
        UUID(view["actor_user_id"])
        return (
            view["telegram_user_id"] == callback.from_user.id
            == context.get("telegram_user_id")
            and view["tenant_id"] == context.get("tenant_id")
            and view["menu_id"] == callback.message.message_id
            == data.get("last_menu_message_id")
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False

def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})

def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())

async def display(callback, state, old, phase, language, *, name=None):
    data = await state.get_data()
    if data.get(team.VIEW) != old or not bound(data, old, callback):
        return
    saved = snapshot(data)
    view = {
        key: value for key, value in old.items()
        if key not in ("user_ids", "rights_grant_ids", "rights_grant_id")
    }
    view.update(nonce=uuid4().hex, phase="rights_removal_" + phase)
    lines, rows = [tr("button", language)], []
    if phase == "review":
        if name:
            lines.append(str(name)[:100])
        lines.extend((tr("review", language), tr("authority", language)))
        if view["rights_user_id"] == view["actor_user_id"]:
            lines.append(tr("self", language))
        rows.append([team.button(tr("confirm", language), cb(view, "confirm"))])
        rows.append([team.button(tr("cancel", language), "CT:open")])
    elif phase == "pending":
        attempt = data.get(ATTEMPT, {})
        if retryable(attempt):
            lines.append(tr("pending", language))
            rows.append([team.button(tr("retry", language), cb(view, "retry"))])
        else:
            lines.append(tr("expired", language))
    else:
        lines.append(tr(phase, language))
    rows.append([team.button(tr("team", language), "CT:open")])
    rows.extend(team.back_rows(language))
    sent = await edit_or_replace_menu_message(
        callback=callback, text="\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if same(await state.get_data(), saved):
        await state.update_data(**{
            team.VIEW: {**view, "menu_id": sent.message_id},
            "last_menu_message_id": sent.message_id,
        })

async def show_pending(callback, state, old, attempt, language):
    view = {**old, "rights_user_id": attempt["target_user_id"]}
    await state.update_data(**{team.VIEW: deepcopy(view)})
    await display(callback, state, view, "pending", language)

async def execute(callback, state, old, language, *, replay):
    data = await state.get_data()
    if data.get(team.VIEW) != old or not bound(data, old, callback):
        return
    if replay:
        attempt = deepcopy(data.get(ATTEMPT))
        if (
            not owned(attempt, old)
            or attempt.get("status") != "pending"
            or attempt.get("target_user_id") != old.get("rights_user_id")
        ):
            return
        if not retryable(attempt):
            await display(callback, state, old, "pending", language)
            return
    else:
        if old.get("phase") != "rights_removal_review":
            return
        if pending(data, old):
            await show_pending(callback, state, old, data[ATTEMPT], language)
            return
        attempt = {
            **{key: old[key] for key in IDENTITY},
            "target_user_id": old["rights_user_id"],
            "idempotency_key": "org-remove:" + uuid4().hex,
            "started_at": time(), "status": "pending",
        }
    UUID(attempt["target_user_id"])
    # Store independent objects: MemoryStorage copies only the outer mapping.
    inflight = {**old, "phase": "rights_removal_pending"}
    await state.update_data(**{
        team.VIEW: deepcopy(inflight), ATTEMPT: deepcopy(attempt),
    })
    saved = snapshot(await state.get_data())
    return inflight, attempt, saved

async def submit(callback, state, old, language, *, replay, bot, event_update):
    prepared = await execute(callback, state, old, language, replay=replay)
    if prepared is None:
        return
    inflight, attempt, saved = prepared
    phase = "pending"
    try:
        async with construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.remove_organization_member(
                telegram_user_id=attempt["telegram_user_id"],
                tenant_id=UUID(attempt["tenant_id"]),
                expected_user_id=UUID(attempt["actor_user_id"]),
                target_user_id=UUID(attempt["target_user_id"]),
                idempotency_key=attempt["idempotency_key"],
            )
        item = result["item"]
        if (
            result["actor_user_id"] != attempt["actor_user_id"]
            or not isinstance(item, dict)
            or set(item) != {"user_id", "changed"}
            or item["user_id"] != attempt["target_user_id"]
            or type(item["changed"]) is not bool
        ):
            raise ValueError("Removal receipt differs.")
        attempt.update(status="done", changed=item["changed"])
        language = result.get("language") or language
        phase = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_organization_remove_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        definitive = (
            isinstance(error, ConstructionApiClientError)
            and error.status_code in (400, 401, 403, 404, 409, 422)
            and error.code != "idempotency_in_progress"
        )
        if definitive and not replay:
            attempt["status"] = "denied"
            phase = "denied"
        # After an earlier uncertain outcome, a later denial is not evidence
        # that the original operation did not commit. Preserve its exact key.
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{ATTEMPT: deepcopy(attempt)})
    await display(callback, state, inflight, phase, language)

async def dispatch(callback, state, *, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    old = None
    try:
        parts = callback.data.split(":")
        if (
            len(parts) != 4 or parts[:2] != ["TP", "rm"]
            or parts[3] not in ("open", "resume", "confirm", "retry")
            or await state.get_state() is not None
        ):
            return
        data = await state.get_data()
        old = deepcopy(data.get(team.VIEW))
        if (
            not bound(data, old, callback)
            or old.get("nonce") != parts[2]
        ):
            return
        action = parts[3]
        if action in ("open", "resume") and pending(data, old):
            await show_pending(callback, state, old, data[ATTEMPT], language)
            return
        if action == "resume":
            return
        if action == "open":
            if old.get("phase") != "rights_scopes":
                return
            target_id = UUID(old["rights_user_id"])
            saved = snapshot(data)
            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await service.team_members(
                    telegram_user_id=old["telegram_user_id"],
                    tenant_id=UUID(old["tenant_id"]),
                    expected_user_id=UUID(old["actor_user_id"]),
                )
            if not same(await state.get_data(), saved):
                return
            if result["actor_user_id"] != old["actor_user_id"]:
                raise ValueError("Organization actor changed.")
            member = next((
                value for value in result["items"]
                if value["user_id"] == str(target_id)
            ), None)
            if member is None:
                raise ValueError("Selected member is no longer visible.")
            language = result.get("language") or language
            await display(
                callback, state, old, "review", language,
                name=member.get("display_name"),
            )
            return
        required = (
            "rights_removal_review" if action == "confirm"
            else "rights_removal_pending"
        )
        if old.get("phase") != required:
            return
        await submit(
            callback, state, old, language, replay=action == "retry",
            bot=bot, event_update=event_update,
        )
    except Exception as error:
        logger.warning(
            "construction_bot_organization_remove_view_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        data = await state.get_data()
        if isinstance(old, dict) and data.get(team.VIEW) == old and bound(data, old, callback):
            await display(callback, state, old, "denied", language)
