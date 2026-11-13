"""Tax-profile choice inside the existing onboarding router."""
from copy import deepcopy
from html import escape
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardButton

from handlers.construction import construction_onboarding as guide
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_language_bot_api import problem
from services.construction.construction_onboarding_tax_bot_api import OnboardingTaxBotApi
from ui.construction_onboarding_tax import tr
from ui.construction_tax_profiles import profile_text


def fresh(view, result, phase):
    return {
        "telegram_user_id": view["telegram_user_id"], "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"], "language": result["language"],
        "menu_id": view["menu_id"], "phase": phase, "nonce": uuid4().hex,
    }


def button(view, key, action):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=guide.cb(view, action),
    )


def back_rows(view):
    return [
        [button(view, "back", "refresh")],
        guide.back_rows(view)[1],
    ]


async def checked_selection(api, view, *, expected=None):
    progress = await api.read(**guide.binding(view))
    item = progress["item"]
    if not item["started"] or item["current_step"] != "TAXES":
        raise problem(409, "ONBOARDING_STATE_CONFLICT")
    selection = await api.selection(**guide.binding(view))
    current = selection["item"]
    if (
        current is None
        or current["row_version"] != item["row_version"]
        or expected is not None and current["row_version"] != expected
    ):
        raise problem(409, "ROW_VERSION_CONFLICT")
    return selection


async def list_page(callback, state, saved, view, api, *, cursor=None):
    selection = await checked_selection(api, view)
    result = await api.list_profiles(cursor=cursor, **guide.binding(view))
    selected_id = selection["item"]["onboarding_tax_profile_id"]
    selected = next(
        (item for item in result["items"] if item["id"] == selected_id), None,
    )
    if selected_id is not None and selected is None:
        selected = (
            await api.get_profile(
                profile_id=UUID(selected_id), **guide.binding(view),
            )
        )["item"]
    if not guide.same(await state.get_data(), saved):
        return

    view = fresh(view, result, "tax_list")
    view.update(
        profile_ids=[item["id"] for item in result["items"]],
        next_cursor=result["next_cursor"],
        expected_row_version=selection["item"]["row_version"],
    )
    language = view["language"]
    text = tr("title", language)
    if selected is None:
        text += "\n\n" + tr("unselected", language)
    else:
        text += "\n\n" + tr("selected", language) + ": " + escape(selected["name"][:200])
    if not result["items"]:
        text += "\n\n" + tr("empty", language)
    text += "\n\n" + tr("note", language)
    rows = [
        [InlineKeyboardButton(
            text=("✓ " if item["id"] == selected_id else "") + item["name"][:85],
            callback_data=guide.cb(view, "taxpick", index),
        )]
        for index, item in enumerate(result["items"])
    ]
    if view["next_cursor"] is not None:
        rows.append([button(view, "next", "taxnext")])
    rows.append([button(view, "refresh_list", "tax")])
    await guide.show(callback, state, saved, view, text, rows + back_rows(view))


async def review_selection(callback, state, saved, view, api, index):
    ids = view.get("profile_ids", [])
    if index is None or not 0 <= index < len(ids):
        return
    # Pin the exact ID before awaits. Never reinterpret the index in a new page.
    profile_id = UUID(ids[index])
    await checked_selection(api, view, expected=view["expected_row_version"])
    result = await api.get_profile(profile_id=profile_id, **guide.binding(view))
    new_view = fresh(view, result, "tax_review")
    new_view.update(
        selected_profile_id=str(profile_id),
        expected_row_version=view["expected_row_version"],
    )
    text = (
        tr("review", new_view["language"]) + "\n\n"
        + escape(profile_text(result["item"], new_view["language"]))
        + "\n\n" + tr("note", new_view["language"])
    )
    await guide.show(callback, state, saved, new_view, text, [
        [button(new_view, "confirm", "taxsave")],
        [button(new_view, "cancel", "tax")],
        back_rows(new_view)[0],
    ])


async def handle_tax_action(
    callback, state, saved, view, action, index, *, bot=None, event_update=None,
):
    phase = view.get("phase")
    if action == "tax":
        if phase == "overview":
            progress = view["progress"]
            if not progress["started"] or progress["current_step"] != "TAXES":
                return
        elif phase not in ("tax_list", "tax_review"):
            return
    elif action in ("taxnext", "taxpick"):
        if phase != "tax_list":
            return
        if action == "taxnext" and view.get("next_cursor") is None:
            return
    elif action == "taxsave":
        if phase != "tax_review":
            return
    else:
        return

    writing = False
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            api = OnboardingTaxBotApi(service)
            if action in ("tax", "taxnext"):
                await list_page(
                    callback, state, saved, view, api,
                    cursor=view["next_cursor"] if action == "taxnext" else None,
                )
                return
            if action == "taxpick":
                await review_selection(callback, state, saved, view, api, index)
                return
            if not guide.same(await state.get_data(), saved):
                return
            profile_id = UUID(view["selected_profile_id"])
            version = view["expected_row_version"]
            view = {**view, "phase": "sending", "nonce": uuid4().hex}
            await state.update_data(**{guide.VIEW: view})
            saved = guide.snapshot(await state.get_data())
            writing = True
            result = await api.select(
                profile_id=profile_id, expected_row_version=version,
                **guide.binding(view),
            )
        # Do not hide a successful PATCH behind a second HTTP read.
        view = fresh(view, result, "tax_done")
        await guide.show(
            callback, state, saved, view,
            tr("saved", view["language"]), back_rows(view),
        )
    except Exception as error:
        await guide.blocked(
            callback, state, saved, view, error, writing=writing,
        )
