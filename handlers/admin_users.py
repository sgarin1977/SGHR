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
    clear_admin_message_group,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_users import (
    AdminUsersAccessError,
    AdminUsersService,
)
from services.moderation import (
    AdminUserDetailsCard,
    AdminUserHistoryCard,
    AdminUserSearchCard,
    ModerationError,
)
from utils.telegram_cleanup import edit_or_replace_menu_message
from ui.texts import t


admin_users_router = Router()
normalize_language = normalize_admin_language


class AdminUsersFSM(StatesGroup):
    entering_admin_user_search = State()
    waiting_super_admin_user_search = State()
    entering_super_admin_impersonation_admin_user_search = State()


def format_admin_user_history_item(
    card: AdminUserHistoryCard,
    *,
    number: int,
    language: str,
) -> str:
    return t(
        "admin_user_history_item",
        language,
    ).format(
        number=number,
        date=card.date,
        actor=card.actor,
        action=card.action,
        reason=card.reason,
        source=card.source,
    )


def admin_user_history_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_back_to_card_btn",
                        language,
                    ),
                    callback_data=f"ADM_USER_VIEW:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_USERS",
                )
            ],
        ]
    )


def format_admin_user_roles(
    card: AdminUserDetailsCard,
    language: str,
) -> str:
    roles = (
        "\n".join(
            f"- {role}"
            for role in card.roles
        )
        if card.roles
        else t("admin_user_no_roles", language)
    )

    return t("admin_user_roles_text", language).format(
        user_number=card.user_number,
        roles=roles,
    )


def admin_user_roles_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_back_to_card_btn",
                        language,
                    ),
                    callback_data=f"ADM_USER_VIEW:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_USERS",
                )
            ],
        ]
    )


def format_admin_user_details(
    card: AdminUserDetailsCard,
    language: str,
) -> str:
    roles = (
        ", ".join(card.roles)
        if card.roles
        else t("admin_user_no_roles", language)
    )

    return t("admin_user_details", language).format(
        user_number=card.user_number,
        display_name=card.display_name,
        username=card.username,
        roles=roles,
        status=card.status,
        last_seen=card.last_seen,
        complaints=card.complaints_count,
    )


def admin_user_details_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_roles_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_USER_ROLES:{index}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_history_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_USER_HISTORY:{index}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data="ADM_USERS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="ADM_MENU",
                )
            ],
        ]
    )


def format_admin_user_search_card(
    card: AdminUserSearchCard,
    *,
    number: int,
    language: str,
) -> str:
    return t("admin_user_search_card", language).format(
        number=number,
        user_number=card.user_number,
        telegram_id=card.telegram_id,
        username=card.username,
        display_name=card.display_name,
        status=card.status,
    )


def admin_user_search_result_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_open_btn", language),
                    callback_data=f"ADM_USER_VIEW:{index}",
                )
            ]
        ]
    )


def admin_user_search_actions_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_search_again_btn", language),
                    callback_data="ADM_USERS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_PANEL",
                )
            ],
        ]
    )


@admin_users_router.callback_query(F.data == "ADM_USERS")
async def ask_admin_user_search(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminUsersService(
                session
            ).require_regional_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminUsersAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_user_message_ids",
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_user_history_message_ids"
        ),
        preserve_current=True,
    )

    await state.clear()
    await state.set_state(
        AdminUsersFSM.entering_admin_user_search
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_search_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data="ADM_PANEL",
                    )
                ]
            ]
        ),
    )


@admin_users_router.message(
    AdminUsersFSM.entering_admin_user_search
)
async def receive_admin_user_search(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()

    try:
        async with get_session() as session:
            cards = await AdminUsersService(
                session
            ).search_regional_users(
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
            text=t(
                "admin_user_search_error",
                language,
            ).format(
                error=str(exc),
            ),
            reply_markup=(
                admin_user_search_actions_keyboard(
                    language
                )
            ),
        )
        return

    await state.set_state(None)

    if not cards:
        await state.update_data(
            admin_user_search_ids=[],
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_search_empty",
                language,
            ),
            reply_markup=(
                admin_user_search_actions_keyboard(
                    language
                )
            ),
        )
        return

    await state.update_data(
        admin_user_search_ids=[
            str(card.user_id)
            for card in cards
        ],
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_user_search_results",
            language,
        ).format(
            count=len(cards),
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

    for index, card in enumerate(cards):
        card_message = await message.answer(
            format_admin_user_search_card(
                card,
                number=index + 1,
                language=language,
            ),
            reply_markup=(
                admin_user_search_result_keyboard(
                    index=index,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await message.answer(
        t(
            "admin_user_search_actions",
            language,
        ),
        reply_markup=(
            admin_user_search_actions_keyboard(
                language
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_user_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )


@admin_users_router.callback_query(
    F.data.startswith("ADM_USER_VIEW:")
)
async def open_admin_user_details(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminUsersService(
                session
            ).get_regional_user_details(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_user_id=target_user_id,
            )
    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_user_selected_index=index,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_user_message_ids",
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_user_history_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_user_details(
            card,
            language,
        ),
        reply_markup=admin_user_details_keyboard(
            index=index,
            language=language,
        ),
    )


@admin_users_router.callback_query(
    F.data.startswith("ADM_USER_ROLES:")
)
async def open_admin_user_roles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminUsersService(
                session
            ).get_regional_user_details(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_user_id=target_user_id,
            )
    except (
        AdminUsersAccessError,
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
        text=format_admin_user_roles(
            card,
            language,
        ),
        reply_markup=admin_user_roles_keyboard(
            index=index,
            language=language,
        ),
    )


@admin_users_router.callback_query(
    F.data.startswith("ADM_USER_HISTORY:")
)
async def open_admin_user_history(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            history = await AdminUsersService(
                session
            ).list_regional_user_history(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_user_id=target_user_id,
                limit=10,
            )
    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_user_history_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_history_title",
            language,
        ).format(
            user_number=(
                f"user-{target_user_id.hex[:8]}"
            ),
            count=len(history),
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

    if not history:
        empty_message = await callback.message.answer(
            t(
                "admin_user_history_empty",
                language,
            ),
            reply_markup=admin_user_history_keyboard(
                index=index,
                language=language,
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            admin_user_history_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        return

    for number, card in enumerate(
        history,
        start=1,
    ):
        history_message = (
            await callback.message.answer(
                format_admin_user_history_item(
                    card,
                    number=number,
                    language=language,
                )
            )
        )
        rendered_message_ids.append(
            history_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "admin_user_history_actions",
            language,
        ),
        reply_markup=admin_user_history_keyboard(
            index=index,
            language=language,
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_user_history_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )


def format_super_admin_user_search_results(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_user_not_found", language)

    lines = [
        t("super_admin_user_search_header", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        roles = (
            ", ".join(
                super_admin_user_role_label(
                    role,
                    language,
                )
                for role in item.roles
            )
            if item.roles
            else "-"
        )
        lines.append(
            t("super_admin_user_search_card", language).format(
                number=index,
                name=item.display_name,
                user_number=item.user_number,
                telegram_id=item.telegram_id,
                username=item.username,
                status=super_admin_user_status_label(
                    item.status,
                    language,
                ),
                roles=roles,
            )
        )

    return "\n\n".join(lines)


def super_admin_user_search_keyboard(
    items,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{index}. "
                        f"{t('super_admin_user_profile_btn', language)}"
                    ),
                    callback_data=f"SA_USER_OPEN:{index - 1}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("super_admin_back_to_menu_btn", language),
                callback_data="ADM_PANEL",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_users_router.callback_query(F.data == "SA_USERS")
async def super_admin_users_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.answer()

    try:
        async with get_session() as session:
            await AdminUsersService(
                session
            ).require_super_admin_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminUsersAccessError:
        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "admin_access_denied",
                language,
            ),
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id,
        )
        return

    await state.set_state(
        AdminUsersFSM.waiting_super_admin_user_search
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "super_admin_user_search_prompt",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id,
    )


@admin_users_router.message(
    AdminUsersFSM.waiting_super_admin_user_search
)
async def super_admin_user_search_message(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()

    if len(query) < 2:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_user_search_too_short",
                language,
            ),
        )
        return


    try:
        async with get_session() as session:
            items = await AdminUsersService(
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

    await state.update_data(
        super_admin_user_search_ids=[
            str(item.user_id)
            for item in items
        ],
        super_admin_user_search_query=query,
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_super_admin_user_search_results(
            items,
            language,
        ),
        reply_markup=super_admin_user_search_keyboard(
            items,
            language,
        ),
    )

    await state.set_state(None)


def super_admin_user_status_label(
    status: str | None,
    language: str,
) -> str:
    key_by_status = {
        "active": "super_admin_user_status_active",
        "blocked": "super_admin_user_status_blocked",
        "deleted": "super_admin_user_status_deleted",
    }

    normalized_status = (
        status or ""
    ).strip().lower()

    key = key_by_status.get(
        normalized_status
    )

    return t(
        key,
        language,
    ) if key else (
        status or "—"
    )


def super_admin_user_role_label(
    role: str | None,
    language: str,
) -> str:
    key_by_role = {
        "client": "super_admin_user_role_client",
        "specialist": "super_admin_user_role_specialist",
        "support": "super_admin_user_role_support",
        "moderator": "super_admin_user_role_moderator",
        "admin": "super_admin_user_role_admin",
        "super_admin": (
            "super_admin_user_role_super_admin"
        ),
        "finance_admin": (
            "super_admin_user_role_finance_admin"
        ),
        "content_manager": (
            "super_admin_user_role_content_manager"
        ),
    }

    normalized_role = (
        role or ""
    ).strip().lower()

    key = key_by_role.get(
        normalized_role
    )

    return t(
        key,
        language,
    ) if key else (
        role or "—"
    )


def super_admin_user_risk_label(
    risk_flags: str | None,
    language: str,
) -> str:
    normalized_value = (
        risk_flags or ""
    ).strip().lower()

    if normalized_value in {
        "",
        "-",
        "none",
    }:
        return t(
            "super_admin_user_risk_none",
            language,
        )

    if normalized_value.startswith("risk:"):
        return t(
            "super_admin_user_risk_score",
            language,
        ).format(
            score=normalized_value.split(
                ":",
                1,
            )[1]
        )

    return risk_flags or "—"


def format_super_admin_user_card(
    card,
    language: str,
) -> str:
    roles = (
        ", ".join(
            super_admin_user_role_label(
                role,
                language,
            )
            for role in card.roles
        )
        if card.roles
        else "—"
    )

    scopes = (
        ", ".join(card.scopes)
        if card.scopes
        else t(
            "super_admin_user_scopes_empty",
            language,
        )
    )

    return t("super_admin_user_card", language).format(
        name=card.display_name,
        user_number=card.user_number,
        telegram_id=card.telegram_id,
        username=card.username,
        status=super_admin_user_status_label(
            card.status,
            language,
        ),
        active_role=super_admin_user_role_label(
            card.active_role,
            language,
        ),
        roles=roles,
        scopes=scopes,
        last_seen=card.last_seen,
        risk_flags=super_admin_user_risk_label(
            card.risk_flags,
            language,
        ),
        complaints=card.complaints_count,
        blacklist=card.blacklist_count,
    )


def super_admin_user_card_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_user_roles_btn", language),
                    callback_data="SA_USER_ROLES",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_user_scopes_btn", language),
                    callback_data="SA_USER_SCOPES",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_user_audit_btn", language),
                    callback_data="SA_USER_AUDIT",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonate_btn", language),
                    callback_data="SA_USER_IMPERSONATE",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_global_blacklist_btn", language).format(
                        count=0,
                    ),
                    callback_data="SA_USER_GLOBAL_BLACKLIST",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                ),
            ],
        ]
    )


@admin_users_router.callback_query(F.data.startswith("SA_USER_OPEN:"))
async def super_admin_open_user_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        index = int(callback.data.split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    ids = data.get("super_admin_user_search_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminUsersService(
                session
            ).get_super_admin_user_details(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_user_id=target_user_id,
            )

    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_selected_user_id=str(
            target_user_id
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_user_card(
            card,
            language,
        ),
        reply_markup=super_admin_user_card_keyboard(
            language
        ),
    )


def format_super_admin_user_roles(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_user_roles_empty", language)

    lines = [
        t("super_admin_user_roles_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("super_admin_user_role_card", language).format(
                number=index,
                role=item.role,
                status=item.status,
                scope=item.scope,
                granted_by=item.granted_by,
                granted_at=item.granted_at,
            )
        )

    return "\n\n".join(lines)


def super_admin_user_roles_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_role_scope_btn",
                        language,
                    ),
                    callback_data="SA_ROLE_SCOPE",
                ),
                InlineKeyboardButton(
                    text=t(
                        "super_admin_role_history_btn",
                        language,
                    ),
                    callback_data="SA_ROLE_HISTORY",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_back_to_menu_btn",
                        language,
                    ),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )


@admin_users_router.callback_query(F.data == "SA_USER_ROLES")
async def super_admin_user_roles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    target_user_id_raw = data.get("super_admin_selected_user_id")

    if not target_user_id_raw:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(target_user_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await AdminUsersService(
                session
            ).list_super_admin_user_roles(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_user_id=target_user_id,
            )

    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_user_roles(
            items,
            language,
        ),
        reply_markup=super_admin_user_roles_keyboard(
            language
        ),
    )


@admin_users_router.callback_query(F.data == "SA_USER_PROFILE")
async def super_admin_user_profile_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )


    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_selected_user_id"
    )

    if not target_user_id_raw:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            target_user_id_raw
        )

        async with get_session() as session:
            card = await AdminUsersService(
                session
            ).get_super_admin_user_details(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_user_id=target_user_id,
            )

    except (
        AdminUsersAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_user_card(
            card,
            language,
        ),
        reply_markup=super_admin_user_card_keyboard(
            language,
        ),
    )


def super_admin_read_only_admin_user_search_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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


def super_admin_read_only_admin_user_details_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_roles_btn", language),
                    callback_data=f"SA_RO_ADMIN_USER_ROLES:{index}",
                ),
                InlineKeyboardButton(
                    text=t("admin_user_history_btn", language),
                    callback_data=(
                        f"SA_RO_ADMIN_USER_HISTORY:{index}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_back_to_users_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_USERS",
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


@admin_users_router.callback_query(F.data == "SA_RO_ADMIN_USERS")
async def super_admin_read_only_admin_users_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
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

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "user_message_ids"
        ),
        preserve_current=True,
    )

    await state.set_state(
        AdminUsersFSM
        .entering_super_admin_impersonation_admin_user_search
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_ro_admin_user_search_prompt",
            language,
        ),
        reply_markup=(
            super_admin_read_only_admin_user_search_keyboard(
                language
            )
        ),
    )


@admin_users_router.message(
    AdminUsersFSM
    .entering_super_admin_impersonation_admin_user_search
)
async def super_admin_read_only_admin_users_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
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

    if not query:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_ro_admin_user_search_prompt",
                language,
            ),
            reply_markup=(
                super_admin_read_only_admin_user_search_keyboard(
                    language
                )
            ),
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
        await state.set_state(None)
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
            cards = await AdminUsersService(
                session
            ).search_impersonated_admin_users(
                platform_user_id=(
                    message.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
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
            text=t(
                "admin_user_search_error",
                language,
            ).format(
                error=str(exc),
            ),
            reply_markup=(
                super_admin_read_only_admin_user_search_keyboard(
                    language
                )
            ),
        )
        return

    await state.set_state(None)
    await state.update_data(
        super_admin_impersonation_admin_user_search_ids=[
            str(card.user_id)
            for card in cards
        ],
    )

    if not cards:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_search_empty",
                language,
            ),
            reply_markup=(
                super_admin_read_only_admin_user_search_keyboard(
                    language
                )
            ),
        )
        return

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_user_search_results",
            language,
        ).format(
            count=len(cards),
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

    for index, card in enumerate(cards):
        card_message = await message.answer(
            format_admin_user_search_card(
                card,
                number=index + 1,
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
                                "SA_RO_ADMIN_USER_OPEN:"
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

    navigation_message = await message.answer(
        t(
            "super_admin_ro_read_only_label",
            language,
        ),
        reply_markup=(
            super_admin_read_only_admin_user_search_keyboard(
                language
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_ro_admin_user_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )


@admin_users_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_USER_OPEN:")
)
async def super_admin_read_only_admin_user_open(
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
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get(
        "super_admin_impersonation_admin_user_search_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(user_ids)
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
        selected_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return


    try:
        async with get_session() as session:
            card = await AdminUsersService(
                session
            ).get_impersonated_admin_user_details(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                target_user_id=selected_user_id,
            )
    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "user_message_ids"
        ),
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_user_"
            "history_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_user_details(
            card,
            language,
        ),
        reply_markup=(
            super_admin_read_only_admin_user_details_keyboard(
                index=index,
                language=language,
            )
        ),
    )


@admin_users_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_USER_ROLES:")
)
async def super_admin_read_only_admin_user_roles(
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
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get(
        "super_admin_impersonation_admin_user_search_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(user_ids)
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
        selected_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return


    try:
        async with get_session() as session:
            card = await AdminUsersService(
                session
            ).get_impersonated_admin_user_details(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                target_user_id=selected_user_id,
            )
    except (
        AdminUsersAccessError,
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
        text=format_admin_user_roles(
            card,
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_ADMIN_USER_OPEN:"
                            f"{index}"
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


@admin_users_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_USER_HISTORY:")
)
async def super_admin_read_only_admin_user_history(
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
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get(
        "super_admin_impersonation_admin_user_search_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(user_ids)
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
        selected_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return


    try:
        async with get_session() as session:
            history = await AdminUsersService(
                session
            ).list_impersonated_admin_user_history(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                target_user_id=selected_user_id,
                limit=10,
            )
    except (
        AdminUsersAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_user_"
            "history_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_history_title",
            language,
        ).format(
            user_number=(
                f"user-{selected_user_id.hex[:8]}"
            ),
            count=len(history),
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

    if history:
        for number, card in enumerate(
            history,
            start=1,
        ):
            history_message = (
                await callback.message.answer(
                    format_admin_user_history_item(
                        card,
                        number=number,
                        language=language,
                    )
                )
            )
            rendered_message_ids.append(
                history_message.message_id
            )
    else:
        empty_message = await callback.message.answer(
            t(
                "admin_user_history_empty",
                language,
            )
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "super_admin_ro_read_only_label",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_ADMIN_USER_OPEN:"
                            f"{index}"
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
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_ro_admin_user_history_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )
