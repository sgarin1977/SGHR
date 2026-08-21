from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.session import get_session
from services.rate_limit import RateLimitError
from services.user_start import UserStartService
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_menu_message,
    edit_or_replace_tracked_menu_message,
)
start_router = Router()


def normalize_language(language_code: str | None) -> str:
    normalized_language = (
        language_code or ""
    ).strip().lower()

    if normalized_language in {
        "ru",
        "en",
        "pt",
        "uk",
        "pl",
        "de",
        "nl",
    }:
        return normalized_language

    return "ru"

ROLE_TEXT_KEYS = {
    "client": "role_text_client",
    "specialist": "role_text_specialist",
    "support": "role_text_support",
    "moderator": "role_text_moderator",
    "admin": "role_text_admin",
    "super_admin": "role_text_super_admin",
    "finance_admin": "role_text_finance_admin",
    "advertiser": "role_text_advertiser",
}


def role_label(
    role: str,
    language: str,
    role_details: dict[str, str] | None = None,
    unread_counts: dict[str, int] | None = None,
) -> str:
    key = ROLE_TEXT_KEYS.get(role, "role_text_other")
    label = t(key, language)

    detail = (role_details or {}).get(role)
    if detail:
        label = f"{label}: {detail}"

    unread_count = (unread_counts or {}).get(role, 0)
    if unread_count > 0:
        label = f"{label} ({unread_count})"

    return label
def role_switch_keyboard(
    roles: list[str],
    active_role: str | None,
    language: str,
    role_details: dict[str, str] | None = None,
    unread_counts: dict[str, int] | None = None,
    *,
    specialist_cabinets_label: bool = False,
) -> InlineKeyboardMarkup:
    rows = []

    for role in roles:
        is_active = role == active_role
        text = role_label(
            role,
            language,
            role_details,
            unread_counts,
        )

        if (
            role == "specialist"
            and specialist_cabinets_label
        ):
            text = t(
                "role_text_specialist_cabinets",
                language,
            )

            unread_count = (
                unread_counts or {}
            ).get(
                role,
                0,
            )

            if unread_count > 0:
                text = (
                    f"{text} "
                    f"({unread_count})"
                )

        if is_active:
            text = f"✓ {text}"

        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"ROLE_SWITCH:{role}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="GLOBAL_MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def open_active_role_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
    role: str | None,
    *,
    callback_answered: bool = False,
):
    if role in {
        "support",
        "moderator",
        "admin",
        "super_admin",
    }:
        from handlers.admin import show_admin_panel

        await show_admin_panel(
            callback,
            state,
            callback_answered=callback_answered,
        )
        return

    if role == "client":
        from handlers.billing import show_client_cabinet

        await show_client_cabinet(
            callback,
            state,
            callback_answered=callback_answered,
        )
        return

    if role == "specialist":
        from handlers.specialist_cabinets import (
            show_professional_cabinets,
        )

        await show_professional_cabinets(
            callback,
            state,
            callback_answered=(
                callback_answered
            ),
        )
        return

    language = normalize_language(
        callback.from_user.language_code
    )

    if not callback_answered:
        await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "search_main_menu",
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

@start_router.callback_query(F.data == "M_CABINET")
async def open_current_role_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
):
    previous_data = await state.get_data()
    current_message_id = (
        callback.message.message_id
    )

    tracked_message_ids = [
        *(
            previous_data.get(
                "support_list_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "last_search_result_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "last_contact_chat_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "dialog_list_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "cabinet_favorite_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "owner_portfolio_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "admin_scope_list_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "admin_global_blacklist_message_ids"
            )
            or []
        ),
        *(
            previous_data.get(
                "admin_scoped_blacklist_message_ids"
            )
            or []
        ),
        previous_data.get(
            "last_menu_message_id"
        ),
    ]

    await delete_telegram_messages(
        bot=callback.message.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in tracked_message_ids
            if (
                message_id
                and int(message_id)
                != current_message_id
            )
        ],
    )

    await callback.answer()

    await show_role_switch(
        callback,
        callback_answered=True,
        specialist_cabinets_label=True,
    )

def get_main_menu_keyboard(
    language: str = "ru",
    *,
    show_role_switch: bool = False,
    show_admin: bool = False,
    dialogs_callback_data: str = "CLIENT_DIALOGS",
) -> InlineKeyboardMarkup:
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
                    text=t("menu_specialist", language),
                    callback_data=(
                        "M_SPECIALIST_CABINETS"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "menu_dialogs",
                        language,
                    ),
                    callback_data=(
                        dialogs_callback_data
                    ),
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
                    text=t("menu_all_services", language),
                    callback_data="M_ALL_SERVICES",
                )
            ],
        ]
    )

def jobs_menu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("jobs_find_work_btn", language),
                    callback_data="JOBS_PLACEHOLDER:find_work",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("jobs_my_applications_btn", language),
                    callback_data="JOBS_PLACEHOLDER:applications",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("jobs_my_vacancies_btn", language),
                    callback_data="JOBS_PLACEHOLDER:vacancies",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("jobs_employers_btn", language),
                    callback_data="JOBS_PLACEHOLDER:employers",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="GLOBAL_MAIN_MENU",
                )
            ],
        ]
    )

async def get_main_menu_keyboard_for_user(
    telegram_id: int | str,
    language: str = "ru",
    *,
    start_context=None,
) -> InlineKeyboardMarkup:
    if start_context is None:
        async with get_session() as session:
            start_context = await (
                UserStartService(
                    session
                ).get_context(
                    platform_user_id=(
                        telegram_id
                    ),
                    fallback_language=(
                        language
                    ),
                )
            )

    language = start_context.language
    context = start_context.role_context
    available_roles = (
        set(context.available_roles)
        if context
        else set()
    )
    # Global main menu always opens the
    # user's client correspondence.
    dialogs_callback_data = "CLIENT_DIALOGS"

    return get_main_menu_keyboard(
        language,
        show_role_switch=bool(
            context
            and len(
                context.available_roles
            ) > 1
        ),
        show_admin=bool(
            available_roles.intersection(
                {
                    "admin",
                    "super_admin",
                }
            )
        ),
        dialogs_callback_data=(
            dialogs_callback_data
        ),
    )

async def get_main_menu_text(language: str) -> str:
    return t("search_main_menu", language)

async def replace_main_menu_message(
    *,
    message: Message,
    state: FSMContext,
    user_id: int,
    language: str,
    previous_message_id: int | None = None,
) -> Message:
    if previous_message_id is None:
        state_data = await state.get_data()
        previous_message_id = state_data.get(
            "last_menu_message_id"
        )

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=(
            [previous_message_id]
            if previous_message_id
            else []
        ),
    )

    menu_message = await message.answer(
        await get_main_menu_text(language),
        reply_markup=(
            await get_main_menu_keyboard_for_user(
                user_id,
                language,
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

    return menu_message

async def send_global_main_menu(
    callback: CallbackQuery,
    state: FSMContext | None = None,
    language: str | None = None,
):
    await callback.answer()

    if state:
        previous_data = await state.get_data()
        tracked_message_ids = [
            *(
                previous_data.get(
                    "support_list_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "last_search_result_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "last_contact_chat_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "dialog_list_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "cabinet_favorite_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "owner_portfolio_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "admin_scope_list_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "admin_global_blacklist_message_ids"
                )
                or []
            ),
            *(
                previous_data.get(
                    "admin_scoped_blacklist_message_ids"
                )
                or []
            ),
            *[
                message_id
                for state_key in (
                    "super_admin_ro_moderator_blacklist_message_ids",
                    "super_admin_ro_admin_audit_message_ids",
                    "super_admin_ro_admin_global_blacklist_message_ids",
                    "super_admin_ro_admin_support_message_ids",
                    "super_admin_ro_admin_user_message_ids",
                    "super_admin_ro_admin_user_history_message_ids",
                    "super_admin_ro_client_dialog_message_ids",
                    "super_admin_ro_specialist_dialog_message_ids",
                    "super_admin_ro_support_ticket_message_ids",
                    "admin_user_message_ids",
                    "admin_user_history_message_ids",
                    "admin_audit_message_ids",
                    "super_admin_audit_message_ids",
                    "admin_escalated_ticket_message_ids",
                    "support_staff_ticket_message_ids",
                )
                for message_id in (
                    previous_data.get(state_key)
                    or []
                )
            ],
        ]

        previous_menu_message_id = (
            previous_data.get(
                "last_menu_message_id"
            )
        )
        if (
            previous_menu_message_id
            and previous_menu_message_id
            != callback.message.message_id
        ):
            tracked_message_ids.append(
                previous_menu_message_id
            )

        await delete_telegram_messages(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            message_ids=[
                int(message_id)
                for message_id in tracked_message_ids
                if message_id
            ],
        )
        await state.clear()

    fallback_language = normalize_language(
        language
        or callback.from_user.language_code
    )

    async with get_session() as session:
        start_context = await (
            UserStartService(
                session
            ).get_context(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )

    language = start_context.language

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=await get_main_menu_text(language),
        reply_markup=(
            await get_main_menu_keyboard_for_user(
                callback.from_user.id,
                language,
                start_context=(
                    start_context
                ),
            )
        ),
    )

    if state:
        await state.update_data(
            last_menu_message_id=menu_message.message_id
        )


@start_router.callback_query(F.data == "JOBS_MENU")
async def open_jobs_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        language = await (
            UserStartService(
                session
            ).record_placeholder_opened(
                platform_user_id=(
                    callback.from_user.id
                ),
                feature="jobs",
                source="global_menu",
                fallback_language=(
                    fallback_language
                ),
            )
        )

    menu_message = await (
        edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "jobs_menu_title",
                language,
            ),
            reply_markup=(
                jobs_menu_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@start_router.callback_query(F.data.startswith("JOBS_PLACEHOLDER:"))
async def open_jobs_placeholder(
    callback: CallbackQuery,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    feature = (
        callback.data or ""
    ).split(
        ":",
        1,
    )[1]

    async with get_session() as session:
        language = await (
            UserStartService(
                session
            ).record_placeholder_opened(
                platform_user_id=(
                    callback.from_user.id
                ),
                feature=(
                    f"jobs_{feature}"
                ),
                source="jobs_menu",
                fallback_language=(
                    fallback_language
                ),
            )
        )

    await callback.answer(
        t(
            "jobs_under_construction",
            language,
        ),
        show_alert=True,
    )

@start_router.callback_query(F.data == "GLOBAL_MAIN_MENU")
async def global_main_menu(callback: CallbackQuery, state: FSMContext):
    await send_global_main_menu(callback, state)

def all_services_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("all_services_community_btn", language),
                    callback_data="M_COMMUNITY_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("all_services_companies_btn", language),
                    callback_data="M_HR_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("all_services_crm_btn", language),
                    callback_data="CAB_CRM_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("all_services_finance_btn", language),
                    callback_data="CAB_FINANCE_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("all_services_promotion_btn", language),
                    callback_data="M_PROMOTION_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("menu_settings", language),
                    callback_data="M_SETTINGS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("all_services_help_btn", language),
                    callback_data="SUPPORT_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("main_menu", language),
                    callback_data="GLOBAL_MAIN_MENU",
                )
            ],
        ]
    )

@start_router.callback_query(
    F.data == "M_ALL_SERVICES"
)
async def open_all_services(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        language = await (
            UserStartService(
                session
            ).get_language(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )

    await callback.answer()

    menu_message = await (
        edit_or_replace_menu_message(
            callback=callback,
            text=(
                f"{t('all_services_title', language)}"
                "\n\n"
                f"{t('all_services_hint', language)}"
            ),
            reply_markup=(
                all_services_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@start_router.callback_query(
    F.data == "M_SPECIALIST"
)
async def open_active_specialist_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
):
    from handlers.specialist_cabinet_common import (
        show_specialist_cabinet,
    )

    await show_specialist_cabinet(
        callback,
        state,
    )


@start_router.callback_query(
    F.data == "M_SPECIALIST_CABINETS"
)
async def main_menu_specialist_cabinets(
    callback: CallbackQuery,
    state: FSMContext,
):
    from database.session import (
        get_session,
    )
    from fsm.specialist_form import (
        register_specialist,
    )
    from handlers.specialist_cabinets import (
        show_professional_cabinets,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsService,
    )

    async with get_session() as session:
        has_profile = await (
            SpecialistCabinetsService(
                session
            ).has_profile(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
        )

    if has_profile:
        await show_professional_cabinets(
            callback,
            state,
        )
        return

    await register_specialist(
        callback,
        state,
    )

@start_router.callback_query(
    F.data.in_(
        {
            "M_RFQ_STUB",
            "M_COMMUNITY_STUB",
            "M_HR_STUB",
            "M_PROMOTION_STUB",
        }
    )
)
async def main_menu_beta_stub(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    text_key = {
        "M_RFQ_STUB": "main_rfq_stub",
        "M_COMMUNITY_STUB": "all_services_community_stub",
        "M_HR_STUB": "all_services_companies_stub",
        "M_PROMOTION_STUB": "all_services_promotion_stub",
    }.get(callback.data, "feature_disabled_beta_message")

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            text_key,
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "menu_all_services",
                            language,
                        ),
                        callback_data="M_ALL_SERVICES",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="GLOBAL_MAIN_MENU",
                    )
                ],
            ]
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@start_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not message.from_user:
        return
    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id
        ],
    )
    telegram_language = normalize_language(message.from_user.language_code)
    language = telegram_language
    first_name = message.from_user.first_name or t("start_default_first_name", language)
    try:
        async with get_session() as session:
            start_action = await (
                UserStartService(
                    session
                ).register_user(
                    platform_user_id=(
                        message.from_user.id
                    ),
                    username=(
                        message.from_user.username
                    ),
                    first_name=(
                        message.from_user.first_name
                    ),
                    last_name=(
                        message.from_user.last_name
                    ),
                    language_code=(
                        telegram_language
                    ),
                )
            )

        result = start_action.registration
        language = (
            start_action.context.language
        )
    except RateLimitError:
        state_data = await state.get_data()

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=state_data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "error_rate_limited",
                    language,
                ),
                reply_markup=(
                    await get_main_menu_keyboard_for_user(
                        message.from_user.id,
                        language,
                    )
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return


    if result.is_new:
        await replace_main_menu_message(
            message=message,
            state=state,
            language=language,
            user_id=message.from_user.id,
        )

        from handlers.search import resume_post_auth_action

        if await resume_post_auth_action(
            message=message,
            state=state,
            language=language,
        ):
            return

        return

    from handlers.search import resume_post_auth_action

    if await resume_post_auth_action(
        message=message,
        state=state,
        language=language,
    ):
        return

    await replace_main_menu_message(
        message=message,
        state=state,
        user_id=message.from_user.id,
        language=language,
    )

@start_router.callback_query(
    F.data == "ROLE_SWITCH_MENU"
)
async def show_role_switch(
    callback: CallbackQuery,
    *,
    callback_answered: bool = False,
    specialist_cabinets_label: bool = True,
):
    if not callback_answered:
        await callback.answer()

    async with get_session() as session:
        start_context = await (
            UserStartService(
                session
            ).get_context(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    callback.from_user
                    .language_code
                ),
            )
        )

    language = start_context.language
    context = start_context.role_context

    if (
        not context
        or len(context.available_roles) <= 1
    ):
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "role_switch_not_available",
                language,
            ),
        )
        return

    await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "role_switch_prompt",
            language,
        ),
        reply_markup=role_switch_keyboard(
            context.available_roles,
            context.active_role,
            language,
            role_details=(
                context.role_details
            ),
            unread_counts=(
                context.unread_counts
            ),
            specialist_cabinets_label=(
                specialist_cabinets_label
            ),
        ),
    )


@start_router.callback_query(F.data.startswith("ROLE_SWITCH:"))
async def switch_active_role(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    language = normalize_language(
        callback.from_user.language_code
    )
    role = (callback.data or "").split(":", 1)[1]

    try:
        async with get_session() as session:
            start_context = await (
                UserStartService(
                    session
                ).switch_role(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    role=role,
                    fallback_language=(
                        language
                    ),
                )
            )

        context = start_context.role_context
        language = start_context.language

    except ValueError:
        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "role_switch_failed",
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
        return

    await open_active_role_cabinet(
        callback,
        state,
        context.active_role or role,
        callback_answered=True,
    )
