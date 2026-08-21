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
    ADMIN_SPECIALIST_PAGE_SIZE,
    MODERATOR_PROFILE_PAGE_SIZE,
    READ_ONLY_MODERATION_TARGET_ROLES,
    AdminInterfaceLanguageMiddleware,
    clear_admin_message_group,
    format_admin_menu,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from handlers.admin_specialists import (
    format_admin_specialist_item,
    format_pending_profile_queue_item,
    format_pending_specialist_card,
)
from handlers.admin_support import (
    show_super_admin_support_read_only_cabinet,
)
from handlers.admin_users import (
    super_admin_user_role_label,
)
from services.admin_impersonation import (
    AdminImpersonationAccessError,
    AdminImpersonationService,
)
from services.admin_specialists import (
    AdminSpecialistsAccessError,
)
from services.moderation import (
    ImpersonationRoleUnavailableError,
    ModerationError,
)
from ui.texts import t


READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE = 5


admin_impersonation_router = Router()


admin_impersonation_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_impersonation_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


normalize_language = normalize_admin_language


class AdminImpersonationFSM(StatesGroup):
    entering_super_admin_impersonation_reason = State()


def super_admin_impersonation_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_client_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:client",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_specialist_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:specialist",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_support_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:support",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_moderator_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:moderator",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_admin_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:admin",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_stop_btn", language),
                    callback_data="SA_IMPERSONATE_STOP",
                ),
            ],
        ]
    )


def super_admin_read_only_client_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_client_dialogs_btn",
                        language,
                    ),
                    callback_data="SA_RO_CLIENT_DIALOGS:0",
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


def super_admin_read_only_specialist_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_specialist_cabinets_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_SPECIALIST_CABINETS:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_specialist_profile_btn",
                        language,
                    ),
                    callback_data="SA_RO_SPECIALIST_PROFILE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_specialist_dialogs_btn",
                        language,
                    ),
                    callback_data="SA_RO_SPECIALIST_DIALOGS:0",
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


def super_admin_read_only_specialist_cabinets_keyboard(
    *,
    items_count: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    start_number = (
        page
        * READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE
        + 1
    )

    for index in range(items_count):
        number = start_number + index

        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_specialist_open_cabinet_btn",
                        language,
                    ).format(
                        number=number,
                    ),
                    callback_data=(
                        "SA_RO_SPECIALIST_CABINET:"
                        f"{index}"
                    ),
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t(
                    "admin_previous_page",
                    language,
                ),
                callback_data=(
                    "SA_RO_SPECIALIST_CABINETS:"
                    f"{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t(
                    "admin_next_page",
                    language,
                ),
                callback_data=(
                    "SA_RO_SPECIALIST_CABINETS:"
                    f"{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "super_admin_ro_specialist_cabinets_back_btn",
                    language,
                ),
                callback_data="SA_RO_SPECIALIST_HOME",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def super_admin_read_only_moderator_menu_keyboard(
    language: str,
    *,
    back_callback: str = "SA_IMPERSONATE_MENU",
    back_text_key: str = (
        "super_admin_impersonation_change_cabinet_btn"
    ),
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_pending_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_QUEUE:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_portfolio_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_PORTFOLIO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_reviews_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_REVIEWS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_blacklist_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_BLACKLIST:active:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_complaints_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_COMPLAINTS:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(back_text_key, language),
                    callback_data=back_callback,
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


def super_admin_read_only_moderator_queue_keyboard(
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
                callback_data=f"SA_RO_MOD_QUEUE:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_QUEUE:{page + 1}",
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


def format_super_admin_read_only_moderator_cabinets_screen(
    items,
    *,
    page: int,
    language: str,
) -> str:
    parts = [
        t(
            "super_admin_ro_moderator_queue_title",
            language,
        ).format(
            page=page + 1,
            count=len(items),
        ),
        t(
            "super_admin_ro_read_only_label",
            language,
        ),
    ]

    if not items:
        parts.append(
            t(
                "admin_no_pending_profiles",
                language,
            )
        )
    else:
        for index, item in enumerate(items):
            number = (
                page
                * MODERATOR_PROFILE_PAGE_SIZE
                + index
                + 1
            )
            parts.append(
                format_pending_profile_queue_item(
                    item,
                    number=number,
                    language=language,
                )
            )

    return "\n\n".join(parts)


def super_admin_read_only_moderator_cabinets_screen_keyboard(
    *,
    items_count: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

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
                        "super_admin_ro_moderator_open_profile_btn",
                        language,
                    ).format(
                        number=number
                    ),
                    callback_data=(
                        "SA_RO_MOD_PROFILE:"
                        f"{index}"
                    ),
                )
            ]
        )

    rows.extend(
        super_admin_read_only_moderator_queue_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ).inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def super_admin_read_only_admin_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_users_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_USERS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_moderation_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_MODERATION",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_specialists_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_SPECIALISTS:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_escalated_tickets_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_SUPPORT:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_audit_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_AUDIT_QUEUE:all:0",
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


@admin_impersonation_router.callback_query(
    F.data.startswith(
        "SA_RO_ADMIN_SPECIALISTS:"
    )
)
async def super_admin_read_only_admin_specialists(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int(
                (callback.data or "").split(
                    ":",
                    1,
                )[1]
            ),
            0,
        )
    except (
        IndexError,
        TypeError,
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

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        != "admin"
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
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
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cabinet_page = await AdminImpersonationService(
                session
            ).open_admin_specialists(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                status="all",
                page=page,
                page_size=(
                    ADMIN_SPECIALIST_PAGE_SIZE
                ),
            )
    except (
        AdminImpersonationAccessError,
        AdminSpecialistsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_admin_professional_cabinet_ids=[
            str(
                item.professional_cabinet_id
            )
            for item in cabinet_page.items
        ],
        super_admin_impersonation_admin_specialist_ids=[
            str(item.specialist_id)
            for item in cabinet_page.items
        ],
        super_admin_impersonation_admin_specialist_page=(
            cabinet_page.page
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_read_only_admin_cabinets_screen(
                cabinet_page.items,
                page=cabinet_page.page,
                language=language,
            )
        ),
        reply_markup=(
            super_admin_read_only_admin_cabinets_screen_keyboard(
                items_count=len(
                    cabinet_page.items
                ),
                page=cabinet_page.page,
                has_next=(
                    cabinet_page.has_next
                ),
                language=language,
            )
        ),
    )


def format_super_admin_read_only_admin_cabinets_screen(
    items,
    *,
    page: int,
    language: str,
) -> str:
    parts = [
        t(
            "super_admin_ro_admin_specialists_title",
            language,
        ).format(
            page=page + 1,
            count=len(items),
        ),
        t(
            "super_admin_ro_read_only_label",
            language,
        ),
    ]

    if not items:
        parts.append(
            t(
                "admin_specialists_empty",
                language,
            )
        )
    else:
        for index, item in enumerate(items):
            number = (
                page
                * ADMIN_SPECIALIST_PAGE_SIZE
                + index
                + 1
            )
            parts.append(
                format_admin_specialist_item(
                    item,
                    number=number,
                    language=language,
                )
            )

    return "\n\n".join(parts)


def super_admin_read_only_admin_specialists_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[
        list[InlineKeyboardButton]
    ] = []
    navigation: list[
        InlineKeyboardButton
    ] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t(
                    "admin_prev",
                    language,
                ),
                callback_data=(
                    "SA_RO_ADMIN_SPECIALISTS:"
                    f"{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t(
                    "admin_next",
                    language,
                ),
                callback_data=(
                    "SA_RO_ADMIN_SPECIALISTS:"
                    f"{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(
            navigation
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_HOME"
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
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def super_admin_read_only_admin_cabinets_screen_keyboard(
    *,
    items_count: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index in range(items_count):
        number = (
            page
            * ADMIN_SPECIALIST_PAGE_SIZE
            + index
            + 1
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_open_specialist_btn",
                        language,
                    ).format(
                        number=number
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_SPECIALIST_OPEN:"
                        f"{index}"
                    ),
                )
            ]
        )

    rows.extend(
        super_admin_read_only_admin_specialists_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ).inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def super_admin_preview_status_label(
    status: str,
    language: str,
) -> str:
    return t(
        f"super_admin_preview_status_{status}",
        language,
    )


def super_admin_preview_availability_label(
    availability_status: str | bool,
    language: str,
) -> str:
    if isinstance(
        availability_status,
        bool,
    ):
        normalized_status = (
            "available"
            if availability_status
            else "temporarily_unavailable"
        )
    else:
        normalized_status = str(
            availability_status
            or "temporarily_unavailable"
        ).strip().lower()

    key = {
        "available": (
            "spec_availability_now"
        ),
        "busy": (
            "spec_availability_busy"
        ),
        "vacation": (
            "spec_availability_vacation"
        ),
        "temporarily_unavailable": (
            "spec_availability_unavailable"
        ),
    }.get(
        normalized_status,
        "spec_availability_unavailable",
    )

    return t(
        key,
        language,
    )


async def show_super_admin_specialist_read_only_cabinets(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )

    if (
        not target_user_id_raw
        or not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        != "specialist"
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(target_user_id_raw)
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            options = await AdminImpersonationService(
                session
            ).list_specialist_cabinets(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                language=language,
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        AdminImpersonationAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    normalized_page = max(0, page)
    start = (
        normalized_page
        * READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE
    )

    if options and start >= len(options):
        normalized_page = (
            len(options) - 1
        ) // READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE
        start = (
            normalized_page
            * READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE
        )

    visible_options = options[
        start:
        start + READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE
    ]
    has_next = (
        start
        + READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE
        < len(options)
    )

    await state.update_data(
        super_admin_impersonation_specialist_cabinet_ids=[
            str(option.professional_cabinet_id)
            for option in visible_options
        ],
    )

    header = t(
        "super_admin_ro_specialist_cabinets_title",
        language,
    ).format(
        page=normalized_page + 1,
        count=len(options),
    )

    if visible_options:
        rendered_options = [
            t(
                "super_admin_ro_specialist_cabinet_item",
                language,
            ).format(
                number=start + index + 1,
                title=(
                    option.title
                    or t(
                        "super_admin_value_not_specified",
                        language,
                    )
                ),
                profession=option.profession_name,
                status=super_admin_preview_status_label(
                    option.moderation_status,
                    language,
                ),
            )
            for index, option in enumerate(
                visible_options
            )
        ]

        text = "\n\n".join(
            [
                header,
                *rendered_options,
            ]
        )
    else:
        text = (
            f"{header}\n\n"
            f"{t(
                'super_admin_ro_specialist_cabinets_empty',
                language,
            )}"
        )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=text,
        reply_markup=(
            super_admin_read_only_specialist_cabinets_keyboard(
                items_count=len(visible_options),
                page=normalized_page,
                has_next=has_next,
                language=language,
            )
        ),
    )


@admin_impersonation_router.callback_query(
    F.data.startswith(
        "SA_RO_SPECIALIST_CABINETS:"
    )
)
async def super_admin_read_only_specialist_cabinets(
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
                callback.data.split(":", 1)[1]
            ),
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await show_super_admin_specialist_read_only_cabinets(
        callback,
        state,
        page=page,
    )


@admin_impersonation_router.callback_query(
    F.data.startswith(
        "SA_RO_SPECIALIST_CABINET:"
    )
)
async def super_admin_read_only_specialist_cabinet_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        != "specialist"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        index = int(
            callback.data.split(":", 1)[1]
        )
        professional_cabinet_id = UUID(
            str(
                (
                    data.get(
                        "super_admin_impersonation_"
                        "specialist_cabinet_ids"
                    )
                    or []
                )[index]
            )
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_professional_cabinet_id=(
            str(professional_cabinet_id)
        ),
    )

    await show_super_admin_specialist_read_only_cabinet(
        callback,
        state,
    )


async def show_super_admin_specialist_read_only_cabinet(
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
    selected_professional_cabinet_id_raw = data.get(
        "super_admin_impersonation_professional_cabinet_id"
    )
    target_role = data.get(
        "super_admin_impersonation_target_role"
    )
    is_read_only = bool(
        data.get("super_admin_impersonation_read_only")
    )

    if (
        not target_user_id_raw
        or target_role != "specialist"
        or not is_read_only
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(str(target_user_id_raw))
        professional_cabinet_id = (
            UUID(
                str(
                    selected_professional_cabinet_id_raw
                )
            )
            if selected_professional_cabinet_id_raw
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
            cabinet = await AdminImpersonationService(
                session
            ).get_specialist_cabinet(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        AdminImpersonationAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return
    await state.update_data(
        super_admin_impersonation_specialist_id=str(
            cabinet.specialist_id
        ),
        super_admin_impersonation_professional_cabinet_id=(
            str(
                cabinet.professional_cabinet_id
            )
        ),
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_specialist_cabinet",
            language,
        ).format(
            user_number=cabinet.user_number,
            display_name=cabinet.display_name,
            profession=(
                cabinet.profession_name
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            status=(
                super_admin_preview_status_label(
                    cabinet.moderation_status,
                    language,
                )
            ),
            availability=(
                super_admin_preview_availability_label(
                    cabinet.availability_status,
                    language,
                )
            ),
            dialogs_unread=(
                cabinet.dialogs_unread
            ),
        ),
        reply_markup=(
            super_admin_read_only_specialist_menu_keyboard(
                language
            )
        ),
    )


async def show_super_admin_moderator_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    target_role = str(
        data.get(
            "super_admin_impersonation_target_role"
        )
        or ""
    )

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )

    try:
        UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
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
            await AdminImpersonationService(
                session
            ).require_moderator_preview(
                platform_user_id=callback.from_user.id,
                target_user_id=UUID(
                    str(target_user_id_raw)
                ),
                target_role=target_role,
            )
    except (
        AdminImpersonationAccessError,
        AdminSpecialistsAccessError,
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
        text=t(
            (
                "super_admin_ro_admin_moderation_cabinet"
                if target_role == "admin"
                else (
                    "super_admin_impersonation_moderator_cabinet"
                )
            ),
            language,
        ).format(
            user_number=(
                data.get(
                    "super_admin_impersonation_target_user_number"
                )
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            )
        ),
        reply_markup=(
            super_admin_read_only_moderator_menu_keyboard(
                language,
                back_callback=(
                    "SA_RO_ADMIN_HOME"
                    if target_role == "admin"
                    else "SA_IMPERSONATE_MENU"
                ),
                back_text_key=(
                    "super_admin_ro_admin_back_to_dashboard_btn"
                    if target_role == "admin"
                    else (
                        "super_admin_impersonation_change_cabinet_btn"
                    )
                ),
            )
        ),
    )


@admin_impersonation_router.callback_query(F.data == "SA_RO_MOD_HOME")
async def super_admin_read_only_moderator_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_moderator_"
            "blacklist_message_ids"
        ),
        preserve_current=True,
    )

    await show_super_admin_moderator_read_only_cabinet(
        callback,
        state,
    )


@admin_impersonation_router.callback_query(
    F.data.startswith(
        "SA_RO_MOD_QUEUE:"
    )
)
async def super_admin_read_only_moderator_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int(
                (callback.data or "").split(
                    ":",
                    1,
                )[1]
            ),
            0,
        )
    except (
        IndexError,
        TypeError,
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

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
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
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await AdminImpersonationService(
                session
            ).open_moderator_queue(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                page=page,
                page_size=(
                    MODERATOR_PROFILE_PAGE_SIZE
                ),
            )
    except (
        AdminImpersonationAccessError,
        AdminSpecialistsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[
        :MODERATOR_PROFILE_PAGE_SIZE
    ]
    has_next = (
        len(items)
        > MODERATOR_PROFILE_PAGE_SIZE
    )

    await state.update_data(
        super_admin_impersonation_moderator_professional_cabinet_ids=[
            str(
                item.professional_cabinet_id
            )
            for item in visible_items
        ],
        super_admin_impersonation_moderator_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        super_admin_impersonation_moderator_page=(
            page
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_super_admin_read_only_moderator_cabinets_screen(
                visible_items,
                page=page,
                language=language,
            )
        ),
        reply_markup=(
            super_admin_read_only_moderator_cabinets_screen_keyboard(
                items_count=len(
                    visible_items
                ),
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )


@admin_impersonation_router.callback_query(
    F.data.startswith(
        "SA_RO_MOD_PROFILE:"
    )
)
async def super_admin_read_only_moderator_profile(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        IndexError,
        TypeError,
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

    cabinet_ids = data.get(
        "super_admin_impersonation_moderator_professional_cabinet_ids"
    ) or []

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        not in READ_ONLY_MODERATION_TARGET_ROLES
        or index < 0
        or index >= len(cabinet_ids)
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
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
        professional_cabinet_id = UUID(
            cabinet_ids[index]
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

    try:
        async with get_session() as session:
            card = await AdminImpersonationService(
                session
            ).get_moderator_specialist(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
    except (
        AdminImpersonationAccessError,
        AdminSpecialistsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_page"
        )
        or 0
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_MOD_QUEUE:{page}"
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


async def show_super_admin_admin_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
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
            summary = await AdminImpersonationService(
                session
            ).open_admin_cabinet(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
            )
    except (
        AdminImpersonationAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    user_number = (
        data.get(
            "super_admin_impersonation_target_user_number"
        )
        or t(
            "super_admin_value_not_specified",
            language,
        )
    )

    text = (
        f"{t(
            'super_admin_impersonation_admin_cabinet',
            language,
        ).format(
            user_number=user_number,
        )}\n\n"
        f"{format_admin_menu(
            summary,
            language,
        )}"
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=text,
        reply_markup=(
            super_admin_read_only_admin_menu_keyboard(
                language
            )
        ),
    )


@admin_impersonation_router.callback_query(F.data == "SA_RO_ADMIN_HOME")
async def super_admin_read_only_admin_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_"
            "audit_message_ids"
        ),
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_global_"
            "blacklist_message_ids"
        ),
        preserve_current=True,
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

    await show_super_admin_admin_read_only_cabinet(
        callback,
        state,
    )


@admin_impersonation_router.callback_query(
    F.data.startswith(
        "SA_RO_ADMIN_SPECIALIST_OPEN:"
    )
)
async def super_admin_read_only_admin_specialist_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        IndexError,
        TypeError,
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

    cabinet_ids = data.get(
        "super_admin_impersonation_admin_professional_cabinet_ids"
    ) or []

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        != "admin"
        or index < 0
        or index >= len(cabinet_ids)
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
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
        professional_cabinet_id = UUID(
            cabinet_ids[index]
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

    try:
        async with get_session() as session:
            card = await AdminImpersonationService(
                session
            ).get_admin_specialist(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_admin_user_id=(
                    target_user_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
    except (
        AdminImpersonationAccessError,
        AdminSpecialistsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_admin_specialist_page"
        )
        or 0
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_admin_back_to_specialists_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_ADMIN_SPECIALISTS:"
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


@admin_impersonation_router.callback_query(
    F.data == "SA_RO_ADMIN_MODERATION"
)
async def super_admin_read_only_admin_moderation(
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

    await show_super_admin_moderator_read_only_cabinet(
        callback,
        state,
    )


@admin_impersonation_router.callback_query(F.data == "SA_RO_CLIENT_HOME")
async def super_admin_read_only_client_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_client_"
            "dialog_message_ids"
        ),
        preserve_current=True,
    )

    await show_super_admin_client_read_only_cabinet(
        callback,
        state,
    )


@admin_impersonation_router.callback_query(
    F.data == "SA_RO_SPECIALIST_PROFILE"
)
async def super_admin_read_only_specialist_profile(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        != "specialist"
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    specialist_id_raw = data.get(
        "super_admin_impersonation_specialist_id"
    )
    professional_cabinet_id_raw = data.get(
        "super_admin_impersonation_professional_cabinet_id"
    )
    try:
        target_user_id = UUID(
            str(
                target_user_id_raw
            )
        )
        specialist_id = UUID(
            str(
                specialist_id_raw
            )
        )
        professional_cabinet_id = (
            UUID(
                str(
                    professional_cabinet_id_raw
                )
            )
            if professional_cabinet_id_raw
            else None
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

    try:
        async with get_session() as session:
            profile = await AdminImpersonationService(
                session
            ).get_specialist_profile(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                specialist_id=specialist_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
    except AdminImpersonationAccessError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not profile:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_professional_cabinet_id=(
            str(
                profile.professional_cabinet_id
            )
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_ro_specialist_profile",
            language,
        ).format(
            display_name=profile.display_name,
            profession=(
                profile.profession_name
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            location=profile.location,
            description=(
                profile.description
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            status=(
                super_admin_preview_status_label(
                    profile.moderation_status,
                    language,
                )
            ),
            availability=(
                super_admin_preview_availability_label(
                    profile.availability_status,
                    language,
                )
            ),
        ),
        reply_markup=(
            super_admin_read_only_specialist_menu_keyboard(
                language
            )
        ),
    )


@admin_impersonation_router.callback_query(F.data == "SA_RO_SPECIALIST_HOME")
async def super_admin_read_only_specialist_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_specialist_"
            "dialog_message_ids"
        ),
        preserve_current=True,
    )

    await show_super_admin_specialist_read_only_cabinet(
        callback,
        state,
    )


async def show_super_admin_client_read_only_cabinet(
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
        or target_role != "client"
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
            cabinet = await AdminImpersonationService(
                session
            ).get_client_cabinet(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                language=language,
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        AdminImpersonationAccessError,
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
        text=t(
            "super_admin_impersonation_client_cabinet",
            language,
        ).format(
            user_number=cabinet.user_number,
            display_name=(
                cabinet.display_name
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            city_name=(
                cabinet.city_name
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            dialogs_unread=cabinet.dialogs_unread,
        ),
        reply_markup=(
            super_admin_read_only_client_menu_keyboard(
                language
            )
        ),
    )


@admin_impersonation_router.callback_query(
    F.data == "SA_IMPERSONATE_MENU"
)
async def super_admin_impersonation_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()

    if not data.get(
        "super_admin_impersonation_read_only"
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_menu",
            language,
        ),
        reply_markup=super_admin_impersonation_keyboard(
            language
        ),
    )


@admin_impersonation_router.callback_query(F.data == "SA_USER_IMPERSONATE")
async def super_admin_impersonation_start(
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

    await state.set_state(
        AdminImpersonationFSM.entering_super_admin_impersonation_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_reason_prompt",
            language,
        ),
    )


@admin_impersonation_router.message(
    AdminImpersonationFSM.entering_super_admin_impersonation_reason
)
async def super_admin_impersonation_reason_receive(
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
                "super_admin_role_bad_format",
                language,
            ),
        )
        return

    await state.update_data(
        super_admin_impersonation_reason=reason,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_impersonation_menu",
            language,
        ),
        reply_markup=super_admin_impersonation_keyboard(
            language
        ),
    )


@admin_impersonation_router.callback_query(F.data.startswith("SA_IMPERSONATE_ROLE:"))
async def super_admin_impersonation_role(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    try:
        target_role = callback.data.split(":", 1)[1]
    except (IndexError, TypeError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    target_user_id_raw = data.get("super_admin_selected_user_id")
    reason = data.get("super_admin_impersonation_reason")

    if not target_user_id_raw or not reason:
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
            action = await AdminImpersonationService(
                session
            ).start_view(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                target_role=target_role,
                reason=reason,
            )
            preview = action.result

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        AdminImpersonationAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_target_user_id=str(
            target_user_id
        ),
        super_admin_impersonation_target_user_number=(
            preview.target_user_number
        ),
        super_admin_impersonation_target_role=(
            preview.target_role
        ),
        super_admin_impersonation_read_only=True,
    )

    if preview.target_role == "client":
        await show_super_admin_client_read_only_cabinet(
            callback,
            state,
        )
        return
    if preview.target_role == "specialist":
        await show_super_admin_specialist_read_only_cabinets(
            callback,
            state,
            page=0,
        )
        return
    if preview.target_role == "support":
        await show_super_admin_support_read_only_cabinet(
            callback,
            state,
        )
        return

    if preview.target_role == "moderator":
        await show_super_admin_moderator_read_only_cabinet(
            callback,
            state,
        )
        return

    if preview.target_role == "admin":
        await show_super_admin_admin_read_only_cabinet(
            callback,
            state,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_preview",
            language,
        ).format(
            user=preview.target_user_number,
            role=super_admin_user_role_label(
                preview.target_role,
                language,
            ),
        ),
        reply_markup=super_admin_impersonation_keyboard(
            language
        ),
    )


@admin_impersonation_router.callback_query(F.data == "SA_IMPERSONATE_STOP")
async def super_admin_impersonation_stop(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    reason = data.get(
        "super_admin_impersonation_reason"
    ) or "Read-only preview stopped."

    if (
        not target_user_id_raw
        or not data.get("super_admin_impersonation_read_only")
    ):
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
            await AdminImpersonationService(
                session
            ).stop_view(
                platform_user_id=callback.from_user.id,
                target_user_id=target_user_id,
                reason=reason,
            )

    except (
        AdminImpersonationAccessError,
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
            "super_admin_ro_moderator_"
            "blacklist_message_ids"
        ),
        preserve_current=True,
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

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_admin_global_"
            "blacklist_message_ids"
        ),
        preserve_current=True,
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

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_client_"
            "dialog_message_ids"
        ),
        preserve_current=True,
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

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_ro_support_"
            "ticket_message_ids"
        ),
        preserve_current=True,
    )

    await state.update_data(
        super_admin_impersonation_reason=None,
        super_admin_impersonation_target_user_id=None,
        super_admin_impersonation_target_user_number=None,
        super_admin_impersonation_target_role=None,
        super_admin_impersonation_read_only=None,
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_stopped",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_to_user_btn",
                            language,
                        ),
                        callback_data="SA_USER_PROFILE",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_scopes_to_panel_btn",
                            language,
                        ),
                        callback_data="SA_PANEL",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "main_menu",
                            language,
                        ),
                        callback_data="MAIN_MENU",
                    )
                ],
            ]
        ),
    )
