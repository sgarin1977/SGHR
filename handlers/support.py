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
from database.repositories.event import EventRepository

support_router = Router()


SUPPORT_CATEGORIES = [
    "account",
    "specialist_profile",
    "request",
    "dialog",
    "complaint",
    "other",
]

SUPPORT_TICKETS_PAGE_SIZE = 5
SUPPORT_ACTIVE_STATUSES = {"open", "in_progress"}
SUPPORT_RESOLVED_STATUSES = {"resolved", "closed", "rejected"}

class SupportFSM(StatesGroup):
    entering_message = State()
    confirming_ticket = State()
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
                    text=t("billing_back", language),
                    callback_data="M_CABINET",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )

def support_empty_tickets_keyboard(language: str) -> InlineKeyboardMarkup:
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
                    text=t("support_back_to_settings_btn", language),
                    callback_data="SUPPORT_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
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

def support_ticket_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_send_btn", language),
                    callback_data="SUPPORT_SEND",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_edit_btn", language),
                    callback_data="SUPPORT_EDIT_MESSAGE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_cancel_btn", language),
                    callback_data="SUPPORT_CANCEL",
                )
            ],
        ]
    )

def support_ticket_view_label(view: str, language: str) -> str:
    key = (
        "support_tickets_resolved_btn"
        if view == "resolved"
        else "support_tickets_active_btn"
    )
    return t(key, language)


def format_support_tickets_header(
    tickets,
    language: str,
    *,
    view: str,
) -> str:
    return (
        f"{t('support_tickets_title', language)}\n"
        f"{support_ticket_view_label(view, language)} ({len(tickets)})"
    )


def format_support_ticket_card(ticket, language: str, *, number: int) -> str:
    return t("support_ticket_card", language).format(
        number=number,
        ticket_id=str(ticket.id)[:8],
        category=t(f"support_category_{ticket.category or 'other'}", language),
        status=t(f"support_status_{ticket.status}", language),
        updated_at=ticket.updated_at.strftime("%Y-%m-%d") if ticket.updated_at else "-",
    )
def support_tickets_keyboard(
    tickets,
    language: str,
    *,
    view: str = "active",
    page: int = 0,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_tickets_active_btn", language),
                callback_data="SUPPORT_MY_TICKETS:active:0",
            ),
            InlineKeyboardButton(
                text=t("support_tickets_resolved_btn", language),
                callback_data="SUPPORT_MY_TICKETS:resolved:0",
            ),
        ]
    ]
    pagination = []
    if page > 0:
        pagination.append(
            InlineKeyboardButton(
                text="<",
                callback_data=f"SUPPORT_MY_TICKETS:{view}:{page - 1}",
            )
        )
    if has_next:
        pagination.append(
            InlineKeyboardButton(
                text=">",
                callback_data=f"SUPPORT_MY_TICKETS:{view}:{page + 1}",
            )
        )
    if pagination:
        rows.append(pagination)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_create_btn", language),
                callback_data="SUPPORT_CREATE",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="SUPPORT_MENU",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def support_ticket_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_request_open', language)}",
                    callback_data=f"SUPPORT_VIEW:{index}",
                )
            ]
        ]
    )

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
    if can_reply:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("support_close_btn", language),
                    callback_data=f"SUPPORT_CLOSE:{index}",
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
        f"{t('admin_status', language)}: {t(f'support_status_{ticket.status}', language)}",
        f"{t('admin_support_category', language)}: {t(f'support_category_{ticket.category or 'other'}', language)}",
        "",
        t("admin_support_messages", language),
    ]

    for message in view.messages[-10:]:
        sender_role = message.sender_role or "system"
        sender_label = t(f"support_sender_{sender_role}", language)

        text = (message.message_text or "").strip()
        if text == "[deleted by user request]":
            text = t("support_message_deleted_by_user", language)

        lines.append(
            t("support_message_line", language).format(
                sender_role=sender_label,
                message=text[:700],
            )
        )

    return "\n".join(lines)

def format_support_ticket_draft(data: dict, language: str) -> str:
    category = data.get("support_category") or "other"
    message_text = (data.get("support_message_text") or "").strip()

    return t("support_ticket_draft", language).format(
        category=t(f"support_category_{category}", language),
        message=message_text,
    )

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

    async with get_session() as session:
        fresh_user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if fresh_user:
            await EventRepository(session).create_event(
                event_type="support_opened",
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                entity_type="support",
                entity_id=None,
                payload={
                    "source": "support_menu",
                },
                platform="telegram",
            )
            await session.commit()

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
async def choose_support_category(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    category = (callback.data or "").split(":", 1)[1]

    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await state.update_data(
        support_category=category,
        support_priority="P3",
    )

    async with get_session() as session:
        await EventRepository(session).create_event(
            event_type="ticket_category",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "category": category,
            },
            platform="telegram",
        )
        await session.commit()

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

    message_text = (message.text or "").strip()
    if len(message_text) < 10:
        await message.answer(t("support_message_too_short", language))
        return

    await state.update_data(support_message_text=message_text)
    data = await state.get_data()
    await state.set_state(SupportFSM.confirming_ticket)

    await message.answer(
        format_support_ticket_draft(data, language),
        reply_markup=support_ticket_confirm_keyboard(language),
    )

@support_router.callback_query(F.data == "SUPPORT_EDIT_MESSAGE")
async def edit_support_message(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await state.set_state(SupportFSM.entering_message)
    await callback.message.answer(t("support_message_prompt", language))
    await callback.answer()


@support_router.callback_query(F.data == "SUPPORT_CANCEL")
async def cancel_support_ticket_draft(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    await state.clear()

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("support_ticket_cancelled", language),
        reply_markup=support_menu_keyboard(language),
    )
    await callback.answer()


@support_router.callback_query(F.data == "SUPPORT_SEND")
async def send_support_ticket(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    user, language = await get_support_user_context(
        callback.from_user.id,
        fallback_language,
    )

    if not user:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    category = data.get("support_category")
    priority = data.get("support_priority") or "P3"
    message_text = (data.get("support_message_text") or "").strip()

    if not message_text:
        await callback.answer(t("support_ticket_already_sent", language), show_alert=True)
        return

    if len(message_text) < 10:
        await state.set_state(SupportFSM.entering_message)
        await callback.message.answer(t("support_message_too_short", language))
        await callback.answer()
        return

    try:
        async with get_session() as session:
            fresh_user = await UserService(session).get_user_by_telegram_id(
                callback.from_user.id
            )
            if not fresh_user:
                await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
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
                message_text=message_text,
            )

            await EventRepository(session).create_event(
                event_type="ticket_created",
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                entity_type="support_ticket",
                entity_id=ticket.id,
                payload={
                    "category": category,
                    "priority": priority,
                },
                platform="telegram",
            )
            await session.commit()
    except SupportServiceError as exc:
        await callback.message.answer(
            t("support_error", language).format(error=str(exc))
        )
        await callback.answer()
        return

    await state.clear()
    await callback.message.answer(
        t("support_ticket_created", language).format(
            ticket_id=str(ticket.id)[:8],
        ),
        reply_markup=support_menu_keyboard(language),
    )
    await callback.answer()

@support_router.callback_query(F.data == "SUPPORT_MY_TICKETS")
@support_router.callback_query(F.data.startswith("SUPPORT_MY_TICKETS:"))
async def list_my_support_tickets(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    fallback_language = normalize_language(callback.from_user.language_code)

    view = "active"
    page = 0
    if callback.data and callback.data.startswith("SUPPORT_MY_TICKETS:"):
        parts = callback.data.split(":")
        if len(parts) >= 3:
            view = parts[1]
            page = max(0, int(parts[2]))

    statuses = (
        SUPPORT_RESOLVED_STATUSES
        if view == "resolved"
        else SUPPORT_ACTIVE_STATUSES
    )


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
            statuses=statuses,
            limit=SUPPORT_TICKETS_PAGE_SIZE,
            offset=page * SUPPORT_TICKETS_PAGE_SIZE,
        )
        visible_tickets = tickets[:SUPPORT_TICKETS_PAGE_SIZE]
        has_next = len(tickets) > SUPPORT_TICKETS_PAGE_SIZE

        await EventRepository(session).create_event(
            event_type="ticket_list",
            tenant_id=fresh_user.tenant_id,
            user_id=fresh_user.id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "view": view,
                "page": page,
                "count": len(visible_tickets),
                "has_next": has_next,
            },
            platform="telegram",
        )
        await session.commit()

    if not visible_tickets:
        await callback.message.answer(
            t("support_no_tickets", language),
            reply_markup=support_empty_tickets_keyboard(language),
        )
        await callback.answer()
        return

    await state.update_data(
        support_ticket_ids=[str(ticket.id) for ticket in visible_tickets],
        support_tickets_view=view,
        support_tickets_page=page,
    )

    await callback.message.answer(
        format_support_tickets_header(
            visible_tickets,
            language,
            view=view,
        )
    )

    start_number = page * SUPPORT_TICKETS_PAGE_SIZE + 1
    for index, ticket in enumerate(visible_tickets):
        await callback.message.answer(
            format_support_ticket_card(
                ticket,
                language,
                number=start_number + index,
            ),
            reply_markup=support_ticket_card_keyboard(
                index=index,
                language=language,
            ),
        )

    await callback.message.answer(
        t("dialog_list_actions_title", language),
        reply_markup=support_tickets_keyboard(
            visible_tickets,
            language,
            view=view,
            page=page,
            has_next=has_next,
        ),
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

@support_router.callback_query(F.data.startswith("SUPPORT_CLOSE:"))
async def close_my_support_ticket(callback: CallbackQuery, state: FSMContext):
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

            ticket = await SupportService(
                SupportRepository(session)
            ).close_user_ticket(
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                ticket_id=UUID(ids[index]),
            )

            await EventRepository(session).create_event(
                event_type="closed",
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                entity_type="support_ticket",
                entity_id=ticket.id,
                payload={
                    "source": "user_support_ticket",
                    "status": "closed",
                },
                platform="telegram",
            )
            await session.commit()
    except SupportServiceError as exc:
        message = str(exc)
        if message == "Support ticket is already closed.":
            message = t("support_ticket_already_closed", language)
        await callback.answer(message, show_alert=True)
        return

    await callback.message.answer(
        t("support_ticket_closed", language),
        reply_markup=support_menu_keyboard(language),
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

            await EventRepository(session).create_event(
                event_type="ticket_message",
                tenant_id=fresh_user.tenant_id,
                user_id=fresh_user.id,
                entity_type="support_ticket",
                entity_id=UUID(ticket_id),
                payload={
                    "sender_role": "user",
                },
                platform="telegram",
            )
            await session.commit()
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