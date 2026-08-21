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
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_users import (
    AdminUsersAccessError,
    AdminUsersService,
)
from services.moderation import (
    AdminGlobalBlacklistCard,
    ModerationError,
)
from services.super_admin_global_blacklist import (
    SuperAdminGlobalBlacklistAccessError,
    SuperAdminGlobalBlacklistService,
)
from ui.texts import t


super_admin_global_blacklist_router = Router()
normalize_language = normalize_admin_language

ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE = 5


class SuperAdminGlobalBlacklistFSM(StatesGroup):
    entering_super_admin_global_blacklist_add = State()
    confirming_super_admin_global_blacklist_add = State()
    confirming_super_admin_global_blacklist_add_final = State()
    entering_super_admin_global_blacklist_revoke = State()
    confirming_super_admin_global_blacklist_revoke = State()
    confirming_super_admin_global_blacklist_revoke_final = State()


def format_global_blacklist_card(
    card: AdminGlobalBlacklistCard,
    *,
    number: int,
    language: str,
) -> str:
    comment = (
        card.comment
        or t("admin_global_blacklist_no_comment", language)
    )

    return t(
        "admin_global_blacklist_card",
        language,
    ).format(
        number=number,
        user=card.user_label,
        reason=card.reason,
        comment=comment,
        status=card.status,
        actor=card.actor_label,
        date=card.created_at.strftime("%Y-%m-%d"),
    )


def format_super_admin_global_blacklist_screen(
    cards: list[AdminGlobalBlacklistCard],
    *,
    view_label: str,
    page: int,
    language: str,
) -> str:
    header = t(
        "admin_global_blacklist_queue_title",
        language,
    ).format(
        view=view_label,
        count=len(cards),
    )

    if not cards:
        return (
            f"{header}\n\n"
            f"{t('admin_global_blacklist_empty', language)}"
        )

    start_number = (
        page
        * ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE
        + 1
    )

    rendered_cards = [
        format_global_blacklist_card(
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


def super_admin_global_blacklist_card_keyboard(
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
                        "admin_global_blacklist_revoke_btn",
                        language,
                    ),
                    callback_data=f"SA_GBL_REVOKE:{index}",
                )
            ]
        ]
    )


def super_admin_global_blacklist_queue_keyboard(
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
                    "admin_global_blacklist_active_btn",
                    language,
                ),
                callback_data="SA_GBL_QUEUE:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_history_btn",
                    language,
                ),
                callback_data="SA_GBL_QUEUE:history:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_add_btn",
                    language,
                ),
                callback_data="SA_GBL_ADD",
            )
        ],
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_GBL_QUEUE:{view}:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_GBL_QUEUE:{view}:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="SA_PANEL",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("main_menu", language),
                callback_data="MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def super_admin_global_blacklist_screen_keyboard(
    cards: list[AdminGlobalBlacklistCard],
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    start_number = (
        page
        * ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE
        + 1
    )

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
                            "admin_global_blacklist_revoke_btn",
                            language,
                        )
                    ),
                    callback_data=(
                        f"SA_GBL_REVOKE:{index}"
                    ),
                )
            ]
        )

    queue_keyboard = (
        super_admin_global_blacklist_queue_keyboard(
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
        inline_keyboard=rows,
    )


@super_admin_global_blacklist_router.callback_query(F.data == "SA_GLOBAL_BLACKLIST")
async def open_super_admin_global_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_global_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )


@super_admin_global_blacklist_router.callback_query(F.data.startswith("SA_GBL_QUEUE:"))
async def change_super_admin_global_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1 and parts[1] in {"active", "history"}
        else "active"
    )

    try:
        page = max(0, int(parts[2]))
    except (IndexError, TypeError, ValueError):
        page = 0

    await open_super_admin_global_blacklist_queue(
        callback,
        state,
        view=view,
        page=page,
    )


@super_admin_global_blacklist_router.callback_query(F.data == "SA_GBL_ADD")
async def ask_super_admin_global_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await SuperAdminGlobalBlacklistService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except SuperAdminGlobalBlacklistAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        SuperAdminGlobalBlacklistFSM
        .entering_super_admin_global_blacklist_add
    )
    await state.update_data(
        super_admin_global_blacklist_add_user_id=None,
        super_admin_global_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            "Введите пользователя и причину одним сообщением.\n\n"
            "Формат:\n"
            "user-49ba690f причина блокировки\n\n"
            "Можно указать user-facing ID, Telegram ID или username.\n"
            "Причина минимум 3 символа."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data="SA_GBL_ADD_CANCEL",
                    )
                ]
            ]
        ),
    )


@super_admin_global_blacklist_router.message(
    SuperAdminGlobalBlacklistFSM.entering_super_admin_global_blacklist_add
)
async def receive_super_admin_global_blacklist_add(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)

    if len(parts) != 2:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                "Неверный формат.\n\n"
                "Пример:\n"
                "user-49ba690f test global block"
            ),
        )
        return

    query, reason = (
        parts[0].strip(),
        parts[1].strip(),
    )

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


    try:
        async with get_session() as session:
            matches = await AdminUsersService(
                session
            ).search_super_admin_users(
                platform_user_id=(
                    message.from_user.id
                ),
                query=query,
            )
    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=str(exc),
        )
        return

    if not matches:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_not_found",
                language,
            ),
        )
        return

    if len(matches) > 1:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                "Найдено несколько пользователей. "
                "Уточните user-facing ID, Telegram ID или username."
            ),
        )
        return

    target = matches[0]

    await state.update_data(
        super_admin_global_blacklist_add_user_id=str(
            target.user_id
        ),
        super_admin_global_blacklist_add_reason=reason,
    )
    await state.set_state(
        SuperAdminGlobalBlacklistFSM
        .confirming_super_admin_global_blacklist_add
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_user_global_block_confirmation",
            language,
        ).format(
            user_number=target.user_number,
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_block_confirm_btn",
                            language,
                        ),
                        callback_data="SA_GBL_ADD_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data="SA_GBL_ADD",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data="SA_GBL_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(
    SuperAdminGlobalBlacklistFSM.confirming_super_admin_global_blacklist_add,
    F.data == "SA_GBL_ADD_CONFIRM",
)
async def confirm_super_admin_global_blacklist_add_first(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    target_user_id = data.get(
        "super_admin_global_blacklist_add_user_id"
    )
    reason = data.get(
        "super_admin_global_blacklist_add_reason"
    )

    if not target_user_id or not reason:
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_id = UUID(target_user_id)
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        SuperAdminGlobalBlacklistFSM
        .confirming_super_admin_global_blacklist_add_final
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_block_final_confirmation",
            language,
        ).format(
            user_number=f"user-{target_id.hex[:8]}",
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_block_final_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_ADD_FINAL_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data="SA_GBL_ADD",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data="SA_GBL_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(
    SuperAdminGlobalBlacklistFSM
    .confirming_super_admin_global_blacklist_add_final,
    F.data == "SA_GBL_ADD_FINAL_CONFIRM",
)
async def execute_super_admin_global_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    target_user_id = data.get(
        "super_admin_global_blacklist_add_user_id"
    )
    reason = data.get(
        "super_admin_global_blacklist_add_reason"
    )

    try:
        target_id = UUID(str(target_user_id))
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SuperAdminGlobalBlacklistService(
                    session
                ).block_user(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    user_id=target_id,
                    reason=reason,
                )
            )
            result = action.result
    except SuperAdminGlobalBlacklistAccessError:
        await state.set_state(None)
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_add_user_id=None,
        super_admin_global_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_block_completed",
            language,
        ).format(
            status=result.status,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(F.data == "SA_GBL_ADD_CANCEL")
async def cancel_super_admin_global_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_add_user_id=None,
        super_admin_global_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_block_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )


async def open_super_admin_global_blacklist_queue(
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
            result = await (
                SuperAdminGlobalBlacklistService(
                    session
                ).open_queue(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    view=view,
                    page=page,
                    page_size=(
                        ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE
                    ),
                )
            )
    except SuperAdminGlobalBlacklistAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_global_blacklist_ids=[
            str(card.blacklist_id)
            for card in result.items
        ],
        super_admin_global_blacklist_user_ids=[
            str(card.user_id)
            for card in result.items
        ],
        super_admin_global_blacklist_can_revoke=[
            card.can_revoke
            for card in result.items
        ],
        super_admin_global_blacklist_view=(
            result.view
        ),
        super_admin_global_blacklist_page=(
            result.page
        ),
        admin_global_blacklist_message_ids=[],
    )

    view_label = t(
        (
            "admin_global_blacklist_history_title"
            if result.view == "history"
            else "admin_global_blacklist_active_title"
        ),
        language,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_global_blacklist_screen(
                result.items,
                view_label=view_label,
                page=result.page,
                language=language,
            )
        ),
        reply_markup=(
            super_admin_global_blacklist_screen_keyboard(
                result.items,
                view=result.view,
                page=result.page,
                has_next=result.has_next,
                language=language,
            )
        ),
    )


@super_admin_global_blacklist_router.callback_query(F.data.startswith("SA_GBL_REVOKE:"))
async def ask_super_admin_global_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await SuperAdminGlobalBlacklistService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except SuperAdminGlobalBlacklistAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t(
                "admin_user_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = (
        data.get(
            "super_admin_global_blacklist_user_ids"
        )
        or []
    )
    can_revoke = (
        data.get(
            "super_admin_global_blacklist_can_revoke"
        )
        or []
    )

    if (
        index < 0
        or index >= len(user_ids)
        or index >= len(can_revoke)
        or not can_revoke[index]
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(
        SuperAdminGlobalBlacklistFSM
        .entering_super_admin_global_blacklist_revoke
    )
    await state.update_data(
        super_admin_global_blacklist_revoke_index=(
            index
        ),
        super_admin_global_blacklist_revoke_reason=None,
        admin_global_blacklist_message_ids=[],
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_reason_prompt",
            language,
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
                            "SA_GBL_REVOKE_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )


@super_admin_global_blacklist_router.message(
    SuperAdminGlobalBlacklistFSM.entering_super_admin_global_blacklist_revoke
)
async def receive_super_admin_global_blacklist_revoke_reason(
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
    index = data.get(
        "super_admin_global_blacklist_revoke_index"
    )
    user_ids = (
        data.get(
            "super_admin_global_blacklist_user_ids"
        )
        or []
    )

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_not_found",
                language,
            ),
        )
        return

    try:
        target_user_id = UUID(
            str(user_ids[index])
        )
    except (TypeError, ValueError):
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_not_found",
                language,
            ),
        )
        return

    await state.update_data(
        super_admin_global_blacklist_revoke_reason=reason,
    )
    await state.set_state(
        SuperAdminGlobalBlacklistFSM
        .confirming_super_admin_global_blacklist_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_user_global_unblock_confirmation",
            language,
        ).format(
            user_number=(
                f"user-{target_user_id.hex[:8]}"
            ),
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_unblock_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_GBL_REVOKE:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(
    SuperAdminGlobalBlacklistFSM
    .confirming_super_admin_global_blacklist_revoke,
    F.data == "SA_GBL_REVOKE_CONFIRM",
)
async def confirm_super_admin_global_blacklist_revoke_first(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("super_admin_global_blacklist_revoke_index")
    reason = data.get("super_admin_global_blacklist_revoke_reason")
    user_ids = data.get("super_admin_global_blacklist_user_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
        or not reason
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        SuperAdminGlobalBlacklistFSM
        .confirming_super_admin_global_blacklist_revoke_final
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_final_confirmation",
            language,
        ).format(
            user_number=(
                f"user-{target_user_id.hex[:8]}"
            ),
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_unblock_final_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_FINAL_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_GBL_REVOKE:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(
    SuperAdminGlobalBlacklistFSM
    .confirming_super_admin_global_blacklist_revoke_final,
    F.data == "SA_GBL_REVOKE_FINAL_CONFIRM",
)
async def execute_super_admin_global_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("super_admin_global_blacklist_revoke_index")
    reason = data.get("super_admin_global_blacklist_revoke_reason")
    user_ids = data.get("super_admin_global_blacklist_user_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SuperAdminGlobalBlacklistService(
                    session
                ).unblock_user(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    user_id=target_user_id,
                    reason=reason,
                )
            )
            result = action.result
    except SuperAdminGlobalBlacklistAccessError:
        await state.set_state(None)
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_revoke_index=None,
        super_admin_global_blacklist_revoke_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_completed",
            language,
        ).format(
            status=result.status,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(F.data == "SA_GBL_REVOKE_CANCEL")
async def cancel_super_admin_global_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_revoke_index=None,
        super_admin_global_blacklist_revoke_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )


@super_admin_global_blacklist_router.callback_query(F.data == "SA_USER_GLOBAL_BLACKLIST")
async def super_admin_user_global_blacklist_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()

    if not data.get("super_admin_selected_user_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await open_super_admin_global_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )
