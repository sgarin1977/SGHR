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
    MODERATOR_PROFILE_PAGE_SIZE,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_complaints import (
    AdminComplaintsAccessError,
    AdminComplaintsService,
)
from services.moderation import (
    ModerationError,
    ModeratorComplaintCard,
    ModeratorComplaintQueueCard,
)
from ui.texts import t


logger = logging.getLogger(__name__)

admin_complaints_router = Router()
normalize_language = normalize_admin_language


class AdminComplaintsFSM(StatesGroup):
    entering_complaint_resolution_reason = State()
    entering_complaint_admin_reason = State()
    confirming_complaint_admin = State()


def complaints_empty_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="ADM_MENU",
                )
            ]
        ]
    )


def format_complaint_queue_item(
    card: ModeratorComplaintQueueCard,
    *,
    number: int,
    language: str,
) -> str:
    created_at = (
        card.created_at.strftime("%Y-%m-%d")
        if card.created_at
        else "-"
    )

    escalation = (
        f"\n{t('moderator_complaint_admin_target', language)}"
        if card.requires_admin_escalation
        else ""
    )

    conversation_context = (
        "\n"
        + t(
            "moderator_complaint_conversation_context",
            language,
        )
        if card.has_conversation_context
        else ""
    )

    return t(
        "moderator_complaint_queue_item",
        language,
    ).format(
        number=number,
        reporter=card.reporter_label,
        target=(
            card.target_label
            + conversation_context
        ),
        reason=card.reason,
        status=card.status,
        date=created_at,
        escalation=escalation,
    )
def format_complaints_queue_screen(
    cards: list[ModeratorComplaintQueueCard],
    *,
    page: int,
    language: str,
) -> str:
    header = t(
        "moderator_complaint_queue_title",
        language,
    ).format(
        count=len(cards),
    )

    if not cards:
        return (
            f"{header}\n\n"
            f"{t('admin_no_open_complaints', language)}"
        )

    start_number = page * 5 + 1

    rendered_cards = [
        format_complaint_queue_item(
            card,
            number=start_number + offset,
            language=language,
        )
        for offset, card in enumerate(cards)
    ]

    return "\n\n".join(
        [
            header,
            *rendered_cards,
        ]
    )
def complaints_queue_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("moderator_complaint_filter_btn", language),
                callback_data="ADM_CP_FILTER",
            )
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_CP_QUEUE:{view}:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_CP_QUEUE:{view}:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("moderator_back_btn", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )
def complaints_queue_screen_keyboard(
    cards: list[ModeratorComplaintQueueCard],
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    start_number = page * 5 + 1

    for index, card in enumerate(cards):
        number = start_number + index

        item_row = [
            InlineKeyboardButton(
                text=(
                    f"{number}. "
                    + t(
                        "moderator_open_btn",
                        language,
                    )
                ),
                callback_data=(
                    f"ADM_CP_VIEW:{index}"
                ),
            )
        ]

        if (
            card.status == "new"
            and not card.requires_admin_escalation
        ):
            item_row.append(
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_take_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_CP_TAKE:{index}"
                    ),
                )
            )

        rows.append(item_row)

    queue_keyboard = complaints_queue_keyboard(
        view=view,
        page=page,
        has_next=has_next,
        language=language,
    )

    rows.extend(
        queue_keyboard.inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )
def complaints_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_open", language),
                    callback_data="ADM_CP_QUEUE:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_new", language),
                    callback_data="ADM_CP_QUEUE:new:0",
                ),
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_review", language),
                    callback_data="ADM_CP_QUEUE:in_review:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_resolved", language),
                    callback_data="ADM_CP_QUEUE:resolved:0",
                ),
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_rejected", language),
                    callback_data="ADM_CP_QUEUE:rejected:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_back_btn", language),
                    callback_data="ADM_COMPLAINTS",
                )
            ],
        ]
    )
def complaint_keyboard(
    *,
    index: int,
    total: int,
    status: str,
    requires_admin_escalation: bool,
    view: str,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if status == "in_review":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_resolve_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_RESOLVE:{index}",
                ),
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_reject_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_REJECT:{index}",
                ),
            ]
        )

    elif status == "new":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_reject_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_REJECT:{index}",
                )
            ]
        )

    if (
        status in {"new", "in_review"}
        and not requires_admin_escalation
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_scoped_blacklist_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_CP_SCOPED_BLOCK:{index}"
                    ),
                )
            ]
        )

    if status in {"new", "in_review"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_admin_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_ADMIN:{index}",
                )
            ]
        )

    nav = []

    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_CP_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_CP_VIEW:{index + 1}",
            )
        )

    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_back_queue_btn",
                    language,
                ),
                callback_data=(
                    f"ADM_CP_QUEUE:{view}:{page}"
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )
def complaint_resolution_reason_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_changes_cancel_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_CP_VIEW:{index}"
                    ),
                )
            ]
        ]
    )
def complaint_resolution_result_keyboard(
    *,
    view: str,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_back_queue_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_CP_QUEUE:{view}:{page}"
                    ),
                )
            ]
        ]
    )
def format_complaint_card(
    card: ModeratorComplaintCard,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    comment = (
        card.comment
        or t(
            "admin_no_comment",
            language,
        )
    )

    history = (
        "\n".join(card.history)
        if card.history
        else t(
            "moderator_complaint_history_empty",
            language,
        )
    )

    target_type_labels = {
        "specialist": t(
            "complaint_target_specialist",
            language,
        ),
        "professional_cabinet": t(
            "complaint_target_professional_cabinet",
            language,
        ),
        "user": t(
            "complaint_target_user",
            language,
        ),
        "message": t(
            "complaint_target_message",
            language,
        ),
        "thread": t(
            "complaint_target_dialog",
            language,
        ),
        "contact_request": t(
            "complaint_target_contact_request",
            language,
        ),
        "review": t(
            "complaint_target_review",
            language,
        ),
        "portfolio_item": t(
            "complaint_target_portfolio",
            language,
        ),
    }
    target_type_label = (
        target_type_labels.get(
            card.target_type,
            card.target_type.replace(
                "_",
                " ",
            ).title(),
        )
    )

    reason_labels = {
        "fake": t(
            "complaint_reason_fake",
            language,
        ),
        "contact": t(
            "complaint_reason_contact",
            language,
        ),
        "abuse": t(
            "complaint_reason_abuse",
            language,
        ),
        "other": t(
            "complaint_reason_other",
            language,
        ),
    }
    reason_label = reason_labels.get(
        card.reason,
        card.reason,
    )

    escalation = ""

    if card.requires_admin_escalation:
        escalation = (
            "\n\n"
            + t(
                "moderator_complaint_admin_target",
                language,
            )
        )
    conversation_context = (
        "\n"
        + t(
            "moderator_complaint_conversation_context",
            language,
        )
        if card.has_conversation_context
        else ""
    )
    return t(
        "moderator_complaint_card",
        language,
    ).format(
        index=index + 1,
        total=total,
        reporter=card.reporter_label,
        target=(
            card.target_label
            + conversation_context
        ),
        target_type=target_type_label,
        status=card.status,
        reason=reason_label,
        comment=comment,
        date=card.created_at.strftime(
            "%Y-%m-%d"
        ),
        history=history,
        escalation=escalation,
    )
def format_super_admin_read_only_moderator_complaints_screen(
    cards: list[ModeratorComplaintQueueCard],
    *,
    view_label: str,
    page: int,
    language: str,
) -> str:
    header = t(
        "super_admin_ro_moderator_complaints_title",
        language,
    ).format(
        view=view_label,
        page=page + 1,
        count=len(cards),
    )

    start_number = (
        page * MODERATOR_PROFILE_PAGE_SIZE + 1
    )

    rendered_cards = [
        format_complaint_queue_item(
            card,
            number=start_number + index,
            language=language,
        )
        for index, card in enumerate(cards)
    ]

    return "\n\n".join(
        [
            header,
            *rendered_cards,
            t(
                "super_admin_ro_read_only_label",
                language,
            ),
        ]
    )
def super_admin_read_only_moderator_complaints_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_open",
                    language,
                ),
                callback_data="SA_RO_MOD_COMPLAINTS:open:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_new",
                    language,
                ),
                callback_data="SA_RO_MOD_COMPLAINTS:new:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_review",
                    language,
                ),
                callback_data=(
                    "SA_RO_MOD_COMPLAINTS:in_review:0"
                ),
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_resolved",
                    language,
                ),
                callback_data=(
                    "SA_RO_MOD_COMPLAINTS:resolved:0"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_rejected",
                    language,
                ),
                callback_data=(
                    "SA_RO_MOD_COMPLAINTS:rejected:0"
                ),
            )
        ],
    ]

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_MOD_COMPLAINTS:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_MOD_COMPLAINTS:{view}:{page + 1}"
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
                        "super_admin_ro_moderator_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_HOME",
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
def super_admin_read_only_moderator_complaints_screen_keyboard(
    *,
    items_count: int,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for index in range(items_count):
        number = (
            page
            * MODERATOR_PROFILE_PAGE_SIZE
            + index
            + 1
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_open_complaint_btn",
                        language,
                    ).format(
                        number=number,
                    ),
                    callback_data=(
                        f"SA_RO_MOD_COMPLAINT:{index}"
                    ),
                )
            ]
        )

    queue_keyboard = (
        super_admin_read_only_moderator_complaints_keyboard(
            view=view,
            page=page,
            has_next=has_next,
            language=language,
        )
    )

    rows.extend(
        queue_keyboard.inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )
@admin_complaints_router.callback_query(
    F.data.startswith("SA_RO_MOD_COMPLAINTS:")
)
async def super_admin_read_only_moderator_complaints(
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

    statuses_by_view = {
        "open": {"new", "in_review"},
        "new": {"new"},
        "in_review": {"in_review"},
        "resolved": {"resolved"},
        "rejected": {"rejected"},
    }

    if view not in statuses_by_view:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if not data.get(
        "super_admin_impersonation_read_only"
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
            results = await AdminComplaintsService(
                session
            ).open_impersonated_complaints_queue(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                statuses=statuses_by_view[view],
                page=page,
                page_size=(
                    MODERATOR_PROFILE_PAGE_SIZE
                ),
            )
    except (
        AdminComplaintsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    cards = results[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(results) > MODERATOR_PROFILE_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_moderator_complaint_ids=[
            str(card.complaint_id)
            for card in cards
        ],
        super_admin_impersonation_moderator_complaint_view=view,
        super_admin_impersonation_moderator_complaint_page=page,
    )

    view_labels = {
        "open": t("moderator_complaint_filter_open", language),
        "new": t("moderator_complaint_filter_new", language),
        "in_review": t(
            "moderator_complaint_filter_review",
            language,
        ),
        "resolved": t(
            "moderator_complaint_filter_resolved",
            language,
        ),
        "rejected": t(
            "moderator_complaint_filter_rejected",
            language,
        ),
    }

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_read_only_moderator_complaints_screen(
                cards,
                view_label=view_labels[view],
                page=page,
                language=language,
            )
        ),
        reply_markup=(
            super_admin_read_only_moderator_complaints_screen_keyboard(
                items_count=len(cards),
                view=view,
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
@admin_complaints_router.callback_query(
    F.data.startswith("SA_RO_MOD_COMPLAINT:")
)
async def super_admin_read_only_moderator_complaint(
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

    complaint_ids = data.get(
        "super_admin_impersonation_moderator_complaint_ids"
    ) or []

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or index < 0
        or index >= len(complaint_ids)
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
        complaint_id = UUID(complaint_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminComplaintsService(
                session
            ).get_impersonated_complaint_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                complaint_id=complaint_id,
            )
    except (
        AdminComplaintsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_complaint_page"
        ) or 0
    )
    view = str(
        data.get(
            "super_admin_impersonation_moderator_complaint_view"
        ) or "open"
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_complaint_card(
            card,
            index=index,
            total=len(complaint_ids),
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_moderator_back_to_complaints_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_MOD_COMPLAINTS:{view}:{page}"
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
@admin_complaints_router.callback_query(F.data == "ADM_COMPLAINTS")
async def list_open_complaints(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_complaints_queue(
        callback,
        state,
        view="open",
        page=0,
    )
@admin_complaints_router.callback_query(F.data.startswith("ADM_CP_QUEUE:"))
async def change_complaints_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1
        else "open"
    )

    try:
        page = int(parts[2])
    except (IndexError, TypeError, ValueError):
        page = 0

    await open_complaints_queue(
        callback,
        state,
        view=view,
        page=max(page, 0),
    )
@admin_complaints_router.callback_query(
    F.data == "ADM_CP_FILTER"
)
async def show_complaints_filter(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_complaint_filter_title",
            language,
        ),
        reply_markup=(
            complaints_filter_keyboard(
                language
            )
        ),
    )
async def open_complaints_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
    callback_answered: bool = False,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    statuses_by_view = {
        "open": {"new", "in_review"},
        "new": {"new"},
        "in_review": {"in_review"},
        "resolved": {"resolved"},
        "rejected": {"rejected"},
    }
    statuses = statuses_by_view.get(
        view,
        {"new", "in_review"},
    )
    normalized_view = (
        view
        if view in statuses_by_view
        else "open"
    )

    try:
        async with get_session() as session:
            results = await AdminComplaintsService(
                session
            ).open_complaints_queue(
                platform_user_id=(
                    callback.from_user.id
                ),
                statuses=statuses,
                page=page,
                page_size=5,
            )

    except (
        AdminComplaintsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    cards = results[:5]
    has_next = len(results) > 5

    await state.update_data(
        admin_complaint_ids=[
            str(card.complaint_id)
            for card in cards
        ],
        admin_complaint_view=normalized_view,
        admin_complaint_page=page,
        admin_complaint_has_next=has_next,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        callback_answered=callback_answered,
        text=(
            format_complaints_queue_screen(
                cards,
                page=page,
                language=language,
            )
        ),
        reply_markup=(
            complaints_queue_screen_keyboard(
                cards,
                view=normalized_view,
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
@admin_complaints_router.callback_query(F.data.startswith("ADM_CP_TAKE:"))
async def take_complaint_from_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        index = -1

    complaint_ids = data.get("admin_complaint_ids") or []
    view = data.get("admin_complaint_view") or "open"
    page = int(data.get("admin_complaint_page") or 0)

    if index < 0 or index >= len(complaint_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminComplaintsService(
                session
            ).take_complaint(
                platform_user_id=(
                    callback.from_user.id
                ),
                complaint_id=UUID(
                    complaint_ids[index]
                ),
            )

    except (
        AdminComplaintsAccessError,
        ModerationError,
    ):
        await callback.answer(
            t(
                "moderator_complaint_take_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    await callback.answer(
        t(
            "moderator_complaint_taken",
            language,
        )
    )

    await open_complaints_queue(
        callback,
        state,
        view=view,
        page=page,
        callback_answered=True,
    )
async def show_complaint(
    callback: CallbackQuery,
    state: FSMContext,
    index: int,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    ids = data.get("admin_complaint_ids") or []

    view = (
        data.get("admin_complaint_view")
        or "open"
    )
    page = int(
        data.get("admin_complaint_page")
        or 0
    )

    if not ids:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_open_complaints",
                language,
            ),
            reply_markup=(
                complaints_empty_keyboard(
                    language
                )
            ),
        )
        return

    index = max(
        0,
        min(int(index), len(ids) - 1),
    )

    try:
        async with get_session() as session:
            card = await AdminComplaintsService(
                session
            ).get_complaint_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                complaint_id=UUID(ids[index]),
            )

    except (
        AdminComplaintsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_complaint_card(
                card,
                index=index,
                total=len(ids),
                language=language,
            )
        ),
        reply_markup=(
            complaint_keyboard(
                index=index,
                total=len(ids),
                status=card.status,
                requires_admin_escalation=(
                    card.requires_admin_escalation
                ),
                view=view,
                page=page,
                language=language,
            )
        ),
    )
@admin_complaints_router.callback_query(F.data.startswith("ADM_CP_VIEW:"))
async def view_complaint(
    callback: CallbackQuery,
    state: FSMContext,
):
    index = int(
        callback.data.split(":", 1)[1]
    )

    await state.set_state(None)
    await state.update_data(
        admin_complaint_id=None,
        admin_complaint_resolution_status=None,
        admin_complaint_resolution_index=None,
    )

    await show_complaint(
        callback,
        state,
        index=index,
    )
@admin_complaints_router.callback_query(F.data.startswith("ADM_CP_REVIEW:"))
async def ask_review_complaint_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    await prepare_complaint_resolution(
        callback,
        state,
        status="in_review",
    )
@admin_complaints_router.callback_query(F.data.startswith("ADM_CP_RESOLVE:"))
async def ask_resolve_complaint_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_complaint_resolution(callback, state, status="resolved")
@admin_complaints_router.callback_query(F.data.startswith("ADM_CP_REJECT:"))
async def ask_reject_complaint_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_complaint_resolution(callback, state, status="rejected")
async def prepare_complaint_resolution(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    index = int(
        callback.data.split(":", 1)[1]
    )
    ids = data.get(
        "admin_complaint_ids"
    ) or []

    if index < 0 or index >= len(ids):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_complaint_id=ids[index],
        admin_complaint_resolution_status=status,
        admin_complaint_resolution_index=index,
    )
    await state.set_state(
        AdminComplaintsFSM
        .entering_complaint_resolution_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_reason_prompt",
            language,
        ),
        reply_markup=(
            complaint_resolution_reason_keyboard(
                index=index,
                language=language,
            )
        ),
    )
@admin_complaints_router.message(
    AdminComplaintsFSM
    .entering_complaint_resolution_reason
)
async def receive_complaint_resolution_reason(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (
        message.text or ""
    ).strip()
    complaint_id = data.get(
        "admin_complaint_id"
    )
    status = (
        data.get(
            "admin_complaint_resolution_status"
        )
        or "resolved"
    )
    index = int(
        data.get(
            "admin_complaint_resolution_index"
        )
        or 0
    )
    view = (
        data.get("admin_complaint_view")
        or "open"
    )
    page = int(
        data.get("admin_complaint_page")
        or 0
    )

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('admin_reason_too_short', language)}\n\n"
                f"{t('admin_reason_prompt', language)}"
            ),
            reply_markup=(
                complaint_resolution_reason_keyboard(
                    index=index,
                    language=language,
                )
            ),
        )
        return

    if not complaint_id:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=(
                complaint_resolution_result_keyboard(
                    view=view,
                    page=page,
                    language=language,
                )
            ),
        )
        await state.set_state(None)
        await state.update_data(
            admin_complaint_id=None,
            admin_complaint_resolution_status=None,
            admin_complaint_resolution_index=None,
        )
        return

    admin_user_id = None

    try:
        complaint_uuid = UUID(
            str(complaint_id)
        )

        async with get_session() as session:
            action = await AdminComplaintsService(
                session
            ).resolve_complaint(
                platform_user_id=(
                    message.from_user.id
                ),
                complaint_id=complaint_uuid,
                status=status,
                reason=reason,
            )
            result = action.result
            admin_user_id = action.actor.user_id

        logger.info(
            "admin_complaint_updated "
            "telegram_id=%s "
            "admin_user_id=%s "
            "complaint_id=%s "
            "status=%s",
            message.from_user.id,
            admin_user_id,
            complaint_uuid,
            result.status,
        )

    except (
        AdminComplaintsAccessError,
        ValueError,
        ModerationError,
    ) as exc:
        logger.warning(
            "admin_complaint_update_failed "
            "telegram_id=%s "
            "admin_user_id=%s "
            "complaint_id=%s "
            "status=%s "
            "error=%s",
            message.from_user.id,
            admin_user_id,
            complaint_id,
            status,
            exc,
        )

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{exc}\n\n"
                f"{t('admin_reason_prompt', language)}"
            ),
            reply_markup=(
                complaint_resolution_reason_keyboard(
                    index=index,
                    language=language,
                )
            ),
        )
        return

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_complaint_updated",
            language,
        ).format(
            status=result.status,
        ),
        reply_markup=(
            complaint_resolution_result_keyboard(
                view=view,
                page=page,
                language=language,
            )
        ),
    )

    await state.set_state(None)
    await state.update_data(
        admin_complaint_id=None,
        admin_complaint_resolution_status=None,
        admin_complaint_resolution_index=None,
    )
@admin_complaints_router.callback_query(
    F.data.startswith("ADM_CP_ADMIN:")
)
async def ask_complaint_admin_reason(
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
    except (TypeError, ValueError):
        index = -1

    complaint_ids = (
        data.get("admin_complaint_ids")
        or []
    )
    view = (
        data.get("admin_complaint_view")
        or "open"
    )
    page = int(
        data.get("admin_complaint_page")
        or 0
    )

    if index < 0 or index >= len(complaint_ids):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_complaint_admin_id=(
            complaint_ids[index]
        ),
        moderator_complaint_admin_index=index,
        moderator_complaint_admin_view=view,
        moderator_complaint_admin_page=page,
    )
    await state.set_state(
        AdminComplaintsFSM
        .entering_complaint_admin_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_complaint_admin_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_ADMIN_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_complaints_router.message(
    AdminComplaintsFSM
    .entering_complaint_admin_reason
)
async def receive_complaint_admin_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (
        message.text or ""
    ).strip()
    data = await state.get_data()

    view = (
        data.get(
            "moderator_complaint_admin_view"
        )
        or "open"
    )
    page = int(
        data.get(
            "moderator_complaint_admin_page"
        )
        or 0
    )

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('admin_reason_too_short', language)}\n\n"
                f"{t('moderator_complaint_admin_reason_prompt', language)}"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "moderator_changes_cancel_btn",
                                language,
                            ),
                            callback_data=(
                                "ADM_CP_ADMIN_CANCEL"
                            ),
                        )
                    ]
                ]
            ),
        )
        return

    if not data.get(
        "moderator_complaint_admin_id"
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=(
                complaint_resolution_result_keyboard(
                    view=view,
                    page=page,
                    language=language,
                )
            ),
        )
        await state.set_state(None)
        await state.update_data(
            moderator_complaint_admin_id=None,
            moderator_complaint_admin_index=None,
            moderator_complaint_admin_reason=None,
            moderator_complaint_admin_view=None,
            moderator_complaint_admin_page=None,
        )
        return

    await state.update_data(
        moderator_complaint_admin_reason=reason,
    )
    await state.set_state(
        AdminComplaintsFSM
        .confirming_complaint_admin
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_complaint_admin_confirmation",
            language,
        ).format(
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_complaint_admin_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_ADMIN_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_ADMIN_EDIT"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_ADMIN_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )
@admin_complaints_router.callback_query(
    F.data == "ADM_CP_ADMIN_EDIT"
)
async def edit_complaint_admin_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "moderator_complaint_admin_id"
    ):
        await state.set_state(None)
        await state.update_data(
            moderator_complaint_admin_id=None,
            moderator_complaint_admin_index=None,
            moderator_complaint_admin_reason=None,
            moderator_complaint_admin_view=None,
            moderator_complaint_admin_page=None,
        )
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminComplaintsFSM
        .entering_complaint_admin_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_complaint_admin_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_ADMIN_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_complaints_router.callback_query(
    F.data == "ADM_CP_ADMIN_CANCEL"
)
async def cancel_complaint_admin_escalation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    view = (
        data.get(
            "moderator_complaint_admin_view"
        )
        or "open"
    )
    page = int(
        data.get(
            "moderator_complaint_admin_page"
        )
        or 0
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_complaint_admin_cancelled",
            language,
        ),
        reply_markup=(
            complaint_resolution_result_keyboard(
                view=view,
                page=page,
                language=language,
            )
        ),
    )

    await state.set_state(None)
    await state.update_data(
        moderator_complaint_admin_id=None,
        moderator_complaint_admin_index=None,
        moderator_complaint_admin_reason=None,
        moderator_complaint_admin_view=None,
        moderator_complaint_admin_page=None,
    )
@admin_complaints_router.callback_query(
    F.data == "ADM_CP_ADMIN_CONFIRM"
)
async def confirm_complaint_admin_escalation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    complaint_id = data.get(
        "moderator_complaint_admin_id"
    )
    reason = (
        data.get(
            "moderator_complaint_admin_reason"
        )
        or ""
    ).strip()
    view = (
        data.get(
            "moderator_complaint_admin_view"
        )
        or "open"
    )
    page = int(
        data.get(
            "moderator_complaint_admin_page"
        )
        or 0
    )

    if not complaint_id or len(reason) < 3:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=(
                complaint_resolution_result_keyboard(
                    view=view,
                    page=page,
                    language=language,
                )
            ),
        )
        await state.set_state(None)
        await state.update_data(
            moderator_complaint_admin_id=None,
            moderator_complaint_admin_index=None,
            moderator_complaint_admin_reason=None,
            moderator_complaint_admin_view=None,
            moderator_complaint_admin_page=None,
        )
        return

    try:
        async with get_session() as session:
            action = await AdminComplaintsService(
                session
            ).escalate_complaint(
                platform_user_id=(
                    callback.from_user.id
                ),
                complaint_id=UUID(
                    str(complaint_id)
                ),
                reason=reason,
            )
            result = action.result

    except (
        AdminComplaintsAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "complaint_escalated_to_admin "
        "telegram_id=%s "
        "complaint_id=%s "
        "status=%s",
        callback.from_user.id,
        complaint_id,
        result.status,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_complaint_admin_completed",
            language,
        ),
        reply_markup=(
            complaint_resolution_result_keyboard(
                view=view,
                page=page,
                language=language,
            )
        ),
    )

    await state.set_state(None)
    await state.update_data(
        moderator_complaint_admin_id=None,
        moderator_complaint_admin_index=None,
        moderator_complaint_admin_reason=None,
        moderator_complaint_admin_view=None,
        moderator_complaint_admin_page=None,
    )
