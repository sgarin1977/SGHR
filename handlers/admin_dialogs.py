from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from database.session import get_session
from handlers.admin_common import (
    AdminInterfaceLanguageMiddleware,
    clear_admin_message_group,
    normalize_admin_language,
    replace_admin_callback_screen,
)
from handlers.search import format_chat_message_body
from services.admin_dialogs import (
    AdminDialogsAccessError,
    AdminDialogsService,
)
from services.contact_chat import ContactChatError
from services.moderation import ModerationError
from ui.texts import t


READ_ONLY_CLIENT_PAGE_SIZE = 5


admin_dialogs_router = Router()


admin_dialogs_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_dialogs_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


normalize_language = normalize_admin_language


def super_admin_read_only_specialist_dialogs_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_SPECIALIST_DIALOGS:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_SPECIALIST_DIALOGS:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_SPECIALIST_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_super_admin_read_only_message_history(
    messages,
    *,
    other_name: str,
    language: str,
) -> str:
    history_lines = []

    for message in messages:
        sender_name = (
            t("contact_chat_you_label", language)
            if message.is_sent_by_viewer
            else other_name
        )
        sent_at = message.created_at.strftime(
            "%d.%m %H:%M"
        )
        message_body = (
            format_chat_message_body(
                message,
                language,
            )
            or "—"
        )

        history_lines.append(
            f"{sender_name} · {sent_at}\n"
            f"{message_body}"
        )

    return "\n\n".join(history_lines) or "—"


def format_super_admin_read_only_specialist_dialog(
    item,
    *,
    number: int,
    language: str,
) -> str:
    message = (item.last_message_text or "").strip()

    if len(message) > 300:
        message = f"{message[:297]}..."

    return t(
        "super_admin_ro_specialist_dialog_item",
        language,
    ).format(
        number=number,
        client=item.specialist_name or "-",
        profession=item.profession_name or "-",
        status=admin_dialog_status_label(
            item.status,
            language,
        ),
        unread=item.unread_count,
        message=message or "-",
    )


def format_super_admin_read_only_specialist_dialog_detail(
    detail,
    *,
    language: str,
) -> str:
    messages = format_super_admin_read_only_message_history(
        detail.messages,
        other_name=detail.client_name or "—",
        language=language,
    )

    return t(
        "super_admin_ro_specialist_dialog_detail",
        language,
    ).format(
        client=detail.client_name or "—",
        profession=detail.profession_name or "—",
        status=admin_dialog_status_label(
            detail.thread_status,
            language,
        ),
        messages=messages,
    )


def super_admin_read_only_client_dialogs_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_CLIENT_DIALOGS:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_CLIENT_DIALOGS:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_CLIENT_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_super_admin_read_only_client_dialog(
    item,
    *,
    number: int,
    language: str,
) -> str:
    message = (item.last_message_text or "").strip()

    if len(message) > 300:
        message = f"{message[:297]}..."

    return t(
        "super_admin_ro_client_dialog_item",
        language,
    ).format(
        number=number,
        specialist=item.specialist_name or "-",
        profession=item.profession_name or "-",
        status=admin_dialog_status_label(
            item.status,
            language,
        ),
        unread=item.unread_count,
        message=message or "-",
    )


def format_super_admin_read_only_client_dialog_detail(
    detail,
    *,
    language: str,
) -> str:
    messages = format_super_admin_read_only_message_history(
        detail.messages,
        other_name=detail.specialist_name or "—",
        language=language,
    )

    return t(
        "super_admin_ro_client_dialog_detail",
        language,
    ).format(
        specialist=detail.specialist_name or "—",
        profession=detail.profession_name or "—",
        status=admin_dialog_status_label(
            detail.thread_status,
            language,
        ),
        messages=messages,
    )


@admin_dialogs_router.callback_query(
    F.data.startswith("SA_RO_CLIENT_DIALOGS:")
)
async def super_admin_read_only_client_dialogs(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "client"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminDialogsService(
                session
            ).list_impersonated_client_threads(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                page=page,
                page_size=READ_ONLY_CLIENT_PAGE_SIZE,
                language=language,
            )
    except (
        AdminDialogsAccessError,
        ContactChatError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = list(result.items)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        super_admin_impersonation_client_thread_ids=[
            str(item.thread_id)
            for item in visible_items
        ],
        super_admin_impersonation_client_dialogs_page=page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_client_"
            "dialog_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "super_admin_ro_client_dialogs_title",
            language,
        ).format(
            page=page + 1,
            count=len(visible_items),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not visible_items:
        empty_message = await callback.message.answer(
            t(
                "client_dialogs_empty",
                language,
            ),
            reply_markup=(
                super_admin_read_only_client_dialogs_keyboard(
                    page=page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            super_admin_ro_client_dialog_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    start_number = (
        page
        * READ_ONLY_CLIENT_PAGE_SIZE
        + 1
    )

    for index, item in enumerate(visible_items):
        number = start_number + index

        card_message = await callback.message.answer(
            format_super_admin_read_only_client_dialog(
                item,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_client_open_dialog_btn",
                                language,
                            ).format(
                                number=number
                            ),
                            callback_data=(
                                "SA_RO_CLIENT_DIALOG_OPEN:"
                                f"{index}"
                            ),
                        )
                    ]
                ]
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "super_admin_ro_read_only_label",
            language,
        ),
        reply_markup=(
            super_admin_read_only_client_dialogs_keyboard(
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_ro_client_dialog_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()


@admin_dialogs_router.callback_query(
    F.data.startswith("SA_RO_CLIENT_DIALOG_OPEN:")
)
async def super_admin_read_only_client_dialog_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    thread_ids = data.get(
        "super_admin_impersonation_client_thread_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "client"
        or index < 0
        or index >= len(thread_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        thread_id = UUID(thread_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            detail = await AdminDialogsService(
                session
            ).get_impersonated_client_thread(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                thread_id=thread_id,
                language=language,
            )
    except (
        AdminDialogsAccessError,
        ContactChatError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_client_dialogs_page"
        ) or 0
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_client_"
            "dialog_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_read_only_client_dialog_detail(
                detail,
                language=language,
            )
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_client_back_to_dialogs_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_CLIENT_DIALOGS:"
                            f"{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_IMPERSONATE_STOP"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_dialogs_router.callback_query(
    F.data.startswith("SA_RO_SPECIALIST_DIALOGS:")
)
async def super_admin_read_only_specialist_dialogs(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "specialist"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        professional_cabinet_id = (
            UUID(
                str(
                    data.get(
                        "super_admin_impersonation_"
                        "professional_cabinet_id"
                    )
                )
            )
            if data.get(
                "super_admin_impersonation_"
                "professional_cabinet_id"
            )
            else None
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminDialogsService(
                session
            ).list_impersonated_specialist_threads(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                page=page,
                page_size=READ_ONLY_CLIENT_PAGE_SIZE,
                language=language,
            )
    except (
        AdminDialogsAccessError,
        ContactChatError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = list(result.items)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        super_admin_impersonation_specialist_thread_ids=[
            str(item.thread_id)
            for item in visible_items
        ],
        super_admin_impersonation_specialist_dialogs_page=page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_specialist_"
            "dialog_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "super_admin_ro_specialist_dialogs_title",
            language,
        ).format(
            page=page + 1,
            count=len(visible_items),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not visible_items:
        empty_message = await callback.message.answer(
            t(
                "client_dialogs_empty",
                language,
            ),
            reply_markup=(
                super_admin_read_only_specialist_dialogs_keyboard(
                    page=page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            super_admin_ro_specialist_dialog_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    start_number = (
        page
        * READ_ONLY_CLIENT_PAGE_SIZE
        + 1
    )

    for index, item in enumerate(visible_items):
        number = start_number + index

        card_message = await callback.message.answer(
            format_super_admin_read_only_specialist_dialog(
                item,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_specialist_open_dialog_btn",
                                language,
                            ).format(
                                number=number
                            ),
                            callback_data=(
                                "SA_RO_SPECIALIST_DIALOG_OPEN:"
                                f"{index}"
                            ),
                        )
                    ]
                ]
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "super_admin_ro_read_only_label",
            language,
        ),
        reply_markup=(
            super_admin_read_only_specialist_dialogs_keyboard(
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_ro_specialist_dialog_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()


@admin_dialogs_router.callback_query(
    F.data.startswith("SA_RO_SPECIALIST_DIALOG_OPEN:")
)
async def super_admin_read_only_specialist_dialog_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    thread_ids = data.get(
        "super_admin_impersonation_specialist_thread_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "specialist"
        or index < 0
        or index >= len(thread_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        thread_id = UUID(thread_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            detail = await AdminDialogsService(
                session
            ).get_impersonated_specialist_thread(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                thread_id=thread_id,
                language=language,
            )
    except (
        AdminDialogsAccessError,
        ContactChatError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_specialist_dialogs_page"
        ) or 0
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_specialist_"
            "dialog_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_read_only_specialist_dialog_detail(
                detail,
                language=language,
            )
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_specialist_back_to_dialogs_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_SPECIALIST_DIALOGS:"
                            f"{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_IMPERSONATE_STOP"
                        ),
                    )
                ],
            ]
        ),
    )


def admin_dialog_detection_label(
    detected_type: str,
    language: str,
) -> str:
    key_by_type = {
        "phone": "admin_dialog_detection_phone",
        "email": "admin_dialog_detection_email",
        "telegram_username": (
            "admin_dialog_detection_telegram_username"
        ),
        "messenger_phone": (
            "admin_dialog_detection_messenger_phone"
        ),
        "external_payment": (
            "admin_dialog_detection_external_payment"
        ),
    }

    return t(
        key_by_type.get(
            detected_type,
            "admin_dialog_detection_unknown",
        ),
        language,
    )


def admin_dialog_risk_label(
    severity: str | None,
    language: str,
) -> str:
    key_by_severity = {
        "low": "admin_dialog_risk_low",
        "medium": "admin_dialog_risk_medium",
        "high": "admin_dialog_risk_high",
        "critical": "admin_dialog_risk_critical",
    }

    key = key_by_severity.get(
        (severity or "").strip().lower()
    )

    return t(key, language) if key else "—"


def admin_dialog_status_label(
    status: str | None,
    language: str,
) -> str:
    key_by_status = {
        "waiting_specialist": "admin_dialog_status_waiting_specialist",
        "waiting_client": "admin_dialog_status_waiting_client",
        "open": "admin_dialog_status_open",
        "in_discussion": "admin_dialog_status_in_discussion",
        "completed": "admin_dialog_status_completed",
        "closed": "admin_dialog_status_closed",
    }

    key = key_by_status.get(
        (status or "").strip().lower(),
        "admin_dialog_status_other",
    )
    return t(key, language)


def admin_dialog_context_label(
    item,
    language: str,
) -> str:
    parts = []

    if item.has_complaint:
        parts.append(
            t(
                "admin_dialog_context_complaint",
                language,
            )
        )

    if item.has_risk_flag:
        parts.append(
            t(
                "admin_dialog_context_risk",
                language,
            )
        )

    return " + ".join(parts) or "—"


def admin_dialog_queue_keyboard(
    items,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items):
        rows.append(
            [
                InlineKeyboardButton(
                text=t(
                    "admin_dialog_queue_button",
                    language,
                ).format(
                    number=index + 1,
                    context=admin_dialog_context_label(
                        item,
                        language,
                    ),
                    status=admin_dialog_status_label(
                        item.thread_status,
                        language,
                    ),
                    messages_count=item.messages_count,
                ),
                    callback_data=f"ADM_ADMIN_THREAD:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dialog_back_btn",
                    language,
                ),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin_dialog_contexts(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            items = await AdminDialogsService(
                session
            ).list_admin_contexts(
                platform_user_id=callback.from_user.id
            )
    except (
        AdminDialogsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_dialog_thread_ids=[
            str(item.thread_id)
            for item in items
        ],
        admin_dialog_contexts=[
            {
                "has_complaint": item.has_complaint,
                "has_risk_flag": item.has_risk_flag,
                "thread_status": item.thread_status,
            }
            for item in items
        ],
    )

    if not items:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_dialog_queue_empty",
                language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_dialog_back_btn",
                                language,
                            ),
                            callback_data="ADM_PANEL",
                        )
                    ]
                ]
            ),
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dialog_queue_title",
            language,
        ),
        reply_markup=admin_dialog_queue_keyboard(
            items,
            language,
        ),
    )


@admin_dialogs_router.callback_query(
    F.data == "ADM_DIALOGS_STUB"
)
async def admin_dialogs_entry(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_admin_dialog_contexts(
        callback,
        state,
    )


@admin_dialogs_router.callback_query(
    F.data.startswith("ADM_ADMIN_THREAD:")
)
async def open_admin_dialog_thread(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        IndexError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    data = await state.get_data()
    thread_ids = data.get(
        "admin_dialog_thread_ids"
    ) or []
    contexts = data.get(
        "admin_dialog_contexts"
    ) or []

    if index < 0 or index >= len(thread_ids):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            messages = await AdminDialogsService(
                session
            ).get_admin_thread_messages(
                platform_user_id=callback.from_user.id,
                thread_id=UUID(
                    thread_ids[index]
                ),
            )
    except (
        AdminDialogsAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not messages:
        history = t(
            "admin_support_no_messages",
            language,
        )
        thread_status = "—"
    else:
        thread_status = admin_dialog_status_label(
            messages[0].thread_status,
            language,
        )
        history_lines = []

        for message in messages:
            if message.is_masked:
                detected_labels = [
                    admin_dialog_detection_label(
                        detected_type,
                        language,
                    )
                    for detected_type in (
                        message.risk_detected_types
                    )
                ]

                reasons = ", ".join(
                    detected_labels
                ) or t(
                    "admin_dialog_detection_unknown",
                    language,
                )

                message_text = t(
                    "admin_dialog_masked_message",
                    language,
                ).format(
                    reasons=reasons,
                    severity=admin_dialog_risk_label(
                        message.risk_severity,
                        language,
                    ),
                )
            else:
                message_text = (
                    message.original_text
                    or t(
                        "admin_dialog_empty_message",
                        language,
                    )
                )

            sender_label = t(
                (
                    "admin_dialog_sender_client"
                    if (
                        message.sender_user_id
                        == message.client_user_id
                    )
                    else "admin_dialog_sender_specialist"
                ),
                language,
            )

            history_lines.append(
                f"{sender_label}: {message_text}"
            )

        history = "\n".join(history_lines)

    selected_context = (
        contexts[index]
        if index < len(contexts)
        else {}
    )

    context_parts = []

    if selected_context.get("has_complaint"):
        context_parts.append(
            t(
                "admin_dialog_context_complaint",
                language,
            )
        )

    if selected_context.get("has_risk_flag"):
        context_parts.append(
            t(
                "admin_dialog_context_risk",
                language,
            )
        )

    context_label = " + ".join(context_parts) or "—"

    screen_text = t(
        "admin_dialog_detail",
        language,
    ).format(
        number=index + 1,
        context=context_label,
        status=thread_status,
        history=history,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=screen_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_dialog_back_to_list_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_DIALOGS_STUB"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data="ADM_PANEL",
                    )
                ],
            ]
        ),
    )
