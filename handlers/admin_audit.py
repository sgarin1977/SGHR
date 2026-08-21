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
from services.admin_audit import (
    AdminAuditAccessError,
    AdminAuditService,
)
from services.moderation import (
    AdminAuditCard,
    ModerationError,
)
from ui.texts import t


ADMIN_AUDIT_PAGE_SIZE = 5

admin_audit_router = Router()

admin_audit_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)

normalize_language = normalize_admin_language


def format_admin_audit_card(
    card: AdminAuditCard,
    *,
    number: int,
    language: str,
) -> str:
    return t(
        "admin_audit_card",
        language,
    ).format(
        number=number,
        date=card.date,
        actor=card.actor,
        action=card.action,
        target=card.target,
        reason=card.reason,
        source=card.source,
    )


def admin_audit_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_open_btn", language),
                    callback_data=f"ADM_AUDIT_OPEN:{index}",
                )
            ]
        ]
    )


def super_admin_audit_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_open_btn", language),
                    callback_data=f"SA_AUDIT_OPEN:{index}",
                )
            ]
        ]
    )


def admin_audit_details_keyboard(
    *,
    target_type: str,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_back_to_list_btn", language),
                    callback_data=(
                        f"ADM_AUDIT_QUEUE:{target_type}:{page}"
                    ),
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


def admin_audit_queue_keyboard(
    *,
    target_type: str,
    page: int,
    has_next: bool,
    language: str,
    prefix: str = "ADM_AUDIT",
    back_callback: str = "ADM_PANEL",
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_audit_filter_btn", language),
                callback_data=f"{prefix}_FILTER",
            )
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"{prefix}_QUEUE:{target_type}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"{prefix}_QUEUE:{target_type}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data=back_callback,
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_audit_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_all", language),
                    callback_data="ADM_AUDIT_QUEUE:all:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_users", language),
                    callback_data="ADM_AUDIT_QUEUE:user:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_specialists", language),
                    callback_data="ADM_AUDIT_QUEUE:specialist:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_support", language),
                    callback_data="ADM_AUDIT_QUEUE:support_ticket:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_complaints", language),
                    callback_data="ADM_AUDIT_QUEUE:complaint:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_reviews", language),
                    callback_data="ADM_AUDIT_QUEUE:review:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_portfolio", language),
                    callback_data=(
                        "ADM_AUDIT_QUEUE:"
                        "specialist_portfolio_item:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_blacklist", language),
                    callback_data="ADM_AUDIT_QUEUE:blacklist:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_LOGS",
                )
            ],
        ]
    )


def super_admin_audit_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_all", language),
                    callback_data="SA_AUDIT_QUEUE:all:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_users", language),
                    callback_data="SA_AUDIT_QUEUE:user:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_specialists", language),
                    callback_data="SA_AUDIT_QUEUE:specialist:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_support", language),
                    callback_data="SA_AUDIT_QUEUE:support_ticket:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_complaints", language),
                    callback_data="SA_AUDIT_QUEUE:complaint:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_reviews", language),
                    callback_data="SA_AUDIT_QUEUE:review:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_portfolio", language),
                    callback_data="SA_AUDIT_QUEUE:specialist_portfolio_item:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_blacklist", language),
                    callback_data="SA_AUDIT_QUEUE:blacklist:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
        ]
    )


def super_admin_read_only_admin_audit_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_all", language),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:all:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_users",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:user:0"
                    ),
                ),
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_specialists",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:specialist:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_support",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:"
                        "support_ticket:0"
                    ),
                ),
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_complaints",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:complaint:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_reviews",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:review:0"
                    ),
                ),
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_portfolio",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:"
                        "specialist_portfolio_item:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_blacklist",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:blacklist:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_btn",
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


@admin_audit_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_AUDIT_QUEUE:")
)
async def super_admin_read_only_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, target_type, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
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
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminAuditService(
                session
            ).open_impersonated_admin_audit(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                target_type=target_type,
                page=page,
                page_size=(
                    ADMIN_AUDIT_PAGE_SIZE
                ),
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
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

    await state.update_data(
        super_admin_impersonation_admin_audit_action_ids=[
            str(card.action_id)
            for card in result.items
        ],
        super_admin_impersonation_admin_audit_target_type=(
            result.target_type
        ),
        super_admin_impersonation_admin_audit_page=result.page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "audit_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "super_admin_ro_admin_audit_title",
            language,
        ).format(
            target_type=result.target_type,
            page=result.page + 1,
            count=len(result.items),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not result.items:
        empty_message = await callback.message.answer(
            t("admin_audit_empty", language),
            reply_markup=admin_audit_queue_keyboard(
                target_type=result.target_type,
                page=result.page,
                has_next=False,
                language=language,
                prefix="SA_RO_ADMIN_AUDIT",
                back_callback="SA_RO_ADMIN_HOME",
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            super_admin_ro_admin_audit_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    start_number = (
        result.page
        * ADMIN_AUDIT_PAGE_SIZE
        + 1
    )

    for index, card in enumerate(result.items):
        card_message = await callback.message.answer(
            format_admin_audit_card(
                card,
                number=start_number + index,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_audit_open_btn",
                                language,
                            ),
                            callback_data=(
                                "SA_RO_ADMIN_AUDIT_OPEN:"
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
        reply_markup=admin_audit_queue_keyboard(
            target_type=result.target_type,
            page=result.page,
            has_next=result.has_next,
            language=language,
            prefix="SA_RO_ADMIN_AUDIT",
            back_callback="SA_RO_ADMIN_HOME",
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_ro_admin_audit_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()


@admin_audit_router.callback_query(
    F.data == "SA_RO_ADMIN_AUDIT_FILTER"
)
async def super_admin_read_only_admin_audit_filter(
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
            "audit_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_audit_filter_title",
            language,
        ),
        reply_markup=(
            super_admin_read_only_admin_audit_filter_keyboard(
                language
            )
        ),
    )


@admin_audit_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_AUDIT_OPEN:")
)
async def super_admin_read_only_admin_audit_open(
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
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    action_ids = data.get(
        "super_admin_impersonation_admin_audit_action_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(action_ids)
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
        action_id = UUID(action_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminAuditService(
                session
            ).get_impersonated_admin_audit_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                action_id=action_id,
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
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

    target_type = str(
        data.get(
            "super_admin_impersonation_admin_audit_target_type"
        ) or "all"
    )
    page = int(
        data.get(
            "super_admin_impersonation_admin_audit_page"
        ) or 0
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "audit_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_audit_details",
            language,
        ).format(
            date=card.date,
            actor=card.actor,
            action=card.action,
            target=card.target,
            target_type=card.target_type,
            reason=card.reason,
            source=card.source,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_admin_back_to_audit_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_ADMIN_AUDIT_QUEUE:"
                            f"{target_type}:{page}"
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


@admin_audit_router.callback_query(F.data == "SA_USER_AUDIT")
async def super_admin_user_audit_alias(
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

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="user",
        page=0,
    )


@admin_audit_router.callback_query(F.data == "ADM_LOGS")
async def admin_audit_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_admin_audit_queue(
        callback,
        state,
        target_type="all",
        page=0,
    )


@admin_audit_router.callback_query(
    F.data.startswith("ADM_AUDIT_QUEUE:")
)
async def change_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    if len(parts) != 3:
        await callback.answer()
        return

    target_type = parts[1]

    try:
        page = max(0, int(parts[2]))
    except ValueError:
        page = 0

    await open_admin_audit_queue(
        callback,
        state,
        target_type=target_type,
        page=page,
    )


@admin_audit_router.callback_query(
    F.data == "ADM_AUDIT_FILTER"
)
async def open_admin_audit_filter(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminAuditService(
                session
            ).require_regional_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_audit_message_ids",
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_audit_message_ids"
        ),
        preserve_current=True,
    )

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
        text=t(
            "admin_audit_filter_title",
            language,
        ),
        reply_markup=admin_audit_filter_keyboard(
            language
        ),
    )


@admin_audit_router.callback_query(
    F.data.startswith("ADM_AUDIT_OPEN:")
)
async def open_admin_audit_details(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int((callback.data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    action_ids = data.get("admin_audit_action_ids") or []
    target_type = data.get("admin_audit_target_type") or "all"
    page = int(data.get("admin_audit_page") or 0)

    if index < 0 or index >= len(action_ids):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        action_id = UUID(action_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminAuditService(
                session
            ).get_regional_audit_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                action_id=action_id,
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
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

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_audit_message_ids",
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_audit_details",
            language,
        ).format(
            date=card.date,
            actor=card.actor,
            action=card.action,
            target=card.target,
            target_type=card.target_type,
            reason=card.reason,
            source=card.source,
        ),
        reply_markup=admin_audit_details_keyboard(
            target_type=target_type,
            page=page,
            language=language,
        ),
    )


async def open_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    target_type: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            result = await AdminAuditService(
                session
            ).open_regional_audit(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_type=target_type,
                page=page,
                page_size=(
                    ADMIN_AUDIT_PAGE_SIZE
                ),
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
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

    await state.update_data(
        admin_audit_action_ids=[
            str(card.action_id)
            for card in result.items
        ],
        admin_audit_target_type=result.target_type,
        admin_audit_page=result.page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_audit_message_ids",
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "admin_audit_queue_title",
            language,
        ).format(
            filter=result.target_type,
            page=result.page + 1,
            count=len(result.items),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not result.items:
        empty_message = await callback.message.answer(
            t(
                "admin_audit_empty",
                language,
            ),
            reply_markup=admin_audit_queue_keyboard(
                target_type=result.target_type,
                page=result.page,
                has_next=False,
                language=language,
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            admin_audit_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    start_number = (
        result.page
        * ADMIN_AUDIT_PAGE_SIZE
        + 1
    )

    for offset, card in enumerate(result.items):
        card_message = await callback.message.answer(
            format_admin_audit_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=(
                super_admin_audit_card_keyboard(
                    index=offset,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "admin_audit_actions_title",
            language,
        ),
        reply_markup=admin_audit_queue_keyboard(
            target_type=result.target_type,
            page=result.page,
            has_next=result.has_next,
            language=language,
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_audit_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()


@admin_audit_router.callback_query(F.data == "SA_AUDIT")
async def super_admin_audit_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="all",
        page=0,
    )


@admin_audit_router.callback_query(F.data.startswith("SA_AUDIT_QUEUE:"))
async def change_super_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    if len(parts) != 3:
        await callback.answer()
        return

    target_type = parts[1]

    try:
        page = max(0, int(parts[2]))
    except ValueError:
        page = 0

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type=target_type,
        page=page,
    )


@admin_audit_router.callback_query(F.data == "SA_AUDIT_FILTER")
async def open_super_admin_audit_filter(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminAuditService(
                session
            ).require_super_admin_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_audit_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_audit_filter_title",
            language,
        ),
        reply_markup=(
            super_admin_audit_filter_keyboard(
                language
            )
        ),
    )


async def open_super_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    target_type: str,
    page: int,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            result = await AdminAuditService(
                session
            ).open_super_admin_audit(
                platform_user_id=(
                    callback.from_user.id
                ),
                target_type=target_type,
                page=page,
                page_size=(
                    ADMIN_AUDIT_PAGE_SIZE
                ),
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
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

    await state.update_data(
        super_admin_audit_action_ids=[
            str(card.action_id)
            for card in result.items
        ],
        super_admin_audit_target_type=result.target_type,
        super_admin_audit_page=result.page,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_audit_message_ids"
        ),
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "admin_audit_queue_title",
            language,
        ).format(
            filter=result.target_type,
            page=result.page + 1,
            count=len(result.items),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not result.items:
        empty_message = await callback.message.answer(
            t(
                "admin_audit_empty",
                language,
            ),
            reply_markup=admin_audit_queue_keyboard(
                target_type=result.target_type,
                page=result.page,
                has_next=False,
                language=language,
                prefix="SA_AUDIT",
                back_callback="ADM_PANEL",
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            super_admin_audit_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        await callback.answer()
        return

    start_number = (
        result.page
        * ADMIN_AUDIT_PAGE_SIZE
        + 1
    )

    for offset, card in enumerate(result.items):
        card_message = await callback.message.answer(
            format_admin_audit_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=(
                super_admin_audit_card_keyboard(
                    index=offset,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "admin_audit_actions_title",
            language,
        ),
        reply_markup=admin_audit_queue_keyboard(
            target_type=result.target_type,
            page=result.page,
            has_next=result.has_next,
            language=language,
            prefix="SA_AUDIT",
            back_callback="ADM_PANEL",
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        super_admin_audit_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

    await callback.answer()


@admin_audit_router.callback_query(F.data.startswith("SA_AUDIT_OPEN:"))
async def open_super_admin_audit_details(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        index = int((callback.data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    action_ids = data.get("super_admin_audit_action_ids") or []
    target_type = data.get("super_admin_audit_target_type") or "all"
    page = int(data.get("super_admin_audit_page") or 0)

    if index < 0 or index >= len(action_ids):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        action_id = UUID(action_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await AdminAuditService(
                session
            ).get_super_admin_audit_detail(
                platform_user_id=(
                    callback.from_user.id
                ),
                action_id=action_id,
            )
    except AdminAuditAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
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

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_audit_message_ids"
        ),
        preserve_current=True,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_audit_event_detail",
            language,
        ).format(
            timestamp=card.timestamp,
            actor=card.actor,
            action=card.action,
            target_type=card.target_type,
            target=card.target,
            reason=card.reason,
            before_summary=card.before_summary,
            after_summary=card.after_summary,
            payload_summary=card.payload_summary,
            correlation_id=card.correlation_id,
            source=card.source,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_audit_back_to_list_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_AUDIT_QUEUE:"
                            f"{target_type}:{page}"
                        ),
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
        ),
    )
