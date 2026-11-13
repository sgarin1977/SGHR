"""Open existing settings screens from the saved onboarding step."""
from copy import deepcopy
from uuid import uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton

from handlers.construction import construction_onboarding as guide
from handlers.construction import construction_stage2_settings as shell
from handlers.construction.construction_lead_create import private, lock_for
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_onboarding_bot_api import OnboardingBotApi
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)
from services.construction.construction_currency_bot_api import ConstructionCurrencyBotApi
from ui.construction_stage2_settings import tr as settings_tr
from ui.construction_region_settings import tr as region_tr
from ui.construction_unit_catalog import tr as units_tr
from ui.construction_pricing_profiles import tr as pricing_tr

router = Router(name="construction_onboarding_settings")
EXPECTED_STEP = {
    "language": "LOCALIZATION", "region": "LOCALIZATION",
    "currency": "LOCALIZATION", "units": "LOCALIZATION",
    "pricing": "PRICING_PROFILES",
}


def cb(view, action):
    return "OBC:" + action + ":" + view["nonce"]


def configuration_rows(view, step):
    language = view["language"]
    if step == "COMPANY":
        return [[InlineKeyboardButton(
            text="⚙️ " + guide.tr("COMPANY", language),
            callback_data="CP:open",
        )]]
    if step == "PRICING_PROFILES":
        options = [("pricing", pricing_tr("title", language))]
    elif step == "LOCALIZATION":
        options = [
            ("language", settings_tr("language", language)),
            ("region", region_tr("title", language)),
            ("currency", settings_tr("currency", language)),
            ("units", units_tr("title", language)),
        ]
    else:
        return []
    return [
        [InlineKeyboardButton(text="⚙️ " + text, callback_data=cb(view, action))]
        for action, text in options
    ]


@router.callback_query(F.data.startswith("OBC:"))
async def configure_callback(callback, state, bot=None, event_update=None):
    if not private(callback):
        await callback.answer(show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        if await state.get_state() is not None:
            return
        data = await state.get_data()
        view = deepcopy(data.get(guide.VIEW))
        parts = callback.data.split(":")
        if (
            len(parts) != 3 or parts[1] not in EXPECTED_STEP
            or not shell.bound(data, view, callback.from_user.id, callback.message.message_id)
            or view.get("phase") != "overview" or view.get("nonce") != parts[2]
        ):
            return
        action = parts[1]
        expected_step = EXPECTED_STEP[action]
        progress = view.get("progress", {})
        if not progress.get("started") or progress.get("current_step") != expected_step:
            return
        saved = guide.snapshot(data)
        parent = view
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                current = await OnboardingBotApi(service).read(**guide.binding(view))
                if not guide.same(await state.get_data(), saved):
                    return
                if (
                    current["actor_user_id"] != view["actor_user_id"]
                    or not current["item"]["started"]
                    or current["item"]["current_step"] != expected_step
                ):
                    raise problem(409, "ONBOARDING_STATE_CONFLICT")

                parent = {
                    key: view[key] for key in (
                        "telegram_user_id", "tenant_id", "actor_user_id", "menu_id",
                    )
                }
                parent["language"] = current["language"]
                parent["resource"] = "currency" if action == "currency" else "language"

                # Consume the old wizard nonce. The settings marker lets existing
                # screen guards reject results arriving after another navigation.
                marker = {
                    **parent, "phase": "handoff", "nonce": uuid4().hex,
                }
                await state.update_data(**{guide.VIEW: {}, shell.VIEW: marker})
                saved = {
                    **saved, guide.VIEW: {}, shell.VIEW: deepcopy(marker),
                }
                if not guide.same(await state.get_data(), saved):
                    return
                source = {key: deepcopy(saved.get(key)) for key in shell.KEYS}

                if action in ("language", "currency"):
                    cls = (
                        ConstructionCurrencyBotApi if action == "currency"
                        else ConstructionLanguageBotApi
                    )
                    result = await cls(service).read(**guide.binding(parent))
                    if not guide.same(await state.get_data(), saved):
                        return
                    # The resource read supplies its own version.
                    await shell.choices(callback, state, source, parent, result, 0)
                elif action == "region":
                    from handlers.construction.construction_region_settings import open_settings
                    await open_settings(callback, state, source, parent, service)
                elif action == "units":
                    from handlers.construction.construction_unit_catalog import show_catalog
                    await show_catalog(callback, state, source, parent, service, page=0)
                else:
                    from handlers.construction.construction_pricing_profiles import open_profiles
                    await open_profiles(callback, state, source, parent, service)
        except Exception as error:
            await guide.blocked(callback, state, saved, parent, error)
