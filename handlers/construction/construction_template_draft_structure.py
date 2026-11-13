"""UUID-pinned section/question navigation; no question execution or writes."""
from copy import deepcopy
from html import escape
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardButton

from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_drafts as drafts
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_template_draft_structure_bot_api import TemplateDraftStructureBotApi
from services.construction.construction_template_draft_label_bot_api import TemplateDraftLabelBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_template_draft_structure import tr
from ui.construction_unit_catalog import unit_text

PAGE_SIZE = 6
PHASES = ("structure_sections", "structure_questions", "structure_question")


def button(view, key, action, index=None):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cards.cb(view, action, index),
    )


def navigation_rows(view):
    return [[button(view, "structure", "str_sections")]]


def selector(item):
    return {
        name: item[name] for name in (
            "template_id", "version_id", "version", "version_row_version",
            "template_row_version", "definition_hash",
        )
    }


def find(rows, identity):
    item = next((item for item in rows if item["id"] == identity), None)
    if item is None:
        raise problem(404, "not_found")
    return item


def back_rows(view, target):
    rows = [[button(view, "refresh_structure", "str_refresh")]]
    if target["kind"] == "question":
        rows.append([button(view, "back_questions", "str_qp", target.get("page", 0))])
    if target["kind"] != "sections":
        rows.append([button(view, "back_sections", "str_sections")])
    rows.append([button(view, "back_draft", "str_draft")])
    rows.extend(cards.back_rows(view))
    return rows


async def render(callback, state, saved, view, api, target, *, reload=False):
    result = await api.read_structure(
        template_id=UUID(view["draft"]["template_id"]),
        version_id=UUID(view["draft"]["version_id"]), **cards.binding(view),
    )
    item = result["item"]
    if not reload and (
        item["version_row_version"] != view["draft"]["version_row_version"]
        or item["definition_hash"] != view["draft"]["definition_hash"]
    ):
        raise problem(409, "ROW_VERSION_CONFLICT")
    target = deepcopy(target)
    next_view = {
        **cards.fresh(view, result, "structure_" + target["kind"]),
        "draft": selector(item),
    }
    language = next_view["language"]
    prefix = (
        tr("original_language", language) + ": " + item["source_language"]
        + "\n" + tr("number", language) + ": " + str(item["version"])
    )
    section = None
    if target["kind"] != "sections":
        section = find(item["sections"], target["section_id"])
    rows = []

    if target["kind"] == "question":
        question = find(section["questions"], target["question_id"])
        unit = None
        if question["unit_code"] is not None:
            unit = await api.unit_option(
                code=question["unit_code"], expected_language=language,
                **cards.binding(view),
            )
        text = escape(question["label"]) + "\n\n" + prefix
        text += (
            "\n\n" + tr("type", language) + ": " + tr(question["question_type"], language)
            + "\n" + tr("active" if question["active"] else "inactive", language)
            + "\n" + tr("required", language) + ": " + tr("yes" if question["required"] else "no", language)
            + "\n" + tr("unit", language) + ": "
            + (tr("empty", language) if unit is None else escape(unit_text(unit)))
            + "\n" + tr("condition", language) + ": " + tr("yes" if question["has_condition"] else "no", language)
            + "\n" + tr("options", language) + ": " + str(question["option_count"])
        )
    else:
        items = item["sections"] if target["kind"] == "sections" else section["questions"]
        pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = target.get("page", 0)
        if reload:
            page = min(page, pages - 1)
        if type(page) is not int or not 0 <= page < pages:
            raise ValueError("Structure page is unavailable.")
        target["page"] = page
        visible = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        next_view["choice_ids"] = [entry["id"] for entry in visible]
        pick = "str_section" if target["kind"] == "sections" else "str_question"
        paging = "str_sp" if target["kind"] == "sections" else "str_qp"
        text = tr("sections_title" if target["kind"] == "sections" else "questions_title", language)
        text += "\n\n" + prefix
        if section is not None:
            text += "\n\n" + escape(section["label"])
            text += "\n" + tr("active" if section["active"] else "inactive", language)
        text += "\n\n" + str(page + 1) + " / " + str(pages)
        rows = [
            [InlineKeyboardButton(
                text=("⏸ " if not entry["active"] else "") + entry["label"][:85],
                callback_data=cards.cb(next_view, pick, index),
            )]
            for index, entry in enumerate(visible)
        ]
        if page:
            rows.append([button(next_view, "previous", paging, page - 1)])
        if page + 1 < pages:
            rows.append([button(next_view, "next", paging, page + 1)])

    if target["kind"] in ("questions", "question"):
        rows.append([button(next_view, "edit_label", "str_label")])
        rows.append([button(next_view, "change_order", "str_order")])
        rows.append([button(next_view, "change_active", "str_active")])
        if target["kind"] == "question":
            rows.append([button(next_view, "change_required", "str_required")])
    next_view["structure"] = target
    await cards.show(
        callback, state, saved, next_view, text, rows + back_rows(next_view, target),
    )


async def flag_review(callback, state, saved, view, api, *, kind, item_id, field):
    from handlers.construction import construction_template_draft_header as editor
    result = await api.prepare_flag(
        template_id=UUID(view["draft"]["template_id"]),
        version_id=UUID(view["draft"]["version_id"]),
        expected_row_version=view["draft"]["version_row_version"],
        expected_definition_hash=view["draft"]["definition_hash"],
        kind=kind, item_id=item_id, field=field, **cards.binding(view),
    )
    next_view = {
        **cards.fresh(view, result, "header_review"),
        "draft": selector(result["item"]),
        "header_request": deepcopy(result["request"]),
    }
    language = next_view["language"]
    text = (
        tr("review_flags", language)
        + "\n\n" + tr("original_language", language) + ": "
        + result["request"]["source_language"]
        + "\n\n" + escape(result["label"])
        + "\n\n" + tr(field + "_flag", language) + ": "
        + tr("yes" if result["original"] else "no", language)
        + " → " + tr("yes" if result["value"] else "no", language)
        + "\n\n" + tr("flags_note", language)
    )
    await editor.show(callback, state, saved, next_view, text, [
        [editor.button(next_view, "save", "hdr_save")],
        [editor.button(next_view, "cancel", "hdr_back")],
    ])


async def handle_structure_action(
    callback, state, saved, view, action, index, *, bot=None, event_update=None,
):
    phase = view.get("phase")
    current = view.get("structure", {})
    if not isinstance(view.get("draft"), dict):
        return
    reload = action == "str_refresh"
    if action in ("str_label", "str_active", "str_required", "str_order"):
        if phase not in ("structure_questions", "structure_question"):
            return
        if action == "str_required" and phase != "structure_question":
            return
        target = None
    elif action == "str_draft":
        if phase not in PHASES + ("structure_blocked",):
            return
        target = None
    elif action == "str_refresh":
        if phase == "structure_blocked":
            target = {"kind": "sections", "page": 0}
        elif phase in PHASES:
            target = deepcopy(current)
        else:
            return
    elif action == "str_sections":
        if phase not in ("draft",) + PHASES:
            return
        target = {"kind": "sections", "page": 0}
    elif action == "str_sp":
        if phase != "structure_sections":
            return
        target = {"kind": "sections", "page": index}
    elif action == "str_section":
        if phase != "structure_sections" or index >= len(view.get("choice_ids", [])):
            return
        target = {
            "kind": "questions", "section_id": view["choice_ids"][index], "page": 0,
        }
    elif action == "str_qp":
        if phase not in ("structure_questions", "structure_question"):
            return
        target = {
            "kind": "questions", "section_id": current["section_id"], "page": index,
        }
    elif action == "str_question":
        if phase != "structure_questions" or index >= len(view.get("choice_ids", [])):
            return
        target = {
            "kind": "question", "section_id": current["section_id"],
            "question_id": view["choice_ids"][index], "page": current["page"],
        }
    else:
        return

    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            if action in ("str_label", "str_active", "str_required", "str_order"):
                from handlers.construction import construction_template_draft_header as editor
                pending = drafts.state_map(await state.get_data(), cards.HEADER_PENDING)
                if pending.get(drafts.slot(view)):
                    await editor.show(
                        callback, state, saved, view, editor.tr("pending", view["language"]),
                        [*editor.recovery_rows(view, await state.get_data()), *cards.back_rows(view)],
                    )
                else:
                    kind = "QUESTION" if phase == "structure_question" else "SECTION"
                    identity = current["question_id"] if kind == "QUESTION" else current["section_id"]
                    if action in ("str_label", "str_order"):
                        await editor.input_form(
                            callback, state, saved, view, TemplateDraftLabelBotApi(service),
                            "label" if action == "str_label" else "sort_order",
                            kind=kind, item_id=UUID(identity),
                        )
                    else:
                        await flag_review(
                            callback, state, saved, view, TemplateDraftLabelBotApi(service),
                            kind=kind, item_id=UUID(identity),
                            field="active" if action == "str_active" else "required",
                        )
                return
            api = TemplateDraftStructureBotApi(service)
            if action == "str_draft":
                await drafts.open_draft(callback, state, saved, view, api, refresh=True)
            else:
                await render(callback, state, saved, view, api, target, reload=reload)
    except Exception as error:
        cards.logger.warning(
            "construction_bot_draft_structure_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        view = {
            **cards.fresh(view, {"actor_user_id": view["actor_user_id"], "language": view["language"]},
                          "structure_blocked"),
            "draft": deepcopy(view["draft"]),
        }
        status = getattr(error, "status_code", None)
        notice = "conflict" if status == 409 else "invalid_change" if status == 422 else "unavailable"
        await cards.show(callback, state, saved, view, tr(notice, view["language"]), [
            [button(view, "refresh_structure", "str_refresh")],
            [button(view, "back_draft", "str_draft")],
            *cards.back_rows(view),
        ])
