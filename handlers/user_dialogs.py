from __future__ import annotations

import logging
from uuid import UUID
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.session import get_session
from handlers.start import normalize_language
from handlers.search import SpecialistSearchFSM, build_contact_translation_callback, complaint_reason_keyboard, format_chat_message_body
from services.user_dialogs import UserDialogsAccessError, UserDialogsService, UserDialogsThreadError
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message, send_telegram_attachment, split_telegram_text, delete_telegram_messages
from services.contact_chat import ContactChatError
from services.moderation import ModerationError
from handlers.billing_common import replace_billing_input_screen
from handlers.billing_common import clear_cross_feature_messages

user_dialogs_router = Router()
logger = logging.getLogger(__name__)
CLIENT_DIALOGS_PAGE_SIZE = 5

class UserDialogsFSM(StatesGroup):
    entering_messages_search = State()


async def get_user_dialog_language(
    platform_user_id: int | str,
    fallback_language: str | None,
) -> str:
    language = normalize_language(
        fallback_language
    )

    try:
        async with get_session() as session:
            actor = await UserDialogsService(
                session
            ).require_actor(
                platform_user_id=(
                    platform_user_id
                ),
            )
    except UserDialogsAccessError:
        return language

    return normalize_language(
        actor.language
    )


def client_dialogs_keyboard(
    *,
    items_count: int,
    page: int,
    view: str,
    language: str,
    show_role_switch: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("messages_tab_new", language),
                callback_data="CLIENT_DIALOGS:new:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_correspondence", language),
                callback_data="CLIENT_DIALOGS:active:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_tab_completed", language),
                callback_data="CLIENT_DIALOGS:completed:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_archive", language),
                callback_data="CLIENT_DIALOGS:archive:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_search_btn", language),
                callback_data="CLIENT_DIALOG_SEARCH",
            )
        ],
    ]

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"CLIENT_DIALOGS:{view}:{page - 1}",
            )
        )
    if items_count >= CLIENT_DIALOGS_PAGE_SIZE:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"CLIENT_DIALOGS:{view}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_dialog_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("messages_open_chat", language),
                    callback_data=f"CLIENT_DIALOG_OPEN:{index}",
                )
            ]
        ]
    )


def client_dialog_status_label(status: str | None, language: str) -> str:
    key = {
        "waiting_specialist": "client_dialog_status_waiting_specialist",
        "waiting_client": "client_dialog_status_waiting_client",
        "open": "client_dialog_status_open",
        "in_discussion": "client_dialog_status_in_discussion",
        "completed": "client_dialog_status_completed",
        "closed": "client_dialog_status_closed",
    }.get(status or "", "client_dialog_status_other")

    return t(key, language)


def compact_dialog_text(value: str | None, limit: int = 56) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def format_dialog_card(
    *,
    item,
    display_number: int,
    language: str,
) -> str:
    name = (
        item.specialist_name
        or item.client_name
        or t("client_dialog_unknown_user", language)
    )
    unread = int(item.unread_count or 0)

    if item.last_message_text == "[deleted by user request]":
        last_text = t("dialog_message_deleted", language)
    else:
        last_text = compact_dialog_text(
            item.last_message_text,
            limit=96,
        )

    if unread > 0:
        status = t("messages_card_status_new", language)
    elif item.status == "waiting_client":
        status = t("messages_card_status_waiting_you", language)
    elif item.status == "waiting_specialist":
        status = t("messages_card_status_waiting_other", language)
    elif item.status in {"completed", "closed"}:
        status = t("messages_card_status_completed", language)
    else:
        status = t("messages_card_status_in_progress", language)

    lines = [
        f"👤 {name}",
    ]

    if item.profession_name:
        lines.append(f"💼 {item.profession_name}")

    lines.append(status)

    if last_text:
        lines.append(f"💬 {last_text}")

    if item.last_message_at:
        lines.append(
            f"🕘 {item.last_message_at:%d.%m %H:%M}",
        )

    if unread > 0:
        lines.append(
            t(
                "messages_card_unread",
                language,
            ).format(count=unread)
        )

    return "\n".join(lines)


def format_messages_list_text(
    items,
    *,
    unread_messages: int,
    language: str,
) -> str:
    title = (
        t(
            "messages_title_with_unread",
            language,
        ).format(count=unread_messages)
        if unread_messages > 0
        else t("messages_title", language)
    )

    lines = [
        title,
        t("messages_hint", language),
    ]

    if not items:
        lines.extend(
            [
                "",
                t("messages_empty", language),
            ]
        )

    return "\n".join(lines)


def format_client_dialogs_text(
    items,
    language: str,
    *,
    unread_messages: int,
) -> str:
    return format_messages_list_text(
        items,
        unread_messages=unread_messages,
        language=language,
    )


def format_thread_history(
    messages,
    *,
    counterpart_name: str,
    language: str,
) -> str:
    if not messages:
        return t("client_thread_no_messages", language)

    lines = []

    for message in messages:
        if message.is_system:
            lines.append(
                format_chat_message_body(message, language)
            )
            continue

        sender_name = (
            t("contact_chat_you_label", language)
            if message.is_sent_by_viewer
            else counterpart_name
        )
        sent_at = message.created_at.strftime("%d.%m %H:%M")

        lines.append(
            f"{sender_name} · {sent_at}\n"
            f"{format_chat_message_body(message, language)}"
        )

    return "\n\n".join(lines)


def message_thread_status_label(
    status: str | None,
    *,
    viewer_role: str,
    language: str,
) -> str:
    if status in {"completed", "closed"}:
        return t("messages_card_status_completed", language)

    waiting_for_viewer = (
        "waiting_client"
        if viewer_role == "client"
        else "waiting_specialist"
    )

    if status == waiting_for_viewer:
        return t("messages_card_status_waiting_you", language)

    if status in {"waiting_client", "waiting_specialist"}:
        return t("messages_card_status_waiting_other", language)

    return t("messages_card_status_in_progress", language)


def format_open_thread_chat_text(
    detail,
    *,
    counterpart_name: str,
    viewer_role: str,
    language: str,
) -> str:
    history = format_thread_history(
        detail.messages or [],
        counterpart_name=counterpart_name,
        language=language,
    )

    lines = [
        f"💬 {counterpart_name}",
    ]

    if detail.profession_name:
        lines.append(f"💼 {detail.profession_name}")

    lines.extend(
        [
            message_thread_status_label(
                detail.thread_status,
                viewer_role=viewer_role,
                language=language,
            ),
            "",
            history,
        ]
    )

    return "\n".join(lines)


def format_client_thread_detail_text(
    detail,
    language: str,
) -> str:
    return format_open_thread_chat_text(
        detail,
        counterpart_name=detail.specialist_name,
        viewer_role="client",
        language=language,
    )


def format_specialist_thread_detail_text(
    detail,
    language: str,
) -> str:
    return format_open_thread_chat_text(
        detail,
        counterpart_name=detail.client_name,
        viewer_role="specialist",
        language=language,
    )


def message_thread_keyboard(
    language: str,
    *,
    role: str,
    allow_finish: bool = True,
    show_original: bool = False,
    thread_id: UUID | str | None = None,
) -> InlineKeyboardMarkup:
    normalized_role = (
        "specialist"
        if role == "specialist"
        else "client"
    )
    back_callback = (
        "SPEC_DIALOGS"
        if normalized_role == "specialist"
        else "CLIENT_DIALOGS"
    )
    report_callback = (
        "SPEC_THREAD_REPORT"
        if normalized_role == "specialist"
        else "search_report_thread_pending"
    )

    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "contact_chat_attach_btn",
                    language,
                ),
                callback_data="CONTACT_ATTACH_FILE",
            )
        ]
    ]

    if allow_finish:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_finish_btn",
                        language,
                    ),
                    callback_data="SPEC_THREAD_COMPLETE",
                )
            ]
        )

    if (
        show_original
        and thread_id is not None
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_show_original_btn",
                        language,
                    ),
                    callback_data=(
                        build_contact_translation_callback(
                            action="original",
                            thread_id=thread_id,
                            role=normalized_role,
                        )
                    ),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_report_btn",
                        language,
                    ),
                    callback_data=report_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_back_btn",
                        language,
                    ),
                    callback_data=back_callback,
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def completion_confirmation_keyboard(
    *,
    thread_id: UUID,
    role: str,
    language: str,
) -> InlineKeyboardMarkup:
    role_code = "s" if role == "specialist" else "c"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "messages_completion_confirm_btn",
                        language,
                    ),
                    callback_data=(
                        f"TCF:{thread_id}:{role_code}"
                    ),
                )
            ]
        ]
    )


def completed_conversation_keyboard(
    *,
    contact_request_id: str | None,
    role: str,
    language: str,
) -> InlineKeyboardMarkup:
    back_callback = (
        "CLIENT_DIALOGS"
        if role == "client"
        else "SPEC_DIALOGS"
    )

    rows: list[list[InlineKeyboardButton]] = []

    if contact_request_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("review_leave_btn", language),
                    callback_data=(
                        f"review_start:{contact_request_id}"
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("contact_chat_back_btn", language),
                callback_data=back_callback,
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def specialist_dialogs_keyboard(
    *,
    items_count: int,
    page: int,
    view: str,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("messages_tab_new", language),
                callback_data="SPEC_DIALOGS_VIEW:new:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_correspondence", language),
                callback_data="SPEC_DIALOGS_VIEW:active:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_tab_completed", language),
                callback_data="SPEC_DIALOGS_VIEW:completed:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_archive", language),
                callback_data="SPEC_DIALOGS_VIEW:archive:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_search_btn", language),
                callback_data="SPEC_DIALOG_SEARCH",
            )
        ],
    ]

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=(
                    f"SPEC_DIALOGS_VIEW:{view}:{page - 1}"
                ),
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"SPEC_DIALOGS_VIEW:{view}:{page + 1}"
                ),
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def specialist_dialog_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("messages_open_chat", language),
                    callback_data=f"SPEC_DIALOG_OPEN:{index}",
                )
            ]
        ]
    )


def format_specialist_dialogs_text(
    *,
    dialogs,
    view: str,
    page: int,
    unread_messages: int,
    language: str,
) -> str:
    return format_messages_list_text(
        dialogs,
        unread_messages=unread_messages,
        language=language,
    )


async def show_specialist_dialogs(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str = "active",
    page: int = 0,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    state_data = await state.get_data()
    search_query = state_data.get(
        "specialist_messages_search_query",
    )

    try:
        async with get_session() as session:
            action = await (
                UserDialogsService(
                    session
                ).list_specialist_dialogs(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    view=view,
                    page=page,
                    page_size=(
                        CLIENT_DIALOGS_PAGE_SIZE
                    ),
                    search_query=search_query,
                )
            )

    except UserDialogsAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return

    language = action.actor.language
    visible_dialogs = action.items
    unread_messages = (
        action.unread_messages
    )
    has_next = action.has_next
    page = action.page

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                state_data.get(
                    "dialog_list_message_ids"
                )
                or []
            )
            if message_id
        ],
    )

    await state.update_data(
        specialist_dialog_ids=[
            str(item.thread_id)
            for item in visible_dialogs
        ],
        specialist_dialogs_view=view,
        specialist_dialogs_page=page,
    )

    rendered_message_ids: list[int] = []

    header_message = (
        await callback.message.answer(
            format_specialist_dialogs_text(
                dialogs=visible_dialogs,
                view=view,
                page=page,
                unread_messages=(
                    unread_messages
                ),
                language=language,
            ),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    for index, item in enumerate(
        visible_dialogs
    ):
        display_number = (
            page * CLIENT_DIALOGS_PAGE_SIZE
            + index
            + 1
        )

        card_message = (
            await callback.message.answer(
                format_dialog_card(
                    item=item,
                    display_number=(
                        display_number
                    ),
                    language=language,
                ),
                reply_markup=(
                    specialist_dialog_card_keyboard(
                        index=index,
                        language=language,
                    )
                ),
            )
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = (
        await callback.message.answer(
            t(
                "messages_hint",
                language,
            ),
            reply_markup=(
                specialist_dialogs_keyboard(
                    items_count=len(
                        visible_dialogs
                    ),
                    page=page,
                    view=view,
                    has_next=has_next,
                    language=language,
                )
            ),
        )
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        dialog_list_message_ids=(
            rendered_message_ids
        ),
    )

    await callback.answer()


async def send_dialog_attachment_or_fallback(
    *,
    bot,
    chat_id: int,
    attachment: dict,
    language: str,
    caption: str | None = None,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
) -> Message:
    attachment_message = await (
        send_telegram_attachment(
            bot=bot,
            chat_id=chat_id,
            attachment=attachment,
            caption=caption,
            reply_markup=reply_markup,
        )
    )

    if attachment_message is not None:
        return attachment_message

    fallback_parts = [
        str(caption or "").strip(),
        t(
            "contact_attachment_send_error",
            language,
        ),
    ]
    fallback_text = "\n\n".join(
        part
        for part in fallback_parts
        if part
    )

    return await bot.send_message(
        chat_id=chat_id,
        text=fallback_text,
        reply_markup=reply_markup,
    )


async def send_specialist_thread_detail(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    thread_id: str,
    language: str,
) -> None:
    try:
        async with get_session() as session:
            result = await UserDialogsService(
                session
            ).get_specialist_dialog(
                platform_user_id=(
                    callback.from_user.id
                ),
                thread_id=thread_id,
            )
    except UserDialogsAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except Exception:
        await callback.answer(
            t(
                "contact_thread_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    detail = result.detail
    language = normalize_language(
        result.actor.language
    )

    data = await state.get_data()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                data.get(
                    "dialog_list_message_ids"
                )
                or []
            )
            if message_id
        ],
    )

    await state.update_data(
        dialog_list_message_ids=[],
    )

    await state.update_data(
        active_contact_request_id=(
            str(detail.contact_request_id)
            if detail.contact_request_id
            else None
        ),
        active_thread_id=thread_id,
        active_thread_role="specialist",
    )
    await state.set_state(
        SpecialistSearchFSM.entering_thread_message,
    )

    attachment_items = [
        item
        for item in detail.messages
        if item.attachment
    ]
    chat_chunks = split_telegram_text(
        format_specialist_thread_detail_text(
            detail,
            language,
        )
    )

    rendered_message_ids: list[int] = []

    for index, chunk in enumerate(chat_chunks):
        is_last_chunk = index == len(chat_chunks) - 1

        chat_message = await callback.message.answer(
            chunk,
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="specialist",
                    thread_id=thread_id,
                    show_original=(
                        detail.show_original_button
                    ),
                )
                if (
                    is_last_chunk
                    and not attachment_items
                )
                else None
            ),
        )
        rendered_message_ids.append(
            chat_message.message_id
        )

    for index, item in enumerate(attachment_items):
        is_last_attachment = (
            index == len(attachment_items) - 1
        )
        sender_name = (
            t("contact_chat_you_label", language)
            if item.is_sent_by_viewer
            else detail.client_name
        )
        sent_at = item.created_at.strftime(
            "%d.%m %H:%M"
        )

        attachment_message = await send_dialog_attachment_or_fallback(
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            attachment=item.attachment,
            language=language,
            caption=(
                f"{sender_name} · {sent_at}\n"
                f"{format_chat_message_body(item, language)}"
            ),
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="specialist",
                    thread_id=thread_id,
                    show_original=(
                        detail.show_original_button
                    ),
                )
                if is_last_attachment
                else None
            ),
        )

        if attachment_message:
            rendered_message_ids.append(
                attachment_message.message_id
            )

    await state.update_data(
        last_contact_chat_message_ids=(
            rendered_message_ids
        ),
    )

    await callback.answer()


@user_dialogs_router.callback_query(F.data.startswith("SPEC_DIALOG_OPEN:"))
async def open_specialist_dialog(callback: CallbackQuery, state: FSMContext):
    language = await get_user_dialog_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    thread_ids = data.get("specialist_dialog_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(thread_ids):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await send_specialist_thread_detail(
        callback=callback,
        state=state,
        thread_id=thread_ids[index],
        language=language,
    )


@user_dialogs_router.callback_query(F.data == "SPEC_DIALOGS")
async def specialist_dialogs_entry(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_cross_feature_messages(
        callback=callback,
        state=state,
    )
    await state.update_data(
        specialist_messages_search_query=None,
    )
    await show_specialist_dialogs(
        callback,
        state,
        view="active",
        page=0,
    )


@user_dialogs_router.callback_query(F.data.startswith("SPEC_DIALOGS_VIEW:"))
async def specialist_dialogs_view(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    view = parts[1] if len(parts) > 1 else "active"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0

    if view not in{"new", "active", "completed", "archive"}:
        view = "active"
    if page < 0:
        page = 0

    await show_specialist_dialogs(callback, state, view=view, page=page)


@user_dialogs_router.callback_query(F.data == "SPEC_THREAD_COMPLETE")
async def finish_thread_from_chat(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_user_dialog_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    thread_id = data.get("active_thread_id")
    role = data.get("active_thread_role") or "client"
    contact_request_id = data.get("active_contact_request_id")

    if not thread_id:
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            completion = await (
                UserDialogsService(session)
                .finish_dialog(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    thread_id=thread_id,
                )
            )
    except UserDialogsAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except UserDialogsThreadError:
        await callback.answer(
            t(
                "contact_thread_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except ContactChatError as exc:
        await callback.answer(
            t(
                "contact_request_error",
                language,
            ).format(
                error=str(exc),
            ),
            show_alert=True,
        )
        return

    result = completion.result
    language = normalize_language(
        completion.actor.language
    )
    receiver_chat_id = (
        completion.receiver_chat_id
    )
    receiver_language = (
        completion.receiver_language
    )

    if result.action == "requested":
        pending_keyboard = message_thread_keyboard(
            language,
            role=role,
            allow_finish=False,
        )

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "messages_completion_requested",
                language,
            ),
            reply_markup=pending_keyboard,
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        await callback.answer()

        if (
            receiver_chat_id
            and result.requested_for_role
        ):
            try:
                await callback.message.bot.send_message(
                    chat_id=receiver_chat_id,
                    text=t(
                        "messages_completion_request_received",
                        receiver_language,
                    ),
                    reply_markup=completion_confirmation_keyboard(
                        thread_id=result.thread_id,
                        role=result.requested_for_role,
                        language=receiver_language,
                    ),
                )
            except (
                TelegramBadRequest,
                TelegramForbiddenError,
            ) as exc:
                logger.warning(
                    "completion_request_delivery_failed "
                    "thread_id=%s receiver_user_id=%s "
                    "error=%s",
                    result.thread_id,
                    result.requested_for_user_id,
                    exc,
                )

    elif result.action == "pending":
        try:
            await callback.message.edit_reply_markup(
                reply_markup=message_thread_keyboard(
                    language,
                    role=role,
                    allow_finish=False,
                ),
            )
        except TelegramBadRequest:
            pass

        await callback.answer(
            t(
                "messages_completion_already_requested",
                language,
            ),
            show_alert=True,
        )
        return

    else:
        await state.update_data(
            active_thread_id=None,
            review_thread_id=thread_id,
            review_thread_role=role,
        )

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "messages_completion_confirmed",
                language,
            ),
            reply_markup=completed_conversation_keyboard(
                contact_request_id=contact_request_id,
                role=role,
                language=language,
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        await callback.answer()


@user_dialogs_router.callback_query(
    F.data.startswith("TCF:")
)
async def confirm_thread_completion_from_notification(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_user_dialog_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        _, thread_id_raw, role_code = (
            callback.data or ""
        ).split(":", 2)
        thread_id = UUID(thread_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    if role_code not in {"c", "s"}:
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    role = (
        "specialist"
        if role_code == "s"
        else "client"
    )

    try:
        async with get_session() as session:
            completion = await (
                UserDialogsService(session)
                .finish_dialog(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    thread_id=thread_id,
                )
            )
    except UserDialogsAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except UserDialogsThreadError:
        await callback.answer(
            t(
                "contact_thread_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except ContactChatError as exc:
        await callback.answer(
            t(
                "contact_request_error",
                language,
            ).format(
                error=str(exc),
            ),
            show_alert=True,
        )
        return

    result = completion.result
    language = normalize_language(
        completion.actor.language
    )

    if result.action != "completed":
        await callback.answer(
            t("messages_completion_requested", language),
            show_alert=True,
        )
        return

    contact_request_id = (
        str(result.contact_request_id)
        if result.contact_request_id
        else None
    )

    await state.update_data(
        active_thread_id=None,
        active_contact_request_id=contact_request_id,
        review_thread_id=str(result.thread_id),
        review_thread_role=role,
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "messages_completion_confirmed",
            language,
        ),
        reply_markup=completed_conversation_keyboard(
            contact_request_id=contact_request_id,
            role=role,
            language=language,
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await callback.answer()


@user_dialogs_router.callback_query(
    F.data == "SPEC_THREAD_REPORT"
)
async def report_specialist_thread(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = (
        await get_user_dialog_language(
            callback.from_user.id,
            callback.from_user.language_code,
        )
    )
    data = await state.get_data()
    thread_id = data.get(
        "active_thread_id"
    )

    if not thread_id:
        await callback.answer(
            t(
                "contact_thread_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            target = await (
                UserDialogsService(session)
                .resolve_complaint_target(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    thread_id=thread_id,
                )
            )
    except UserDialogsAccessError:
        await callback.answer(
            t(
                "auth_required_start",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        ModerationError,
        UserDialogsThreadError,
    ):
        await callback.answer(
            t(
                "contact_thread_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        target.actor.language
    )
    target_type = target.target_type
    target_id = target.target_id
    conversation_thread_id = (
        target.conversation_thread_id
    )

    await state.update_data(
        pending_report_target_type=(
            target_type
        ),
        pending_report_target_id=str(
            target_id
        ),
        pending_report_conversation_thread_id=str(
            conversation_thread_id
        ),
        pending_report_target_summary=None,
        pending_report_reason=None,
        pending_report_comment=None,
        user_language=language,
    )
    await state.set_state(
        SpecialistSearchFSM.viewing_results
    )

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "complaint_reason_prompt",
                language,
            ),
            reply_markup=(
                complaint_reason_keyboard(
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
    await callback.answer()


async def open_messages_search_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    role: str,
) -> None:
    language = await get_user_dialog_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await state.update_data(
        messages_search_role=role,
    )
    await state.set_state(
        UserDialogsFSM.entering_messages_search,
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "messages_search_prompt",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@user_dialogs_router.callback_query(F.data == "CLIENT_DIALOG_SEARCH")
async def start_client_messages_search(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_messages_search_prompt(
        callback,
        state,
        role="client",
    )


@user_dialogs_router.callback_query(F.data == "SPEC_DIALOG_SEARCH")
async def start_specialist_messages_search(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_messages_search_prompt(
        callback,
        state,
        role="specialist",
    )


@user_dialogs_router.message(
    UserDialogsFSM.entering_messages_search
)
async def receive_messages_search(
    message: Message,
    state: FSMContext,
):
    language = await get_user_dialog_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    search_query = (
        message.text or ""
    ).strip()
    data = await state.get_data()
    role = data.get(
        "messages_search_role"
    )

    if not search_query:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('messages_search_empty_query', language)}\n\n"
                f"{t('messages_search_prompt', language)}"
            ),
        )
        return

    view = (
        data.get(
            "client_dialog_view"
        )
        if role == "client"
        else data.get(
            "specialist_dialogs_view"
        )
    ) or "active"

    try:
        async with get_session() as session:
            search_result = await (
                UserDialogsService(session)
                .search_dialogs(
                    platform_user_id=(
                        message.from_user.id
                    ),
                    role=role,
                    view=view,
                    search_query=search_query,
                    page_size=(
                        CLIENT_DIALOGS_PAGE_SIZE
                    ),
                )
            )
    except UserDialogsAccessError:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "billing_start_required",
                language,
            ),
        )
        await state.set_state(None)
        return

    role = search_result.role
    view = search_result.view
    items = search_result.items
    unread_messages = (
        search_result.unread_messages
    )
    has_next = search_result.has_next
    language = normalize_language(
        search_result.actor.language
    )

    if role == "client":
        await state.update_data(
            client_messages_search_query=search_query,
            client_dialog_thread_ids=[
                str(item.thread_id)
                for item in items
            ],
            client_dialog_view=view,
            client_dialog_page=0,
        )

        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id,
                data.get("last_menu_message_id"),
                *(
                    data.get("dialog_list_message_ids")
                    or []
                ),
            ],
        )

        rendered_message_ids: list[int] = []

        header_message = await message.answer(
            format_client_dialogs_text(
                items,
                language,
                unread_messages=unread_messages,
            )
        )
        rendered_message_ids.append(
            header_message.message_id
        )

        for index, item in enumerate(items):
            card_message = await message.answer(
                format_dialog_card(
                    item=item,
                    display_number=index + 1,
                    language=language,
                ),
                reply_markup=client_dialog_card_keyboard(
                    index=index,
                    language=language,
                ),
            )
            rendered_message_ids.append(
                card_message.message_id
            )

        navigation_message = await message.answer(
            t("messages_hint", language),
            reply_markup=client_dialogs_keyboard(
                items_count=len(items),
                page=0,
                view=view,
                language=language,
                show_role_switch=False,
            ),
        )
        rendered_message_ids.append(
            navigation_message.message_id
        )

        await state.update_data(
            dialog_list_message_ids=rendered_message_ids,
            last_menu_message_id=None,
        )

    else:
        visible_items = items

        await state.update_data(
            specialist_messages_search_query=search_query,
            specialist_dialog_ids=[
                str(item.thread_id)
                for item in visible_items
            ],
            specialist_dialogs_view=view,
            specialist_dialogs_page=0,
        )

        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id,
                data.get("last_menu_message_id"),
                *(
                    data.get("dialog_list_message_ids")
                    or []
                ),
            ],
        )

        rendered_message_ids: list[int] = []

        header_message = await message.answer(
            format_specialist_dialogs_text(
                dialogs=visible_items,
                view=view,
                page=0,
                unread_messages=unread_messages,
                language=language,
            )
        )
        rendered_message_ids.append(
            header_message.message_id
        )

        for index, item in enumerate(visible_items):
            card_message = await message.answer(
                format_dialog_card(
                    item=item,
                    display_number=index + 1,
                    language=language,
                ),
                reply_markup=specialist_dialog_card_keyboard(
                    index=index,
                    language=language,
                ),
            )
            rendered_message_ids.append(
                card_message.message_id
            )

        navigation_message = await message.answer(
            t("messages_hint", language),
            reply_markup=specialist_dialogs_keyboard(
                items_count=len(visible_items),
                page=0,
                view=view,
                has_next=has_next,
                language=language,
            ),
        )
        rendered_message_ids.append(
            navigation_message.message_id
        )

        await state.update_data(
            dialog_list_message_ids=rendered_message_ids,
            last_menu_message_id=None,
        )

    await state.set_state(None)


@user_dialogs_router.callback_query(F.data == "CLIENT_DIALOGS")
@user_dialogs_router.callback_query(F.data.startswith("CLIENT_DIALOGS:"))
async def show_client_dialogs(callback: CallbackQuery, state: FSMContext):
    if callback.data == "CLIENT_DIALOGS":
        await clear_cross_feature_messages(
            callback=callback,
            state=state,
        )
    language = normalize_language(
        callback.from_user.language_code
    )

    view = "active"
    page = 0

    if (
        callback.data
        and callback.data.startswith(
            "CLIENT_DIALOGS:"
        )
    ):
        parts = callback.data.split(":")

        if (
            len(parts) >= 2
            and parts[1] in {
                "new",
                "active",
                "completed",
                "archive",
            }
        ):
            view = parts[1]

        if (
            len(parts) >= 3
            and parts[2].isdigit()
        ):
            page = int(parts[2])

    if callback.data == "CLIENT_DIALOGS":
        await state.update_data(
            client_messages_search_query=None,
        )

    state_data = await state.get_data()
    search_query = state_data.get(
        "client_messages_search_query",
    )

    try:
        async with get_session() as session:
            action = await (
                UserDialogsService(
                    session
                ).list_client_dialogs(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    view=view,
                    page=page,
                    page_size=(
                        CLIENT_DIALOGS_PAGE_SIZE
                    ),
                    search_query=search_query,
                )
            )

    except UserDialogsAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return

    language = action.actor.language
    items = action.items
    unread_messages = (
        action.unread_messages
    )
    show_role_switch = (
        action.show_role_switch
    )
    page = action.page

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                state_data.get(
                    "dialog_list_message_ids"
                )
                or []
            )
            if message_id
        ],
    )

    await state.update_data(
        client_dialog_thread_ids=[
            str(item.thread_id)
            for item in items
        ],
        client_dialog_view=view,
        client_dialog_page=page,
    )

    rendered_message_ids: list[int] = []

    header_message = (
        await callback.message.answer(
            format_client_dialogs_text(
                items,
                language,
                unread_messages=(
                    unread_messages
                ),
            )
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    for index, item in enumerate(items):
        display_number = (
            page * CLIENT_DIALOGS_PAGE_SIZE
            + index
            + 1
        )

        card_message = (
            await callback.message.answer(
                format_dialog_card(
                    item=item,
                    display_number=(
                        display_number
                    ),
                    language=language,
                ),
                reply_markup=(
                    client_dialog_card_keyboard(
                        index=index,
                        language=language,
                    )
                ),
            )
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = (
        await callback.message.answer(
            t(
                "messages_hint",
                language,
            ),
            reply_markup=(
                client_dialogs_keyboard(
                    items_count=len(items),
                    page=page,
                    view=view,
                    language=language,
                    show_role_switch=(
                        show_role_switch
                    ),
                )
            ),
        )
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        dialog_list_message_ids=(
            rendered_message_ids
        ),
    )

    await callback.answer()


async def send_client_thread_detail(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    thread_id: str,
    language: str,
) -> None:
    try:
        async with get_session() as session:
            result = await UserDialogsService(
                session
            ).get_client_dialog(
                platform_user_id=(
                    callback.from_user.id
                ),
                thread_id=thread_id,
            )
    except UserDialogsAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except Exception:
        await callback.answer(
            t(
                "contact_thread_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    detail = result.detail
    language = normalize_language(
        result.actor.language
    )

    data = await state.get_data()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                data.get(
                    "dialog_list_message_ids"
                )
                or []
            )
            if message_id
        ],
    )

    await state.update_data(
        dialog_list_message_ids=[],
    )

    await state.update_data(
        active_contact_request_id=(
            str(detail.contact_request_id)
            if detail.contact_request_id
            else None
        ),
        active_thread_id=thread_id,
        active_thread_role="client",
    )
    await state.set_state(
        SpecialistSearchFSM.entering_thread_message,
    )

    attachment_items = [
        item
        for item in detail.messages
        if item.attachment
    ]
    chat_chunks = split_telegram_text(
        format_client_thread_detail_text(
            detail,
            language,
        )
    )

    rendered_message_ids: list[int] = []

    for index, chunk in enumerate(chat_chunks):
        is_last_chunk = index == len(chat_chunks) - 1

        chat_message = await callback.message.answer(
            chunk,
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="client",
                    thread_id=thread_id,
                    show_original=(
                        detail.show_original_button
                    ),
                )
                if is_last_chunk and not attachment_items
                else None
            ),
        )

        rendered_message_ids.append(
            chat_message.message_id
        )

    for index, item in enumerate(attachment_items):
        is_last_attachment = (
            index == len(attachment_items) - 1
        )
        sender_name = (
            t("contact_chat_you_label", language)
            if item.is_sent_by_viewer
            else detail.specialist_name
        )
        sent_at = item.created_at.strftime(
            "%d.%m %H:%M"
        )

        attachment_message = await send_dialog_attachment_or_fallback(
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            attachment=item.attachment,
            language=language,
            caption=(
                f"{sender_name} · {sent_at}\n"
                f"{format_chat_message_body(item, language)}"
            ),
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="client",
                    thread_id=thread_id,
                    show_original=(
                        detail.show_original_button
                    ),
                )
                if is_last_attachment
                else None
            ),
        )

        if attachment_message:
            rendered_message_ids.append(
                attachment_message.message_id
            )
    await state.update_data(
        last_contact_chat_message_ids=(
            rendered_message_ids
        ),
    )
    await callback.answer()


@user_dialogs_router.callback_query(F.data.startswith("CLIENT_DIALOG_OPEN:"))
async def open_client_dialog(callback: CallbackQuery, state: FSMContext):
    language = await get_user_dialog_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    thread_ids = data.get("client_dialog_thread_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(thread_ids):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    thread_id = thread_ids[index]

    await send_client_thread_detail(
        callback=callback,
        state=state,
        thread_id=thread_id,
        language=language,
    )
