from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.repositories.privacy import PrivacyRepository
from database.repositories.translation import TranslationRepository
from database.session import get_session
from handlers.start import normalize_language, send_global_main_menu
from services.privacy import PrivacyError, PrivacyService
from services.user import UserService
from ui.texts import t
from aiogram.fsm.context import FSMContext
settings_router = Router()


def translation_settings_keyboard(
    *,
    language: str,
    message_language: str,
    auto_translate_enabled: bool,
    show_original_button: bool,
) -> InlineKeyboardMarkup:
    original_text = (
        t("settings_show_original_on", language)
        if show_original_button
        else t("settings_show_original_off", language)
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings_interface_language_label", language),
                    callback_data="SET_NOOP",
                )
            ],
            [
                InlineKeyboardButton(text="RU", callback_data="SET_UI_LANG:ru"),
                InlineKeyboardButton(text="EN", callback_data="SET_UI_LANG:en"),
                InlineKeyboardButton(text="PT", callback_data="SET_UI_LANG:pt"),
            ],
            [
                InlineKeyboardButton(
                    text=t("settings_message_language_label", language),
                    callback_data="SET_NOOP",
                )
            ],
            [
                InlineKeyboardButton(text="RU", callback_data="SET_MSG_LANG:ru"),
                InlineKeyboardButton(text="EN", callback_data="SET_MSG_LANG:en"),
                InlineKeyboardButton(text="PT", callback_data="SET_MSG_LANG:pt"),
            ],
            [
                InlineKeyboardButton(
                    text=original_text,
                    callback_data="SET_SHOW_ORIGINAL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_open_btn", language),
                    callback_data="PRIVACY_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_open_btn", language),
                    callback_data="SUPPORT_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="SET_MAIN_MENU",
                )
            ],
        ]
    )
async def show_translation_settings(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        repository = TranslationRepository(session)
        settings = await repository.get_language_settings(user.id)
        language = normalize_language(settings.interface_language or user.language_code)
        await session.commit()

    await callback.message.answer(
        t("settings_translation_title", language).format(
            interface_language=settings.interface_language,
            message_language=settings.message_language,
            auto_translate=t(
                "settings_enabled" if settings.auto_translate_enabled else "settings_disabled",
                language,
            ),
            show_original=t(
                "settings_enabled" if settings.show_original_button else "settings_disabled",
                language,
            ),
        ),
        reply_markup=translation_settings_keyboard(
            language=language,
            message_language=settings.message_language,
            auto_translate_enabled=settings.auto_translate_enabled,
            show_original_button=settings.show_original_button,
        ),
    )
    await callback.answer()

async def get_user_settings_context(callback: CallbackQuery):
    fallback_language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            return None, fallback_language

        settings = await TranslationRepository(session).get_language_settings(user.id)
        language = normalize_language(settings.interface_language or user.language_code)
        await session.commit()

    return user, language


def privacy_settings_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("privacy_hide_profile_btn", language),
                    callback_data="PRIVACY_HIDE_PROFILE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_my_data_btn", language),
                    callback_data="PRIVACY_MY_DATA",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_delete_geo_btn", language),
                    callback_data="PRIVACY_DELETE_GEO_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_delete_profile_btn", language),
                    callback_data="PRIVACY_DELETE_PROFILE_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_back_to_settings", language),
                    callback_data="M_SETTINGS",
                )
            ],
        ]
    )


def privacy_confirm_keyboard(
    *,
    language: str,
    confirm_callback: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("privacy_confirm_btn", language),
                    callback_data=confirm_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="PRIVACY_MENU",
                )
            ],
        ]
    )
@settings_router.callback_query(F.data == "SET_NOOP")
async def settings_noop(callback: CallbackQuery):
    await callback.answer()
@settings_router.callback_query(F.data == "M_SETTINGS")
async def open_settings(callback: CallbackQuery):
    await show_translation_settings(callback)

@settings_router.callback_query(F.data.startswith("SET_UI_LANG:"))
async def set_interface_language(callback: CallbackQuery):
    fallback_language = normalize_language(callback.from_user.language_code)
    interface_language = normalize_language(callback.data.split(":", 1)[1])

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", fallback_language), show_alert=True)
            return

        await TranslationRepository(session).update_language_settings(
            user_id=user.id,
            interface_language=interface_language,
        )
        user.language_code = interface_language
        await session.commit()

    await show_translation_settings(callback)

@settings_router.callback_query(F.data.startswith("SET_MSG_LANG:"))
async def set_message_language(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    message_language = callback.data.split(":", 1)[1]

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        await TranslationRepository(session).update_language_settings(
            user_id=user.id,
            message_language=message_language,
        )
        await session.commit()

    await show_translation_settings(callback)


@settings_router.callback_query(F.data == "SET_AUTO_TRANSLATE")
async def toggle_auto_translate(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    await callback.answer(t("feature_disabled_beta_message", language), show_alert=True)

@settings_router.callback_query(F.data == "SET_SHOW_ORIGINAL")
async def toggle_show_original(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        repository = TranslationRepository(session)
        settings = await repository.get_language_settings(user.id)
        await repository.update_language_settings(
            user_id=user.id,
            show_original_button=not settings.show_original_button,
        )
        await session.commit()

    await show_translation_settings(callback)


@settings_router.callback_query(F.data == "SET_MAIN_MENU")
async def settings_to_main_menu(callback: CallbackQuery, state: FSMContext):
    await send_global_main_menu(callback, state)

@settings_router.callback_query(F.data == "PRIVACY_MENU")
async def open_privacy_settings(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("privacy_settings_title", language),
        reply_markup=privacy_settings_keyboard(language),
    )
    await callback.answer()


@settings_router.callback_query(F.data == "PRIVACY_HIDE_PROFILE")
async def hide_profile(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            fresh_user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
            if not fresh_user:
                await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
                return

            await PrivacyService(PrivacyRepository(session)).hide_specialist_profile(
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
            )
    except PrivacyError:
        await callback.answer(t("privacy_profile_not_found", language), show_alert=True)
        return

    await callback.message.answer(t("privacy_profile_hidden", language))
    await callback.answer()


@settings_router.callback_query(F.data == "PRIVACY_MY_DATA")
async def request_my_data(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        fresh_user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not fresh_user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        await PrivacyService(PrivacyRepository(session)).request_data_export(
            tenant_id=fresh_user.tenant_id,
            user_id=fresh_user.id,
        )

    await callback.message.answer(t("privacy_data_export_requested", language))
    await callback.answer()


@settings_router.callback_query(F.data == "PRIVACY_DELETE_GEO_CONFIRM")
async def confirm_delete_geo(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("privacy_confirm_delete_geo", language),
        reply_markup=privacy_confirm_keyboard(
            language=language,
            confirm_callback="PRIVACY_DELETE_GEO",
        ),
    )
    await callback.answer()


@settings_router.callback_query(F.data == "PRIVACY_DELETE_GEO")
async def delete_geo(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        fresh_user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not fresh_user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        await PrivacyService(PrivacyRepository(session)).delete_geo_data(
            tenant_id=fresh_user.tenant_id,
            user_id=fresh_user.id,
        )

    await callback.message.answer(t("privacy_geo_deleted", language))
    await callback.answer()


@settings_router.callback_query(F.data == "PRIVACY_DELETE_PROFILE_CONFIRM")
async def confirm_delete_profile(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("privacy_confirm_delete_profile", language),
        reply_markup=privacy_confirm_keyboard(
            language=language,
            confirm_callback="PRIVACY_DELETE_PROFILE",
        ),
    )
    await callback.answer()


@settings_router.callback_query(F.data == "PRIVACY_DELETE_PROFILE")
async def schedule_delete_profile(callback: CallbackQuery):
    user, language = await get_user_settings_context(callback)
    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        fresh_user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not fresh_user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        await PrivacyService(PrivacyRepository(session)).schedule_profile_deletion(
            tenant_id=fresh_user.tenant_id,
            user_id=fresh_user.id,
        )

    await callback.message.answer(t("privacy_deletion_scheduled", language))
    await callback.answer()