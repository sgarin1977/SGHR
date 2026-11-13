"""Publication review, immutable pending request and original receipt recovery."""
from copy import deepcopy
from html import escape
from uuid import uuid4

from aiogram.types import InlineKeyboardButton

from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_drafts as drafts
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_template_publish_bot_api import TemplatePublishBotApi
from ui.construction_template_publish import tr


def button(view, key, action):
    return InlineKeyboardButton(text=tr(key, view["language"]), callback_data=cards.cb(view, action))


def recovery_rows(view, data):
    if drafts.state_map(data, cards.PUBLISH_PENDING).get(drafts.slot(view)):
        return [[button(view, "retry", "pubretry")]]
    return []


def publication_rows(view, data):
    rows = recovery_rows(view, data)
    if rows or view.get("phase") != "draft_checked":
        return rows
    return [[button(view, "begin", "pubreview")]]


async def blocked(callback, state, saved, view):
    next_view = {
        **cards.fresh(view, {
            "actor_user_id": view["actor_user_id"], "language": view["language"],
        }, "pub_blocked"),
        "draft": deepcopy(view["draft"]),
    }
    await cards.show(callback, state, saved, next_view, tr("blocked", next_view["language"]), [
        [button(next_view, "back", "pubcancel")], *cards.back_rows(next_view),
    ])


async def review(callback, state, saved, view, api):
    data = await state.get_data()
    if drafts.state_map(data, cards.PUBLISH_PENDING).get(drafts.slot(view)):
        await cards.show(callback, state, saved, view, tr("pending", view["language"]), [
            *recovery_rows(view, data), *cards.back_rows(view),
        ])
        return
    if drafts.state_map(data, cards.HEADER_PENDING).get(drafts.slot(view)):
        from handlers.construction.construction_template_draft_header import recovery_rows as edit_rows
        await cards.show(callback, state, saved, view, tr("edit_pending", view["language"]), [
            *edit_rows(view, data), *cards.back_rows(view),
        ])
        return
    result = await api.prepare_publish(selector=deepcopy(view["draft"]), **cards.binding(view))
    request = result["request"]
    summary = request["summary"]
    next_view = {
        **cards.fresh(view, result, "pub_review"),
        "draft": deepcopy(request["selector"]), "publication_request": deepcopy(request),
    }
    language = next_view["language"]
    text = (
        tr("review", language)
        + "\n\n" + tr("original", language) + ": " + summary["source_language"]
        + "\n\n" + escape(summary["title"])
        + "\n" + tr("number", language) + ": " + str(request["selector"]["version"])
        + "\n" + tr("sections", language) + ": " + str(summary["section_count"])
        + "\n" + tr("questions", language) + ": " + str(summary["question_count"])
        + "\n" + tr("active" if summary["active"] else "inactive", language)
        + "\n\n" + tr("immutable", language)
    )
    await cards.show(callback, state, saved, next_view, text, [
        [button(next_view, "confirm", "pubconfirm")],
        [button(next_view, "back", "pubcancel")],
    ])


async def execute(callback, state, saved, view, api, *, retry):
    data = await state.get_data()
    if not cards.same(data, saved):
        return
    operations = drafts.state_map(data, cards.PUBLISH_PENDING)
    slot = drafts.slot(view)
    if retry:
        operation = operations.get(slot)
        if not isinstance(operation, dict):
            return
    else:
        if view.get("phase") != "pub_review" or slot in operations:
            return
        operation = {**deepcopy(view["publication_request"]), "key": uuid4().hex}
        operations[slot] = deepcopy(operation)
    sending = {**view, "phase": "sending", "nonce": uuid4().hex}
    await state.update_data(**{cards.VIEW: sending, cards.PUBLISH_PENDING: operations})
    saved = {
        **saved, cards.VIEW: deepcopy(sending), cards.PUBLISH_PENDING: deepcopy(operations),
    }
    if not cards.same(await state.get_data(), saved):
        return
    try:
        result = await api.publish(request=deepcopy(operation), **cards.binding(view))
    except Exception as error:
        cards.logger.warning(
            "construction_bot_template_publish_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None), getattr(error, "code", None),
        )
        if not cards.same(await state.get_data(), saved):
            return
        definite = getattr(error, "status_code", None) in (400, 422) or getattr(error, "code", None) in (
            "ROW_VERSION_CONFLICT", "TEMPLATE_VERSION_CONFLICT",
        )
        next_view = {
            **cards.fresh(view, {
                "actor_user_id": view["actor_user_id"], "language": view["language"],
            }, "pub_blocked" if definite else "pub_pending"),
            "draft": deepcopy(operation["selector"]),
        }
        if definite:
            operations.pop(slot, None)
            await state.update_data(**{cards.PUBLISH_PENDING: operations})
            saved = {**saved, cards.PUBLISH_PENDING: deepcopy(operations)}
            rows = [[button(next_view, "back", "pubcancel")], *cards.back_rows(next_view)]
        else:
            rows = [[button(next_view, "retry", "pubretry")], *cards.back_rows(next_view)]
        await cards.show(callback, state, saved, next_view,
                         tr("blocked" if definite else "pending", next_view["language"]), rows)
        return
    if not cards.same(await state.get_data(), saved):
        return

    item = result["item"]
    known = drafts.state_map(await state.get_data(), cards.DRAFTS)
    records = known.get(slot, {})
    if not isinstance(records, dict):
        raise ValueError("Known draft selectors differ.")
    record = records.get(item["template_id"])
    if isinstance(record, dict) and record.get("version_id") == item["version_id"]:
        records.pop(item["template_id"])
    operations.pop(slot, None)
    await state.update_data(**{cards.PUBLISH_PENDING: operations, cards.DRAFTS: known})
    saved = {
        **saved, cards.PUBLISH_PENDING: deepcopy(operations), cards.DRAFTS: deepcopy(known),
    }
    next_view = {
        **cards.fresh(view, result, "pub_saved"),
        "published_choice": {
            name: item[name] for name in ("template_id", "version_id", "content_hash")
        },
    }
    text = (
        tr("saved", next_view["language"])
        + "\n\n" + tr("original", next_view["language"]) + ": " + item["source_language"]
        + "\n" + escape(item["title"])
        + "\n" + tr("number", next_view["language"]) + ": " + str(item["version"])
    )
    await cards.show(callback, state, saved, next_view, text, [
        [button(next_view, "open", "pubopen")], *cards.back_rows(next_view),
    ])


async def handle_publication_action(
    callback, state, saved, view, action, *, bot=None, event_update=None,
):
    phase = view.get("phase")
    if action == "pubreview" and phase != "draft_checked":
        return
    if action == "pubconfirm" and phase != "pub_review":
        return
    if action == "pubcancel" and phase not in ("pub_review", "pub_blocked"):
        return
    if action == "pubopen" and phase != "pub_saved":
        return
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            api = TemplatePublishBotApi(service)
            if action == "pubreview":
                await review(callback, state, saved, view, api)
            elif action in ("pubconfirm", "pubretry"):
                await execute(callback, state, saved, view, api, retry=action == "pubretry")
            elif action == "pubcancel":
                await drafts.open_draft(callback, state, saved, view, api, refresh=True)
            elif action == "pubopen":
                await cards.card(callback, state, saved, view, api, deepcopy(view["published_choice"]))
    except Exception as error:
        cards.logger.warning(
            "construction_bot_template_publication_ui_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None), getattr(error, "code", None),
        )
        if drafts.state_map(await state.get_data(), cards.PUBLISH_PENDING).get(drafts.slot(view)):
            await cards.show(callback, state, saved, view, tr("pending", view["language"]), [
                *recovery_rows(view, await state.get_data()), *cards.back_rows(view),
            ])
        elif isinstance(view.get("draft"), dict):
            await blocked(callback, state, saved, view)
        else:
            await cards.show(callback, state, saved, view, tr("unavailable", view["language"]),
                             cards.back_rows(view))
