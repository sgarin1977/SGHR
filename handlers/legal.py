from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from handlers.start import get_main_menu_keyboard
from database.models import User
from database.repositories.legal import LegalRepository
from database.repositories.user import UserRepository
from database.session import get_session
from services.legal import LegalService, MissingLegalDocumentError


legal_router = Router()

CB_SPECIALIST_START = "SS_START"
CB_LEGAL_ACCEPT_SPECIALIST = "LEGAL_ACCEPT_SPECIALIST"


def legal_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять и продолжить",
                    callback_data=CB_LEGAL_ACCEPT_SPECIALIST,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в меню",
                    callback_data="M",
                )
            ],
        ]
    )


def specialist_allowed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Продолжить регистрацию специалиста",
                    callback_data="register_specialist",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в меню",
                    callback_data="M",
                )
            ],
        ]
    )


def build_legal_gate_text(missing_documents) -> str:
    titles = []
    for doc in missing_documents:
        title = doc.title or doc.doc_type
        titles.append(f"• {title} v{doc.version}")

    docs_text = "\n".join(titles)

    return (
        "Перед созданием профиля специалиста нужно принять обязательные "
        "юридические согласия SGHR Beta.\n\n"
        "Обязательные документы:\n"
        f"{docs_text}\n\n"
        "Продолжая, вы подтверждаете согласие с правилами сервиса, "
        "обработкой данных, публикацией профиля специалиста, использованием "
        "города/геолокации для поиска и автоматическим переводом сообщений."
    )


async def get_current_user(session, telegram_id: int) -> User | None:
    user_repo = UserRepository(session)
    account = await user_repo.get_by_platform_account("telegram", str(telegram_id))

    if not account:
        return None

    return await session.get(User, account.user_id)


@legal_router.callback_query(F.data == CB_SPECIALIST_START)
async def specialist_start_legal_gate(callback: CallbackQuery):
    async with get_session() as session:
        user = await get_current_user(session, callback.from_user.id)

        if not user:
            await callback.message.answer("Сначала нажмите /start, чтобы зарегистрироваться в SGHR Beta.")
            await callback.answer()
            return

        legal_service = LegalService(LegalRepository(session))

        try:
            missing = await legal_service.get_missing_specialist_consents(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language=user.language_code or "ru",
            )
        except MissingLegalDocumentError as exc:
            await callback.message.answer(
                "Юридические документы для Beta 0.3 ещё не настроены. "
                "Передайте администратору: "
                f"{exc}"
            )
            await callback.answer()
            return

        if not missing:
            await callback.message.answer(
                "Юридические согласия уже приняты. Можно продолжить регистрацию специалиста.",
                reply_markup=specialist_allowed_keyboard(),
            )
            await callback.answer()
            return

        await callback.message.answer(
            build_legal_gate_text(missing),
            reply_markup=legal_gate_keyboard(),
        )
        await callback.answer()


@legal_router.callback_query(F.data == CB_LEGAL_ACCEPT_SPECIALIST)
async def accept_specialist_legal_gate(callback: CallbackQuery):
    async with get_session() as session:
        user = await get_current_user(session, callback.from_user.id)

        if not user:
            await callback.message.answer("Сначала нажмите /start, чтобы зарегистрироваться в SGHR Beta.")
            await callback.answer()
            return

        legal_service = LegalService(LegalRepository(session))

        try:
            await legal_service.accept_required_specialist_consents(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language=user.language_code or "ru",
                platform="telegram",
            )
        except MissingLegalDocumentError as exc:
            await callback.message.answer(
                "Не удалось принять согласия: юридические документы не настроены. "
                f"Передайте администратору: {exc}"
            )
            await callback.answer()
            return

        await callback.message.answer(
            "Согласия приняты. Теперь можно продолжить регистрацию специалиста.",
            reply_markup=specialist_allowed_keyboard(),
        )
        await callback.answer()
        
@legal_router.callback_query(F.data == "M")
async def back_to_main_menu(callback: CallbackQuery):
    await callback.message.answer(
        "Главное меню SGHR Beta.",
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()