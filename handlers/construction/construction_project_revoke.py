"""Explicit revocation of one existing project grant through the API."""
import logging
from time import time
from uuid import UUID, uuid4

from aiogram import F, Router

from handlers.construction import construction_project_team as team
from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_team_transport import Grant
from ui.construction_project_revoke import tr
from ui.construction_project_team import clip, member_name
from ui.texts import t

router = Router(name="construction_project_revoke")
logger = logging.getLogger(__name__)
ATTEMPT = "construction_project_revoke_attempt"


async def retain_project_revoke_attempt(state, previous, user_id):
    value = previous.get(ATTEMPT)
    if (
        isinstance(value, dict) and value.get("telegram_user_id") == user_id
        and value.get("status") == "pending"
    ):
        await state.update_data(**{ATTEMPT: dict(value)})


def choices(result, project_id):
    allowed = {option["role"] for option in result["roles"]}
    output, seen = [], set()
    for member in result["items"]:
        user_id = str(UUID(member["user_id"]))
        for value in member["grants"]:
            grant = Grant.model_validate(value)
            if (
                grant.scope_type != "project" or grant.scope_id != project_id
                or grant.role not in allowed
            ):
                continue
            if grant.id in seen:
                raise ValueError("Duplicate project grant.")
            seen.add(grant.id)
            output.append((
                {"user_id": user_id, "grant": grant.model_dump(mode="json")},
                member,
            ))
    return output


async def fetch(service, selected):
    return await service.project_team(
        telegram_user_id=selected["telegram_user_id"],
        tenant_id=UUID(selected["tenant_id"]),
        project_id=UUID(selected["project_id"]),
        expected_user_id=UUID(selected["actor_user_id"]),
        client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
        grant_selection=True,
    )


def valid_result(result, selected):
    return (
        result["actor_user_id"] == selected["actor_user_id"]
        and result["project_id"] == selected["project_id"]
    )


async def pending_screen(
    callback, state, selected, attempt, language, notice=None, *,
    namespace="PG", phase="revoke_pending",
):
    view = team.view_for(selected, phase, attempt_id=attempt["id"])
    safe = 0 <= time() - attempt["started_at"] < 86340
    text = tr("pending" if safe else "expired", language)
    text += "\n\n" + tr(attempt["command"]["grant"]["role"], language)
    if "new_role" in attempt["command"]:
        text += " → " + tr(attempt["command"]["new_role"], language)
    if notice:
        text = notice + "\n\n" + text
    rows = []
    if safe:
        rows.append([team.button(
            tr("retry", language), namespace + ":submit:" + view["nonce"],
        )])
    rows.extend(team.rows_back(selected, language, view))
    await team.show(callback, state, selected, view, text, rows)


def receipt_matches(result, selected, command):
    from api.routes.construction_access_mutations import GrantMutationResponse

    expected = Grant.model_validate(command["grant"])
    item = GrantMutationResponse.model_validate(result["item"])
    return (
        valid_result(result, selected) and item.id == expected.id
        and str(item.user_id) == command["user_id"] and item.role == expected.role
        and item.scope_type == "project" and str(item.scope_id) == selected["project_id"]
        and item.status == "revoked" and item.expires_at == expected.expires_at
    )


@router.callback_query(F.data.startswith("PG:"))
async def revoke_callback(callback, state, bot=None, event_update=None):
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
            view = data.get(team.VIEW)
            parts = callback.data.split(":")
            if (
                not isinstance(view, dict) or len(parts) not in (3, 4)
                or parts[2] != view.get("nonce")
            ):
                raise ValueError("Stale revocation command.")
            selected = team.selected_from(data, view, callback)
            if selected is None:
                raise ValueError("Project context changed.")
            action, offset = parts[1], 0
            if action in ("open", "submit"):
                if len(parts) != 3:
                    raise ValueError("Unexpected payload.")
            elif action in ("page", "choose"):
                if len(parts) != 4:
                    raise ValueError("Missing selector.")
            else:
                raise ValueError("Unknown revocation command.")

            attempt = data.get(ATTEMPT)
            if action == "open":
                if view.get("phase") != "list":
                    raise ValueError("Open the project team first.")
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if team.same_attempt(attempt, selected):
                        await pending_screen(callback, state, selected, attempt, language)
                    else:
                        blocked = team.view_for(selected, "revoke_blocked")
                        await team.show(
                            callback, state, selected, blocked,
                            tr("other_revoke_pending", language),
                            team.rows_back(selected, language, blocked),
                        )
                    return
            elif action == "submit":
                if view.get("phase") not in ("revoke_review", "revoke_pending"):
                    raise ValueError("Explicit revocation review required.")
                if isinstance(attempt, dict) and attempt.get("status") == "pending":
                    if (
                        not team.same_attempt(attempt, selected)
                        or (view["phase"] == "revoke_pending"
                            and view.get("attempt_id") != attempt["id"])
                        or (view["phase"] == "revoke_review"
                            and view.get("command") != attempt["command"])
                    ):
                        raise ValueError("Pending revocation differs.")
                else:
                    if view["phase"] != "revoke_review":
                        raise ValueError("Pending revocation unavailable.")
                    attempt = {
                        key: selected[key] for key in (
                            "telegram_user_id", "tenant_id", "project_id", "actor_user_id",
                        )
                    }
                    attempt.update(
                        id=uuid4().hex, command=view["command"],
                        started_at=time(), status="pending",
                    )
                    await state.update_data(**{ATTEMPT: attempt})
                if not 0 <= time() - attempt["started_at"] < 86340:
                    await pending_screen(callback, state, selected, attempt, language)
                    return
                command = attempt["command"]
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    result = await service.revoke_project_participant(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(selected["tenant_id"]),
                        project_id=UUID(selected["project_id"]),
                        expected_user_id=UUID(selected["actor_user_id"]),
                        target_user_id=UUID(command["user_id"]),
                        grant=command["grant"], idempotency_key=attempt["id"],
                    )
                if (
                    team.selected_from(await state.get_data(), selected, callback) != selected
                    or not receipt_matches(result, selected, command)
                ):
                    raise ValueError("Revocation receipt differs.")
                await state.update_data(**{ATTEMPT: {**attempt, "status": "done"}})
                done = team.view_for(selected, "revoke_done")
                await team.show(
                    callback, state, selected, done, tr("revoked", language),
                    team.rows_back(selected, language, done),
                )
                return
            elif view.get("phase") != "revoke_choose":
                raise ValueError("Select a project role first.")

            if action == "page":
                offset = int(parts[3])
                if (
                    str(offset) != parts[3] or offset < 0
                    or offset not in (
                        0, view["offset"], view["offset"] + 5,
                        max(0, view["offset"] - 5),
                    )
                ):
                    raise ValueError("Invalid revocation page.")
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await fetch(service, selected)
            if (
                team.selected_from(await state.get_data(), selected, callback) != selected
                or not valid_result(result, selected)
            ):
                raise ValueError("Team response binding differs.")
            language = result["language"] or language
            available = choices(result, UUID(selected["project_id"]))
            heading = "\n\n".join([
                clip(result["organization"]), clip(result["project_name"]),
            ])
            if action == "choose":
                index = int(parts[3])
                if str(index) != parts[3] or not 0 <= index < len(view["commands"]):
                    raise ValueError("Invalid grant selector.")
                command = view["commands"][index]
                found = next((item for item in available if item[0] == command), None)
                if found is None:
                    raise ValueError("Selected grant is no longer available.")
                grant, member = found[0]["grant"], found[1]
                review = team.view_for(selected, "revoke_review", command=command)
                text = "\n\n".join([
                    tr("review_revoke", language), heading,
                    member_name(member, 1, language), tr(grant["role"], language),
                    tr("deadline", language) + ": "
                    + (grant["expires_at"] or tr("no_expiry", language)),
                    tr("impact", language),
                ])
                rows = [[team.button(
                    tr("confirm_revoke", language), "PG:submit:" + review["nonce"],
                )]]
                rows.extend(team.rows_back(selected, language, review))
                await team.show(callback, state, selected, review, text, rows)
                return

            total = len(available)
            offset = min(offset, ((total - 1) // 5) * 5) if total else 0
            page = available[offset:offset + 5]
            current = team.view_for(
                selected, "revoke_choose", offset=offset,
                commands=[item[0] for item in page],
            )
            rows = []
            for index, (command, member) in enumerate(page):
                label = (
                    member_name(member, offset + index + 1, language)
                    + " · " + tr(command["grant"]["role"], language)
                )
                rows.append([team.button(
                    clip(label, 120), f'PG:choose:{current["nonce"]}:{index}',
                )])
            for position, label in ((offset - 5, "previous"), (offset + 5, "next")):
                if 0 <= position < total:
                    rows.append([team.button(
                        tr(label, language), f'PG:page:{current["nonce"]}:{position}',
                    )])
            rows.extend(team.rows_back(selected, language, current))
            text = "\n\n".join([
                tr("choose_revoke" if total else "empty_revoke", language), heading,
                f"{offset + 1}–{offset + len(page)} / {total}" if page else "—",
            ])
            await team.show(callback, state, selected, current, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_project_revoke_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            data = await state.get_data()
            attempt = data.get(ATTEMPT)
            current = (
                selected is not None
                and team.selected_from(data, selected, callback) == selected
            )
            if current and team.same_attempt(attempt, selected) and attempt.get("status") == "pending":
                await pending_screen(
                    callback, state, selected, attempt, language,
                    t("construction_bot_rate_limited" if getattr(error, "status_code", None) == 429
                      else "construction_bot_error", language),
                )
            elif current:
                failed = team.view_for(selected, "revoke_error")
                await team.show(
                    callback, state, selected, failed,
                    t("construction_bot_unavailable" if getattr(error, "status_code", None)
                      in (401, 403, 404) else "construction_bot_error", language),
                    team.rows_back(selected, language, failed),
                )
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))
