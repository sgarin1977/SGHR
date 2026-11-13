"""Private universal-area reads through the common API."""
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_areas import areas_text, tr
from ui.construction_area_translation import area_button_name
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_project_areas")
logger = logging.getLogger(__name__)
VIEW = "construction_project_areas_view"
CONTEXT = "construction_suite_context"
PROJECT = "construction_project_card_view"
FORM = "construction_project_create_form"
CARD = "construction_client_card_view"
CATALOG = "construction_catalog_project_card"


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def binding(data, *, key, token, sender, menu):
    if key == CATALOG:
        from handlers.construction.construction_project_catalog import project_binding
        return project_binding(data, token=token, sender=sender, menu=menu)
    if key not in (PROJECT, FORM):
        return None
    context, source = data.get(CONTEXT), data.get(key)
    if (
        not isinstance(context, dict) or not isinstance(source, dict)
        or context.get("telegram_user_id") != sender
        or source.get("telegram_user_id") != sender
        or source.get("tenant_id") != context.get("tenant_id")
        or data.get("last_menu_message_id") != menu
        or source.get("menu_id") != menu
    ):
        return None
    if key == PROJECT:
        if source.get("nonce") != token:
            return None
        card = data.get(CARD)
        if (
            not isinstance(card, dict)
            or card.get("nonce") != source.get("source_nonce")
            or card.get("telegram_user_id") != sender
            or card.get("tenant_id") != source.get("tenant_id")
            or card.get("actor_user_id") != source.get("actor_user_id")
            or card.get("client_id") != source.get("client_id")
            or card.get("section") != "projects"
            or card.get("menu_id") != menu
            or source.get("project_id") not in card.get("project_ids", [])
        ):
            return None
        client_id = str(UUID(source["client_id"]))
        back = "PC:refresh:" + token
    else:
        if (
            source.get("id") != token or source.get("phase") != "done"
            or type(source.get("step")) is not int
            or not 0 <= source["step"] <= 10
        ):
            return None
        client_id = None
        back = f'PF:open:{token}:{source["step"]}'
    return {
        "source_key": key, "source_token": token,
        "telegram_user_id": sender,
        "tenant_id": str(UUID(source["tenant_id"])),
        "project_id": str(UUID(source["project_id"])),
        "actor_user_id": str(UUID(source["actor_user_id"])),
        "client_id": client_id, "back": back,
    }


def back_rows(language):
    return [
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
        [button(t("all_services_suites_btn", language), "M_SUITES")],
    ]


async def show(callback, state, text, rows, *, selected=None, view=None):
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    values = {
        VIEW: {**view, "menu_id": sent.message_id} if view else {},
        "last_menu_message_id": sent.message_id,
    }
    if selected is not None:
        data = await state.get_data()
        current = binding(
            data, key=selected["source_key"], token=selected["source_token"],
            sender=callback.from_user.id, menu=callback.message.message_id,
        )
        if current != selected:
            return
        key = selected["source_key"]
        values[key] = {**data[key], "menu_id": sent.message_id}
        if key == PROJECT:
            # Preserve all four client-list positions and its project selection.
            values[CARD] = {**data[CARD], "menu_id": sent.message_id}
            from handlers.construction.construction_client_catalog import sync_origin
            values.update(sync_origin(data, data[CARD], sent.message_id))
        if key == CATALOG:
            values["construction_project_catalog_view"] = {
                **data["construction_project_catalog_view"], "menu_id": sent.message_id,
            }
    await state.update_data(**values)


@router.callback_query(F.data.startswith("PA:"))
async def areas_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            data = await state.get_data()
            parts = callback.data.split(":")
            selected = None
            if len(parts) == 3 and parts[1] == "open":
                candidates = [
                    value for key in (PROJECT, FORM, CATALOG)
                    if (value := binding(
                        data, key=key, token=parts[2], sender=callback.from_user.id,
                        menu=callback.message.message_id,
                    )) is not None
                ]
                if len(candidates) == 1:
                    selected = candidates[0]
                offset = 0
            elif len(parts) == 4 and parts[1] == "page":
                saved = data.get(VIEW, {})
                if isinstance(saved, dict) and saved.get("nonce") == parts[2]:
                    selected = binding(
                        data, key=saved.get("source_key"), token=saved.get("source_token"),
                        sender=callback.from_user.id, menu=callback.message.message_id,
                    )
                    if selected is not None and any(
                        saved.get(field) != selected[field] for field in (
                            "telegram_user_id", "tenant_id", "project_id", "actor_user_id",
                        )
                    ):
                        selected = None
                    offset = int(parts[3])
                    if (
                        str(offset) != parts[3] or offset < 0
                        or type(saved.get("offset")) is not int
                        or offset not in (
                            0, saved["offset"], saved["offset"] + 5,
                            max(0, saved["offset"] - 5),
                        )
                    ):
                        selected = None
                else:
                    offset = 0
            else:
                offset = 0
            if selected is None:
                await callback.message.answer(t("construction_lead_form_stale", language))
                return

            from handlers.construction.construction_area_create import suspend_area_form
            await suspend_area_form(state)
            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await service.project_areas(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=UUID(selected["tenant_id"]),
                    project_id=UUID(selected["project_id"]),
                    expected_user_id=UUID(selected["actor_user_id"]),
                    client_id=UUID(selected["client_id"]) if selected["client_id"] else None,
                    display_offset=offset,
                )
            current = binding(
                await state.get_data(),
                key=selected["source_key"], token=selected["source_token"],
                sender=callback.from_user.id, menu=callback.message.message_id,
            )
            if current != selected:
                return
            if (
                result["actor_user_id"] != selected["actor_user_id"]
                or result["project_id"] != selected["project_id"]
            ):
                raise ValueError("Area selection changed.")
            language = result["language"] or language
            total = len(result["items"])
            offset = min(offset, ((total - 1) // 5) * 5) if total else 0
            actual_offset = result.get("display_offset")
            if actual_offset is not None and (
                type(actual_offset) is not int or actual_offset != offset
            ):
                raise ValueError("Translated area page differs.")
            view = {
                field: selected[field] for field in (
                    "source_key", "source_token", "telegram_user_id",
                    "tenant_id", "project_id", "actor_user_id",
                )
            }
            view.update(
                nonce=uuid4().hex,
                offset=offset,
                editable_areas=[
                    {"id": item["id"], "row_version": item["row_version"]}
                    for item in result["items"][offset:offset + 5]
                ] if result.get("can_edit_area") is True else [],
            )
            def page(position):
                return f'PA:page:{view["nonce"]}:{position}'
            rows = []
            if result.get("can_edit_area") is True:
                from ui.construction_area_edit import tr as edit_tr
                for index, item in enumerate(result["items"][offset:offset + 5]):
                    rows.append([button(
                        edit_tr("edit", language) + " · "
                        + str(offset + index + 1) + ". "
                        + area_button_name(result, item, language),
                        f'AE:open:{view["nonce"]}:{index}',
                    )])
            if offset:
                rows.append([button(tr("previous", language), page(max(0, offset - 5)))])
            if offset + 5 < total:
                rows.append([button(tr("next", language), page(offset + 5))])
            if result.get("can_create_area") is True:
                rows.append([button(tr("create", language), "AC:new:" + view["nonce"])])
            rows.extend([
                [button(tr("refresh", language), page(offset))],
                [button(tr("back", language), selected["back"])],
            ])
            rows.extend(back_rows(language))
            await show(
                callback, state, areas_text(result, offset=offset, language=language),
                rows, selected=selected, view=view,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_project_areas_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if isinstance(error, ConstructionApiClientError) and error.status_code == 429:
                key = "construction_bot_rate_limited"
            elif isinstance(error, ConstructionApiClientError) and error.status_code in (401, 403, 404):
                key = "construction_bot_unavailable"
            else:
                key = "construction_bot_error"
            await show(callback, state, t(key, language), back_rows(language))

# universal_area_editor_v1
