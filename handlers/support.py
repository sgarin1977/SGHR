from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.repositories.support import SupportRepository
from database.repositories.translation import TranslationRepository
from database.session import get_session
from handlers.start import normalize_language
from services.support import SupportService, SupportServiceError
from services.user import UserService
from ui.texts import t


support_router = Router()


SUPPORT_CATEGORIES = [
    "account",
    "specialist_profile",
    "payment",
    "translation",
    "complaint",
    "technical",
    "other",
]

SUPPORT_PRIORITIES = ["P1", "P2", "P3", "P4"]


class SupportFSM(StatesGroup):
    entering_message = State()
    entering_reply = State()


def support_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_create_btn", language),
                    callback_data="SUPPORT_CREATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_my_tickets_btn", language),
                    callback_data="SUPPORT_MY_TICKETS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_back_to_settings_btn", language),
                    callback_data="M_SETTINGS",
                )
            ],
        ]
    )


def support_category_keyboard(language: str) -> InlineKeyboardMarkup:
    rows = []
    for category in SUPPORT_CATEGORIES:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(f"support_category_{category}", language),
                    callback_data=f"SUPPORT_CAT:{category}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_back_to_settings_btn", language),
                callback_data="SUPPORT_MENU",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_priority_keyboard(language: str) -> InlineKeyboardMarkup:
    rows = []
    for priority in SUPPORT_PRIORITIES:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(f"support_priority_{priority.lower()}", language),
                    callback_data=f"SUPPORT_PRI:{priority}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_back_to_settings_btn", language),
                callback_data="SUPPORT_CREATE",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def support_tickets_keyboard(tickets, language: str) -> InlineKeyboardMarkup:
    rows = []

    for index, ticket in enumerate(tickets):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("support_ticket_button", language).format(
                        ticket_id=str(ticket.id)[:8],
                        status=ticket.status,
                        priority=ticket.priority,
                    ),
                    callback_data=f"SUPPORT_VIEW:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_back_to_settings_btn", language),
                callback_data="SUPPORT_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_ticket_view_keyboard(
    *,
    index: int,
    can_reply: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if can_reply:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("support_reply_btn", language),
                    callback_data=f"SUPPORT_REPLY:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_my_tickets_btn", language),
                callback_data="SUPPORT_MY_TICKETS",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_back_to_settings_btn", language),
                callback_data="SUPPORT_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_support_ticket_view(view, language: str) -> str:
    ticket = view.ticket
    lines = [
        t("support_ticket_view_title", language).format(
            ticket_id=str(ticket.id)[:8],
        ),
        "",
        f"{t('admin_status', language)}: {ticket.status}",
        f"{t('admin_support_priority', language)}: {ticket.priority}",
        f"{t('admin_support_category', language)}: {ticket.category or '-'}",
        "",
        t("admin_support_messages", language),
    ]

    for message in view.messages[-10:]:
        lines.append(
            t("support_message_line", language).format(
                sender_role=message.sender_role,
                message=message.message_text[:700],
            )
        )

    return "\n".join(lines)

async def get_support_user_context(telegram_id: int, fallback_language: str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, fallback_language

        settings = await TranslationRepository(session).get_language_settings(user.id)
        language = normalize_language(settings.interface_language or user.language_code)
        await session.commit()
        return user, language


@support_router.callback_query(F.data == "SUPPORT_MENU")
async def open_support_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    fallback_language = normalize_language(callback.from_user.language_code)
    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("support_title", language),
        reply_markup=support_menu_keyboard(language),
    )
    await callback.answer()


@support_router.callback_query(F.data == "SUPPORT_CREATE")
async def choose_support_category(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    fallback_language = normalize_language(callback.from_user.language_code)
    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("support_category_prompt", language),
        reply_markup=support_category_keyboard(language),
    )
    await callback.answer()


@support_router.callback_query(F.data.startswith("SUPPORT_CAT:"))
async def choose_support_priority(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    category = (callback.data or "").split(":", 1)[1]

    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await state.update_data(support_category=category)
    await callback.message.answer(
        t("support_priority_prompt", language),
        reply_markup=support_priority_keyboard(language),
    )
    await callback.answer()


@support_router.callback_query(F.data.startswith("SUPPORT_PRI:"))
async def ask_support_message(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    priority = (callback.data or "").split(":", 1)[1]

    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await state.update_data(support_priority=priority)
    await state.set_state(SupportFSM.entering_message)

    await callback.message.answer(t("support_message_prompt", language))
    await callback.answer()


@support_router.message(SupportFSM.entering_message)
async def receive_support_message(message: Message, state: FSMContext):
    fallback_language = normalize_language(message.from_user.language_code)
    user, language = await get_support_user_context(
        message.from_user.id,
        fallback_language,
    )

    if not user:
        await message.answer(t("search_contact_user_not_found", language))
        await state.clear()
        return

    data = await state.get_data()
    category = data.get("support_category")
    priority = data.get("support_priority") or "P3"

    try:
        async with get_session() as session:
            fresh_user = await UserService(session).get_user_by_telegram_id(
                message.from_user.id
            )
            if not fresh_user:
                await message.answer(t("search_contact_user_not_found", language))
                await state.clear()
                return

            ticket = await SupportService(
                SupportRepository(session)
            ).create_ticket(
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                subject=None,
                priority=priority,
                category=category,
                message_text=message.text or "",
            )
    except SupportServiceError as exc:
        await message.answer(
            t("support_error", language).format(error=str(exc))
        )
        return

    await state.clear()
    await message.answer(
        t("support_ticket_created", language).format(
            ticket_id=str(ticket.id)[:8],
        ),
        reply_markup=support_menu_keyboard(language),
    )


@support_router.callback_query(F.data == "SUPPORT_MY_TICKETS")
async def list_my_support_tickets(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    fallback_language = normalize_language(callback.from_user.language_code)

    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        fresh_user = await UserService(session).get_user_by_telegram_id(
            callback.from_user.id
        )
        if not fresh_user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        tickets = await SupportService(
            SupportRepository(session)
        ).list_user_tickets(
            tenant_id=fresh_user.tenant_id,
            user_id=fresh_user.id,
            limit=10,
        )

    if not tickets:
        await callback.message.answer(
            t("support_no_tickets", language),
            reply_markup=support_menu_keyboard(language),
        )
        await callback.answer()
        return

    await state.update_data(
        support_ticket_ids=[str(ticket.id) for ticket in tickets],
    )

    await callback.message.answer(
        t("support_tickets_title", language),
        reply_markup=support_tickets_keyboard(tickets, language),
    )
    await callback.answer()

@support_router.callback_query(F.data.startswith("SUPPORT_VIEW:"))
async def view_my_support_ticket(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    ids = data.get("support_ticket_ids") or []

    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            fresh_user = await UserService(session).get_user_by_telegram_id(
                callback.from_user.id
            )
            if not fresh_user:
                await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
                return

            view = await SupportService(
                SupportRepository(session)
            ).get_user_ticket_view(
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                ticket_id=UUID(ids[index]),
            )
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    can_reply = view.ticket.status not in {"resolved", "closed", "rejected"}

    await callback.message.answer(
        format_support_ticket_view(view, language),
        reply_markup=support_ticket_view_keyboard(
            index=index,
            can_reply=can_reply,
            language=language,
        ),
    )
    await callback.answer()


@support_router.callback_query(F.data.startswith("SUPPORT_REPLY:"))
async def ask_user_support_reply(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    ids = data.get("support_ticket_ids") or []

    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(
        support_reply_ticket_id=ids[index],
        support_reply_ticket_index=index,
    )
    await state.set_state(SupportFSM.entering_reply)

    await callback.message.answer(t("support_reply_prompt", language))
    await callback.answer()


@support_router.message(SupportFSM.entering_reply)
async def receive_user_support_reply(message: Message, state: FSMContext):
    fallback_language = normalize_language(message.from_user.language_code)
    data = await state.get_data()
    ticket_id = data.get("support_reply_ticket_id")

    user, language = await get_support_user_context(
        message.from_user.id,
        fallback_language,
    )

    if not user:
        await message.answer(t("search_contact_user_not_found", language))
        await state.clear()
        return

    if not ticket_id:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            fresh_user = await UserService(session).get_user_by_telegram_id(
                message.from_user.id
            )
            if not fresh_user:
                await message.answer(t("search_contact_user_not_found", language))
                await state.clear()
                return

            await SupportService(
                SupportRepository(session)
            ).add_user_message(
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                ticket_id=UUID(ticket_id),
                message_text=message.text or "",
            )
    except SupportServiceError as exc:
        await message.answer(
            t("support_error", language).format(error=str(exc))
        )
        return

    await state.clear()
    await message.answer(
        t("support_reply_sent", language),
        reply_markup=support_menu_keyboard(language),
    )