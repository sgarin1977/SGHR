"""Read-only project activity pages through the shared API."""
from copy import deepcopy
import logging
from uuid import UUID

from aiogram import F, Router

from handlers.construction import construction_project_areas as areas
from handlers.construction import construction_project_team as team
from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_activities import page_text, tr
from ui.texts import t

router = Router(name="construction_project_activities")
logger = logging.getLogger(__name__)
KEYS = (
    team.VIEW, areas.CONTEXT, areas.PROJECT, areas.FORM, areas.CARD, areas.CATALOG,
    "construction_project_catalog_view", "construction_client_catalog_view",
    "construction_activity_terminal_form", "last_menu_message_id",
)

def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})

def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())

def cb(view, action):
    return "PAV:" + action + ":" + view["nonce"]

def rows(view, selected, language, pending=None):
    from handlers.construction.construction_project_activity_terminal import controls
    result = controls(view, selected, language, pending)
    if view.get("next_cursor") is not None:
        result.append([areas.button(tr("next", language), cb(view, "next"))])
    result.extend((
        [areas.button(tr("refresh", language), cb(view, "refresh"))],
        [areas.button(tr("first", language), cb(view, "first"))],
        [areas.button(tr("back", language), selected["back"])],
        *areas.back_rows(language),
    ))
    return result

@router.callback_query(F.data.startswith("PAV:"))
async def activities_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        selected, saved = None, None
        try:
            if await state.get_state() is not None:
                return
            data = await state.get_data()
            parts = callback.data.split(":")
            if len(parts) != 3:
                return
            cursor = None
            if parts[1] == "open":
                choices = [
                    value for key in (areas.PROJECT, areas.FORM, areas.CATALOG)
                    if (value := areas.binding(
                        data, key=key, token=parts[2],
                        sender=callback.from_user.id, menu=callback.message.message_id,
                    )) is not None
                ]
                if len(choices) != 1:
                    return
                selected = choices[0]
                from handlers.construction.construction_activity_plan import suspend_activity_form
                from handlers.construction.construction_activity_terminal import suspend_terminal_form
                from handlers.construction.construction_area_create import suspend_area_form
                for suspend in (suspend_activity_form, suspend_terminal_form, suspend_area_form):
                    await suspend(state)
                data = await state.get_data()
            else:
                old = data.get(team.VIEW)
                if (
                    parts[1] not in ("next", "refresh", "first")
                    or not isinstance(old, dict) or old.get("nonce") != parts[2]
                    or old.get("phase") not in (
                        "project_activities", "project_activities_error",
                        "project_activity_terminal",
                    )
                ):
                    return
                selected = team.selected_from(data, old, callback)
                if selected is None:
                    return
                cursor = old.get("next_cursor") if parts[1] == "next" else (
                    old.get("cursor") if parts[1] == "refresh" else None
                )
                if parts[1] == "next" and cursor is None:
                    return
            from handlers.construction.construction_activity_terminal import suspend_terminal_form
            await suspend_terminal_form(state)
            data = await state.get_data()
            saved = snapshot(data)
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await service.project_planned_activities(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=UUID(selected["tenant_id"]),
                    project_id=UUID(selected["project_id"]),
                    expected_user_id=UUID(selected["actor_user_id"]),
                    client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
                    cursor=cursor,
                )
            if not same(await state.get_data(), saved):
                return
            if (
                result["actor_user_id"] != selected["actor_user_id"]
                or result["project_id"] != selected["project_id"]
                or not isinstance(result["items"], list) or len(result["items"]) > 5
            ):
                raise ValueError("Project activity context changed.")
            language = result.get("language") or language
            view = team.view_for(
                selected, "project_activities", cursor=cursor,
                next_cursor=result["next_cursor"],
                activity_ids=[str(UUID(item["id"])) for item in result["items"]],
            )
            await team.show(
                callback, state, selected, view, page_text(result, language),
                rows(
                    view, selected, language,
                    pending=data.get("construction_activity_terminal_form"),
                ), expected_snapshot=saved,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_project_activities_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if selected is not None and saved is not None and same(await state.get_data(), saved):
                view = team.view_for(
                    selected, "project_activities_error", cursor=None, next_cursor=None,
                )
                key = (
                    "construction_bot_rate_limited" if getattr(error, "status_code", None) == 429
                    else "construction_bot_unavailable" if isinstance(error, ConstructionApiClientError)
                         and error.status_code in (401, 403, 404)
                    else "construction_bot_error"
                )
                await team.show(
                    callback, state, selected, view, t(key, language),
                    rows(
                        view, selected, language,
                        pending=data.get("construction_activity_terminal_form"),
                    ), expected_snapshot=saved,
                )

# project_activity_terminal_v1
