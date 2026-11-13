"""Unit catalog screen sharing the current settings context and nonce."""
from uuid import UUID, uuid4

from handlers.construction import construction_stage2_settings as settings
from services.construction.construction_unit_catalog_bot_api import (
    ConstructionUnitCatalogBotApi,
)
from ui.construction_unit_catalog import tr, unit_text
from ui.texts import t

PAGE_SIZE = 8


async def show_catalog(callback, state, saved, view, service, *, page):
    if not settings.unchanged(await state.get_data(), saved):
        return
    result = await ConstructionUnitCatalogBotApi(service).read(
        telegram_user_id=view["telegram_user_id"],
        tenant_id=UUID(view["tenant_id"]),
        expected_user_id=UUID(view["actor_user_id"]),
    )
    if not settings.unchanged(await state.get_data(), saved):
        return

    units = result["item"]["units"]
    pages = (len(units) + PAGE_SIZE - 1) // PAGE_SIZE
    if type(page) is not int or not 0 <= page < pages:
        raise ValueError("Unit catalog page is unavailable.")

    next_view = {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"],
        "language": result["language"],
        "menu_id": view["menu_id"],
        "phase": "units",
        "nonce": uuid4().hex,
        "units_page": page,
    }
    if "resource" in view:
        next_view["resource"] = view["resource"]

    language = next_view["language"]
    first = page * PAGE_SIZE
    visible = units[first:first + PAGE_SIZE]
    text = (
        tr("title", language) + "\n\n"
        + result["organization"][:200] + "\n\n"
        + tr("description", language) + "\n\n"
        + "\n".join(
            str(first + index + 1) + ". " + unit_text(unit)
            for index, unit in enumerate(visible)
        )
        + "\n\n" + str(page + 1) + " / " + str(pages)
    )
    rows = []
    if page:
        rows.append([settings.button(
            tr("previous", language), settings.cb(next_view, "up", page - 1),
        )])
    if page + 1 < pages:
        rows.append([settings.button(
            tr("next", language), settings.cb(next_view, "up", page + 1),
        )])
    rows.extend([
        [settings.button(
            tr("refresh", language), settings.cb(next_view, "up", page),
        )],
        [settings.button(
            tr("settings", language), settings.cb(next_view, "refresh"),
        )],
        [settings.button(
            t("construction_bot_home_btn", language), "CS_HOME",
        )],
    ])
    await settings.publish(callback, state, saved, next_view, text, rows)
