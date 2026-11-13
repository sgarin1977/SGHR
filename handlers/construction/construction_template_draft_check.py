"""A fresh readiness screen for an exact draft, without publication."""
from copy import deepcopy

from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_drafts as drafts
from services.construction.construction_template_draft_check_bot_api import TemplateDraftCheckBotApi
from ui.construction_template_draft_check import tr


def navigation_rows(view):
    from aiogram.types import InlineKeyboardButton
    return [[InlineKeyboardButton(
        text=tr("check", view["language"]),
        callback_data=cards.cb(view, "draftcheck"),
    )]]


async def show_check(callback, state, saved, view, service):
    from handlers.construction.construction_template_draft_header import recovery_rows
    selector = deepcopy(view["draft"])
    if drafts.state_map(await state.get_data(), cards.HEADER_PENDING).get(drafts.slot(view)):
        blocked = {
            **cards.fresh(view, {
                "actor_user_id": view["actor_user_id"], "language": view["language"],
            }, "draft_check_failed"),
            "draft": selector,
        }
        await cards.show(
            callback, state, saved, blocked, tr("edit_pending", blocked["language"]),
            [*recovery_rows(blocked, await state.get_data()), *cards.back_rows(blocked)],
        )
        return
    try:
        result = await TemplateDraftCheckBotApi(service).check_draft(
            selector=selector, **cards.binding(view),
        )
        item = result["item"]
        checked = {
            **cards.fresh(view, result, "draft_checked"),
            "draft": selector,
        }
        language = checked["language"]
        text = (
            tr("checked", language)
            + "\n\n" + tr("number", language) + ": " + str(item["version"])
            + "\n" + tr("sections", language) + ": " + str(item["section_count"])
            + "\n" + tr("questions", language) + ": " + str(item["question_count"])
            + "\n" + tr("bindings", language) + ": " + str(item["binding_count"])
            + "\n" + tr("active" if item["active"] else "inactive", language)
            + "\n\n" + tr("note", language)
        )
        from handlers.construction.construction_template_publish import publication_rows
        await cards.show(callback, state, saved, checked, text, [
            *publication_rows(checked, await state.get_data()),
            *navigation_rows(checked),
            [drafts.button(checked, "refresh_draft", "draftrefresh")],
            *cards.back_rows(checked),
        ])
    except Exception as error:
        cards.logger.warning(
            "construction_bot_draft_check_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        blocked = {
            **cards.fresh(view, {
                "actor_user_id": view["actor_user_id"], "language": view["language"],
            }, "draft_check_failed"),
            "draft": selector,
        }
        await cards.show(callback, state, saved, blocked, tr("failed", blocked["language"]), [
            [drafts.button(blocked, "refresh_draft", "draftrefresh")],
            *cards.back_rows(blocked),
        ])
