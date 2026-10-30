"""Fresh, read-only member permissions through the existing team APIs."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_team as organization
from handlers.construction import construction_project_team as project
from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_team_permissions import (
    tr, member_name, scope_text, scope_projection, permission_label,
)
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_team_permissions")
logger = logging.getLogger(__name__)
CONTEXT = "construction_suite_context"


def cb(view, action, position):
    return "TP:" + action + ":" + view["nonce"] + ":" + str(position)


def snapshot(data, key):
    return deepcopy({
        name: data.get(name) for name in (key, CONTEXT, "last_menu_message_id")
    })


def unchanged(data, saved):
    return all(data.get(key) == value for key, value in saved.items())


async def permissions_navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith(("TP:", "CT:", "PT:"))
    ):
        saved = await state.get_data()
        values = {}
        for key in (organization.VIEW, project.VIEW):
            view = saved.get(key)
            if (
                isinstance(view, dict)
                and view.get("telegram_user_id") == event.from_user.id
                and str(view.get("phase", "")).startswith("rights_")
            ):
                values[key] = {}
        if values:
            await state.update_data(**values)
    return await handler(event, data)


async def show(callback, state, mode, selected, old, saved, view, text, rows):
    if not unchanged(await state.get_data(), saved):
        return
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    current = await state.get_data()
    if not unchanged(current, saved):
        return
    key = organization.VIEW if mode == "o" else project.VIEW
    values = {
        key: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    }
    if mode == "p":
        if project.selected_from(current, old, callback) != selected:
            return
        source_key = selected["source_key"]
        values[source_key] = {**current[source_key], "menu_id": sent.message_id}
        if source_key == project.PROJECT:
            values[project.CARD] = {
                **current[project.CARD], "menu_id": sent.message_id,
            }
            from handlers.construction.construction_client_catalog import sync_origin
            values.update(sync_origin(current, current[project.CARD], sent.message_id))
        if source_key == project.CATALOG:
            values["construction_project_catalog_view"] = {
                **current["construction_project_catalog_view"],
                "menu_id": sent.message_id,
            }
    await state.update_data(**values)


def back(view, mode, language):
    callback = (
        "CT:page:" + view["nonce"] + ":" + str(view["offset"])
        if mode == "o" else "PT:list:" + view["nonce"]
    )
    return [
        [organization.button(tr("team", language), callback)],
        [organization.button(t("construction_bot_home_btn", language), "CS_HOME")],
    ]


def clean_view(old, member_id):
    view = {
        key: value for key, value in old.items()
        if key not in (
            "user_ids", "rights_grant_ids", "rights_grant_id",
            "rights_scope_offset", "rights_permission_offset",
        )
    }
    view.update(nonce=uuid4().hex, rights_user_id=member_id)
    return view


@router.callback_query(F.data.startswith("TP:"))
async def permissions_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        if (callback.data or "").startswith("TP:rm:"):
            from handlers.construction.construction_organization_remove import dispatch
            await dispatch(callback, state, bot=bot, event_update=event_update)
            return
        if (callback.data or "").startswith("TP:ta:"):
            from handlers.construction.construction_organization_access import dispatch as access_dispatch
            await access_dispatch(callback, state, bot=bot, event_update=event_update)
            return
        if (callback.data or "").startswith("TP:rv:"):
            from handlers.construction.construction_organization_revoke import dispatch as revoke_dispatch
            await revoke_dispatch(callback, state, bot=bot, event_update=event_update)
            return
        if (callback.data or "").startswith("TP:bl:"):
            from handlers.construction.construction_organization_block import dispatch as block_dispatch
            await block_dispatch(callback, state, bot=bot, event_update=event_update)
            return
        saved = None
        old = None
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            parts = callback.data.split(":")
            if len(parts) != 4 or parts[1] not in ("o", "p", "os", "ps", "og", "pg", "or", "pr"):
                raise ValueError("Unknown permissions command.")
            action, mode = parts[1], parts[1][0]
            key = organization.VIEW if mode == "o" else project.VIEW
            data = await state.get_data()
            old = deepcopy(data.get(key))
            context = data.get(CONTEXT)
            if (
                not isinstance(old, dict) or not isinstance(context, dict)
                or old.get("nonce") != parts[2]
                or old.get("telegram_user_id") != callback.from_user.id
                or context.get("telegram_user_id") != callback.from_user.id
                or old.get("tenant_id") != context.get("tenant_id")
                or old.get("menu_id") != callback.message.message_id
                or old.get("menu_id") != data.get("last_menu_message_id")
            ):
                raise ValueError("Stale member permissions selection.")
            position = int(parts[3])
            if str(position) != parts[3] or position < 0:
                raise ValueError("Invalid permissions position.")
            selected = project.selected_from(data, old, callback) if mode == "p" else None
            if mode == "p" and selected is None:
                raise ValueError("Project origin changed.")
            if action in ("o", "p"):
                if mode == "p" and old.get("phase") != "list":
                    raise ValueError("Open the project team first.")
                if mode == "o" and str(old.get("phase", "")).startswith("rights_"):
                    raise ValueError("Open the organization team first.")
                if not 0 <= position < len(old.get("user_ids", [])):
                    raise ValueError("Invalid member selector.")
                member_id = old["user_ids"][position]
            else:
                member_id = old["rights_user_id"]
                if action.endswith("g"):
                    if old.get("phase") != "rights_scopes":
                        raise ValueError("Open member scopes first.")
                    if not 0 <= position < len(old["rights_grant_ids"]):
                        raise ValueError("Invalid grant selector.")
                elif action.endswith("r"):
                    if old.get("phase") != "rights_permissions":
                        raise ValueError("Open grant permissions first.")
                    current_offset = old["rights_permission_offset"]
                    if position not in (0, current_offset, max(0, current_offset - 10), current_offset + 10):
                        raise ValueError("Invalid permissions page.")
                elif action.endswith("s"):
                    current_offset = old.get("rights_scope_offset", 0)
                    if position not in (0, current_offset, max(0, current_offset - 5), current_offset + 5):
                        raise ValueError("Invalid scope page.")
            UUID(member_id)
            saved = snapshot(data, key)
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                if mode == "o":
                    result = await service.team_members(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(old["tenant_id"]),
                        expected_user_id=UUID(old["actor_user_id"]),
                    )
                else:
                    result = await service.project_team(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(selected["tenant_id"]),
                        project_id=UUID(selected["project_id"]),
                        expected_user_id=UUID(selected["actor_user_id"]),
                        client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
                    )
            if (
                not unchanged(await state.get_data(), saved)
                or result["actor_user_id"] != old["actor_user_id"]
                or mode == "p" and result["project_id"] != selected["project_id"]
            ):
                raise ValueError("Member permissions context changed.")
            member = next((item for item in result["items"] if item["user_id"] == member_id), None)
            if member is None:
                raise ValueError("Member is no longer visible.")
            language = result["language"] or language
            grants = sorted(member["grants"], key=lambda grant: (
                grant["scope_type"], grant["scope_id"], grant["role"], grant["id"],
            ))
            view = clean_view(old, member_id)
            heading = [
                tr("rights", language), str(result["organization"])[:100],
                member_name(member, 1, language),
            ]
            rows = []
            if action in ("o", "p", "os", "ps"):
                offset = 0 if action in ("o", "p") else position
                offset = min(offset, ((len(grants) - 1) // 5) * 5) if grants else 0
                page = grants[offset:offset + 5]
                view.update(
                    phase="rights_scopes", rights_scope_offset=offset,
                    rights_grant_ids=[grant["id"] for grant in page],
                )
                names = await load_scope_names(
                    result, page, old=old, selected=selected,
                    bot=bot, event_update=event_update,
                )
                if not unchanged(await state.get_data(), saved):
                    return
                lines = [*heading, tr("scopes", language)]
                for index, grant in enumerate(page):
                    lines.append(
                        str(offset + index + 1) + ". " + tr(grant["role"], language)
                        + "\n" + scope_text(grant, language, names=names, organization=result["organization"])
                    )
                    rows.append([organization.button(
                        str(offset + index + 1) + ". " + tr(grant["role"], language)
                        + " · " + tr(grant["scope_type"], language),
                        cb(view, mode + "g", index),
                    )])
                for offset_value, label in ((offset - 5, "previous"), (offset + 5, "next")):
                    if 0 <= offset_value < len(grants):
                        rows.append([organization.button(
                            tr(label, language), cb(view, mode + "s", offset_value),
                        )])
                rows.append([organization.button(tr("refresh", language), cb(view, mode + "s", offset))])
            else:
                grant_id = (
                    old["rights_grant_ids"][position]
                    if action.endswith("g") else old["rights_grant_id"]
                )
                grant = next((item for item in grants if item["id"] == grant_id), None)
                if grant is None:
                    raise ValueError("The original grant is no longer active.")
                codes = scope_projection(member, grant)
                names = await load_scope_names(
                    result, [grant], old=old, selected=selected,
                    bot=bot, event_update=event_update,
                )
                if not unchanged(await state.get_data(), saved):
                    return
                offset = 0 if action.endswith("g") else position
                offset = min(offset, ((len(codes) - 1) // 10) * 10) if codes else 0
                view.update(
                    phase="rights_permissions", rights_grant_id=grant_id,
                    rights_permission_offset=offset,
                    rights_scope_offset=old.get("rights_scope_offset", 0),
                )
                lines = [
                    *heading, tr(grant["role"], language), scope_text(grant, language, names=names, organization=result["organization"]),
                    tr("expiry", language) + ": " + (grant["expires_at"] or tr("unlimited", language)),
                    tr("scope_note", language),
                    f"{offset + 1}–{min(offset + 10, len(codes))} / {len(codes)}"
                    if codes else tr("empty", language),
                ]
                lines.extend(
                    str(offset + index + 1) + ". " + permission_label(code, language)
                    for index, code in enumerate(codes[offset:offset + 10])
                )
                for offset_value, label in ((offset - 10, "previous"), (offset + 10, "next")):
                    if 0 <= offset_value < len(codes):
                        rows.append([organization.button(
                            tr(label, language), cb(view, mode + "r", offset_value),
                        )])
                rows.extend([
                    [organization.button(tr("refresh", language), cb(view, mode + "r", offset))],
                    [organization.button(tr("scopes", language), cb(view, mode + "s", view["rights_scope_offset"]))],
                ])
            if mode == "o" and view.get("phase") == "rights_scopes":
                from handlers.construction.construction_organization_remove import cb as removal_cb
                from ui.construction_organization_remove import tr as removal_tr
                rows.append([organization.button(
                    removal_tr("button", language), removal_cb(view, "open"),
                )])
            if mode == "o" and view.get("phase") == "rights_scopes":
                from handlers.construction.construction_organization_access import cb as assign_cb
                from ui.construction_organization_access import tr as assign_tr
                rows.append([organization.button(
                    assign_tr("assign", language), assign_cb(view, "open"),
                )])
            if (
                mode == "o" and view.get("phase") == "rights_permissions"
                and grant["scope_type"] == "tenant"
                and grant["scope_id"] == view["tenant_id"]
            ):
                from handlers.construction.construction_organization_access import cb as replace_cb
                from ui.construction_organization_access import tr as replace_tr
                rows.append([organization.button(
                    replace_tr("replace", language), replace_cb(view, "replace"),
                )])
            if (
                mode == "o" and view.get("phase") == "rights_permissions"
                and grant["scope_type"] == "tenant"
                and grant["scope_id"] == view["tenant_id"]
            ):
                from handlers.construction.construction_organization_revoke import cb as revoke_cb
                from ui.construction_organization_access import tr as revoke_tr
                rows.append([organization.button(
                    revoke_tr("revoke", language), revoke_cb(view, "review"),
                )])
            if mode == "o" and view.get("phase") == "rights_scopes":
                from handlers.construction.construction_organization_block import cb as block_cb
                from ui.construction_organization_access import tr as block_tr
                rows.append([organization.button(
                    block_tr("block", language), block_cb(view, "review"),
                )])
            lines.append(tr("readonly", language))
            rows.extend(back(view, mode, language))
            text = "\n\n".join(lines)
            if len(text) > 3900:
                raise ValueError("Permissions presentation is too large.")
            await show(callback, state, mode, selected, old, saved, view, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_team_permissions_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if saved is not None and unchanged(await state.get_data(), saved):
                view = {**old, "nonce": uuid4().hex, "phase": "rights_error"}
                await show(
                    callback, state, mode, selected, old, saved, view,
                    t("construction_bot_unavailable", language),
                    back(view, mode, language),
                )
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))

async def load_scope_names(result, grants, *, old, selected, bot, event_update):
    names = {}
    needed = set()
    for grant in grants:
        identifier = str(UUID(grant["scope_id"]))
        if grant["scope_type"] == "tenant" and identifier != old["tenant_id"]:
            raise ValueError("Tenant scope differs from the current organization.")
        if grant["scope_type"] == "project":
            needed.add(identifier)

    if selected is not None:
        known = result.get("project_name")
        identifier = selected["project_id"]
        if (
            result.get("project_id") == identifier
            and isinstance(known, str) and known.strip()
        ):
            names[identifier] = " ".join(known.split())[:150]

    missing = sorted(needed - set(names))
    if missing:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            loaded = await service.project_scope_names(
                telegram_user_id=old["telegram_user_id"],
                tenant_id=UUID(old["tenant_id"]),
                expected_user_id=UUID(old["actor_user_id"]),
                project_ids=tuple(UUID(value) for value in missing),
            )
        projection = loaded["project_names"]
        if (
            loaded["actor_user_id"] != old["actor_user_id"]
            or loaded["tenant_id"] != old["tenant_id"]
            or not isinstance(projection, dict)
            or set(projection) != set(missing)
            or any(
                value is not None and (
                    not isinstance(value, str) or not value.strip()
                )
                for value in projection.values()
            )
        ):
            raise ValueError("Project scope names are not bound to the current actor.")
        names.update(projection)
    return names

# construction_scope_names_v1
