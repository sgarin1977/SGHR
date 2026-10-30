"""Private project participants through existing team and grant APIs."""
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from copy import deepcopy
from aiogram.types import InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from handlers.construction.construction_project_areas import (
    binding, button, back_rows, PROJECT, FORM as PROJECT_FORM, CARD, CATALOG,
)
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_team import (
    tr, clip, member_name, project_team_text,
)
from ui.texts import t
from ui.construction_invitations import parse_grant_expiry, tr as invitation_tr
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_project_team")
date_router = Router(name="construction_project_team_date")
logger = logging.getLogger(__name__)
VIEW = "construction_project_team_view"
ATTEMPT = "construction_project_team_attempt"


async def retain_project_team_attempt(state, previous, user_id):
    value = previous.get(ATTEMPT)
    if (
        isinstance(value, dict) and value.get("telegram_user_id") == user_id
        and value.get("status") == "pending"
    ):
        await state.update_data(**{ATTEMPT: dict(value)})


async def project_team_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith(("AT:", "PAV:", "PT:", "PG:", "PX:", "IV:", "TP:")):
        saved = (await state.get_data()).get(VIEW)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


def selected_from(data, view, callback):
    if not isinstance(view, dict):
        return None
    source_message = getattr(callback, "message", None)
    menu = (
        source_message.message_id if source_message is not None
        else data.get("last_menu_message_id")
    )
    selected = binding(
        data, key=view.get("source_key"), token=view.get("source_token"),
        sender=callback.from_user.id, menu=menu,
    )
    if selected is None or any(
        view.get(key) != selected[key]
        for key in ("telegram_user_id", "tenant_id", "project_id", "actor_user_id")
    ):
        return None
    return selected


def same_attempt(attempt, selected):
    return isinstance(attempt, dict) and all(
        attempt.get(key) == selected[key]
        for key in ("telegram_user_id", "tenant_id", "project_id", "actor_user_id")
    )


async def show(callback, state, selected, view, text, rows, *, expected_snapshot=None):
    before = await state.get_data()
    if expected_snapshot is not None and any(
        before.get(key) != value for key, value in expected_snapshot.items()
    ):
        return
    if selected_from(before, selected, callback) != selected:
        return
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(callback, "message", None) is None:
        sent = await callback.answer(text, reply_markup=markup)
    else:
        sent = await edit_or_replace_menu_message(
            callback=callback, text=text, reply_markup=markup,
        )
    data = await state.get_data()
    if expected_snapshot is not None and any(
        data.get(key) != value for key, value in expected_snapshot.items()
    ):
        return
    if selected_from(data, selected, callback) != selected:
        return
    key = selected["source_key"]
    values = {
        VIEW: {**selected, **view, "nonce": view["nonce"], "menu_id": sent.message_id},
        key: {**data[key], "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    }
    if key == PROJECT:
        values[CARD] = {**data[CARD], "menu_id": sent.message_id}
        from handlers.construction.construction_client_catalog import sync_origin
        values.update(sync_origin(data, data[CARD], sent.message_id))
    if key == CATALOG:
        values["construction_project_catalog_view"] = {
            **data["construction_project_catalog_view"], "menu_id": sent.message_id,
        }
    await state.update_data(**values)
    return sent.message_id


def view_for(selected, phase, **values):
    return {**selected, "phase": phase, "nonce": uuid4().hex, **values}


def rows_back(selected, language, view):
    return [
        [button(tr("list", language), "PT:list:" + view["nonce"])],
        [button(tr("back", language), selected["back"])],
        *back_rows(language),
    ]


async def pending_screen(callback, state, selected, attempt, language, notice=None):
    view = view_for(selected, "pending", attempt_id=attempt["id"])
    safe = 0 <= time() - attempt["started_at"] < 86340
    text = tr("pending" if safe else "expired", language)
    if notice:
        text = notice + "\n\n" + text
    rows = []
    if safe:
        rows.append([button(tr("retry", language), "PT:submit:" + view["nonce"])])
    rows.extend(rows_back(selected, language, view))
    await show(callback, state, selected, view, text, rows)


async def fetch(service, selected, *, selection=False):
    return await service.project_team(
        telegram_user_id=selected["telegram_user_id"],
        tenant_id=UUID(selected["tenant_id"]),
        project_id=UUID(selected["project_id"]),
        expected_user_id=UUID(selected["actor_user_id"]),
        client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
        selection=selection,
    )


@router.callback_query(F.data.startswith("PT:"))
async def project_team_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        selected = None
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            data = await state.get_data()
            parts = callback.data.split(":")
            old = data.get(VIEW, {})
            if len(parts) == 3 and parts[1] == "open":
                choices = [
                    value for key in (PROJECT, PROJECT_FORM, CATALOG)
                    if (value := binding(
                        data, key=key, token=parts[2],
                        sender=callback.from_user.id, menu=callback.message.message_id,
                    )) is not None
                ]
                if len(choices) != 1:
                    raise ValueError("Stale project selector.")
                selected, action, offset = choices[0], "list", 0
                from handlers.construction.construction_area_create import suspend_area_form
                await suspend_area_form(state)
                await state.update_data(**{"construction_project_areas_view": {}})
            else:
                if len(parts) not in (3, 4) or not isinstance(old, dict):
                    raise ValueError("Invalid project team command.")
                if old.get("nonce") != parts[2]:
                    raise ValueError("Stale team command.")
                selected = selected_from(data, old, callback)
                if selected is None:
                    raise ValueError("Team context changed.")
                action, offset = parts[1], 0
                if action in ("page", "pickpage"):
                    if len(parts) != 4:
                        raise ValueError("Invalid page command.")
                    offset = int(parts[3])
                    if (
                        str(offset) != parts[3] or offset < 0
                        or old.get("phase") != ("list" if action == "page" else "choose")
                        or offset not in (
                            0, old["offset"], old["offset"] + 5,
                            max(0, old["offset"] - 5),
                        )
                    ):
                        raise ValueError("Invalid page position.")
                elif action in ("user", "role"):
                    if len(parts) != 4:
                        raise ValueError("Invalid selection command.")
                elif len(parts) != 3:
                    raise ValueError("Unexpected command payload.")

            if action == "submit":
                if old.get("phase") not in ("review", "pending"):
                    raise ValueError("Explicit review required.")
                attempt = data.get(ATTEMPT)
                if old["phase"] == "review":
                    if isinstance(attempt, dict) and attempt.get("status") == "pending":
                        raise ValueError("An earlier operation is unresolved.")
                    deadline = old.get("expires_at")
                    if deadline is not None:
                        deadline = parse_grant_expiry(deadline)
                    attempt = {
                        key: selected[key] for key in (
                            "telegram_user_id", "tenant_id", "project_id", "actor_user_id",
                        )
                    }
                    attempt.update(
                        id=uuid4().hex, target_user_id=old["target_user_id"],
                        role=old["role"], expires_at=deadline,
                        started_at=time(), status="pending",
                    )
                    await state.update_data(**{ATTEMPT: attempt})
                elif (
                    not same_attempt(attempt, selected)
                    or attempt.get("id") != old.get("attempt_id")
                    or attempt.get("status") != "pending"
                ):
                    raise ValueError("Pending command differs.")
                if not 0 <= time() - attempt["started_at"] < 86340:
                    await pending_screen(callback, state, selected, attempt, language)
                    return
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    receipt = await service.create_project_participant(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(selected["tenant_id"]),
                        project_id=UUID(selected["project_id"]),
                        expected_user_id=UUID(selected["actor_user_id"]),
                        target_user_id=UUID(attempt["target_user_id"]),
                        role=attempt["role"], idempotency_key=attempt["id"],
                        expires_at=attempt.get("expires_at"),
                    )
                if (
                    selected_from(await state.get_data(), selected, callback) != selected
                    or receipt["actor_user_id"] != selected["actor_user_id"]
                    or receipt["project_id"] != selected["project_id"]
                    or receipt["item"]["user_id"] != attempt["target_user_id"]
                    or receipt["item"]["role"] != attempt["role"]
                    or receipt["item"]["scope_type"] != "project"
                    or receipt["item"]["scope_id"] != selected["project_id"]
                    or receipt["item"]["status"] != "active"
                    or receipt["item"]["expires_at"] != attempt.get("expires_at")
                ):
                    raise ValueError("Receipt binding differs.")
                await state.update_data(**{
                    ATTEMPT: {**attempt, "status": "done", "grant_id": receipt["item"]["id"]},
                })
                view = view_for(selected, "done")
                await show(
                    callback, state, selected, view, tr("done", language),
                    rows_back(selected, language, view),
                )
                return

            if action in ("add", "resume"):
                if old.get("phase") != "list":
                    raise ValueError("Open the project team first.")
                attempt = data.get(ATTEMPT)
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if not same_attempt(attempt, selected):
                        view = view_for(selected, "blocked")
                        await show(
                            callback, state, selected, view, tr("other_pending", language),
                            rows_back(selected, language, view),
                        )
                    else:
                        await pending_screen(callback, state, selected, attempt, language)
                    return
                if action == "resume":
                    raise ValueError("No pending operation.")

            selection = action in ("add", "pickpage", "user", "role", "noexpiry", "date")
            if action not in (
                "list", "page", "add", "pickpage", "user", "role", "noexpiry", "date",
            ):
                raise ValueError("Unsupported command.")
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await fetch(service, selected, selection=selection)
            if (
                selected_from(await state.get_data(), selected, callback) != selected
                or result["actor_user_id"] != selected["actor_user_id"]
                or result["project_id"] != selected["project_id"]
            ):
                raise ValueError("Project team response differs.")
            language = result["language"] or language
            if not selection:
                total = len(result["items"])
                offset = min(offset, ((total - 1) // 5) * 5) if total else 0
                from ui.construction_team_permissions import member_button
                page_members = result["items"][offset:offset + 5]
                view = view_for(
                    selected, "list", offset=offset,
                    user_ids=[member["user_id"] for member in page_members],
                )
                rows = [[button(
                    member_button(member, offset + index + 1, language),
                    "TP:p:" + view["nonce"] + ":" + str(index),
                )] for index, member in enumerate(page_members)]
                for position, label in (
                    (offset - 5, "previous"), (offset + 5, "next"),
                ):
                    if 0 <= position < total:
                        rows.append([button(
                            tr(label, language), f'PT:page:{view["nonce"]}:{position}',
                        )])
                attempt = (await state.get_data()).get(ATTEMPT)
                if same_attempt(attempt, selected) and attempt.get("status") == "pending":
                    rows.append([button(tr("retry", language), "PT:resume:" + view["nonce"])])
                elif result["can_add"]:
                    rows.append([button(tr("add", language), "PT:add:" + view["nonce"])])
                if any(
                    grant["scope_type"] == "project"
                    and grant["scope_id"] == selected["project_id"]
                    for member in result["items"] for grant in member["grants"]
                ):
                    from ui.construction_project_revoke import tr as revoke_tr
                    rows.append([button(
                        revoke_tr("revoke", language), "PG:open:" + view["nonce"],
                    )])
                    from ui.construction_project_replace import tr as replace_tr
                    rows.append([button(
                        replace_tr("replace", language), "PX:open:" + view["nonce"],
                    )])
                from ui.construction_invitations import tr as invitation_tr
                rows.append([button(
                    invitation_tr("button", language), "IV:project:" + view["nonce"],
                )])
                rows.extend([
                    [button(tr("refresh", language), f'PT:page:{view["nonce"]}:{offset}')],
                    [button(tr("back", language), selected["back"])],
                    *back_rows(language),
                ])
                await show(
                    callback, state, selected, view,
                    project_team_text(result, offset=offset, language=language), rows,
                )
                return

            if result["can_add"] is not True:
                raise ValueError("Candidate discovery unavailable.")
            roles = [option["role"] for option in result["roles"]]
            candidates = result["candidates"]
            if action in ("add", "pickpage"):
                total = len(candidates)
                offset = min(offset, ((total - 1) // 5) * 5) if total else 0
                page = candidates[offset:offset + 5]
                view = view_for(
                    selected, "choose", offset=offset,
                    user_ids=[member["user_id"] for member in page],
                )
                text = "\n\n".join([
                    tr("choose", language), clip(result["organization"]),
                    clip(result["project_name"]),
                    f"{offset + 1}–{offset + len(page)} / {total}" if page else "—",
                ])
                rows = [[button(
                    member_name(member, offset + index + 1, language),
                    f'PT:user:{view["nonce"]}:{index}',
                )] for index, member in enumerate(page)]
                for position, label in ((offset - 5, "previous"), (offset + 5, "next")):
                    if 0 <= position < total:
                        rows.append([button(
                            tr(label, language), f'PT:pickpage:{view["nonce"]}:{position}',
                        )])
            else:
                if action == "user":
                    if old.get("phase") != "choose":
                        raise ValueError("Choose a candidate first.")
                    index = int(parts[3])
                    if str(index) != parts[3] or not 0 <= index < len(old["user_ids"]):
                        raise ValueError("Invalid candidate selector.")
                    target = old["user_ids"][index]
                else:
                    target = old.get("target_user_id")
                member = next((m for m in candidates if m["user_id"] == target), None)
                if member is None:
                    raise ValueError("Candidate no longer available.")
                text = member_name(member, 1, language) + "\n\n"
                if action == "user":
                    view = view_for(selected, "roles", target_user_id=target, roles=roles)
                    text += tr("role", language)
                    rows = [[button(
                        tr(role, language), f'PT:role:{view["nonce"]}:{index}',
                    )] for index, role in enumerate(roles)]
                else:
                    if action == "role":
                        if old.get("phase") != "roles":
                            raise ValueError("Choose a role first.")
                        index = int(parts[3])
                        if str(index) != parts[3] or not 0 <= index < len(old["roles"]):
                            raise ValueError("Invalid role selector.")
                        role = old["roles"][index]
                    else:
                        allowed = ("expiry", "review") if action == "date" else ("expiry", "date")
                        if old.get("phase") not in allowed:
                            raise ValueError("Confirm the access duration first.")
                        role = old["role"]
                    if role not in roles:
                        raise ValueError("Role no longer available.")
                    phase = (
                        "date" if action == "date"
                        else "expiry" if action == "role" else "review"
                    )
                    view = view_for(
                        selected, phase, target_user_id=target, role=role,
                        expires_at=old.get("expires_at") if action == "date" else None,
                    )
                    text += tr(role, language) + "\n\n"
                    if phase in ("expiry", "date"):
                        text += tr("date_prompt" if phase == "date" else "expiry", language)
                        rows = [[button(
                            tr("no_expiry", language), "PT:noexpiry:" + view["nonce"],
                        )]]
                        if phase == "expiry":
                            rows.append([button(
                                tr("date", language), "PT:date:" + view["nonce"],
                            )])
                    else:
                        text += tr("no_expiry", language) + "\n\n" + tr("review", language)
                        rows = [
                            [button(tr("date", language), "PT:date:" + view["nonce"])],
                            [button(tr("confirm", language), "PT:submit:" + view["nonce"])],
                        ]
            rows.extend(rows_back(selected, language, view))
            await show(callback, state, selected, view, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_project_team_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            data = await state.get_data()
            attempt = data.get(ATTEMPT)
            if (
                selected is not None and same_attempt(attempt, selected)
                and attempt.get("status") == "pending"
                and selected_from(data, selected, callback) == selected
            ):
                await pending_screen(
                    callback, state, selected, attempt, language,
                    t("construction_bot_rate_limited" if getattr(error, "status_code", None) == 429
                      else "construction_bot_error", language),
                )
            elif selected is not None and selected_from(data, selected, callback) == selected:
                view = view_for(selected, "error")
                await show(
                    callback, state, selected, view,
                    t("construction_bot_unavailable" if getattr(error, "status_code", None)
                      in (401, 403, 404) else "construction_bot_error", language),
                    rows_back(selected, language, view),
                )
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))

# Project participant deadline input v1.
class ProjectTeamDateInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(VIEW)
        return (
            isinstance(view, dict) and view.get("phase") == "date"
            and view.get("menu_id") == data.get("last_menu_message_id")
            and selected_from(data, view, message) is not None
        )


@date_router.message(ProjectTeamDateInput())
async def project_team_date_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ProjectTeamDateInput()(message, state):
            return
        data = await state.get_data()
        old = deepcopy(data[VIEW])
        selected = selected_from(data, old, message)
        language = message.from_user.language_code or "ru"
        try:
            deadline = parse_grant_expiry(message.text)
        except ValueError:
            await message.answer(invitation_tr("grant_invalid", language))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await fetch(service, selected, selection=True)
            current = await state.get_data()
            if (
                current.get(VIEW) != old
                or selected_from(current, old, message) != selected
                or await state.get_state() is not None
            ):
                return
            if (
                result["actor_user_id"] != selected["actor_user_id"]
                or result["project_id"] != selected["project_id"]
                or result["can_add"] is not True
                or old["role"] not in [item["role"] for item in result["roles"]]
            ):
                raise ValueError("Project grant authority changed.")
            member = next((
                item for item in result["candidates"]
                if item["user_id"] == old["target_user_id"]
            ), None)
            if member is None:
                raise ValueError("Candidate no longer available.")
            language = result["language"] or language
            view = view_for(
                selected, "review", target_user_id=old["target_user_id"],
                role=old["role"], expires_at=deadline,
            )
            text = "\n\n".join([
                member_name(member, 1, language), tr(old["role"], language),
                tr("until", language) + ": " + deadline.replace("T", " ").replace("Z", " UTC"),
                tr("review", language),
            ])
            rows = [
                [button(tr("date", language), "PT:date:" + view["nonce"])],
                [button(tr("confirm", language), "PT:submit:" + view["nonce"])],
                *rows_back(selected, language, view),
            ]
            await show(message, state, selected, view, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_project_date_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            current = await state.get_data()
            if current.get(VIEW) == old:
                await state.update_data(**{VIEW: {}})
            await message.answer(t("construction_bot_unavailable", language))

# project_activity_terminal_v1
