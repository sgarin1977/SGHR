from contextvars import ContextVar
from typing import (
    Any,
    Awaitable,
    Callable,
)

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from database.session import get_session
from handlers.start import (
    normalize_language as normalize_base_language,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
    UserSettingsValidationError,
)
from services.moderation import AdminMenuSummary
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_menu_message,
    edit_or_replace_tracked_menu_message,
)


ADMIN_MODERATION_MENU_ROLES = frozenset(
    {
        "super_admin",
        "admin",
        "moderator",
    }
)

ADMIN_PAYMENT_MENU_ROLES = {
    "super_admin",
    "finance_admin",
}
ADMIN_ROLE_MENU_ROLES = set()
ADMIN_LOG_MENU_ROLES = {
    "super_admin",
    "admin",
}
ADMIN_SUPPORT_MENU_ROLES = {"support"}
ADMIN_DICT_MENU_ROLES = {"super_admin"}
ADMIN_DIALOGS_MENU_ROLES = {
    "super_admin",
    "admin",
    "moderator",
}
ADMIN_PROMOTION_MENU_ROLES = {
    "super_admin",
    "advertiser",
}
ADMIN_SYSTEM_MENU_ROLES = {"super_admin"}
ADMIN_SUPPORT_STATS_ROLES = {
    "support",
    "admin",
    "super_admin",
}
ADMIN_GLOBAL_BLACKLIST_ROLES = {
    "super_admin",
}


READ_ONLY_MODERATION_TARGET_ROLES = frozenset(
    {
        "moderator",
        "admin",
    }
)

MODERATOR_PROFILE_PAGE_SIZE = 5
ADMIN_SPECIALIST_PAGE_SIZE = 5


_admin_interface_language = ContextVar[
    str | None
](
    "admin_interface_language",
    default=None,
)


def normalize_admin_language(
    language_code: str | None,
) -> str:
    return normalize_base_language(
        _admin_interface_language.get()
        or language_code
    )


class AdminInterfaceLanguageMiddleware(
    BaseMiddleware
):
    async def __call__(
        self,
        handler: Callable[
            [
                TelegramObject,
                dict[str, Any],
            ],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user = getattr(
            event,
            "from_user",
            None,
        )

        resolved_language = (
            normalize_base_language(
                telegram_user.language_code
                if telegram_user
                else None
            )
        )

        if telegram_user:
            async with get_session() as session:
                try:
                    settings_context = await (
                        UserSettingsService(
                            session
                        ).get_context(
                            platform_user_id=(
                                telegram_user.id
                            ),
                        )
                    )
                except (
                    UserSettingsNotFoundError,
                    UserSettingsValidationError,
                ):
                    pass
                else:
                    resolved_language = (
                        normalize_base_language(
                            settings_context
                            .interface_language
                        )
                    )

        token = _admin_interface_language.set(
            resolved_language
        )

        try:
            return await handler(
                event,
                data,
            )
        finally:
            _admin_interface_language.reset(
                token
            )


async def replace_admin_input_screen(
    *,
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
) -> None:
    data = await state.get_data()

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id,
        ],
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=text,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_menu_message_id=menu_message_id,
    )


async def replace_admin_callback_screen(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
    callback_answered: bool = False,
) -> None:
    if not callback_answered:
        await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=text,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


async def clear_admin_message_group(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    state_key: str,
    preserve_current: bool = False,
) -> None:
    data = await state.get_data()
    current_message_id = (
        callback.message.message_id
    )
    tracked_message_ids = (
        data.get(state_key)
        or []
    )

    candidate_message_ids = [
        *tracked_message_ids,
        *(
            []
            if preserve_current
            else [current_message_id]
        ),
    ]

    message_ids = list(
        dict.fromkeys(
            message_id
            for message_id
            in candidate_message_ids
            if (
                isinstance(message_id, int)
                and (
                    not preserve_current
                    or message_id
                    != current_message_id
                )
            )
        )
    )

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=message_ids,
    )

    await state.update_data(
        **{
            state_key: [],
        }
    )


def admin_panel_keyboard(
    language: str,
    roles: set[str] | None = None,
    *,
    show_role_switch: bool = False,
) -> InlineKeyboardMarkup:
    roles = roles or set()
    rows = []

    if roles.intersection(ADMIN_ROLE_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_users_roles_section_btn", language),
                    callback_data="SA_USERS",
                )
            ]
        )

    if roles.intersection(ADMIN_DICT_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_dictionaries_section_btn", language),
                    callback_data="ADM_DICT",
                )
            ]
        )

    if roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_moderation_section_btn", language),
                    callback_data="ADM_MODERATION_MENU",
                )
            ]
        )

    if roles.intersection(ADMIN_DIALOGS_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_dialogs_section_btn", language),
                    callback_data="ADM_DIALOGS_STUB",
                )
            ]
        )

    if roles.intersection(ADMIN_PAYMENT_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_finance_section_btn", language),
                    callback_data="SA_FINANCE",
                )
            ]
        )

    if roles.intersection(ADMIN_PROMOTION_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_promotion_section_btn", language),
                    callback_data="ADM_PROMOTION_STUB",
                )
            ]
        )

    if roles.intersection(ADMIN_SYSTEM_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_system_section_btn", language),
                    callback_data="SA_SYSTEM",
                )
            ]
        )

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="ADM_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_menu(
    summary: AdminMenuSummary,
    language: str,
) -> str:
    return t("admin_menu_text", language).format(
        users=summary.users,
        specialists=summary.professional_cabinets,
        tickets=summary.tickets,
        complaints=summary.complaints,
        audit_alerts=summary.audit_alerts,
    )
