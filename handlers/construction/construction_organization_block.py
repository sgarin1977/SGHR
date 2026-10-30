"""One confirmed unkeyed block; uncertain requests are never resubmitted."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_organization_access as parent
from handlers.construction import construction_team as team
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_organization_access_api import validate_block_data
from ui.construction_organization_access import tr
from ui.construction_team import tr as team_tr

logger = logging.getLogger(__name__)
ATTEMPT = "construction_organization_block_attempt"
PENDING_KEYS = (
    parent.ATTEMPT,
    "construction_organization_remove_attempt",
    "construction_project_team_attempt",
    "construction_project_replace_attempt",
    "construction_project_revoke_attempt",
)
KEYS = (team.VIEW, team.CONTEXT, "last_menu_message_id", ATTEMPT, *PENDING_KEYS)


def cb(view, action):
    return "TP:bl:" + view["nonce"] + ":" + action


def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})


def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())


def other_pending(data):
    return any(
        isinstance(data.get(key), dict) and data[key].get("status") == "pending"
        for key in PENDING_KEYS
    )


def phase(view):
    return view.get("phase", "").removeprefix("rights_block_")


def recovery_rows(view, data, language):
    attempt = data.get(ATTEMPT)
    if (
        not parent.owned(attempt, view)
        or attempt.get("status") not in ("sending", "uncertain")
    ):
        return []
    return [[team.button(tr("block_inspect", language), cb(view, "inspect"))]]


def validate_context(result, view, target_id):
    counts = result.get("scope_counts")
    if (
        result.get("actor_user_id") != view["actor_user_id"]
        or result.get("target_user_id") != str(target_id)
        or not isinstance(counts, dict)
        or set(counts) != {"tenant", "project", "document"}
        or any(type(value) is not int or value < 0 for value in counts.values())
    ):
        raise ValueError("Construction block context differs.")
    return dict(counts)


async def display(callback, state, old, mode, language, *, name=None, **values):
    data = await state.get_data()
    if data.get(team.VIEW) != old or not parent.bound(data, old, callback):
        return
    saved = snapshot(data)
    view = {
        key: old[key] for key in (
            *parent.IDENTITY, "menu_id", "offset", "rights_user_id",
            "scope_counts", "attempted",
        ) if key in old
    }
    view.update(values)
    view.update(phase="rights_block_" + mode, nonce=uuid4().hex)
    lines = [tr("block", language)]
    if name:
        lines.append(str(name)[:100])
    if mode in ("review", "observed"):
        lines.append(tr("block_counts", language))
        lines.extend(
            team_tr(kind, language) + ": " + str(view["scope_counts"][kind])
            for kind in ("tenant", "project", "document")
        )
    rows = []
    if mode == "review":
        lines.extend((tr("block_review", language), tr("block_impact", language)))
        rows.append([team.button(tr("block_confirm", language), cb(view, "confirm"))])
    elif mode == "done":
        lines.append(tr("block_done", language) + " " + str(values["revoked_count"]))
    else:
        lines.append(tr("block_" + mode, language))
    if mode in ("uncertain", "observed"):
        rows.append([team.button(tr("block_inspect", language), cb(view, "inspect"))])
    rows.append([team.button(team_tr("refresh", language), "CT:open")])
    rows.extend(team.back_rows(language))
    sent = await parent.edit_or_replace_menu_message(
        callback=callback, text="\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if same(await state.get_data(), saved):
        await state.update_data(**{
            team.VIEW: {**view, "menu_id": sent.message_id},
            "last_menu_message_id": sent.message_id,
        })


async def submit(callback, state, old, language, *, bot, event_update):
    data = await state.get_data()
    if (
        data.get(team.VIEW) != old or not parent.bound(data, old, callback)
        or phase(old) != "review" or old.get("attempted") is not False
    ):
        return
    if other_pending(data):
        await display(callback, state, old, "other_pending", language)
        return
    attempt = {
        **{key: old[key] for key in parent.IDENTITY},
        "id": uuid4().hex, "target_user_id": old["rights_user_id"],
        "status": "sending",
    }
    inflight = {
        **old, "attempted": True,
        "nonce": uuid4().hex, "phase": "rights_block_sending",
    }
    # Independent copies are necessary with aiogram MemoryStorage.
    await state.update_data(**{
        ATTEMPT: deepcopy(attempt), team.VIEW: deepcopy(inflight),
    })
    saved = snapshot(await state.get_data())
    mode, revoked_count = "uncertain", None
    try:
        async with parent.construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.block_organization_access(
                telegram_user_id=attempt["telegram_user_id"],
                tenant_id=UUID(attempt["tenant_id"]),
                expected_user_id=UUID(attempt["actor_user_id"]),
                target_user_id=UUID(attempt["target_user_id"]),
            )
        item = validate_block_data(
            result["item"], target_user_id=UUID(attempt["target_user_id"]),
        )
        if result["actor_user_id"] != attempt["actor_user_id"]:
            raise ValueError("Block response actor differs.")
        revoked_count = len(item.revoked_grant_ids)
        attempt.update(status="done", revoked_count=revoked_count)
        language = result.get("language") or language
        mode = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_organization_block_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        definitive = (
            isinstance(error, ConstructionApiClientError)
            and error.status_code in (400, 401, 403, 404, 409, 422, 429)
        )
        mode = "denied" if definitive else "uncertain"
        attempt["status"] = mode
    if same(await state.get_data(), saved):
        await state.update_data(**{ATTEMPT: deepcopy(attempt)})
        await display(
            callback, state, inflight, mode, language, revoked_count=revoked_count,
        )


async def dispatch(callback, state, *, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    old = None
    try:
        parts = callback.data.split(":")
        if (
            len(parts) != 4 or parts[:2] != ["TP", "bl"]
            or parts[3] not in ("review", "confirm", "inspect")
            or await state.get_state() is not None
        ):
            return
        data = await state.get_data()
        old = deepcopy(data.get(team.VIEW))
        if not parent.bound(data, old, callback) or old.get("nonce") != parts[2]:
            return
        action = parts[3]
        if action == "confirm":
            await submit(callback, state, old, language, bot=bot, event_update=event_update)
            return
        if action == "review":
            if old.get("phase") != "rights_scopes":
                return
            if other_pending(data):
                await display(callback, state, old, "other_pending", language)
                return
            target_id = UUID(old["rights_user_id"])
        else:
            attempt = data.get(ATTEMPT)
            if not parent.owned(attempt, old):
                return
            target_id = UUID(attempt["target_user_id"])
        saved = snapshot(data)
        async with parent.construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.organization_block_context(
                telegram_user_id=old["telegram_user_id"],
                tenant_id=UUID(old["tenant_id"]),
                expected_user_id=UUID(old["actor_user_id"]),
                target_user_id=target_id,
                **({"allow_absent": True} if action == "inspect" else {}),
            )
        if not same(await state.get_data(), saved):
            return
        counts = validate_context(result, old, target_id)
        if action == "review" and sum(counts.values()) == 0:
            raise ValueError("No current Construction roles to review.")
        await display(
            callback, state, old,
            "observed" if action == "inspect" else "review",
            result.get("language") or language,
            name=result.get("member_name"), rights_user_id=str(target_id),
            scope_counts=counts, attempted=action == "inspect",
        )
    except Exception as error:
        logger.warning(
            "construction_bot_organization_block_view_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        data = await state.get_data()
        if isinstance(old, dict) and data.get(team.VIEW) == old and parent.bound(data, old, callback):
            await display(callback, state, old, "denied", language)
