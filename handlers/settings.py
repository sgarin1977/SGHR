from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.repositories.translation import TranslationRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard, normalize_language
from services.user import UserService
from ui.texts import t

settings_router = Router()


def translation_settings_keyboard(
    *,
    language: str,
    message_language: str,
    auto_translate_enabled: bool,
    show_original_button: bool,
) -> InlineKeyboardMarkup:
    auto_text = (
        t("settings_auto_translate_on", language)
        if auto_translate_enabled
        else t("settings_auto_translate_off", language)
    )
    original_text = (
        t("settings_show_original_on", language)
        if show_original_button
        else t("settings_show_original_off", language)
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings_message_language_ru", language),
                    callback_data="SET_MSG_LANG:ru",
                ),
                InlineKeyboardButton(
                    text=t("settings_message_language_en", language),
                    callback_data="SET_MSG_LANG:en",
                ),
                InlineKeyboardButton(
                    text=t("settings_message_language_pt", language),
                    callback_data="SET_MSG_LANG:pt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=auto_text,
                    callback_data="SET_AUTO_TRANSLATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=original_text,
                    callback_data="SET_SHOW_ORIGINAL",
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
        await session.commit()

    await callback.message.answer(
        t("settings_translation_title", language).format(
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


@settings_router.callback_query(F.data == "M_SETTINGS")
async def open_settings(callback: CallbackQuery):
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

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        repository = TranslationRepository(session)
        settings = await repository.get_language_settings(user.id)
        await repository.update_language_settings(
            user_id=user.id,
            auto_translate_enabled=not settings.auto_translate_enabled,
        )
        await session.commit()

    await show_translation_settings(callback)


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
async def settings_to_main_menu(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=get_main_menu_keyboard(language),
    )
    await callback.answer()