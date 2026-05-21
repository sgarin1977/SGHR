from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.repositories.legal import LegalRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard
from services.legal import LegalService, MissingLegalDocumentError
from services.user import UserService
from ui.texts import t


legal_router = Router()

CB_SPECIALIST_START = "SS_START"
CB_LEGAL_ACCEPT_SPECIALIST = "LEGAL_ACCEPT_SPECIALIST"
CB_MAIN_MENU = "M"
CB_REGISTER_SPECIALIST = "register_specialist"


def normalize_language(language_code: str | None) -> str:
    if language_code in {"ru", "en", "pt"}:
        return language_code

    return "ru"


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
        titles.append(f"- {title} v{doc.version}")

    docs_text = "\n".join(titles)

    return (
        f"{t('legal_gate_intro', language)}\n\n"
        f"{t('legal_gate_required_docs', language)}\n"
        f"{docs_text}\n\n"
        f"{t('legal_gate_confirmation', language)}"
    )


@legal_router.callback_query(F.data == CB_SPECIALIST_START)
async def specialist_start_legal_gate(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer(t("legal_start_required", language))
            await callback.answer()
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
            await callback.message.answer(
                t("legal_documents_not_configured", language).format(error=exc)
            )
            await callback.answer()
            return

        if not missing:
            await callback.message.answer(
                t("legal_already_accepted", language),
                reply_markup=specialist_allowed_keyboard(language),
            )
            await callback.answer()
            return

        await callback.message.answer(
            build_legal_gate_text(missing, language),
            reply_markup=legal_gate_keyboard(language),
        )
        await callback.answer()


@legal_router.callback_query(F.data == CB_LEGAL_ACCEPT_SPECIALIST)
async def accept_specialist_legal_gate(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer(t("legal_start_required", language))
            await callback.answer()
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
            await callback.message.answer(
                t("legal_accept_failed", language).format(error=exc)
            )
            await callback.answer()
            return

        await callback.message.answer(
            t("legal_accepted", language),
            reply_markup=specialist_allowed_keyboard(language),
        )
        await callback.answer()


@legal_router.callback_query(F.data == CB_MAIN_MENU)
async def back_to_main_menu(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    await callback.message.answer(
        t("legal_main_menu", language),
        reply_markup=get_main_menu_keyboard(language),
    )
    await callback.answer()