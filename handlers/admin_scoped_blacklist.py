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
    clear_admin_message_group,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from handlers.admin_complaints import (
    complaint_resolution_result_keyboard,
)
from services.admin_scoped_blacklist import (
    AdminScopedBlacklistAccessError,
    AdminScopedBlacklistService,
)
from services.moderation import (
    ModerationError,
    ModeratorScopedBlacklistCard,
)
from ui.texts import t


logger = logging.getLogger(__name__)

admin_scoped_blacklist_router = Router()
normalize_language = normalize_admin_language


class AdminScopedBlacklistFSM(StatesGroup):
    confirming_blacklist_add = State()
    confirming_blacklist_revoke = State()
    confirming_complaint_scoped_block = State()
    confirming_specialist_scoped_block = State()
    entering_blacklist_add_reason = State()
    entering_blacklist_add_user = State()
    entering_blacklist_revoke_reason = State()
    entering_complaint_scoped_block_reason = State()
    entering_specialist_scoped_block_reason = State()


def format_scoped_blacklist_card(
    card: ModeratorScopedBlacklistCard,
    *,
    number: int,
    language: str,
) -> str:
    comment = (
        card.comment
        or t(
            "moderator_blacklist_no_comment",
            language,
        )
    )

    revoke_reason = (
        card.revoke_reason
        or t(
            "moderator_blacklist_no_revoke_reason",
            language,
        )
    )

    revoke_line = ""

    if card.status == "revoked":
        revoke_line = (
            "\n"
            + t(
                "moderator_blacklist_revoke_reason_line",
                language,
            ).format(reason=revoke_reason)
        )

    return t(
        "moderator_blacklist_card",
        language,
    ).format(
        number=number,
        user=card.user_label,
        scope=card.scope_label,
        reason=card.reason,
        comment=comment,
        status=card.status,
        date=card.created_at.strftime("%Y-%m-%d"),
        revoke_line=revoke_line,
    )
def format_scoped_blacklist_screen(
    cards: list[ModeratorScopedBlacklistCard],
    *,
    view_label: str,
    page: int,
    language: str,
) -> str:
    header = t(
        "moderator_blacklist_queue_title",
        language,
    ).format(
        view=view_label,
        count=len(cards),
    )

    if not cards:
        return (
            f"{header}\n\n"
            f"{t('moderator_blacklist_empty', language)}"
        )

    start_number = page * 5 + 1

    rendered_cards = [
        format_scoped_blacklist_card(
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
def scoped_blacklist_card_keyboard(
    *,
    index: int,
    can_revoke: bool,
    language: str,
) -> InlineKeyboardMarkup | None:
    if not can_revoke:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_blacklist_revoke_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_BL_REVOKE:{index}"
                    ),
                )
            ]
        ]
    )
def scoped_blacklist_queue_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_active_btn",
                    language,
                ),
                callback_data="ADM_BL_QUEUE:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_history_btn",
                    language,
                ),
                callback_data="ADM_BL_QUEUE:revoked:0",
            ),
        ]
    ]

    if view == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_blacklist_add_btn",
                        language,
                    ),
                    callback_data="ADM_BL_ADD",
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_BL_QUEUE:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_BL_QUEUE:{view}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_back_btn",
                    language,
                ),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )
def scoped_blacklist_screen_keyboard(
    cards: list[ModeratorScopedBlacklistCard],
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    start_number = page * 5 + 1

    for index, card in enumerate(cards):
        if not card.can_revoke:
            continue

        number = start_number + index

        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{number}. "
                        + t(
                            "moderator_blacklist_revoke_btn",
                            language,
                        )
                    ),
                    callback_data=(
                        f"ADM_BL_REVOKE:{index}"
                    ),
                )
            ]
        )

    queue_keyboard = scoped_blacklist_queue_keyboard(
        view=view,
        page=page,
        has_next=has_next,
        language=language,
    )

    rows.extend(
        queue_keyboard.inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_ADD"
)
async def ask_blacklist_add_user(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminScopedBlacklistService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_blacklist_add_user
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_add_user_prompt",
            language,
        ),
    )
@admin_scoped_blacklist_router.message(
    AdminScopedBlacklistFSM.entering_blacklist_add_user
)
async def receive_blacklist_add_user(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    telegram_id = (message.text or "").strip()

    if not telegram_id.isdigit():
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "moderator_blacklist_invalid_user",
                language,
            ),
        )
        return

    await state.update_data(
        moderator_blacklist_add_telegram_id=(
            telegram_id
        ),
    )
    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_blacklist_add_reason
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
            language,
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_ADD_EDIT_REASON"
)
async def edit_blacklist_add_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "moderator_blacklist_add_telegram_id"
    ):
        await state.set_state(None)
        await state.update_data(
            moderator_blacklist_add_tenant_id=None,
            moderator_blacklist_add_telegram_id=None,
            moderator_blacklist_add_reason=None,
        )

        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_blacklist_add_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
            language,
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_ADD_CANCEL"
)
async def cancel_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(None)
    await state.update_data(
        moderator_blacklist_add_tenant_id=None,
        moderator_blacklist_add_telegram_id=None,
        moderator_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_add_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_active_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:active:0"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_ADD_CONFIRM"
)
async def confirm_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    telegram_id = (
        data.get(
            "moderator_blacklist_add_telegram_id"
        )
        or ""
    ).strip()
    reason = (
        data.get(
            "moderator_blacklist_add_reason"
        )
        or ""
    ).strip()

    if not telegram_id.isdigit() or len(reason) < 3:
        await state.set_state(None)
        await state.update_data(
            moderator_blacklist_add_tenant_id=None,
            moderator_blacklist_add_telegram_id=None,
            moderator_blacklist_add_reason=None,
        )

        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await AdminScopedBlacklistService(
                session
            ).add_by_telegram_id(
                platform_user_id=(
                    callback.from_user.id
                ),
                telegram_id=telegram_id,
                reason=reason,
            )
            result = action.result

    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "scoped_blacklist_added_manually "
        "telegram_id=%s target_telegram_id=%s "
        "blacklist_id=%s",
        callback.from_user.id,
        telegram_id,
        result.entity_id,
    )

    await state.set_state(None)
    await state.update_data(
        moderator_blacklist_add_tenant_id=None,
        moderator_blacklist_add_telegram_id=None,
        moderator_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_created",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_active_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:active:0"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.message(
    AdminScopedBlacklistFSM.entering_blacklist_add_reason
)
async def receive_blacklist_add_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

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

    telegram_id = data.get(
        "moderator_blacklist_add_telegram_id"
    )

    if not telegram_id:
        await state.set_state(None)
        await state.update_data(
            moderator_blacklist_add_tenant_id=None,
            moderator_blacklist_add_telegram_id=None,
            moderator_blacklist_add_reason=None,
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

    await state.update_data(
        moderator_blacklist_add_reason=reason,
    )
    await state.set_state(
        AdminScopedBlacklistFSM.confirming_blacklist_add
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_blacklist_add_confirmation",
            language,
        ).format(
            telegram_id=telegram_id,
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_add_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_BL_ADD_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_ADD_EDIT_REASON"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data="ADM_BL_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )
def super_admin_read_only_moderator_blacklist_keyboard(
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
                    "moderator_blacklist_active_btn",
                    language,
                ),
                callback_data="SA_RO_MOD_BLACKLIST:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_history_btn",
                    language,
                ),
                callback_data="SA_RO_MOD_BLACKLIST:revoked:0",
            ),
        ]
    ]

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_MOD_BLACKLIST:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_MOD_BLACKLIST:{view}:{page + 1}"
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
@admin_scoped_blacklist_router.callback_query(
    F.data.startswith("SA_RO_MOD_BLACKLIST:")
)
async def super_admin_read_only_moderator_blacklist(
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

    if view not in {"active", "revoked"}:
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
            cards = await AdminScopedBlacklistService(
                session
            ).open_impersonated_queue(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_user_id=target_user_id,
                view=view,
                page=page,
                page_size=(
                    MODERATOR_PROFILE_PAGE_SIZE
                ),
            )
    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_cards = cards[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(cards) > MODERATOR_PROFILE_PAGE_SIZE

    view_label = t(
        (
            "moderator_blacklist_history_title"
            if view == "revoked"
            else "moderator_blacklist_active_title"
        ),
        language,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_moderator_"
            "blacklist_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "super_admin_ro_moderator_blacklist_title",
            language,
        ).format(
            view=view_label,
            page=page + 1,
            count=len(visible_cards),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if visible_cards:
        start_number = (
            page
            * MODERATOR_PROFILE_PAGE_SIZE
            + 1
        )

        for offset, card in enumerate(
            visible_cards
        ):
            card_message = (
                await callback.message.answer(
                    format_scoped_blacklist_card(
                        card,
                        number=(
                            start_number
                            + offset
                        ),
                        language=language,
                    )
                )
            )
            rendered_message_ids.append(
                card_message.message_id
            )
    else:
        empty_message = (
            await callback.message.answer(
                t(
                    "moderator_blacklist_empty",
                    language,
                )
            )
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

    navigation_message = (
        await callback.message.answer(
            t(
                "super_admin_ro_read_only_label",
                language,
            ),
            reply_markup=(
                super_admin_read_only_moderator_blacklist_keyboard(
                    view=view,
                    page=page,
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
        super_admin_ro_moderator_blacklist_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()
@admin_scoped_blacklist_router.callback_query(
    F.data.startswith("ADM_SP_SCOPED_BLOCK:")
)
async def ask_specialist_scoped_block_reason(
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
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    specialist_ids = (
        data.get(
            "admin_pending_specialist_ids"
        )
        or []
    )
    page = int(
        data.get(
            "admin_pending_specialist_page"
        )
        or 0
    )

    if index < 0 or index >= len(specialist_ids):
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
            await AdminScopedBlacklistService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_scoped_block_specialist_id=(
            specialist_ids[index]
        ),
        moderator_scoped_block_page=page,
    )
    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_specialist_scoped_block_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
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
                            "ADM_SP_SCOPED_BLOCK_CANCEL:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.message(
    AdminScopedBlacklistFSM
    .entering_specialist_scoped_block_reason
)
async def receive_specialist_scoped_block_reason(
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

    specialist_id = data.get(
        "moderator_scoped_block_specialist_id"
    )
    page = int(
        data.get(
            "moderator_scoped_block_page"
        )
        or 0
    )

    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_changes_cancel_btn",
                        language,
                    ),
                    callback_data=(
                        "ADM_SP_SCOPED_BLOCK_CANCEL:"
                        f"{page}"
                    ),
                )
            ]
        ]
    )

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('admin_reason_too_short', language)}"
                "\n\n"
                f"{t('moderator_scoped_block_reason_prompt', language)}"
            ),
            reply_markup=cancel_keyboard,
        )
        return

    if not specialist_id:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "moderator_back_to_queue_btn",
                                language,
                            ),
                            callback_data=(
                                f"ADM_SP_QUEUE:{page}"
                            ),
                        )
                    ]
                ]
            ),
        )

        await state.set_state(None)
        await state.update_data(
            moderator_scoped_block_specialist_id=None,
            moderator_scoped_block_reason=None,
            moderator_scoped_block_page=None,
        )
        return

    await state.update_data(
        moderator_scoped_block_reason=reason,
    )
    await state.set_state(
        AdminScopedBlacklistFSM
        .confirming_specialist_scoped_block
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_scoped_block_confirmation",
            language,
        ).format(
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_SCOPED_BLOCK_CONFIRM"
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
                            "ADM_SP_SCOPED_BLOCK_EDIT"
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
                            "ADM_SP_SCOPED_BLOCK_CANCEL:"
                            f"{page}"
                        ),
                    )
                ],
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_SP_SCOPED_BLOCK_EDIT"
)
async def edit_specialist_scoped_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    specialist_id = data.get(
        "moderator_scoped_block_specialist_id"
    )
    page = int(
        data.get(
            "moderator_scoped_block_page"
        )
        or 0
    )

    if not specialist_id:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "moderator_back_to_queue_btn",
                                language,
                            ),
                            callback_data=(
                                f"ADM_SP_QUEUE:{page}"
                            ),
                        )
                    ]
                ]
            ),
        )

        await state.set_state(None)
        await state.update_data(
            moderator_scoped_block_specialist_id=None,
            moderator_scoped_block_reason=None,
            moderator_scoped_block_page=None,
        )
        return

    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_specialist_scoped_block_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
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
                            "ADM_SP_SCOPED_BLOCK_CANCEL:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data.startswith(
        "ADM_SP_SCOPED_BLOCK_CANCEL:"
    )
)
async def cancel_specialist_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            0,
            int(
                (
                    callback.data or ""
                ).split(
                    ":",
                    1,
                )[1]
            ),
        )
    except (TypeError, ValueError):
        page = 0

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_QUEUE:{page}"
                        ),
                    )
                ]
            ]
        ),
    )

    await state.set_state(None)
    await state.update_data(
        moderator_scoped_block_specialist_id=None,
        moderator_scoped_block_reason=None,
        moderator_scoped_block_page=None,
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_SP_SCOPED_BLOCK_CONFIRM"
)
async def confirm_specialist_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    specialist_id = data.get(
        "moderator_scoped_block_specialist_id"
    )
    reason = (
        data.get(
            "moderator_scoped_block_reason"
        )
        or ""
    ).strip()
    page = int(
        data.get(
            "moderator_scoped_block_page"
        )
        or 0
    )

    result_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_back_to_queue_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_SP_QUEUE:{page}"
                    ),
                )
            ]
        ]
    )

    if (
        not specialist_id
        or len(reason) < 3
    ):
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=result_keyboard,
        )

        await state.set_state(None)
        await state.update_data(
            moderator_scoped_block_specialist_id=None,
            moderator_scoped_block_reason=None,
            moderator_scoped_block_page=None,
        )
        return

    try:
        async with get_session() as session:
            action = await AdminScopedBlacklistService(
                session
            ).add_specialist_owner(
                platform_user_id=(
                    callback.from_user.id
                ),
                specialist_id=UUID(
                    str(specialist_id)
                ),
                reason=reason,
            )
            result = action.result
            moderator_user_id = (
                action.actor.user_id
            )

    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        logger.warning(
            "moderator_scoped_blacklist_failed "
            "telegram_id=%s "
            "specialist_id=%s "
            "error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "moderator_scoped_blacklist_created "
        "telegram_id=%s "
        "moderator_user_id=%s "
        "specialist_id=%s "
        "blacklist_id=%s",
        callback.from_user.id,
        moderator_user_id,
        specialist_id,
        result.entity_id,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_created",
            language,
        ),
        reply_markup=result_keyboard,
    )

    await state.set_state(None)
    await state.update_data(
        moderator_scoped_block_specialist_id=None,
        moderator_scoped_block_reason=None,
        moderator_scoped_block_page=None,
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_SCOPED_BLACKLIST"
)
async def open_active_scoped_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_scoped_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )
@admin_scoped_blacklist_router.callback_query(
    F.data.startswith("ADM_BL_QUEUE:")
)
async def change_scoped_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1
        and parts[1] in {"active", "revoked"}
        else "active"
    )

    try:
        page = max(
            0,
            int(parts[2]),
        )
    except (IndexError, TypeError, ValueError):
        page = 0

    await open_scoped_blacklist_queue(
        callback,
        state,
        view=view,
        page=page,
    )
async def open_scoped_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            cards = await AdminScopedBlacklistService(
                session
            ).open_queue(
                platform_user_id=(
                    callback.from_user.id
                ),
                view=view,
                page=page,
                page_size=5,
            )

    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    has_next = len(cards) > 5
    visible_cards = cards[:5]

    await state.update_data(
        moderator_blacklist_ids=[
            str(card.blacklist_id)
            for card in visible_cards
        ],
        moderator_blacklist_can_revoke=[
            card.can_revoke
            for card in visible_cards
        ],
        moderator_blacklist_view=view,
        moderator_blacklist_page=page,
        moderator_blacklist_has_next=has_next,
        admin_scoped_blacklist_message_ids=[],
    )

    view_label = t(
        (
            "moderator_blacklist_history_title"
            if view == "revoked"
            else "moderator_blacklist_active_title"
        ),
        language,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_scoped_blacklist_screen(
                visible_cards,
                view_label=view_label,
                page=page,
                language=language,
            )
        ),
        reply_markup=(
            scoped_blacklist_screen_keyboard(
                visible_cards,
                view=view,
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data.startswith("ADM_BL_REVOKE:")
)
async def ask_scoped_blacklist_revoke_reason(
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

    blacklist_ids = (
        data.get("moderator_blacklist_ids")
        or []
    )
    revoke_flags = (
        data.get("moderator_blacklist_can_revoke")
        or []
    )

    if (
        index < 0
        or index >= len(blacklist_ids)
        or index >= len(revoke_flags)
        or not revoke_flags[index]
    ):
        await callback.answer(
            t(
                "moderator_blacklist_revoke_forbidden",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminScopedBlacklistService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_blacklist_revoke_id=(
            blacklist_ids[index]
        ),
        moderator_blacklist_revoke_reason=None,
        admin_scoped_blacklist_message_ids=[],
    )
    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_blacklist_revoke_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoke_reason_prompt",
            language,
        ),
    )
@admin_scoped_blacklist_router.message(
    AdminScopedBlacklistFSM.entering_blacklist_revoke_reason
)
async def receive_scoped_blacklist_revoke_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

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

    if not data.get(
        "moderator_blacklist_revoke_id"
    ):
        await state.set_state(None)
        await state.update_data(
            moderator_blacklist_revoke_id=None,
            moderator_blacklist_revoke_reason=None,
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

    await state.update_data(
        moderator_blacklist_revoke_reason=reason,
    )
    await state.set_state(
        AdminScopedBlacklistFSM.confirming_blacklist_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_blacklist_revoke_confirmation",
            language,
        ).format(
            reason=reason
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_revoke_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_REVOKE_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data="ADM_BL_REVOKE_EDIT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_REVOKE_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_REVOKE_EDIT"
)
async def edit_scoped_blacklist_revoke_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "moderator_blacklist_revoke_id"
    ):
        await state.set_state(None)
        await state.update_data(
            moderator_blacklist_revoke_id=None,
            moderator_blacklist_revoke_reason=None,
        )

        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_blacklist_revoke_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoke_reason_prompt",
            language,
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_REVOKE_CANCEL"
)
async def cancel_scoped_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    view = (
        data.get("moderator_blacklist_view")
        or "active"
    )
    page = int(
        data.get("moderator_blacklist_page")
        or 0
    )

    await state.set_state(None)
    await state.update_data(
        moderator_blacklist_revoke_id=None,
        moderator_blacklist_revoke_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoke_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_BL_QUEUE:{view}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_BL_REVOKE_CONFIRM"
)
async def confirm_scoped_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    blacklist_id = data.get(
        "moderator_blacklist_revoke_id"
    )
    reason = (
        data.get("moderator_blacklist_revoke_reason")
        or ""
    ).strip()

    if not blacklist_id or len(reason) < 3:
        await state.set_state(None)
        await state.update_data(
            moderator_blacklist_revoke_id=None,
            moderator_blacklist_revoke_reason=None,
        )

        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await AdminScopedBlacklistService(
                session
            ).revoke(
                platform_user_id=(
                    callback.from_user.id
                ),
                blacklist_id=UUID(blacklist_id),
                reason=reason,
            )
            result = action.result

    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "scoped_blacklist_revoked "
        "telegram_id=%s blacklist_id=%s status=%s",
        callback.from_user.id,
        blacklist_id,
        result.status,
    )

    await state.set_state(None)
    await state.update_data(
        moderator_blacklist_revoke_id=None,
        moderator_blacklist_revoke_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoked",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_active_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:active:0"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_history_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:revoked:0"
                        ),
                    )
                ],
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data.startswith("ADM_CP_SCOPED_BLOCK:")
)
async def ask_complaint_scoped_block_reason(
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

    try:
        async with get_session() as session:
            await AdminScopedBlacklistService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except (
        AdminScopedBlacklistAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_complaint_scoped_id=(
            complaint_ids[index]
        ),
        moderator_complaint_scoped_index=index,
        moderator_complaint_scoped_view=view,
        moderator_complaint_scoped_page=page,
    )
    await state.set_state(
        AdminScopedBlacklistFSM
        .entering_complaint_scoped_block_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
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
                            "ADM_CP_SCOPED_BLOCK_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.message(
    AdminScopedBlacklistFSM
    .entering_complaint_scoped_block_reason
)
async def receive_complaint_scoped_block_reason(
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
            "moderator_complaint_scoped_view"
        )
        or "open"
    )
    page = int(
        data.get(
            "moderator_complaint_scoped_page"
        )
        or 0
    )

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('admin_reason_too_short', language)}\n\n"
                f"{t('moderator_scoped_block_reason_prompt', language)}"
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
                                "ADM_CP_SCOPED_BLOCK_CANCEL"
                            ),
                        )
                    ]
                ]
            ),
        )
        return

    if not data.get(
        "moderator_complaint_scoped_id"
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
            moderator_complaint_scoped_id=None,
            moderator_complaint_scoped_index=None,
            moderator_complaint_scoped_reason=None,
            moderator_complaint_scoped_view=None,
            moderator_complaint_scoped_page=None,
        )
        return

    await state.update_data(
        moderator_complaint_scoped_reason=reason,
    )
    await state.set_state(
        AdminScopedBlacklistFSM
        .confirming_complaint_scoped_block
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_scoped_block_confirmation",
            language,
        ).format(
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_SCOPED_BLOCK_CONFIRM"
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
                            "ADM_CP_SCOPED_BLOCK_EDIT"
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
                            "ADM_CP_SCOPED_BLOCK_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_CP_SCOPED_BLOCK_EDIT"
)
async def edit_complaint_scoped_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "moderator_complaint_scoped_id"
    ):
        await state.set_state(None)
        await state.update_data(
            moderator_complaint_scoped_id=None,
            moderator_complaint_scoped_index=None,
            moderator_complaint_scoped_reason=None,
            moderator_complaint_scoped_view=None,
            moderator_complaint_scoped_page=None,
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
        AdminScopedBlacklistFSM
        .entering_complaint_scoped_block_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
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
                            "ADM_CP_SCOPED_BLOCK_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_CP_SCOPED_BLOCK_CANCEL"
)
async def cancel_complaint_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    view = (
        data.get(
            "moderator_complaint_scoped_view"
        )
        or "open"
    )
    page = int(
        data.get(
            "moderator_complaint_scoped_page"
        )
        or 0
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_cancelled",
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
        moderator_complaint_scoped_id=None,
        moderator_complaint_scoped_index=None,
        moderator_complaint_scoped_reason=None,
        moderator_complaint_scoped_view=None,
        moderator_complaint_scoped_page=None,
    )
@admin_scoped_blacklist_router.callback_query(
    F.data == "ADM_CP_SCOPED_BLOCK_CONFIRM"
)
async def confirm_complaint_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    complaint_id = data.get(
        "moderator_complaint_scoped_id"
    )
    reason = (
        data.get(
            "moderator_complaint_scoped_reason"
        )
        or ""
    ).strip()
    view = (
        data.get(
            "moderator_complaint_scoped_view"
        )
        or "open"
    )
    page = int(
        data.get(
            "moderator_complaint_scoped_page"
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
            moderator_complaint_scoped_id=None,
            moderator_complaint_scoped_index=None,
            moderator_complaint_scoped_reason=None,
            moderator_complaint_scoped_view=None,
            moderator_complaint_scoped_page=None,
        )
        return

    try:
        async with get_session() as session:
            action = await AdminScopedBlacklistService(
                session
            ).add_complaint_target(
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
        AdminScopedBlacklistAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "complaint_scoped_blacklist_created "
        "telegram_id=%s "
        "complaint_id=%s "
        "blacklist_id=%s",
        callback.from_user.id,
        complaint_id,
        result.entity_id,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_created",
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
        moderator_complaint_scoped_id=None,
        moderator_complaint_scoped_index=None,
        moderator_complaint_scoped_reason=None,
        moderator_complaint_scoped_view=None,
        moderator_complaint_scoped_page=None,
    )
