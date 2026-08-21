import logging
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.session import get_session
from handlers.admin_common import (
    AdminInterfaceLanguageMiddleware,
    clear_admin_message_group,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_support import (
    AdminSupportAccessError,
    AdminSupportService,
)
from services.moderation import (
    ImpersonationRoleUnavailableError,
    ModerationError,
)
from services.support import SupportServiceError
from ui.texts import t


logger = logging.getLogger(__name__)

admin_support_router = Router()
admin_support_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_support_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)

normalize_language = normalize_admin_language

SUPPORT_STAFF_PAGE_SIZE = 5
ADMIN_ESCALATED_TICKET_PAGE_SIZE = 5


class AdminSupportFSM(StatesGroup):
    entering_admin_ticket_action_reason = State()
    entering_support_escalation_reason = State()
    entering_support_reply = State()
    entering_support_search = State()


def support_staff_menu_keyboard(
    language: str,
    *,
    show_role_switch: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_staff_open_btn", language),
                callback_data="ADM_SUPPORT_VIEW:open:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_in_progress_btn", language),
                callback_data="ADM_SUPPORT_VIEW:in_progress:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_resolved_btn", language),
                callback_data="ADM_SUPPORT_VIEW:resolved:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_search_btn", language),
                callback_data="ADM_SUPPORT_SEARCH",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_stats_btn", language),
                callback_data="ADM_SUPPORT_STATS",
            )
        ],
    ]

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

def format_support_staff_menu(counts: dict[str, int], language: str) -> str:
    return t("support_staff_menu_title", language).format(
        open_count=counts.get("open", 0),
        in_progress_count=counts.get("in_progress", 0),
        resolved_count=counts.get("resolved", 0),
    )

def support_staff_status_filter(view: str) -> set[str]:
    return {
        "open": {"open"},
        "in_progress": {"in_progress"},
        "resolved": {"resolved"},
    }.get(view, {"open"})

def support_staff_view_label(view: str, language: str) -> str:
    key = {
        "open": "support_staff_open_btn",
        "in_progress": "support_staff_in_progress_btn",
        "resolved": "support_staff_resolved_btn",
    }.get(view, "support_staff_open_btn")
    return t(key, language)

def format_support_staff_ticket_header(
    tickets,
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> str:
    view_label = support_staff_view_label(view, language)
    start = page * SUPPORT_STAFF_PAGE_SIZE + 1
    end = start + len(tickets) - 1

    if not tickets:
        return (
            f"{t('support_staff_list_title', language)}\n"
            f"{t('support_staff_empty_list', language).format(view=view_label)}"
        )

    return (
        f"{t('support_staff_list_title', language)}\n"
        f"{view_label}\n"
        f"{t('support_staff_shown_range', language).format(start=start, end=end)}"
    )

def format_support_staff_search_header(
    tickets,
    *,
    query: str,
    language: str,
) -> str:
    if not tickets:
        return t("support_staff_search_empty", language).format(query=query)

    return t("support_staff_search_results", language).format(
        query=query,
        count=len(tickets),
    )

def admin_escalated_ticket_card_keyboard(
    *,
    index: int,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_support_assign", language),
                    callback_data=(
                        f"ADM_ADMIN_TICKET_ACTION:assign:{index}"
                    ),
                ),
                InlineKeyboardButton(
                    text=t("admin_support_resolve", language),
                    callback_data=(
                        f"ADM_ADMIN_TICKET_ACTION:resolve:{index}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data=f"ADM_ADMIN_SUPPORT:{page}",
                )
            ],
        ]
    )

def format_admin_escalated_ticket(
    ticket,
    *,
    number: int,
    language: str,
) -> str:
    category = t(
        f"support_category_{ticket.category or 'other'}",
        language,
    )
    status = t(
        f"support_status_{ticket.status}",
        language,
    )
    user_number = f"user-{ticket.user_id.hex[:8]}"
    ticket_number = str(ticket.id).split("-", 1)[0]
    updated_at = (
        ticket.updated_at.strftime("%Y-%m-%d %H:%M")
        if ticket.updated_at
        else "-"
    )

    return t(
        "admin_escalated_ticket_card",
        language,
    ).format(
        number=number,
        ticket_number=ticket_number,
        user_number=user_number,
        category=category,
        priority=ticket.priority,
        status=status,
        updated_at=updated_at,
    )

def admin_escalated_ticket_item_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_open_btn", language),
                    callback_data=(
                        f"ADM_ADMIN_SUPPORT_OPEN:{index}"
                    ),
                )
            ]
        ]
    )

def admin_escalated_tickets_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []
    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_ADMIN_SUPPORT:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_ADMIN_SUPPORT:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_support_staff_ticket_card(
    ticket,
    *,
    number: int,
    language: str,
) -> str:
    user_ref = f"user-{str(ticket.user_id)[:8]}"
    category = t(f"support_category_{ticket.category or 'other'}", language)
    status = t(f"support_status_{ticket.status}", language)
    age = ticket.created_at.strftime("%Y-%m-%d") if ticket.created_at else "-"

    return t("support_staff_ticket_card", language).format(
        number=number,
        ticket_id=str(ticket.id)[:8],
        user=user_ref,
        category=category,
        priority=t(f"support_priority_{(ticket.priority or 'P3').lower()}", language),
        age=age,
        status=status,
    )

def support_staff_ticket_card_keyboard(
    *,
    index: int,
    ticket,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_staff_open_ticket_btn", language).format(index=index + 1),
                callback_data=f"ADM_SUP_VIEW:{index}",
            )
        ]
    ]

    if ticket.status == "open":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("support_staff_take_ticket_btn", language).format(index=index + 1),
                    callback_data=f"ADM_SUP_TAKE:{index}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def support_staff_ticket_actions_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_SUPPORT_VIEW:{view}:{page - 1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SUPPORT_VIEW:{view}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_staff_filter_btn", language),
                callback_data="ADM_SUPPORT_FILTERS",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_staff_back_to_panel", language),
                callback_data="ADM_SUPPORT",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def support_staff_filters_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_staff_open_btn", language),
                    callback_data="ADM_SUPPORT_VIEW:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_in_progress_btn", language),
                    callback_data="ADM_SUPPORT_VIEW:in_progress:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_resolved_btn", language),
                    callback_data="ADM_SUPPORT_VIEW:resolved:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_back_to_panel", language),
                    callback_data="ADM_SUPPORT",
                )
            ],
        ]
    )

def format_support_staff_stats(stats: dict, language: str) -> str:
    counts = stats.get("counts") or {}
    avg_response_minutes = stats.get("avg_response_minutes")

    if avg_response_minutes is None:
        avg_response = t("support_staff_stats_no_response", language)
    else:
        avg_response = t("support_staff_stats_avg_minutes", language).format(
            minutes=avg_response_minutes,
        )

    return t("support_staff_stats_title", language).format(
        open_count=counts.get("open", 0),
        in_progress_count=counts.get("in_progress", 0),
        resolved_count=counts.get("resolved", 0),
        closed_count=counts.get("closed", 0),
        total_count=stats.get("total", 0),
        avg_response=avg_response,
    )

def support_staff_stats_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_staff_stats_period_btn", language),
                    callback_data="ADM_SUPPORT_STATS_PERIOD",
                ),
                InlineKeyboardButton(
                    text=t("support_staff_stats_category_btn", language),
                    callback_data="ADM_SUPPORT_STATS_CATEGORY",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_back_to_panel", language),
                    callback_data="ADM_SUPPORT",
                )
            ],
        ]
    )

def support_ticket_keyboard(
    index: int,
    total: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_support_reply", language),
                callback_data=f"ADM_SUP_REPLY:{index}",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_support_assign", language),
                callback_data=f"ADM_SUP_ASSIGN:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_support_escalate", language),
                callback_data=f"ADM_SUP_ESCALATE:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("admin_support_resolve", language),
                callback_data=f"ADM_SUP_RESOLVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_support_close", language),
                callback_data=f"ADM_SUP_CLOSE:{index}",
            ),
        ],
    ]

    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_SUP_VIEW:{index - 1}",
            )
        )
    if index < total - 1:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SUP_VIEW:{index + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_staff_back_to_queue", language),
                callback_data="ADM_SUPPORT",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_support_ticket_card(
    view,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    ticket = view.ticket
    messages = view.messages[-5:]

    status_text = t(f"support_status_{ticket.status}", language)
    category_text = t(f"support_category_{ticket.category or 'other'}", language)
    priority_text = t(f"support_priority_{(ticket.priority or 'P3').lower()}", language)
    user_ref = f"user-{str(ticket.user_id)[:8]}"

    created_at = ticket.created_at.strftime("%Y-%m-%d %H:%M") if ticket.created_at else "-"
    updated_at = ticket.updated_at.strftime("%Y-%m-%d %H:%M") if ticket.updated_at else "-"
    resolved_at = ticket.resolved_at.strftime("%Y-%m-%d %H:%M") if ticket.resolved_at else None

    lines = [
        t("admin_support_ticket_title", language).format(
            ticket_id=str(ticket.id)[:8],
            index=index + 1,
            total=total,
        ),
        "",
        f"{t('admin_support_category', language)}: {category_text}",
        f"{t('admin_support_user', language)}: {user_ref}",
        f"{t('admin_status', language)}: {status_text}",
        f"{t('admin_support_priority', language)}: {priority_text}",
        "",
        t("admin_support_history", language),
        t("admin_support_created_at", language).format(value=created_at),
        t("admin_support_updated_at", language).format(value=updated_at),
    ]

    if resolved_at:
        lines.append(
            t("admin_support_resolved_at", language).format(value=resolved_at)
        )

    lines.extend([
        "",
        t("admin_support_messages", language),
    ])

    if not messages:
        lines.append(t("admin_support_no_messages", language))
    else:
        for message in messages:
            sender_role = message.sender_role or "system"
            sender_label = t(f"support_sender_{sender_role}", language)

            text = (message.message_text or "").strip()
            if text == "[deleted by user request]":
                text = t("support_message_deleted_by_user", language)

            lines.append(
                t("support_message_line", language).format(
                    sender_role=sender_label,
                    message=text[:500],
                )
            )

    return "\n".join(lines)

def super_admin_read_only_support_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_staff_open_btn", language),
                    callback_data="SA_RO_SUPPORT_LIST:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "support_staff_in_progress_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_SUPPORT_LIST:in_progress:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "support_staff_resolved_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_SUPPORT_LIST:resolved:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_MENU",
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

def super_admin_read_only_support_list_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_staff_open_btn", language),
                callback_data="SA_RO_SUPPORT_LIST:open:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "support_staff_in_progress_btn",
                    language,
                ),
                callback_data=(
                    "SA_RO_SUPPORT_LIST:in_progress:0"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "support_staff_resolved_btn",
                    language,
                ),
                callback_data=(
                    "SA_RO_SUPPORT_LIST:resolved:0"
                ),
            )
        ],
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_SUPPORT_LIST:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_SUPPORT_LIST:{view}:{page + 1}"
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
                        "super_admin_ro_support_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_SUPPORT_HOME",
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

def super_admin_read_only_admin_support_keyboard(
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
                callback_data=f"SA_RO_ADMIN_SUPPORT:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_ADMIN_SUPPORT:{page + 1}",
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
                    callback_data="SA_RO_ADMIN_HOME",
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

def format_super_admin_read_only_support_ticket(
    ticket,
    *,
    number: int,
    language: str,
) -> str:
    updated_at = (
        ticket.updated_at.strftime("%Y-%m-%d %H:%M")
        if ticket.updated_at
        else "-"
    )

    return t(
        "super_admin_ro_support_ticket_item",
        language,
    ).format(
        number=number,
        category=t(
            f"support_category_{ticket.category or 'other'}",
            language,
        ),
        status=t(
            f"support_status_{ticket.status}",
            language,
        ),
        priority=t(
            f"support_priority_{(ticket.priority or 'P3').lower()}",
            language,
        ),
        updated_at=updated_at,
    )

def format_super_admin_read_only_support_ticket_detail(
    ticket_view,
    *,
    number: int,
    language: str,
) -> str:
    ticket = ticket_view.ticket
    messages = ticket_view.messages[-10:]

    lines = [
        t(
            "super_admin_ro_support_ticket_title",
            language,
        ).format(number=number),
        "",
        f"{t('admin_support_category', language)}: "
        f"{t(f'support_category_{ticket.category or 'other'}', language)}",
        f"{t('admin_status', language)}: "
        f"{t(f'support_status_{ticket.status}', language)}",
        f"{t('admin_support_priority', language)}: "
        f"{t(f'support_priority_{(ticket.priority or 'P3').lower()}', language)}",
        "",
        t("admin_support_messages", language),
    ]

    if not messages:
        lines.append(
            t("admin_support_no_messages", language)
        )

    for message in messages:
        sender_role = message.sender_role or "system"
        message_text = (
            (message.message_text or "").strip()
            or t("super_admin_value_not_specified", language)
        )

        lines.append(
            t("support_message_line", language).format(
                sender_role=t(
                    f"support_sender_{sender_role}",
                    language,
                ),
                message=message_text[:500],
            )
        )

    lines.extend(
        [
            "",
            t("super_admin_ro_read_only_label", language),
        ]
    )

    return "\n".join(lines)

async def show_super_admin_support_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    target_role = data.get(
        "super_admin_impersonation_target_role"
    )
    is_read_only = bool(
        data.get("super_admin_impersonation_read_only")
    )

    if (
        not target_user_id_raw
        or target_role != "support"
        or not is_read_only
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cabinet = await AdminSupportService(
                session
            ).open_impersonated_support_cabinet(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_staff_user_id=(
                    target_user_id
                ),
            )

    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_support_cabinet",
            language,
        ).format(
            user_number=cabinet.user_number,
            open_tickets=cabinet.open_tickets,
            in_progress_tickets=(
                cabinet.in_progress_tickets
            ),
            resolved_tickets=(
                cabinet.resolved_tickets
            ),
        ),
        reply_markup=(
            super_admin_read_only_support_menu_keyboard(
                language
            )
        ),
    )

@admin_support_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_SUPPORT:")
)
async def super_admin_read_only_admin_support(
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
        ) != "admin"
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
            ticket_page = await AdminSupportService(
                session
            ).list_impersonated_admin_tickets(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                page=page,
                page_size=(
                    ADMIN_ESCALATED_TICKET_PAGE_SIZE
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_admin_support_ticket_ids=[
            str(ticket.id)
            for ticket in ticket_page.tickets
        ],
        super_admin_impersonation_admin_support_page=(
            ticket_page.page
        ),
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "support_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "admin_escalated_tickets_header",
            language,
        ).format(
            page=ticket_page.page + 1,
            count=len(ticket_page.tickets),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not ticket_page.tickets:
        empty_message = await callback.message.answer(
            t(
                "admin_escalated_tickets_empty",
                language,
            ),
            reply_markup=(
                super_admin_read_only_admin_support_keyboard(
                    page=ticket_page.page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            super_admin_ro_admin_support_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    for index, ticket in enumerate(
        ticket_page.tickets
    ):
        number = (
            ticket_page.page
            * ADMIN_ESCALATED_TICKET_PAGE_SIZE
            + index
            + 1
        )

        card_message = await callback.message.answer(
            format_admin_escalated_ticket(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_user_open_btn",
                                language,
                            ),
                            callback_data=(
                                "SA_RO_ADMIN_SUPPORT_OPEN:"
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
            super_admin_read_only_admin_support_keyboard(
                page=ticket_page.page,
                has_next=ticket_page.has_next,
                language=language,
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_ro_admin_support_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()

@admin_support_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_SUPPORT_OPEN:")
)
async def super_admin_read_only_admin_support_open(
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

    ticket_ids = data.get(
        "super_admin_impersonation_admin_support_ticket_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(ticket_ids)
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
        ticket_id = UUID(ticket_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            ticket_view = await AdminSupportService(
                session
            ).get_impersonated_admin_ticket(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                ticket_id=ticket_id,
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_admin_support_page"
        ) or 0
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "support_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_support_ticket_card(
            ticket_view,
            index=index,
            total=len(ticket_ids),
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_admin_back_to_tickets_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_ADMIN_SUPPORT:"
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

@admin_support_router.callback_query(F.data == "SA_RO_SUPPORT_HOME")
async def super_admin_read_only_support_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_support_"
            "ticket_message_ids"
        ),
        preserve_current=True,
    )

    await show_super_admin_support_read_only_cabinet(
        callback,
        state,
    )

@admin_support_router.callback_query(
    F.data.startswith("SA_RO_SUPPORT_LIST:")
)
async def super_admin_read_only_support_list(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, view, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if view not in {"open", "in_progress", "resolved"}:
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
        ) != "support"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminSupportService(
                session
            ).list_impersonated_staff_tickets(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_staff_user_id=(
                    target_user_id
                ),
                statuses={view},
                view=view,
                page=page,
                page_size=SUPPORT_STAFF_PAGE_SIZE,
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_tickets = list(result.tickets)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        super_admin_impersonation_support_ticket_ids=[
            str(ticket.id)
            for ticket in visible_tickets
        ],
        super_admin_impersonation_support_view=view,
        super_admin_impersonation_support_page=page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_support_"
            "ticket_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "super_admin_ro_support_list_title",
            language,
        ).format(
            view=support_staff_view_label(
                view,
                language,
            ),
            page=page + 1,
            count=len(visible_tickets),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    start_number = (
        page
        * SUPPORT_STAFF_PAGE_SIZE
        + 1
    )

    for index, ticket in enumerate(
        visible_tickets
    ):
        number = start_number + index

        card_message = await callback.message.answer(
            format_super_admin_read_only_support_ticket(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_support_open_ticket_btn",
                                language,
                            ).format(
                                number=number
                            ),
                            callback_data=(
                                "SA_RO_SUPPORT_OPEN:"
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
            super_admin_read_only_support_list_keyboard(
                view=view,
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
        super_admin_ro_support_ticket_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()

@admin_support_router.callback_query(
    F.data.startswith("SA_RO_SUPPORT_OPEN:")
)
async def super_admin_read_only_support_open_ticket(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

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

    data = await state.get_data()
    ticket_ids = data.get(
        "super_admin_impersonation_support_ticket_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "support"
        or index < 0
        or index >= len(ticket_ids)
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
        ticket_id = UUID(ticket_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            ticket_view = await AdminSupportService(
                session
            ).get_impersonated_staff_ticket(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_staff_user_id=(
                    target_user_id
                ),
                ticket_id=ticket_id,
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_support_page"
        ) or 0
    )
    view = str(
        data.get(
            "super_admin_impersonation_support_view"
        ) or "open"
    )
    number = page * SUPPORT_STAFF_PAGE_SIZE + index + 1

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_support_"
            "ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_read_only_support_ticket_detail(
                ticket_view,
                number=number,
                language=language,
            )
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_support_back_to_list_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_SUPPORT_LIST:"
                            f"{view}:{page}"
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

@admin_support_router.callback_query(
    (F.data == "ADM_ADMIN_SUPPORT")
    | F.data.startswith("ADM_ADMIN_SUPPORT:")
)
async def open_admin_escalated_tickets(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    page = 0

    if callback.data != "ADM_ADMIN_SUPPORT":
        try:
            page = max(
                int(callback.data.split(":", 1)[1]),
                0,
            )
        except (TypeError, ValueError, IndexError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    try:
        async with get_session() as session:
            ticket_page = await AdminSupportService(
                session
            ).list_admin_escalated_tickets(
                platform_user_id=(
                    callback.from_user.id
                ),
                page=page,
                page_size=(
                    ADMIN_ESCALATED_TICKET_PAGE_SIZE
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_support_ticket_ids=[
            str(ticket.id)
            for ticket in ticket_page.tickets
        ],
        admin_support_view="admin_escalated",
        admin_support_page=ticket_page.page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_escalated_ticket_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "admin_escalated_tickets_header",
            language,
        ).format(
            page=ticket_page.page + 1,
            count=len(ticket_page.tickets),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not ticket_page.tickets:
        empty_message = await callback.message.answer(
            t(
                "admin_escalated_tickets_empty",
                language,
            ),
            reply_markup=(
                admin_escalated_tickets_keyboard(
                    page=ticket_page.page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            admin_escalated_ticket_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    for index, ticket in enumerate(
        ticket_page.tickets
    ):
        number = (
            ticket_page.page
            * ADMIN_ESCALATED_TICKET_PAGE_SIZE
            + index
            + 1
        )

        card_message = await callback.message.answer(
            format_admin_escalated_ticket(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=(
                admin_escalated_ticket_item_keyboard(
                    index=index,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "admin_escalated_tickets_actions",
            language,
        ),
        reply_markup=admin_escalated_tickets_keyboard(
            page=ticket_page.page,
            has_next=ticket_page.has_next,
            language=language,
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_escalated_ticket_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()

@admin_support_router.callback_query(
    F.data.startswith("ADM_ADMIN_SUPPORT_OPEN:")
)
async def open_admin_escalated_ticket_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    ticket_ids = data.get("admin_support_ticket_ids") or []
    page = int(data.get("admin_support_page") or 0)

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if index < 0 or index >= len(ticket_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            view = await AdminSupportService(
                session
            ).get_admin_escalated_ticket(
                platform_user_id=(
                    callback.from_user.id
                ),
                ticket_id=UUID(ticket_ids[index]),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except (ValueError, SupportServiceError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_escalated_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_support_ticket_card(
            view,
            index=index,
            total=len(ticket_ids),
            language=language,
        ),
        reply_markup=(
            admin_escalated_ticket_card_keyboard(
                index=index,
                page=page,
                language=language,
            )
        ),
    )

@admin_support_router.callback_query(
    F.data.startswith("ADM_ADMIN_TICKET_ACTION:")
)
async def ask_admin_ticket_action_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    parts = (callback.data or "").split(":")

    if len(parts) != 3 or parts[1] not in {
        "assign",
        "resolve",
    }:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    action = parts[1]

    try:
        index = int(parts[2])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    ticket_ids = data.get("admin_support_ticket_ids") or []

    if index < 0 or index >= len(ticket_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).require_admin_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_ticket_action=action,
        admin_ticket_action_index=index,
        admin_ticket_action_id=ticket_ids[index],
    )
    await state.set_state(
        AdminSupportFSM.entering_admin_ticket_action_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_ticket_action_reason_prompt",
            language,
        ).format(
            action=t(
                f"admin_ticket_action_{action}",
                language,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data=(
                            "ADM_ADMIN_SUPPORT_OPEN:"
                            f"{index}"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_support_router.message(
    AdminSupportFSM.entering_admin_ticket_action_reason
)
async def execute_admin_ticket_action(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_reason_too_short",
                language,
            ),
        )
        return

    data = await state.get_data()
    action = data.get("admin_ticket_action")
    ticket_id = data.get("admin_ticket_action_id")
    index = int(data.get("admin_ticket_action_index") or 0)
    page = int(data.get("admin_support_page") or 0)

    if (
        action not in {"assign", "resolve"}
        or not ticket_id
    ):
        await state.set_state(None)
        await state.update_data(
            admin_ticket_action=None,
            admin_ticket_action_id=None,
            admin_ticket_action_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
        )
        return

    try:
        async with get_session() as session:
            action_result = await AdminSupportService(
                session
            ).execute_admin_action(
                platform_user_id=(
                    message.from_user.id
                ),
                ticket_id=UUID(ticket_id),
                action=action,
                reason=reason,
            )
            ticket = action_result.result
    except AdminSupportAccessError:
        await state.set_state(None)
        await state.update_data(
            admin_ticket_action=None,
            admin_ticket_action_id=None,
            admin_ticket_action_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        return
    except (ValueError, SupportServiceError) as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "support_error",
                language,
            ).format(
                error=str(exc),
            ),
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_ticket_action=None,
        admin_ticket_action_id=None,
        admin_ticket_action_index=None,
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_ticket_action_completed",
            language,
        ).format(
            action=t(
                f"admin_ticket_action_{action}",
                language,
            ),
            status=t(
                f"support_status_{ticket.status}",
                language,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data=(
                            "ADM_ADMIN_SUPPORT:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_support_router.callback_query(F.data == "ADM_SUPPORT")
async def open_support_staff_menu(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    try:
        async with get_session() as session:
            menu = await AdminSupportService(
                session
            ).open_staff_menu(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
            counts = menu.counts
            show_role_switch = (
                menu.show_role_switch
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_support_staff_menu(
            counts,
            language,
        ),
        reply_markup=support_staff_menu_keyboard(
            language,
            show_role_switch=show_role_switch,
        ),
    )

@admin_support_router.callback_query(F.data == "ADM_SUPPORT_SEARCH")
async def ask_support_ticket_search(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).require_staff_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await state.set_state(
        AdminSupportFSM.entering_support_search
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "support_staff_search_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "support_staff_back_to_panel",
                            language,
                        ),
                        callback_data="ADM_SUPPORT",
                    )
                ]
            ]
        ),
    )

@admin_support_router.message(
    AdminSupportFSM.entering_support_search
)
async def receive_support_ticket_search(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()

    try:
        async with get_session() as session:
            result = await AdminSupportService(
                session
            ).search_staff_tickets(
                platform_user_id=(
                    message.from_user.id
                ),
                query=query,
                limit=SUPPORT_STAFF_PAGE_SIZE,
            )
            tickets = list(result.tickets)
    except AdminSupportAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        return
    except SupportServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "support_error",
                language,
            ).format(
                error=str(exc),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "support_staff_back_to_panel",
                                language,
                            ),
                            callback_data="ADM_SUPPORT",
                        )
                    ]
                ]
            ),
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_support_ticket_ids=[
            str(ticket.id)
            for ticket in tickets
        ],
        admin_support_view="search",
        admin_support_page=0,
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_support_staff_search_header(
            tickets,
            query=query,
            language=language,
        ),
    )

    screen_data = await state.get_data()
    rendered_message_ids: list[int] = []

    header_message_id = screen_data.get(
        "last_menu_message_id"
    )

    if isinstance(header_message_id, int):
        rendered_message_ids.append(
            header_message_id
        )

    for index, ticket in enumerate(tickets):
        card_message = await message.answer(
            format_support_staff_ticket_card(
                ticket,
                number=index + 1,
                language=language,
            ),
            reply_markup=(
                support_staff_ticket_card_keyboard(
                    index=index,
                    ticket=ticket,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await message.answer(
        t(
            "support_staff_list_actions",
            language,
        ),
        reply_markup=(
            support_staff_ticket_actions_keyboard(
                view="open",
                page=0,
                has_next=False,
                language=language,
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        support_staff_ticket_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

@admin_support_router.callback_query(F.data == "ADM_SUPPORT_FILTERS")
async def show_support_ticket_filters(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).require_staff_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "support_staff_filters_title",
            language,
        ),
        reply_markup=(
            support_staff_filters_keyboard(
                language
            )
        ),
    )

@admin_support_router.callback_query(F.data.startswith("ADM_SUPPORT_VIEW:"))
async def list_support_tickets_by_status(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    try:
        _, view, page_raw = (callback.data or "").split(":", 2)
        page = max(0, int(page_raw))
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    statuses = support_staff_status_filter(view)

    try:
        async with get_session() as session:
            result = await AdminSupportService(
                session
            ).list_staff_tickets(
                platform_user_id=(
                    callback.from_user.id
                ),
                statuses=statuses,
                view=view,
                page=page,
                page_size=SUPPORT_STAFF_PAGE_SIZE,
            )
            visible_tickets = list(
                result.tickets
            )
            page = result.page
            has_next = result.has_next
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        admin_support_ticket_ids=[str(ticket.id) for ticket in visible_tickets],
        admin_support_view=view,
        admin_support_page=page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        format_support_staff_ticket_header(
            visible_tickets,
            view=view,
            page=page,
            has_next=has_next,
            language=language,
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    for index, ticket in enumerate(
        visible_tickets
    ):
        number = (
            page
            * SUPPORT_STAFF_PAGE_SIZE
            + index
            + 1
        )

        card_message = await callback.message.answer(
            format_support_staff_ticket_card(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=(
                support_staff_ticket_card_keyboard(
                    index=index,
                    ticket=ticket,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "support_staff_list_actions",
            language,
        ),
        reply_markup=(
            support_staff_ticket_actions_keyboard(
                view=view,
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
        support_staff_ticket_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()

@admin_support_router.callback_query(F.data.startswith("ADM_SUP_TAKE:"))
async def take_support_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    ticket_id = UUID(ids[index])

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).assign_staff_ticket(
                platform_user_id=(
                    callback.from_user.id
                ),
                ticket_id=ticket_id,
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "support_staff_ticket_taken",
            language,
        ),
        reply_markup=(
            support_staff_ticket_actions_keyboard(
                view=(
                    data.get("admin_support_view")
                    or "open"
                ),
                page=int(
                    data.get("admin_support_page")
                    or 0
                ),
                has_next=False,
                language=language,
            )
        ),
    )

async def show_support_ticket(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    if not ids:
        await clear_admin_message_group(
            callback=callback,
            state=state,
            state_key=(
                "support_staff_ticket_message_ids"
            ),
            preserve_current=True,
        )

        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_support_tickets",
                language,
            ),
            reply_markup=support_staff_menu_keyboard(
                language
            ),
        )
        return

    index = max(0, min(int(index), len(ids) - 1))
    try:
        async with get_session() as session:
            view = await AdminSupportService(
                session
            ).get_staff_ticket(
                platform_user_id=(
                    callback.from_user.id
                ),
                ticket_id=UUID(ids[index]),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_support_ticket_card(
            view,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=support_ticket_keyboard(
            index,
            len(ids),
            language,
        ),
    )

@admin_support_router.callback_query(
    F.data.startswith("ADM_SUP_VIEW:")
)
async def view_support_ticket(
    callback: CallbackQuery,
    state: FSMContext,
):
    index = int(
        callback.data.split(":", 1)[1]
    )

    await state.set_state(None)

    await show_support_ticket(
        callback,
        state,
        index=index,
    )

@admin_support_router.callback_query(F.data.startswith("ADM_SUP_REPLY:"))
async def ask_support_reply(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_support_ticket_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).require_staff_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_support_ticket_id=ids[index],
        admin_support_ticket_index=index,
    )
    await state.set_state(
        AdminSupportFSM.entering_support_reply
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_support_reply_prompt",
            language,
        ),
    )

@admin_support_router.message(
    AdminSupportFSM.entering_support_reply
)
async def receive_support_reply(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(
        message.from_user.language_code
    )

    ticket_id = data.get(
        "admin_support_ticket_id"
    )
    index = int(
        data.get(
            "admin_support_ticket_index"
        )
        or 0
    )

    if not ticket_id:
        await state.set_state(None)
        await state.update_data(
            admin_support_ticket_id=None,
            admin_support_ticket_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
        )
        return

    try:
        async with get_session() as session:
            action_result = await AdminSupportService(
                session
            ).add_staff_reply(
                platform_user_id=(
                    message.from_user.id
                ),
                ticket_id=UUID(ticket_id),
                message_text=message.text or "",
            )
            reply_result = action_result.result
    except AdminSupportAccessError:
        await state.set_state(None)
        await state.update_data(
            admin_support_ticket_id=None,
            admin_support_ticket_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        return
    except SupportServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "support_error",
                language,
            ).format(
                error=str(exc),
            ),
        )
        return

    if reply_result.recipient_chat_id is not None:
        try:
            await message.bot.send_message(
                chat_id=(
                    reply_result.recipient_chat_id
                ),
                text=t(
                    "support_staff_reply_received",
                    language,
                ).format(
                    ticket_id=str(ticket_id)[:8],
                    message=message.text or "",
                ),
            )
        except Exception:
            logger.exception(
                "support_reply_notification_failed "
                "ticket_id=%s",
                ticket_id,
            )

    await state.set_state(None)
    await state.update_data(
        admin_support_ticket_id=None,
        admin_support_ticket_index=None,
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_support_reply_sent",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "support_staff_back_to_queue",
                            language,
                        ),
                        callback_data="ADM_SUPPORT",
                    )
                ]
            ]
        ),
    )

async def update_support_ticket_status_from_admin(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_support_ticket_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    ticket_id = UUID(ids[index])

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).update_staff_ticket_status(
                platform_user_id=(
                    callback.from_user.id
                ),
                ticket_id=ticket_id,
                status=status,
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    view = data.get("admin_support_view") or "in_progress"
    page = int(data.get("admin_support_page") or 0)

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_support_status_updated",
            language,
        ).format(
            status=status,
        ),
        reply_markup=(
            support_staff_ticket_actions_keyboard(
                view=view,
                page=page,
                has_next=False,
                language=language,
            )
        ),
    )

@admin_support_router.callback_query(F.data.startswith("ADM_SUP_ASSIGN:"))
async def assign_support_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    ticket_id = UUID(ids[index])

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).assign_staff_ticket(
                platform_user_id=(
                    callback.from_user.id
                ),
                ticket_id=ticket_id,
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "support_staff_ticket_taken",
            language,
        ),
        reply_markup=(
            support_staff_ticket_actions_keyboard(
                view=(
                    data.get("admin_support_view")
                    or "open"
                ),
                page=int(
                    data.get("admin_support_page")
                    or 0
                ),
                has_next=False,
                language=language,
            )
        ),
    )

@admin_support_router.callback_query(F.data.startswith("ADM_SUP_ESCALATE:"))
async def ask_support_ticket_escalation_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).require_staff_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_support_ticket_id=ids[index],
        admin_support_ticket_index=index,
    )
    await state.set_state(
        AdminSupportFSM
        .entering_support_escalation_reason
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_support_escalate_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_back",
                            language,
                        ),
                        callback_data=(
                            "ADM_SUP_VIEW:"
                            f"{index}"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_support_router.message(
    AdminSupportFSM
    .entering_support_escalation_reason
)
async def receive_support_ticket_escalation_reason(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(
        message.from_user.language_code
    )

    ticket_id = data.get(
        "admin_support_ticket_id"
    )
    view = (
        data.get("admin_support_view")
        or "open"
    )
    page = int(
        data.get("admin_support_page")
        or 0
    )

    if not ticket_id:
        await state.set_state(None)
        await state.update_data(
            admin_support_ticket_id=None,
            admin_support_ticket_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
        )
        return

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).escalate_staff_ticket(
                platform_user_id=(
                    message.from_user.id
                ),
                ticket_id=UUID(ticket_id),
                reason=message.text or "",
            )
    except AdminSupportAccessError:
        await state.set_state(None)
        await state.update_data(
            admin_support_ticket_id=None,
            admin_support_ticket_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        return
    except SupportServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "support_error",
                language,
            ).format(
                error=str(exc),
            ),
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_support_ticket_id=None,
        admin_support_ticket_index=None,
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_support_escalated",
            language,
        ),
        reply_markup=(
            support_staff_ticket_actions_keyboard(
                view=view,
                page=page,
                has_next=False,
                language=language,
            )
        ),
    )

@admin_support_router.callback_query(F.data.startswith("ADM_SUP_RESOLVE:"))
async def resolve_support_ticket(callback: CallbackQuery, state: FSMContext):
    await update_support_ticket_status_from_admin(
        callback,
        state,
        status="resolved",
    )

@admin_support_router.callback_query(F.data.startswith("ADM_SUP_CLOSE:"))
async def close_support_ticket(callback: CallbackQuery, state: FSMContext):
    await update_support_ticket_status_from_admin(
        callback,
        state,
        status="closed",
    )

@admin_support_router.callback_query(F.data == "ADM_SUPPORT_STATS")
async def show_support_staff_stats(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            action_result = await AdminSupportService(
                session
            ).get_staff_stats(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
            stats = action_result.result
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_support_staff_stats(
            stats,
            language,
        ),
        reply_markup=(
            support_staff_stats_keyboard(
                language
            )
        ),
    )

@admin_support_router.callback_query(F.data.in_({"ADM_SUPPORT_STATS_PERIOD", "ADM_SUPPORT_STATS_CATEGORY"}))
async def support_staff_stats_filter_pending(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminSupportService(
                session
            ).require_stats_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminSupportAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await callback.answer(
        t("support_staff_stats_filter_later", language),
        show_alert=True,
    )
