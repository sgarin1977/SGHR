from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from database.repositories.translation import TranslationRepository
from database.repositories.legal import LegalRepository
from database.session import get_session
from handlers.start import (
    get_main_menu_keyboard_for_user,
    send_global_main_menu,
)
from services.legal import LegalService, MissingLegalDocumentError
from services.user import UserService
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message
from database.repositories.event import EventRepository

legal_router = Router()

CB_SPECIALIST_START = "SS_START"
CB_LEGAL_ACCEPT_SPECIALIST = "LEGAL_ACCEPT_SPECIALIST"
CB_MAIN_MENU = "M"
CB_REGISTER_SPECIALIST = "register_specialist"
CB_LEGAL_SHOW_DOCS = "LEGAL_SHOW_DOCS"
CB_SPECIALIST_START_CONFIRM = "SS_START_CONFIRM"
CB_SPECIALIST_START_CANCEL = "SS_START_CANCEL"

def normalize_language(language_code: str | None) -> str:
    if language_code in {"ru", "en", "pt"}:
        return language_code

    return "ru"

def specialist_registration_start_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("specialist_registration_start_btn", language),
                    callback_data=CB_SPECIALIST_START_CONFIRM,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cancel", language),
                    callback_data=CB_SPECIALIST_START_CANCEL,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data=CB_MAIN_MENU,
                )
            ],
        ]
    )

def legal_gate_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("legal_accept_continue_btn", language),
                    callback_data=CB_LEGAL_ACCEPT_SPECIALIST,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("legal_show_documents_btn", language),
                    callback_data=CB_LEGAL_SHOW_DOCS,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("legal_back_to_menu_btn", language),
                    callback_data=CB_MAIN_MENU,
                )
            ],
        ]
    )

def specialist_allowed_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("legal_continue_specialist_registration_btn", language),
                    callback_data=CB_REGISTER_SPECIALIST,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("legal_back_to_menu_btn", language),
                    callback_data=CB_MAIN_MENU,
                )
            ],
        ]
    )


def build_legal_gate_text(missing_documents, language: str = "ru") -> str:
    titles = []
    for doc in missing_documents:
        title = doc.title or doc.doc_type
        titles.append(f"- {title}")

    docs_text = "\n".join(titles)

    return (
        f"{t('legal_gate_intro', language)}\n\n"
        f"{t('legal_gate_required_docs', language)}\n"
        f"{docs_text}\n\n"
        f"{t('legal_gate_confirmation', language)}"
    )

@legal_router.callback_query(F.data == CB_SPECIALIST_START)
async def specialist_registration_start_screen(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.answer(
                t(
                    "legal_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

        language = normalize_language(user.language_code)

        await callback.answer()

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "specialist_registration_start_text",
                language,
            ),
            reply_markup=specialist_registration_start_keyboard(
                language
            ),
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id
        )

@legal_router.callback_query(F.data == CB_SPECIALIST_START_CANCEL)
async def specialist_registration_start_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.answer(
        t(
            "spec_cancelled",
            language,
        )
    )

    await state.clear()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "legal_main_menu",
            language,
        ),
        reply_markup=(
            await get_main_menu_keyboard_for_user(
                callback.from_user.id,
                language,
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@legal_router.callback_query(F.data == CB_SPECIALIST_START_CONFIRM)
async def specialist_start_legal_gate(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.answer(
                t(
                    "legal_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

        language = normalize_language(user.language_code)
        legal_service = LegalService(LegalRepository(session))
        await EventRepository(session).create_event(
            event_type="registration_started",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="specialist_registration",
            payload={
                "source": "specialist_start",
            },
            platform="telegram",
        )
        await session.commit()
        try:
            missing = await legal_service.get_missing_specialist_consents(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language=language,
            )
        except MissingLegalDocumentError as exc:
            await callback.answer(
                t(
                    "legal_documents_not_configured",
                    language,
                ).format(
                    error=exc
                ),
                show_alert=True,
            )
            return

        if not missing:
            await callback.answer()

            menu_message = await edit_or_replace_menu_message(
                callback=callback,
                text=t(
                    "legal_already_accepted",
                    language,
                ),
                reply_markup=specialist_allowed_keyboard(
                    language
                ),
            )

            await state.update_data(
                last_menu_message_id=menu_message.message_id
            )
            return

        await callback.answer()

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=build_legal_gate_text(
                missing,
                language,
            ),
            reply_markup=legal_gate_keyboard(
                language
            ),
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id
        )

@legal_router.callback_query(F.data == CB_LEGAL_SHOW_DOCS)
async def show_specialist_legal_documents(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.answer(
                t(
                    "legal_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

        language = normalize_language(user.language_code)
        legal_service = LegalService(LegalRepository(session))

        try:
            missing = await legal_service.get_missing_specialist_consents(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language=language,
            )
        except MissingLegalDocumentError as exc:
            await callback.answer(
                t(
                    "legal_documents_not_configured",
                    language,
                ).format(
                    error=exc
                ),
                show_alert=True,
            )
            return

        documents_text = []

        for doc in missing:
            title = doc.title or doc.doc_type
            content = doc.content_text or doc.content_url or ""
            documents_text.append(f"{title}\n\n{content}")

        if not documents_text:
            await callback.answer()

            menu_message = await edit_or_replace_menu_message(
                callback=callback,
                text=t(
                    "legal_already_accepted",
                    language,
                ),
                reply_markup=specialist_allowed_keyboard(
                    language
                ),
            )

            await state.update_data(
                last_menu_message_id=menu_message.message_id
            )
            return

        await callback.answer()

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text="\n\n---\n\n".join(
                documents_text
            ),
            reply_markup=legal_gate_keyboard(
                language
            ),
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id
        )


@legal_router.callback_query(F.data == CB_LEGAL_ACCEPT_SPECIALIST)
async def accept_specialist_legal_gate(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.answer(
                t(
                    "legal_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

        language = normalize_language(user.language_code)
        legal_service = LegalService(LegalRepository(session))

        try:
            await legal_service.accept_required_specialist_consents(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language=language,
                platform="telegram",
            )
        except MissingLegalDocumentError as exc:
            await callback.answer(
                t(
                    "legal_accept_failed",
                    language,
                ).format(
                    error=exc
                ),
                show_alert=True,
            )
            return

        await callback.answer()

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "legal_accepted",
                language,
            ),
            reply_markup=specialist_allowed_keyboard(
                language
            ),
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id
        )


@legal_router.callback_query(F.data == CB_MAIN_MENU)
async def back_to_main_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    await send_global_main_menu(
        callback,
        state,
    )