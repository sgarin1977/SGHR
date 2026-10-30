"""One selected tenant grant, explicit confirmation and shared pending replay."""
from copy import deepcopy
from uuid import UUID, uuid4
import logging

from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_organization_access as parent
from handlers.construction import construction_team as team
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_team_transport import Grant
from ui.construction_organization_access import tr
from ui.construction_team import tr as team_tr

logger = logging.getLogger(__name__)
OPERATION = "revoke"


def cb(view, action):
    return "TP:rv:" + view["nonce"] + ":" + action


def is_attempt(value):
    return isinstance(value, dict) and value.get("operation") == OPERATION


def recovery_rows(view, data, language):
    attempt = data.get(parent.ATTEMPT)
    if not is_attempt(attempt) or not parent.pending(data) or not parent.owned(attempt, view):
        return []
    return [[team.button(tr("retry", language), cb(view, "resume"))]]


def original(value):
    grant = Grant.model_validate(value)
    if (
        grant.scope_type != "tenant" or grant.role not in ("OWNER", "ADMIN")
        or (
            grant.expires_at is not None
            and (grant.expires_at.tzinfo is None or grant.expires_at.utcoffset() is None)
        )
    ):
        raise ValueError("Organization revocation grant is invalid.")
    return grant


def phase(view):
    return view.get("phase", "").removeprefix("rights_revoke_")


def receipt(result, attempt):
    from api.routes.construction_access_mutations import GrantMutationResponse
    expected = original(attempt["grant"])
    item = GrantMutationResponse.model_validate(result["item"])
    if (
        result["actor_user_id"] != attempt["actor_user_id"]
        or str(item.user_id) != attempt["target_user_id"]
        or item.id != expected.id or item.role != expected.role
        or item.scope_type != "tenant" or item.scope_id != expected.scope_id
        or str(item.scope_id) != attempt["tenant_id"]
        or item.status != "revoked" or item.expires_at != expected.expires_at
        or (item.expires_at is not None and item.expires_at <= item.granted_at)
    ):
        raise ValueError("Organization revocation receipt differs.")
    return str(item.id)


async def display(callback, state, old, mode, language, *, name=None, **values):
    data = await state.get_data()
    if data.get(team.VIEW) != old or not parent.bound(data, old, callback):
        return
    saved = parent.snapshot(data)
    view = {
        key: old[key] for key in (
            *parent.IDENTITY, "menu_id", "offset", "rights_user_id", "revoke_grant",
        ) if key in old
    }
    view.update(values)
    view.update(nonce=uuid4().hex, phase="rights_revoke_" + mode)
    lines = [tr("revoke", language)]
    if name:
        lines.append(str(name)[:100])
    rows = []
    if mode in ("review", "pending"):
        grant = original(view["revoke_grant"])
        if str(grant.scope_id) != view["tenant_id"]:
            raise ValueError("Foreign organization grant.")
        lines.extend((
            tr(grant.role, language),
            tr("until", language) + ": "
            + (view["revoke_grant"]["expires_at"] or tr("no_expiry", language)),
        ))
    if mode == "review":
        lines.extend((tr("revoke_review", language), tr("revoke_impact", language)))
        rows.append([team.button(tr("revoke_confirm", language), cb(view, "confirm"))])
    elif mode == "pending":
        attempt = data.get(parent.ATTEMPT, {})
        if parent.safe(attempt):
            lines.append(tr("pending", language))
            rows.append([team.button(tr("retry", language), cb(view, "retry"))])
        else:
            lines.append(tr("revoke_expired", language))
    else:
        lines.append(tr("revoke_" + mode, language))
    rows.append([team.button(team_tr("refresh", language), "CT:open")])
    rows.extend(team.back_rows(language))
    sent = await parent.edit_or_replace_menu_message(
        callback=callback, text="\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if parent.same(await state.get_data(), saved):
        await state.update_data(**{
            team.VIEW: {**view, "menu_id": sent.message_id},
            "last_menu_message_id": sent.message_id,
        })


async def show_pending(callback, state, old, language):
    data = await state.get_data()
    attempt = data.get(parent.ATTEMPT)
    if not parent.pending(data):
        return
    if not is_attempt(attempt):
        await parent.show_pending(callback, state, old, language)
        return
    if not parent.owned(attempt, old):
        await display(callback, state, old, "other_pending", language)
        return
    await display(
        callback, state, old, "pending", language,
        rights_user_id=attempt["target_user_id"],
        revoke_grant=deepcopy(attempt["grant"]),
    )


async def submit(callback, state, old, language, *, replay, bot, event_update):
    data = await state.get_data()
    if data.get(team.VIEW) != old or not parent.bound(data, old, callback):
        return
    if replay:
        attempt = deepcopy(data.get(parent.ATTEMPT))
        if (
            not parent.pending(data) or not is_attempt(attempt)
            or not parent.owned(attempt, old)
            or attempt["target_user_id"] != old.get("rights_user_id")
            or original(attempt["grant"]) != original(old["revoke_grant"])
        ):
            return
        if not parent.safe(attempt):
            await show_pending(callback, state, old, language)
            return
    else:
        if phase(old) != "review":
            return
        if parent.pending(data):
            await show_pending(callback, state, old, language)
            return
        frozen = original(old["revoke_grant"])
        if str(frozen.scope_id) != old["tenant_id"]:
            return
        attempt = {
            **{key: old[key] for key in parent.IDENTITY},
            "operation": OPERATION,
            "target_user_id": old["rights_user_id"],
            "grant": frozen.model_dump(mode="json"),
            "role": frozen.role,
            "expires_at": frozen.model_dump(mode="json")["expires_at"],
            "idempotency_key": "org-revoke:" + uuid4().hex,
            "started_at": parent.time(), "status": "pending",
        }
    inflight = {**old, "phase": "rights_revoke_pending"}
    await state.update_data(**{
        team.VIEW: deepcopy(inflight), parent.ATTEMPT: deepcopy(attempt),
    })
    saved = parent.snapshot(await state.get_data())
    mode = "pending"
    try:
        async with parent.construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.revoke_organization_access(
                telegram_user_id=attempt["telegram_user_id"],
                tenant_id=UUID(attempt["tenant_id"]),
                expected_user_id=UUID(attempt["actor_user_id"]),
                target_user_id=UUID(attempt["target_user_id"]),
                original_grant=deepcopy(attempt["grant"]),
                idempotency_key=attempt["idempotency_key"],
            )
        attempt.update(status="done", grant_id=receipt(result, attempt))
        language = result.get("language") or language
        mode = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_organization_revoke_failed error_type=%s status=%s code=%s",
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
    if parent.same(await state.get_data(), saved):
        await state.update_data(**{parent.ATTEMPT: deepcopy(attempt)})
        await display(callback, state, inflight, mode, language)


async def dispatch(callback, state, *, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    old = None
    try:
        parts = callback.data.split(":")
        if (
            len(parts) != 4 or parts[:2] != ["TP", "rv"]
            or parts[3] not in ("review", "resume", "confirm", "retry")
            or await state.get_state() is not None
        ):
            return
        data = await state.get_data()
        old = deepcopy(data.get(team.VIEW))
        if not parent.bound(data, old, callback) or old.get("nonce") != parts[2]:
            return
        action = parts[3]
        if action in ("review", "resume") and parent.pending(data):
            await show_pending(callback, state, old, language)
            return
        if action == "resume":
            return
        if action == "review":
            if old.get("phase") != "rights_permissions":
                return
            grant_id = UUID(old["rights_grant_id"])
            target_id = UUID(old["rights_user_id"])
            saved = parent.snapshot(data)
            async with parent.construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await service.organization_revoke_context(
                    telegram_user_id=old["telegram_user_id"],
                    tenant_id=UUID(old["tenant_id"]),
                    expected_user_id=UUID(old["actor_user_id"]),
                    target_user_id=target_id, grant_id=grant_id,
                )
            if not parent.same(await state.get_data(), saved):
                return
            selected = original(result["grant"])
            if (
                result["actor_user_id"] != old["actor_user_id"]
                or result["target_user_id"] != str(target_id)
                or selected.id != grant_id or str(selected.scope_id) != old["tenant_id"]
            ):
                raise ValueError("Revocation context differs.")
            await display(
                callback, state, old, "review", result.get("language") or language,
                name=result.get("member_name"),
                revoke_grant=selected.model_dump(mode="json"),
            )
            return
        if phase(old) != ("review" if action == "confirm" else "pending"):
            return
        await submit(
            callback, state, old, language, replay=action == "retry",
            bot=bot, event_update=event_update,
        )
    except Exception as error:
        logger.warning(
            "construction_bot_organization_revoke_view_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        data = await state.get_data()
        if isinstance(old, dict) and data.get(team.VIEW) == old and parent.bound(data, old, callback):
            await display(callback, state, old, "denied", language)
