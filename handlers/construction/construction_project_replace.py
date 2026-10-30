"""Explicit project-role replacement through the existing atomic API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter

from handlers.construction import construction_project_team as team
from handlers.construction import construction_project_revoke as revoke
from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_team_transport import Grant
from ui.construction_project_replace import tr
from ui.construction_project_team import clip, member_name
from ui.texts import t
from ui.construction_invitations import parse_grant_expiry, tr as invitation_tr
from ui.construction_project_team import tr as team_tr

router = Router(name="construction_project_replace")
logger = logging.getLogger(__name__)
ATTEMPT = "construction_project_replace_attempt"


async def retain_project_replace_attempt(state, previous, user_id):
    value = previous.get(ATTEMPT)
    if (
        isinstance(value, dict) and value.get("telegram_user_id") == user_id
        and value.get("status") == "pending"
    ):
        await state.update_data(**{ATTEMPT: deepcopy(value)})


def available_choices(result, project_id):
    # A delegable current role may keep its role and change only its expiry.
    return revoke.choices(result, project_id)


def current(data, selected, callback, nonce):
    view = data.get(team.VIEW)
    return (
        isinstance(view, dict) and view.get("nonce") == nonce
        and team.selected_from(data, selected, callback) == selected
    )


async def pending_screen(callback, state, selected, attempt, language, notice=None):
    if "new_expires_at" in attempt["command"]:
        deadline = attempt["command"]["new_expires_at"]
        caption = tr("deadline", language) + ": " + (
            deadline or tr("no_expiry", language)
        )
        notice = caption + ("\n\n" + notice if notice else "")
    await revoke.pending_screen(
        callback, state, selected, attempt, language, notice,
        namespace="PX", phase="replace_pending",
    )


def receipt_matches(result, selected, command):
    from api.routes.construction_access_mutations import GrantMutationResponse

    original = Grant.model_validate(command["grant"])
    from api.routes.construction_access_mutations import GrantReplaceRequest
    wanted = GrantReplaceRequest(
        role=command["new_role"], scope_type="project",
        scope_id=original.scope_id,
        expires_at=command.get("new_expires_at", original.expires_at),
    )
    item = GrantMutationResponse.model_validate(result["item"])
    return (
        revoke.valid_result(result, selected) and item.id != original.id
        and str(item.user_id) == command["user_id"]
        and item.role == command["new_role"] and item.status == "active"
        and item.scope_type == "project" and str(item.scope_id) == selected["project_id"]
        and item.expires_at == wanted.expires_at
    )


@router.callback_query(F.data.startswith("PX:"))
async def replace_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        selected, nonce = None, None
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            data = await state.get_data()
            view = data.get(team.VIEW)
            parts = callback.data.split(":")
            if (
                not isinstance(view, dict) or len(parts) not in (3, 4)
                or view.get("nonce") != parts[2]
            ):
                raise ValueError("Stale replacement command.")
            selected = team.selected_from(data, view, callback)
            if selected is None:
                raise ValueError("Project context changed.")
            nonce = view["nonce"]
            action, offset = parts[1], 0
            if action in ("open", "submit", "expiry", "clear", "keep"):
                if len(parts) != 3:
                    raise ValueError("Unexpected command payload.")
            elif action in ("page", "choose", "role"):
                if len(parts) != 4:
                    raise ValueError("Missing selector.")
            else:
                raise ValueError("Unknown replacement command.")

            if action in ("expiry", "clear", "keep"):
                await expiry_action(
                    callback, state, selected, view, action, bot, event_update,
                )
                return

            attempt = data.get(ATTEMPT)
            if action == "open":
                if view.get("phase") != "list":
                    raise ValueError("Open the project team first.")
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if team.same_attempt(attempt, selected):
                        await pending_screen(callback, state, selected, attempt, language)
                    else:
                        blocked = team.view_for(selected, "replace_blocked")
                        await team.show(
                            callback, state, selected, blocked,
                            tr("other_replace_pending", language),
                            team.rows_back(selected, language, blocked),
                        )
                    return
            elif action == "submit":
                if view.get("phase") not in ("replace_review", "replace_pending"):
                    raise ValueError("Explicit replacement review required.")
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if (
                        not team.same_attempt(attempt, selected)
                        or (view["phase"] == "replace_pending"
                            and view.get("attempt_id") != attempt["id"])
                        or (view["phase"] == "replace_review"
                            and attempt["command"] != replacement_command(view))
                    ):
                        raise ValueError("Pending replacement differs.")
                else:
                    if view["phase"] != "replace_review":
                        raise ValueError("Pending replacement unavailable.")
                    frozen = replacement_command(view, validate_future=True)
                    attempt = {
                        key: selected[key] for key in (
                            "telegram_user_id", "tenant_id", "project_id", "actor_user_id",
                        )
                    }
                    attempt.update(
                        id=uuid4().hex,
                        command=deepcopy(frozen),
                        started_at=revoke.time(), status="pending",
                    )
                    await state.update_data(**{ATTEMPT: attempt})
                if not 0 <= revoke.time() - attempt["started_at"] < 86340:
                    await pending_screen(callback, state, selected, attempt, language)
                    return
                command = attempt["command"]
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    result = await service.replace_project_participant(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(selected["tenant_id"]),
                        project_id=UUID(selected["project_id"]),
                        expected_user_id=UUID(selected["actor_user_id"]),
                        target_user_id=UUID(command["user_id"]),
                        grant=deepcopy(command["grant"]), new_role=command["new_role"],
                        idempotency_key=attempt["id"],
                        **({"expires_at": command["new_expires_at"]}
                           if "new_expires_at" in command else {}),
                    )
                if not receipt_matches(result, selected, command):
                    raise ValueError("Replacement receipt differs.")
                latest = await state.get_data()
                saved = latest.get(ATTEMPT)
                if not isinstance(saved, dict) or saved.get("id") != attempt["id"]:
                    raise ValueError("Replacement attempt changed.")
                await state.update_data(**{
                    ATTEMPT: {
                        **attempt, "status": "done", "new_grant_id": result["item"]["id"],
                    },
                })
                if current(latest, selected, callback, nonce):
                    done = team.view_for(selected, "replace_done")
                    await team.show(
                        callback, state, selected, done, tr("replaced", language),
                        team.rows_back(selected, language, done),
                    )
                return
            elif action == "role":
                if view.get("phase") != "replace_roles":
                    raise ValueError("Choose a new role first.")
            elif view.get("phase") != "replace_choose":
                raise ValueError("Choose an existing project role first.")

            if action == "page":
                offset = int(parts[3])
                if (
                    str(offset) != parts[3] or offset < 0
                    or offset not in (
                        0, view["offset"], view["offset"] + 5,
                        max(0, view["offset"] - 5),
                    )
                ):
                    raise ValueError("Invalid replacement page.")
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await revoke.fetch(service, selected)
            if (
                not current(await state.get_data(), selected, callback, nonce)
                or not revoke.valid_result(result, selected)
            ):
                raise ValueError("Replacement selection changed.")
            language = result["language"] or language
            available = available_choices(result, UUID(selected["project_id"]))
            heading = "\n\n".join([
                clip(result["organization"]), clip(result["project_name"]),
            ])
            if action in ("choose", "role"):
                if action == "choose":
                    index = int(parts[3])
                    if str(index) != parts[3] or not 0 <= index < len(view["commands"]):
                        raise ValueError("Invalid original grant selector.")
                    command = view["commands"][index]
                else:
                    command = view["command"]
                found = next((item for item in available if item[0] == command), None)
                if found is None:
                    raise ValueError("Original grant no longer available.")
                grant, member = command["grant"], found[1]
                roles = [
                    option["role"] for option in result["roles"]
                    if option["role"] != grant["role"]
                ]
                base = "\n\n".join([
                    heading, member_name(member, 1, language),
                    tr("old_role", language) + ": " + tr(grant["role"], language),
                ])
                if action == "choose":
                    updated = team.view_for(
                        selected, "replace_roles", command=deepcopy(command), roles=roles,
                    )
                    rows = [[team.button(
                        tr(role, language), f'PX:role:{updated["nonce"]}:{index}',
                    )] for index, role in enumerate(roles)]
                    rows.append([team.button(
                        tr("edit_expiry", language), "PX:expiry:" + updated["nonce"],
                    )])
                    text = base + "\n\n" + tr("role", language)
                else:
                    index = int(parts[3])
                    if str(index) != parts[3] or not 0 <= index < len(view["roles"]):
                        raise ValueError("Invalid new role selector.")
                    new_role = view["roles"][index]
                    if new_role not in roles:
                        raise ValueError("New role no longer available.")
                    updated = team.view_for(
                        selected, "replace_review", command=deepcopy(command),
                        new_role=new_role,
                    )
                    text = "\n\n".join([
                        tr("review_replace", language), base,
                        tr("new_role", language) + ": " + tr(new_role, language),
                        tr("deadline", language) + ": "
                        + (grant["expires_at"] or tr("no_expiry", language)),
                        tr("preserve_expiry", language),
                    ])
                    rows = [
                        [team.button(tr("edit_expiry", language), "PX:expiry:" + updated["nonce"])],
                        [team.button(tr("confirm_replace", language), "PX:submit:" + updated["nonce"])],
                    ]
                rows.extend(team.rows_back(selected, language, updated))
                await team.show(callback, state, selected, updated, text, rows)
                return

            total = len(available)
            offset = min(offset, ((total - 1) // 5) * 5) if total else 0
            page = available[offset:offset + 5]
            updated = team.view_for(
                selected, "replace_choose", offset=offset,
                commands=[deepcopy(item[0]) for item in page],
            )
            rows = [[team.button(
                clip(member_name(member, offset + index + 1, language)
                     + " · " + tr(command["grant"]["role"], language), 120),
                f'PX:choose:{updated["nonce"]}:{index}',
            )] for index, (command, member) in enumerate(page)]
            for position, label in ((offset - 5, "previous"), (offset + 5, "next")):
                if 0 <= position < total:
                    rows.append([team.button(
                        tr(label, language), f'PX:page:{updated["nonce"]}:{position}',
                    )])
            rows.extend(team.rows_back(selected, language, updated))
            text = "\n\n".join([
                tr("choose_replace" if total else "empty_replace", language), heading,
                f"{offset + 1}–{offset + len(page)} / {total}" if page else "—",
            ])
            await team.show(callback, state, selected, updated, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_project_replace_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            data = await state.get_data()
            attempt = data.get(ATTEMPT)
            is_current = selected is not None and current(data, selected, callback, nonce)
            if (
                is_current and team.same_attempt(attempt, selected)
                and attempt.get("status") == "pending"
            ):
                await pending_screen(
                    callback, state, selected, attempt, language,
                    t("construction_bot_rate_limited" if getattr(error, "status_code", None) == 429
                      else "construction_bot_error", language),
                )
            elif is_current:
                failed = team.view_for(selected, "replace_error")
                await team.show(
                    callback, state, selected, failed,
                    t("construction_bot_unavailable" if getattr(error, "status_code", None)
                      in (401, 403, 404) else "construction_bot_error", language),
                    team.rows_back(selected, language, failed),
                )
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))

# Existing project access deadline editing v1.
def replacement_command(view, *, validate_future=False):
    from api.routes.construction_access_mutations import GrantReplaceRequest

    original = Grant.model_validate(view["command"]["grant"])
    output = deepcopy({**view["command"], "new_role": view["new_role"]})
    if "new_expires_at" in view:
        value = view["new_expires_at"]
        if value is not None and validate_future:
            value = parse_grant_expiry(value)
        target = GrantReplaceRequest(
            role=view["new_role"], scope_type="project",
            scope_id=original.scope_id, expires_at=value,
        )
        output["new_expires_at"] = target.model_dump(mode="json")["expires_at"]
        deadline = target.expires_at
    else:
        deadline = original.expires_at
    if view["new_role"] == original.role and deadline == original.expires_at:
        raise ValueError("Project access is unchanged.")
    return output


async def expiry_evidence(state, selected, view, event, bot, event_update):
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await revoke.fetch(service, selected)
    if (
        not current(await state.get_data(), selected, event, view["nonce"])
        or not revoke.valid_result(result, selected)
        or await state.get_state() is not None
    ):
        raise ValueError("Access editing context changed.")
    found = next((
        item for item in revoke.choices(result, UUID(selected["project_id"]))
        if item[0] == view["command"]
    ), None)
    role = view.get("new_role", view["command"]["grant"]["role"])
    if found is None or role not in [item["role"] for item in result["roles"]]:
        raise ValueError("The original grant or delegated role is no longer available.")
    return result, found[1], role


async def expiry_review(event, state, selected, old, result, member, role, deadline):
    language = result["language"] or event.from_user.language_code or "ru"
    updated = team.view_for(
        selected, "replace_review", command=deepcopy(old["command"]),
        new_role=role, new_expires_at=deadline,
    )
    original = old["command"]["grant"]
    def caption(value):
        return value.replace("T", " ").replace("Z", " UTC") if value else tr("no_expiry", language)
    text = "\n\n".join([
        tr("review_access", language),
        clip(result["organization"]), clip(result["project_name"]),
        member_name(member, 1, language),
        tr("old_role", language) + ": " + tr(original["role"], language),
        tr("new_role", language) + ": " + tr(role, language),
        tr("old_expiry", language) + ": " + caption(original["expires_at"]),
        tr("new_expiry", language) + ": " + caption(deadline),
    ])
    rows = [[team.button(
        tr("edit_expiry", language), "PX:expiry:" + updated["nonce"],
    )]]
    try:
        replacement_command(updated)
    except ValueError:
        text += "\n\n" + tr("unchanged", language)
    else:
        rows.append([team.button(
            tr("confirm_replace", language), "PX:submit:" + updated["nonce"],
        )])
    rows.extend(team.rows_back(selected, language, updated))
    await team.show(event, state, selected, updated, text, rows)


async def expiry_action(callback, state, selected, view, action, bot, event_update):
    required = ("replace_roles", "replace_review") if action == "expiry" else ("replace_expiry",)
    if view.get("phase") not in required:
        raise ValueError("Access expiry editing is no longer active.")
    attempt = (await state.get_data()).get(ATTEMPT)
    if isinstance(attempt, dict) and attempt.get("status") == "pending":
        raise ValueError("Resolve the pending replacement first.")
    result, member, role = await expiry_evidence(
        state, selected, view, callback, bot, event_update,
    )
    language = result["language"] or callback.from_user.language_code or "ru"
    if action != "expiry":
        deadline = None if action == "clear" else view["command"]["grant"]["expires_at"]
        await expiry_review(
            callback, state, selected, view, result, member, role, deadline,
        )
        return
    updated = team.view_for(
        selected, "replace_expiry", command=deepcopy(view["command"]), new_role=role,
    )
    text = "\n\n".join([
        member_name(member, 1, language), tr(role, language),
        team_tr("date_prompt", language),
    ])
    rows = [
        [team.button(tr("no_expiry", language), "PX:clear:" + updated["nonce"])],
        [team.button(tr("keep_expiry", language), "PX:keep:" + updated["nonce"])],
        *team.rows_back(selected, language, updated),
    ]
    await team.show(callback, state, selected, updated, text, rows)


class ReplacementExpiryInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(team.VIEW)
        return (
            isinstance(view, dict) and view.get("phase") == "replace_expiry"
            and view.get("menu_id") == data.get("last_menu_message_id")
            and team.selected_from(data, view, message) is not None
        )


@team.date_router.message(ReplacementExpiryInput())
async def replacement_expiry_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ReplacementExpiryInput()(message, state):
            return
        old = deepcopy((await state.get_data())[team.VIEW])
        selected = team.selected_from(await state.get_data(), old, message)
        language = message.from_user.language_code or "ru"
        try:
            deadline = parse_grant_expiry(message.text)
        except ValueError:
            await message.answer(invitation_tr("grant_invalid", language))
            return
        try:
            result, member, role = await expiry_evidence(
                state, selected, old, message, bot, event_update,
            )
            await expiry_review(
                message, state, selected, old, result, member, role, deadline,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_replacement_date_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if (await state.get_data()).get(team.VIEW) == old:
                await state.update_data(**{team.VIEW: {}})
            await message.answer(t("construction_bot_unavailable", language))
