"""Draft creation receipts are kept separately from current draft reads."""
from copy import deepcopy
from html import escape
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardButton

from handlers.construction import construction_organization_templates as cards
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_template_draft_bot_api import TemplateDraftBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_template_drafts import tr


def state_map(data, key):
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Draft navigation state differs.")
    return deepcopy(value)


def slot(view):
    return view["tenant_id"] + ":" + view["actor_user_id"]


def base(item):
    return {
        "template_id": item["template_id"], "base_version_id": item["version_id"],
        "expected_template_row_version": item["template_row_version"],
        "base_version": item["version"],
        "source_starter_version_id": item["source_starter_version_id"],
        "source_language": item["source_language"],
        "section_count": item["section_count"], "question_count": item["question_count"],
    }


def button(view, key, action):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cards.cb(view, action),
    )


def attach_navigation(view, item, data):
    view = {**view, "draft_base": base(item)}
    known = state_map(data, cards.DRAFTS).get(slot(view), {})
    if not isinstance(known, dict):
        raise ValueError("Known draft selectors differ.")
    record = known.get(item["template_id"])
    if record is not None:
        view["known_draft"] = deepcopy(record)
    return view


def recovery_rows(view, data):
    if state_map(data, cards.PENDING).get(slot(view)):
        return [[button(view, "retry", "draftretry")]]
    return []


def draft_card_rows(view, data):
    rows = recovery_rows(view, data)
    if not rows:
        rows.append([button(view, "new", "draftnew")])
    if view.get("known_draft"):
        rows.append([button(view, "open_created", "draftopen")])
    return rows


async def review(callback, state, saved, view, api):
    if state_map(await state.get_data(), cards.PENDING).get(slot(view)):
        return
    result = await api.get_template(
        version_id=UUID(view["choice"]["version_id"]), **cards.binding(view),
    )
    item = result["item"]
    if (
        any(item[name] != value for name, value in view["choice"].items())
        or base(item) != view["draft_base"]
    ):
        raise problem(409, "ROW_VERSION_CONFLICT")
    view = {
        **cards.fresh(view, result, "draft_review"),
        "draft_request": base(item),
    }
    text = (
        tr("review", view["language"]) + "\n\n"
        + escape(item["display_title"][:200])
        + "\n" + tr("version", view["language"]) + ": " + str(item["version"])
        + "\n\n" + tr("note", view["language"])
    )
    await cards.show(callback, state, saved, view, text, [
        [button(view, "confirm", "draftcreate")],
        *cards.back_rows(view),
    ])


async def execute(callback, state, saved, view, api, *, retry):
    data = await state.get_data()
    if not cards.same(data, saved):
        return
    operations = state_map(data, cards.PENDING)
    key = slot(view)
    if retry:
        operation = operations.get(key)
        if not isinstance(operation, dict):
            return
    else:
        if view.get("phase") != "draft_review" or key in operations:
            return
        operation = {**deepcopy(view["draft_request"]), "key": uuid4().hex}
        operations[key] = deepcopy(operation)
    sending = {**view, "phase": "sending", "nonce": uuid4().hex}
    await state.update_data(**{cards.VIEW: sending, cards.PENDING: operations})
    saved = {
        **saved, cards.VIEW: deepcopy(sending), cards.PENDING: deepcopy(operations),
    }
    if not cards.same(await state.get_data(), saved):
        return
    try:
        result = await api.create_draft(
            request=deepcopy(operation), **cards.binding(view),
        )
    except Exception as error:
        cards.logger.warning(
            "construction_bot_template_draft_create_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not cards.same(await state.get_data(), saved):
            return
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        definite = status in (400, 422) or code in (
            "ROW_VERSION_CONFLICT", "TEMPLATE_VERSION_CONFLICT",
        )
        view = {**view, "nonce": uuid4().hex}
        if definite:
            operations.pop(key, None)
            await state.update_data(**{cards.PENDING: operations})
            saved = {**saved, cards.PENDING: deepcopy(operations)}
            view.update(phase="blocked")
            text, rows = tr("conflict", view["language"]), cards.back_rows(view)
        else:
            view.update(phase="draft_pending")
            text = tr("pending", view["language"])
            rows = [[button(view, "retry", "draftretry")], *cards.back_rows(view)]
        await cards.show(callback, state, saved, view, text, rows)
        return
    if not cards.same(await state.get_data(), saved):
        return

    item = result["item"]
    record = {
        name: item[name] for name in (
            "template_id", "version_id", "version", "base_version_id",
        )
    }
    known = state_map(await state.get_data(), cards.DRAFTS)
    records = known.setdefault(key, {})
    if not isinstance(records, dict):
        raise ValueError("Known draft selectors differ.")
    records[item["template_id"]] = deepcopy(record)
    operations.pop(key, None)
    await state.update_data(**{cards.PENDING: operations, cards.DRAFTS: known})
    saved = {
        **saved, cards.PENDING: deepcopy(operations), cards.DRAFTS: deepcopy(known),
    }
    view = {
        **cards.fresh(view, result, "draft_created"),
        "known_draft": record,
    }
    # The successful receipt is historical. No current-state claim or GET here.
    await cards.show(callback, state, saved, view, tr("created", view["language"]), [
        [button(view, "open_created", "draftopen")],
        *cards.back_rows(view),
    ])


async def open_draft(callback, state, saved, view, api, *, refresh):
    selector = view["draft"] if refresh else view["known_draft"]
    template_id, version_id = UUID(selector["template_id"]), UUID(selector["version_id"])
    result = await api.read_draft(
        template_id=template_id, version_id=version_id, **cards.binding(view),
    )
    item = result["item"]
    view = {
        **cards.fresh(view, result, "draft"),
        "draft": {
            name: item[name] for name in (
                "template_id", "version_id", "version", "version_row_version",
                "template_row_version", "definition_hash",
            )
        },
    }
    language = view["language"]
    text = (
        tr("draft", language)
        + "\n\n" + tr("number", language) + ": " + str(item["version"])
        + "\n" + tr("sections", language) + ": " + str(item["section_count"])
        + "\n" + tr("questions", language) + ": " + str(item["question_count"])
        + "\n\n" + tr("draft_note", language)
    )
    from handlers.construction.construction_template_draft_header import header_rows
    from handlers.construction.construction_template_draft_structure import navigation_rows
    from handlers.construction.construction_template_draft_check import navigation_rows as check_rows
    from handlers.construction.construction_template_publish import recovery_rows as publication_recovery
    await cards.show(callback, state, saved, view, text, [
        *navigation_rows(view),
        *check_rows(view),
        *publication_recovery(view, await state.get_data()),
        *header_rows(view, await state.get_data()),
        [button(view, "refresh_draft", "draftrefresh")],
        *cards.back_rows(view),
    ])


async def handle_draft_action(
    callback, state, saved, view, action, *, bot=None, event_update=None,
):
    phase = view.get("phase")
    if action == "draftnew" and phase != "card":
        return
    if action == "draftcreate" and phase != "draft_review":
        return
    if action == "draftopen" and (
        phase not in ("card", "draft_created") or not view.get("known_draft")
    ):
        return
    check_phases = ("draft", "draft_checked", "draft_check_failed")
    if action in ("draftrefresh", "draftcheck") and phase not in check_phases:
        return
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            if action == "draftcheck":
                from handlers.construction.construction_template_draft_check import show_check
                await show_check(callback, state, saved, view, service)
                return
            api = TemplateDraftBotApi(service)
            if action == "draftnew":
                await review(callback, state, saved, view, api)
            elif action in ("draftcreate", "draftretry"):
                await execute(callback, state, saved, view, api, retry=action == "draftretry")
            else:
                await open_draft(
                    callback, state, saved, view, api,
                    refresh=action == "draftrefresh",
                )
    except Exception as error:
        cards.logger.warning(
            "construction_bot_template_draft_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        view = {
            key: view[key] for key in (
                "telegram_user_id", "tenant_id", "actor_user_id", "language", "menu_id",
            )
        }
        view.update(phase="blocked", nonce=uuid4().hex)
        notice = "conflict" if getattr(error, "status_code", None) == 409 else "unavailable"
        await cards.show(callback, state, saved, view,
                         tr(notice, view["language"]), cards.back_rows(view))
