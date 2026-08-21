from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from database.repositories.translation import TRANSLATION_MODES
from database.session import get_session
from handlers.start import normalize_language
from services.translation import TranslationError
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message
from services.user_settings import SpecialistProfileNotFoundError, UserSettingsNotFoundError, UserSettingsService


specialist_settings_router = Router()


def specialist_visible_language_code(
    language_code: str | None,
) -> str:
    normalized = (
        language_code or ""
    ).strip().lower()

    return (
        "ua"
        if normalized == "uk"
        else normalized
    )


def specialist_language_option_rows(
    *,
    callback_prefix: str,
    selected_language: str | None,
) -> list[list[InlineKeyboardButton]]:
    options = (
        ("ru", "RU"),
        ("en", "EN"),
        ("pt", "PT"),
        ("uk", "UA"),
        ("pl", "PL"),
        ("de", "DE"),
        ("nl", "NL"),
    )
    buttons = []

    for code, label in options:
        marker = (
            "● "
            if selected_language == code
            else ""
        )
        buttons.append(
            InlineKeyboardButton(
                text=f"{marker}{label}",
                callback_data=(
                    f"{callback_prefix}:{code}"
                ),
            )
        )

    return [
        buttons[:4],
        buttons[4:],
    ]


def specialist_interface_language_keyboard(
    *,
    language: str,
    interface_language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *specialist_language_option_rows(
                callback_prefix=(
                    "SPEC_SET_UI_LANG"
                ),
                selected_language=(
                    interface_language
                ),
            ),
            [
                InlineKeyboardButton(
                    text=t(
                        "billing_back",
                        language,
                    ),
                    callback_data=(
                        "SPEC_SETTINGS_LANGUAGE"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )


def specialist_language_settings_keyboard(
    *,
    language: str,
    message_language: str,
    translation_mode: str,
    show_original_button: bool,
) -> InlineKeyboardMarkup:
    original_text = t(
        (
            "settings_show_original_on"
            if show_original_button
            else "settings_show_original_off"
        ),
        language,
    )

    def mode_text(mode: str) -> str:
        marker = (
            "●"
            if translation_mode == mode
            else "○"
        )
        label = t(
            f"settings_translation_mode_{mode}",
            language,
        )
        return f"{marker} {label}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=mode_text("off"),
                    callback_data=(
                        "SPEC_SET_TRANSLATION_MODE:off"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=mode_text("standard"),
                    callback_data=(
                        "SPEC_SET_TRANSLATION_MODE:"
                        "standard"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=mode_text("detect"),
                    callback_data=(
                        "SPEC_SET_TRANSLATION_MODE:"
                        "detect"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "settings_message_language_label",
                        language,
                    ),
                    callback_data="SET_NOOP",
                )
            ],
            *specialist_language_option_rows(
                callback_prefix=(
                    "SPEC_SET_MSG_LANG"
                ),
                selected_language=(
                    message_language
                ),
            ),
            [
                InlineKeyboardButton(
                    text=original_text,
                    callback_data=(
                        "SPEC_SET_SHOW_ORIGINAL"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "billing_back",
                        language,
                    ),
                    callback_data=(
                        "SPEC_SETTINGS_LANGUAGE"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )


async def render_specialist_interface_language(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        try:
            context = await UserSettingsService(
                session
            ).get_context(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

        settings = context.settings
        language = context.interface_language

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "settings_interface_language_title",
                language,
            ).format(
                interface_language=(
                    specialist_visible_language_code(
                        settings.interface_language
                    )
                ),
            ),
            reply_markup=(
                specialist_interface_language_keyboard(
                    language=language,
                    interface_language=(
                        settings.interface_language
                    ),
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


async def render_specialist_language_settings(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        try:
            context = await UserSettingsService(
                session
            ).get_context(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

        settings = context.settings
        language = context.interface_language

    translation_mode = (
        settings.translation_mode
        if settings.translation_mode
        in TRANSLATION_MODES
        else "standard"
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "settings_translation_title",
                language,
            ).format(
                translation_mode=t(
                    (
                        "settings_translation_mode_"
                        f"{translation_mode}"
                    ),
                    language,
                ),
                message_language=(
                    specialist_visible_language_code(
                        settings.message_language
                    )
                ),
                show_original=t(
                    (
                        "settings_enabled"
                        if settings
                        .show_original_button
                        else "settings_disabled"
                    ),
                    language,
                ),
            ),
            reply_markup=(
                specialist_language_settings_keyboard(
                    language=language,
                    message_language=(
                        settings.message_language
                    ),
                    translation_mode=(
                        translation_mode
                    ),
                    show_original_button=(
                        settings
                        .show_original_button
                    ),
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@specialist_settings_router.callback_query(
    F.data.startswith("SPEC_SET_UI_LANG:")
)
async def set_specialist_interface_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    interface_language = normalize_language(
        (callback.data or "").split(":", 1)[1]
    )

    async with get_session() as session:
        try:
            await UserSettingsService(
                session
            ).update_interface_language(
                platform_user_id=(
                    callback.from_user.id
                ),
                language_code=interface_language,
                source="specialist_settings",
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    fallback_language,
                ),
                show_alert=True,
            )
            return

    await render_specialist_interface_language(
        callback,
        state,
    )


@specialist_settings_router.callback_query(
    F.data.startswith(
        "SPEC_SET_TRANSLATION_MODE:"
    )
)
async def set_specialist_translation_mode(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    translation_mode = (
        (callback.data or "").split(
            ":",
            1,
        )[1]
    )

    if translation_mode not in TRANSLATION_MODES:
        await callback.answer(
            t(
                "settings_translation_update_failed",
                language,
            ),
            show_alert=True,
        )
        return

    async with get_session() as session:
        try:
            await UserSettingsService(
                session
            ).update_translation_mode(
                platform_user_id=(
                    callback.from_user.id
                ),
                translation_mode=(
                    translation_mode
                ),
                source="specialist_settings",
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return
        except (TranslationError, ValueError):
            await callback.answer(
                t(
                    "settings_translation_update_failed",
                    language,
                ),
                show_alert=True,
            )
            return

    await render_specialist_language_settings(
        callback,
        state,
    )


@specialist_settings_router.callback_query(
    F.data.startswith("SPEC_SET_MSG_LANG:")
)
async def set_specialist_message_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    message_language = (
        (callback.data or "").split(":", 1)[1]
    )

    async with get_session() as session:
        try:
            await UserSettingsService(
                session
            ).update_message_language(
                platform_user_id=(
                    callback.from_user.id
                ),
                language_code=message_language,
                source="specialist_settings",
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

    await render_specialist_language_settings(
        callback,
        state,
    )


@specialist_settings_router.callback_query(
    F.data == "SPEC_SET_SHOW_ORIGINAL"
)
async def toggle_specialist_show_original(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        try:
            await UserSettingsService(
                session
            ).toggle_show_original(
                platform_user_id=(
                    callback.from_user.id
                ),
                source="specialist_settings",
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

    await render_specialist_language_settings(
        callback,
        state,
    )


@specialist_settings_router.callback_query(
    F.data == "SPEC_INTERFACE_LANGUAGE"
)
async def specialist_interface_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    await render_specialist_interface_language(
        callback,
        state,
    )


@specialist_settings_router.callback_query(
    F.data == "SPEC_TRANSLATION_SETTINGS"
)
async def specialist_translation_settings(
    callback: CallbackQuery,
    state: FSMContext,
):
    await render_specialist_language_settings(
        callback,
        state,
    )


@specialist_settings_router.callback_query(F.data == "SPEC_SETTINGS_CONSENTS")
async def specialist_settings_consents(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        service = UserSettingsService(session)

        try:
            context = await service.get_context(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
            consents = await service.list_consents(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "billing_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

    language = context.interface_language

    if consents:
        lines = [t("settings_consents_title", language), ""]
        for index, consent in enumerate(consents, start=1):
            status = (
                t(
                    "settings_consent_revoked",
                    language,
                )
                if consent.is_revoked
                else t(
                    "settings_consent_active",
                    language,
                )
            )
            lines.append(
                t("settings_consent_item", language).format(
                    number=index,
                    consent_type=consent.consent_type,
                    version=consent.version,
                    status=status,
                )
            )
    else:
        lines = [t("settings_consents_empty", language)]

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "billing_back",
                            language,
                        ),
                        callback_data="SPEC_SETTINGS",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="BILL_MENU",
                    )
                ],
            ]
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@specialist_settings_router.callback_query(F.data == "CAB_PROFILE_DELETE_CONFIRM")
async def schedule_specialist_profile_delete(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        service = UserSettingsService(session)

        try:
            context = await service.get_context(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
            language = context.interface_language

            await (
                service
                .schedule_specialist_profile_deletion(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
        except SpecialistProfileNotFoundError:
            await callback.answer(
                t(
                    "cabinet_profile_not_found",
                    language,
                ),
                show_alert=True,
            )
            return
        except UserSettingsNotFoundError:
            await callback.answer(
                t(
                    "billing_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "privacy_deletion_scheduled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "menu_my_cabinet",
                            language,
                        ),
                        callback_data="M_CABINET",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="BILL_MENU",
                    )
                ],
            ]
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
