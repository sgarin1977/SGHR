from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.repositories.translation import TranslationRepository
from database.session import get_session
from services.rate_limit import RateLimitError
from services.user import TelegramUserData, UserService
from ui.texts import t

start_router = Router()


def normalize_language(language_code: str | None) -> str:
    if language_code in {"ru", "en", "pt"}:
        return language_code

    return "ru"


def get_main_menu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("menu_find_specialist", language),
                    callback_data="M_FIND",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("menu_offer_services", language),
                    callback_data="SS_START",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("menu_my_cabinet", language),
                    callback_data="M_CABINET",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("menu_settings", language),
                    callback_data="M_SETTINGS",
                )
            ],
        ]
    )


@start_router.message(CommandStart())
async def cmd_start(message: Message):
    if not message.from_user:
        return

    telegram_language = normalize_language(message.from_user.language_code)
    language = telegram_language
    first_name = message.from_user.first_name or t("start_default_first_name", language)

    try:
        async with get_session() as session:
            service = UserService(session)

            result = await service.register_telegram_user(
                TelegramUserData(
                    platform_user_id=str(message.from_user.id),
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                    last_name=message.from_user.last_name,
                    language_code=telegram_language,
                )
            )

            user = await service.get_user_by_telegram_id(message.from_user.id)
            if user:
                settings = await TranslationRepository(session).get_language_settings(user.id)
                language = normalize_language(settings.interface_language or user.language_code)
    except RateLimitError:
        await message.answer(t("error_rate_limited", language))
        return

    if result.role == "super_admin":
        role_text = t("role_text_super_admin", language)
    else:
        role_text = t("role_text_client", language)

    if result.is_new:
        text = t("start_welcome_new", language).format(
            first_name=first_name,
            role_text=role_text,
        )
        await message.answer(
            text,
            reply_markup=get_main_menu_keyboard(language),
            parse_mode="HTML",
        )
        return

    text = t("start_welcome_existing", language).format(first_name=first_name)
    await message.answer(
        text,
        reply_markup=get_main_menu_keyboard(language),
    )