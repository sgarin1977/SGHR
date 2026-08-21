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
from handlers.admin_audit import (
    open_super_admin_audit_queue,
)
from handlers.admin_common import (
    AdminInterfaceLanguageMiddleware,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from handlers.admin_users import (
    super_admin_user_role_label,
)
from services.admin_governance import (
    AdminGovernanceAccessError,
    AdminGovernanceService,
)
from services.moderation import (
    ModerationError,
    SuperAdminRoleScopeCard,
)
from ui.texts import t


admin_governance_router = Router()


admin_governance_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_governance_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


normalize_language = normalize_admin_language


class AdminGovernanceFSM(StatesGroup):
    entering_super_admin_permission_search = State()
    entering_super_admin_permission_grant = State()
    confirming_super_admin_permission_grant = State()
    entering_super_admin_permission_revoke = State()
    confirming_super_admin_permission_revoke = State()


def parse_super_admin_permission_action(
    text: str | None,
) -> tuple[str, str, str] | None:
    parts = (text or "").strip().split(maxsplit=2)

    if len(parts) != 3:
        return None

    role, permission_code, reason = parts

    return (
        role.strip().lower(),
        permission_code.strip(),
        reason.strip(),
    )


def super_admin_scope_type_label(
    scope_type: str,
    language: str,
) -> str:
    return t(
        f"super_admin_scope_type_{scope_type}",
        language,
    )


def super_admin_scope_status_label(
    status: str,
    language: str,
) -> str:
    return t(
        f"super_admin_scope_status_{status}",
        language,
    )


def format_super_admin_role_scope_card(
    card: SuperAdminRoleScopeCard,
    *,
    number: int,
    language: str,
) -> str:
    lines = [
        t(
            "super_admin_scope_card_user",
            language,
        ).format(
            number=number,
            user_number=card.user_number,
        ),
        t(
            "super_admin_scope_card_role",
            language,
        ).format(
            role=super_admin_user_role_label(
                card.role,
                language,
            )
        ),
        t(
            "super_admin_scope_card_type",
            language,
        ).format(
            scope_type=super_admin_scope_type_label(
                card.scope_type,
                language,
            )
        ),
        t(
            "super_admin_scope_card_value",
            language,
        ).format(
            scope_value=card.scope_value,
        ),
        t(
            "super_admin_scope_card_status",
            language,
        ).format(
            status=super_admin_scope_status_label(
                card.status,
                language,
            )
        ),
        t(
            "super_admin_scope_card_reason",
            language,
        ).format(
            reason=card.reason or t(
                "super_admin_value_not_specified",
                language,
            )
        ),
        t(
            "super_admin_scope_card_granted_by",
            language,
        ).format(
            user_number=card.created_by or t(
                "super_admin_value_not_specified",
                language,
            )
        ),
        t(
            "super_admin_scope_card_created_at",
            language,
        ).format(
            created_at=card.created_at,
        ),
    ]

    if card.status == "revoked":
        lines.extend(
            [
                t(
                    "super_admin_scope_card_revoked_by",
                    language,
                ).format(
                    user_number=card.revoked_by or t(
                        "super_admin_value_not_specified",
                        language,
                    )
                ),
                t(
                    "super_admin_scope_card_revoked_at",
                    language,
                ).format(
                    revoked_at=card.revoked_at,
                ),
            ]
        )

    return "\n".join(lines)


def format_super_admin_role_scopes_screen(
    cards: list[SuperAdminRoleScopeCard],
    *,
    title_lines: list[str],
    page: int,
    language: str,
) -> str:
    header = "\n".join(title_lines)

    if not cards:
        return (
            f"{header}\n\n"
            f"{t('super_admin_scopes_empty', language)}"
        )

    start_number = page * 5 + 1

    rendered_cards = [
        format_super_admin_role_scope_card(
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


def super_admin_role_scope_card_keyboard(
    *,
    index: int,
    status: str,
    language: str,
) -> InlineKeyboardMarkup | None:
    # Super Admin can inspect scopes but cannot mutate them.
    return None


def super_admin_role_scopes_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    user_filtered: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "super_admin_scopes_view_active",
                    language,
                ),
                callback_data=(
                    f"SA_SCOPES_QUEUE:active:{page}:"
                    f"{1 if user_filtered else 0}"
                ),
            ),
            InlineKeyboardButton(
                text=t(
                    "super_admin_scopes_view_history",
                    language,
                ),
                callback_data=(
                    f"SA_SCOPES_QUEUE:history:{page}:"
                    f"{1 if user_filtered else 0}"
                ),
            ),
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_SCOPES_QUEUE:{view}:"
                    f"{page - 1}:"
                    f"{1 if user_filtered else 0}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_SCOPES_QUEUE:{view}:"
                    f"{page + 1}:"
                    f"{1 if user_filtered else 0}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "super_admin_scopes_to_panel_btn",
                    language,
                ),
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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def super_admin_role_scopes_screen_keyboard(
    cards: list[SuperAdminRoleScopeCard],
    *,
    view: str,
    page: int,
    has_next: bool,
    user_filtered: bool,
    language: str,
) -> InlineKeyboardMarkup:
    return super_admin_role_scopes_keyboard(
        view=view,
        page=page,
        has_next=has_next,
        user_filtered=user_filtered,
        language=language,
    )


@admin_governance_router.callback_query(F.data == "SA_SCOPES")
async def open_super_admin_role_scopes(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view="active",
        page=0,
        user_filtered=False,
    )


@admin_governance_router.callback_query(F.data == "SA_USER_SCOPES")
async def open_super_admin_user_role_scopes(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view="active",
        page=0,
        user_filtered=True,
    )


@admin_governance_router.callback_query(F.data.startswith("SA_SCOPES_QUEUE:"))
async def change_super_admin_role_scopes_queue(
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

    user_filtered = (
        len(parts) > 3
        and parts[3] == "1"
    )

    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view=view,
        page=page,
        user_filtered=user_filtered,
    )


async def open_super_admin_role_scopes_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
    user_filtered: bool,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    selected_user_id = None

    if user_filtered:
        raw_selected_user_id = data.get(
            "super_admin_selected_user_id"
        )

        try:
            selected_user_id = UUID(
                str(raw_selected_user_id)
            )
        except (TypeError, ValueError):
            await callback.answer(
                t(
                    "super_admin_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

    try:
        async with get_session() as session:
            result = await AdminGovernanceService(
                session
            ).list_role_scopes(
                platform_user_id=callback.from_user.id,
                user_id=selected_user_id,
                view=view,
                page=page,
                page_size=5,
            )
    except (
        AdminGovernanceAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_scope_ids=[
            str(card.scope_id)
            for card in result.items
        ],
        super_admin_scope_labels=[
            (
                f"{super_admin_scope_type_label(
                    card.scope_type,
                    language,
                )}: {card.scope_value}"
            )
            for card in result.items
        ],
        super_admin_scope_user_ids=[
            str(card.user_id)
            for card in result.items
        ],
        super_admin_scope_view=result.view,
        super_admin_scope_page=result.page,
        super_admin_scope_user_filtered=(
            user_filtered
        ),
        admin_scope_list_message_ids=[],
    )

    view_label = t(
        (
            "super_admin_scopes_view_history"
            if result.view == "history"
            else "super_admin_scopes_view_active"
        ),
        language,
    )

    title_lines = [
        t(
            "super_admin_scopes_title",
            language,
        ),
        t(
            "super_admin_scopes_section",
            language,
        ).format(
            view=view_label,
        ),
        t(
            "super_admin_scopes_count",
            language,
        ).format(
            count=len(result.items),
        ),
    ]

    if selected_user_id:
        title_lines.append(
            t(
                "super_admin_scopes_for_user",
                language,
            ).format(
                user_number=(
                    f"user-{selected_user_id.hex[:8]}"
                ),
            )
        )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_role_scopes_screen(
                result.items,
                title_lines=title_lines,
                page=result.page,
                language=language,
            )
        ),
        reply_markup=(
            super_admin_role_scopes_screen_keyboard(
                result.items,
                view=result.view,
                page=result.page,
                has_next=result.has_next,
                user_filtered=user_filtered,
                language=language,
            )
        ),
    )


def format_super_admin_permissions(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_permissions_empty", language)

    lines = [
        t("super_admin_permissions_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("super_admin_permission_card", language).format(
                number=index,
                role=item.role,
                permission_code=item.permission_code,
                scope=item.scope,
                status=item.status,
                granted_by=item.granted_by,
                created_at=item.created_at,
                description=item.description,
            )
        )

    return "\n\n".join(lines)


def super_admin_permissions_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_search_btn", language),
                    callback_data="SA_PERMISSION_SEARCH",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_grant_btn", language),
                    callback_data="SA_PERMISSION_GRANT",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_permission_revoke_btn", language),
                    callback_data="SA_PERMISSION_REVOKE",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_history_btn", language),
                    callback_data="SA_PERMISSION_HISTORY",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )


def super_admin_permission_confirm_keyboard(
    action: str,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_confirm_btn", language),
                    callback_data=f"SA_PERMISSION_{action.upper()}_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_cancel_btn", language),
                    callback_data="SA_PERMISSION_CANCEL",
                )
            ],
        ]
    )


async def show_super_admin_permissions(
    event: CallbackQuery | Message,
    state: FSMContext,
    *,
    query: str = "",
    actor_telegram_id: int | str | None = None,
) -> None:
    language = normalize_language(
        event.from_user.language_code
    )
    callback_answered = False

    if isinstance(event, CallbackQuery):
        await event.answer()
        callback_answered = True

    try:
        async with get_session() as session:
            items = await AdminGovernanceService(
                session
            ).list_permission_matrix(
                platform_user_id=(
                    actor_telegram_id
                    or event.from_user.id
                ),
                query=query,
                limit=10,
            )

    except (
        AdminGovernanceAccessError,
        ModerationError,
    ) as exc:
        error_text = str(exc)

        if isinstance(event, CallbackQuery):
            await replace_admin_callback_screen(
                callback=event,
                state=state,
                text=error_text,
                callback_answered=callback_answered,
            )
        else:
            await replace_admin_input_screen(
                message=event,
                state=state,
                text=error_text,
            )
        return

    await state.update_data(
        super_admin_permission_query=query,
    )
    await state.set_state(None)

    text = format_super_admin_permissions(
        items,
        language,
    )
    keyboard = super_admin_permissions_keyboard(
        language
    )

    if isinstance(event, CallbackQuery):
        await replace_admin_callback_screen(
            callback=event,
            state=state,
            text=text,
            reply_markup=keyboard,
            callback_answered=callback_answered,
        )
    else:
        await replace_admin_input_screen(
            message=event,
            state=state,
            text=text,
            reply_markup=keyboard,
        )


@admin_governance_router.callback_query(F.data == "SA_PERMISSIONS")
async def super_admin_permissions(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_permissions(
        callback,
        state,
        query="",
    )


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_SEARCH")
async def super_admin_permission_search_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(
        AdminGovernanceFSM.entering_super_admin_permission_search
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_search_prompt",
            language,
        ),
    )


@admin_governance_router.message(AdminGovernanceFSM.entering_super_admin_permission_search)
async def super_admin_permission_search_message(
    message: Message,
    state: FSMContext,
):
    query = (message.text or "").strip()

    await show_super_admin_permissions(
        message,
        state,
        query=query,
    )


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_GRANT")
async def super_admin_permission_grant_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(
        AdminGovernanceFSM.entering_super_admin_permission_grant
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_action_format",
            language,
        ),
    )


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_REVOKE")
async def super_admin_permission_revoke_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(
        AdminGovernanceFSM.entering_super_admin_permission_revoke
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_action_format",
            language,
        ),
    )


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_HISTORY")
async def super_admin_permission_history(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminGovernanceService(
                session
            ).require_super_admin_actor(
                platform_user_id=callback.from_user.id
            )
    except AdminGovernanceAccessError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="permission",
        page=0,
    )


@admin_governance_router.message(
    AdminGovernanceFSM.entering_super_admin_permission_grant
)
async def super_admin_permission_grant_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    parsed = parse_super_admin_permission_action(
        message.text
    )

    if not parsed:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_permission_bad_format",
                language,
            ),
        )
        return

    role, permission_code, reason = parsed

    await state.update_data(
        super_admin_permission_action="grant",
        super_admin_permission_role=role,
        super_admin_permission_code=permission_code,
        super_admin_permission_reason=reason,
    )
    await state.set_state(
        AdminGovernanceFSM.confirming_super_admin_permission_grant
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_permission_grant_confirm",
            language,
        ).format(
            role=role,
            permission_code=permission_code,
            reason=reason,
        ),
        reply_markup=(
            super_admin_permission_confirm_keyboard(
                "grant",
                language,
            )
        ),
    )


@admin_governance_router.message(
    AdminGovernanceFSM.entering_super_admin_permission_revoke
)
async def super_admin_permission_revoke_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    parsed = parse_super_admin_permission_action(
        message.text
    )

    if not parsed:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_permission_bad_format",
                language,
            ),
        )
        return

    role, permission_code, reason = parsed

    await state.update_data(
        super_admin_permission_action="revoke",
        super_admin_permission_role=role,
        super_admin_permission_code=permission_code,
        super_admin_permission_reason=reason,
    )
    await state.set_state(
        AdminGovernanceFSM.confirming_super_admin_permission_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_permission_revoke_confirm",
            language,
        ).format(
            role=role,
            permission_code=permission_code,
            reason=reason,
        ),
        reply_markup=(
            super_admin_permission_confirm_keyboard(
                "revoke",
                language,
            )
        ),
    )


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_CANCEL")
async def super_admin_permission_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.update_data(
        super_admin_permission_action=None,
        super_admin_permission_role=None,
        super_admin_permission_code=None,
        super_admin_permission_reason=None,
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_cancelled",
            language,
        ),
        reply_markup=super_admin_permissions_keyboard(
            language
        ),
    )


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_GRANT_CONFIRM")
async def super_admin_permission_grant_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    await super_admin_permission_execute(callback, state, expected_action="grant")


@admin_governance_router.callback_query(F.data == "SA_PERMISSION_REVOKE_CONFIRM")
async def super_admin_permission_revoke_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    await super_admin_permission_execute(callback, state, expected_action="revoke")


async def super_admin_permission_execute(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    expected_action: str,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    action = data.get("super_admin_permission_action")
    role = data.get("super_admin_permission_role")
    permission_code = data.get("super_admin_permission_code")
    reason = data.get("super_admin_permission_reason")

    if action != expected_action or not role or not permission_code or not reason:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = AdminGovernanceService(session)

            if action == "grant":
                await service.grant_role_permission(
                    platform_user_id=callback.from_user.id,
                    role=role,
                    permission_code=permission_code,
                    reason=reason,
                )
            else:
                await service.revoke_role_permission(
                    platform_user_id=callback.from_user.id,
                    role=role,
                    permission_code=permission_code,
                    reason=reason,
                )

    except (
        AdminGovernanceAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_permission_action=None,
        super_admin_permission_role=None,
        super_admin_permission_code=None,
        super_admin_permission_reason=None,
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_changed",
            language,
        ),
        reply_markup=super_admin_permissions_keyboard(
            language
        ),
    )


@admin_governance_router.callback_query(F.data == "SA_ROLE_SCOPE")
async def super_admin_role_scope_alias(
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

    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view="active",
        page=0,
        user_filtered=True,
    )
