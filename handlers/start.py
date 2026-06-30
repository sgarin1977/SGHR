from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.repositories.translation import TranslationRepository
from database.repositories.event import EventRepository
from database.session import get_session
from services.rate_limit import RateLimitError
from services.user import TelegramUserData, UserService
from ui.texts import t

start_router = Router()


def normalize_language(language_code: str | None) -> str:
    if language_code in {"ru", "en", "pt"}:
        return language_code

    return "ru"

ROLE_TEXT_KEYS = {
    "client": "role_text_client",
    "specialist": "role_text_specialist",
    "support": "role_text_support",
    "moderator": "role_text_moderator",
    "admin": "role_text_admin",
    "super_admin": "role_text_super_admin",
}


def role_label(
    role: str,
    language: str,
    role_details: dict[str, str] | None = None,
    unread_counts: dict[str, int] | None = None,
) -> str:
    key = ROLE_TEXT_KEYS.get(role)
    label = t(key, language) if key else role

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
) -> InlineKeyboardMarkup:
    rows = []

    for role in roles:
        is_active = role == active_role
        text = role_label(role, language, role_details, unread_counts)

        if is_active:
            text = f"* {text}"

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
):
    if role in {"support", "moderator", "admin", "super_admin"}:
        from handlers.admin import show_admin_panel

        await show_admin_panel(callback, state)
        return

    if role == "client":
        from handlers.billing import show_client_cabinet

        await show_client_cabinet(callback, state)
        return

    if role == "specialist":
        from handlers.billing import show_specialist_cabinet

        await show_specialist_cabinet(callback, state)
        return

    language = normalize_language(callback.from_user.language_code)
    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=await get_main_menu_keyboard_for_user(callback.from_user.id, language),
    )
    await callback.answer()

@start_router.callback_query(F.data == "M_CABINET")
async def open_current_role_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
):
    async with get_session() as session:
        service = UserService(session)
        context = await service.get_role_switch_context(callback.from_user.id)

    role = None
    if context:
        if context.active_role in context.available_roles:
            role = context.active_role
        elif context.available_roles:
            role = context.available_roles[0]

    await open_active_role_cabinet(
        callback,
        state,
        role,
    )

def get_main_menu_keyboard(
    language: str = "ru",
    *,
    show_role_switch: bool = False,
    show_admin: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("menu_find_specialist", language),
                callback_data="M_FIND",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("menu_specialist", language),
                callback_data="M_SPECIALIST",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("menu_rfq", language),
                callback_data="M_RFQ_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("menu_dialogs", language),
                callback_data="CLIENT_DIALOGS",
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
                text=t("menu_community", language),
                callback_data="M_COMMUNITY_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("menu_hr", language),
                callback_data="M_HR_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("menu_settings", language),
                callback_data="M_SETTINGS",
            )
        ],
    ]

    if show_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("menu_admin", language),
                    callback_data="ADM_PANEL",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)

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
) -> InlineKeyboardMarkup:
    async with get_session() as session:
        service = UserService(session)
        context = await service.get_role_switch_context(telegram_id)

    available_roles = set(context.available_roles) if context else set()

    return get_main_menu_keyboard(
        language,
        show_role_switch=bool(
            context and len(context.available_roles) > 1
        ),
        show_admin=bool(
            available_roles.intersection({"admin", "super_admin"})
        ),
    )
async def send_global_main_menu(
    callback: CallbackQuery,
    state: FSMContext | None = None,
    language: str | None = None,
):
    if state:
        await state.clear()

    language = normalize_language(language or callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if user:
            settings = await TranslationRepository(session).get_language_settings(user.id)
            language = normalize_language(settings.interface_language or user.language_code)
            await session.commit()

    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=await get_main_menu_keyboard_for_user(callback.from_user.id, language),
    )
    await callback.answer()

@start_router.callback_query(F.data == "JOBS_MENU")
async def open_jobs_menu(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if user:
            settings = await TranslationRepository(session).get_language_settings(user.id)
            language = normalize_language(settings.interface_language or user.language_code)

            await EventRepository(session).create_event(
                event_type="placeholder_opened",
                tenant_id=user.tenant_id,
                user_id=user.id,
                entity_type="feature",
                entity_id=None,
                payload={
                    "feature": "jobs",
                    "source": "global_menu",
                },
                platform="telegram",
            )
            await session.commit()

    await callback.message.answer(
        t("jobs_menu_title", language),
        reply_markup=jobs_menu_keyboard(language),
    )
    await callback.answer()


@start_router.callback_query(F.data.startswith("JOBS_PLACEHOLDER:"))
async def open_jobs_placeholder(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    feature = (callback.data or "").split(":", 1)[1]

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if user:
            settings = await TranslationRepository(session).get_language_settings(user.id)
            language = normalize_language(settings.interface_language or user.language_code)

            await EventRepository(session).create_event(
                event_type="placeholder_opened",
                tenant_id=user.tenant_id,
                user_id=user.id,
                entity_type="feature",
                entity_id=None,
                payload={
                    "feature": f"jobs_{feature}",
                    "source": "jobs_menu",
                },
                platform="telegram",
            )
            await session.commit()

    await callback.answer(t("jobs_under_construction", language), show_alert=True)

async def send_active_role_cabinet_from_message(
    message: Message,
    state: FSMContext,
    role: str | None,
    language: str,
):
    if state:
        await state.clear()

    if role in {"support", "moderator", "admin", "super_admin"}:
        from handlers.admin import show_admin_panel

        await show_admin_panel(message, state)
        return

    if role == "client":
        from handlers.billing import client_cabinet_keyboard, get_client_cabinet_counts

        async with get_session() as session:
            service = UserService(session)
            user = await service.get_user_by_telegram_id(message.from_user.id)
            role_context = await service.get_role_switch_context(message.from_user.id)

            if user:
                await EventRepository(session).create_event(
                    event_type="client_menu_opened",
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    entity_type="user",
                    entity_id=user.id,
                    payload={
                        "active_role": user.active_role,
                    },
                    platform="telegram",
                )
                await session.commit()

        show_role_switch = bool(
            role_context and len(role_context.available_roles) > 1
        )
        counts = await get_client_cabinet_counts(message.from_user.id)

        await message.answer(
            t("client_cabinet_title", language)
            + "\n\n"
            + t("client_cabinet_summary", language).format(**counts),
            reply_markup=client_cabinet_keyboard(
                language,
                show_role_switch=show_role_switch,
            ),
        )
        return
    if role == "specialist":
        from handlers.billing import send_specialist_cabinet_message

        await send_specialist_cabinet_message(message, state)
        return
    await message.answer(
        t("search_main_menu", language),
        reply_markup=await get_main_menu_keyboard_for_user(message.from_user.id, language),
    )

@start_router.callback_query(F.data == "GLOBAL_MAIN_MENU")
async def global_main_menu(callback: CallbackQuery, state: FSMContext):
    await send_global_main_menu(callback, state)

@start_router.callback_query(F.data == "M_SPECIALIST")
async def main_menu_specialist(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_active_role_cabinet(
        callback,
        state,
        "specialist",
    )


@start_router.callback_query(
    F.data.in_({"M_RFQ_STUB", "M_COMMUNITY_STUB", "M_HR_STUB"})
)
async def main_menu_beta_stub(
    callback: CallbackQuery,
):
    language = normalize_language(callback.from_user.language_code)

    text_key = {
        "M_RFQ_STUB": "main_rfq_stub",
        "M_COMMUNITY_STUB": "main_community_stub",
        "M_HR_STUB": "main_hr_stub",
    }.get(callback.data, "feature_disabled_beta_message")

    await callback.message.answer(
        t(text_key, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("menu_find_specialist", language),
                        callback_data="M_FIND",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="GLOBAL_MAIN_MENU",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@start_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not message.from_user:
        return

    telegram_language = normalize_language(message.from_user.language_code)
    language = telegram_language
    first_name = message.from_user.first_name or t("start_default_first_name", language)
    role_context = None
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
                role_context = await service.get_role_switch_context(message.from_user.id)
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
            reply_markup=get_main_menu_keyboard(
                language,
                show_role_switch=bool(
                    role_context and len(role_context.available_roles) > 1
                ),
            ),
            parse_mode="HTML",
        )

        from handlers.search import resume_post_auth_action

        if await resume_post_auth_action(
            message=message,
            state=state,
            language=language,
        ):
            return

        return

    text = t("start_welcome_existing", language).format(first_name=first_name)
    await message.answer(text)
    from handlers.search import resume_post_auth_action

    if await resume_post_auth_action(
        message=message,
        state=state,
        language=language,
    ):
        return
    active_role_is_valid = bool(
        role_context
        and role_context.active_role
        and role_context.active_role in role_context.available_roles
    )

    if active_role_is_valid:
        await send_active_role_cabinet_from_message(
            message,
            state,
            role_context.active_role,
            language,
        )
        return

    if role_context and role_context.active_role and len(role_context.available_roles) > 1:
        await message.answer(
            t("role_switch_prompt", language),
            reply_markup=role_switch_keyboard(
                role_context.available_roles,
                role_context.active_role,
                language,
                role_details=role_context.role_details,
                unread_counts=role_context.unread_counts,
            ),
        )
        return

    await message.answer(
        t("search_main_menu", language),
        reply_markup=get_main_menu_keyboard(
            language,
            show_role_switch=bool(
                role_context and len(role_context.available_roles) > 1
            ),
        ),
    )

@start_router.callback_query(F.data == "ROLE_SWITCH_MENU")
async def show_role_switch(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        service = UserService(session)
        user = await service.get_user_by_telegram_id(callback.from_user.id)

        if user:
            settings = await TranslationRepository(session).get_language_settings(user.id)
            language = normalize_language(settings.interface_language or user.language_code)

        context = await service.get_role_switch_context(callback.from_user.id)

    if not context or len(context.available_roles) <= 1:
        await callback.answer(t("role_switch_not_available", language), show_alert=True)
        return

    await callback.message.answer(
        t("role_switch_prompt", language),
            reply_markup=role_switch_keyboard(
                context.available_roles,
                context.active_role,
                language,
                role_details=context.role_details,
                unread_counts=context.unread_counts,
            ),
    )
    await callback.answer()


@start_router.callback_query(F.data.startswith("ROLE_SWITCH:"))
async def switch_active_role(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    role = (callback.data or "").split(":", 1)[1]

    try:
        async with get_session() as session:
            service = UserService(session)
            context = await service.switch_active_role(callback.from_user.id, role)

            user = await service.get_user_by_telegram_id(callback.from_user.id)
            if user:
                settings = await TranslationRepository(session).get_language_settings(user.id)
                language = normalize_language(settings.interface_language or user.language_code)

    except ValueError:
        await callback.answer(t("role_switch_failed", language), show_alert=True)
        return

    await callback.message.answer(
        t("role_switch_done", language).format(
            role=role_label(
                context.active_role or role,
                language,
                context.role_details,
                context.unread_counts,
            ),
        ),
    )

    await open_active_role_cabinet(
        callback,
        state,
        context.active_role or role,
    )