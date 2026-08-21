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
    READ_ONLY_MODERATION_TARGET_ROLES,
    admin_panel_keyboard,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_portfolio import (
    AdminPortfolioAccessError,
    AdminPortfolioDecisionError,
    AdminPortfolioService,
)
from services.portfolio import PortfolioServiceError
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
)


MODERATOR_PORTFOLIO_PAGE_SIZE = 5


admin_portfolio_router = Router()
logger = logging.getLogger(__name__)


admin_portfolio_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_portfolio_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


normalize_language = normalize_admin_language


class AdminPortfolioFSM(StatesGroup):
    entering_portfolio_moderation_reason = State()
    confirming_portfolio_moderation = State()


async def replace_admin_photo_screen(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    photo: str,
    caption: str,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
    callback_answered: bool = False,
) -> None:
    if not callback_answered:
        await callback.answer()

    data = await state.get_data()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            callback.message.message_id,
            data.get(
                "last_menu_message_id"
            ),
        ],
    )

    menu_message = (
        await callback.message.answer_photo(
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


def portfolio_moderation_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next_page: bool,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_approve", language),
                callback_data=f"ADM_PORT_APPROVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_reject", language),
                callback_data=f"ADM_PORT_REJECT:{index}",
            ),
        ],
    ]

    nav = []

    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PORT_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PORT_VIEW:{index + 1}",
            )
        )

    if nav:
        rows.append(nav)

    page_navigation = []

    if page > 0 and index == 0:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PORT_QUEUE:{page - 1}",
            )
        )

    if has_next_page and index == total - 1:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PORT_QUEUE:{page + 1}",
            )
        )

    if page_navigation:
        rows.append(page_navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def rejected_portfolio_keyboard(
    *,
    index: int,
    total: int,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_restore_portfolio", language),
                callback_data=f"ADM_PORT_RESTORE:{index}",
            )
        ],
    ]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PORT_REJECTED_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PORT_REJECTED_VIEW:{index + 1}",
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


def portfolio_reject_type_keyboard(
    *,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_portfolio_regular_reject_btn",
                        language,
                    ),
                    callback_data="ADM_PORT_REJECT_REGULAR",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_portfolio_forbidden_btn",
                        language,
                    ),
                    callback_data="ADM_PORT_REJECT_FORBIDDEN",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_changes_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_PORT_REJECT_CANCEL",
                )
            ],
        ]
    )


def format_portfolio_moderation_card(
    view,
    *,
    index: int,
    page: int,
    language: str,
) -> str:
    file_type = t(
        (
            "portfolio_photo_label"
            if view.storage_object.file_type
            == "photo"
            else "portfolio_pdf_label"
        ),
        language,
    )

    mime_type = (
        view.storage_object.mime_type
        or "application/octet-stream"
    )
    size_kb = round(
        (
            view.storage_object.size_bytes
            or 0
        )
        / 1024,
        1,
    )

    owner_user_id = (
        view.storage_object.owner_user_id
    )
    owner = (
        f"user-{owner_user_id.hex[:8]}"
        if owner_user_id
        else "-"
    )

    cabinet_title = (
        str(
            view.cabinet_title
            or "-"
        ).strip()
        or "-"
    )
    profession_name = (
        str(
            view.profession_name
            or "-"
        ).strip()
        or "-"
    )

    caption = (
        (
            view.item.description
            or ""
        ).strip()
        or (
            view.item.title
            or ""
        ).strip()
        or t(
            "moderator_portfolio_no_caption",
            language,
        )
    )

    number = (
        page
        * MODERATOR_PORTFOLIO_PAGE_SIZE
        + index
        + 1
    )

    return t(
        "moderator_portfolio_card",
        language,
    ).format(
        page=page + 1,
        number=number,
        cabinet_title=cabinet_title,
        profession_name=profession_name,
        file_type=file_type,
        mime_type=mime_type,
        owner=owner,
        size_kb=size_kb,
        caption=caption[:500],
    )


def super_admin_read_only_moderator_portfolio_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next_page: bool,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ]
    ]

    item_navigation: list[InlineKeyboardButton] = []

    if index > 0:
        item_navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_PORT_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        item_navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_PORT_VIEW:{index + 1}",
            )
        )

    if item_navigation:
        rows.append(item_navigation)

    page_navigation: list[InlineKeyboardButton] = []

    if page > 0 and index == 0:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_PORT_QUEUE:{page - 1}",
            )
        )

    if has_next_page and index == total - 1:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_PORT_QUEUE:{page + 1}",
            )
        )

    if page_navigation:
        rows.append(page_navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_to_portfolio_btn",
                        language,
                    ),
                    callback_data=f"SA_RO_MOD_PORT_QUEUE:{page}",
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


@admin_portfolio_router.callback_query(
    (F.data == "SA_RO_MOD_PORTFOLIO")
    | F.data.startswith("SA_RO_MOD_PORT_QUEUE:")
)
async def super_admin_read_only_moderator_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    if callback.data == "SA_RO_MOD_PORTFOLIO":
        page = 0
    else:
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
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
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
            items = await AdminPortfolioService(
                session
            ).list_impersonated_pending_items(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                page=page,
                page_size=(
                    MODERATOR_PORTFOLIO_PAGE_SIZE
                ),
                language=language,
            )
    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[:MODERATOR_PORTFOLIO_PAGE_SIZE]
    has_next_page = (
        len(items) > MODERATOR_PORTFOLIO_PAGE_SIZE
    )

    await state.update_data(
        super_admin_impersonation_moderator_portfolio_ids=[
            str(view.item.id)
            for view in visible_items
        ],
        super_admin_impersonation_moderator_portfolio_page=page,
        super_admin_impersonation_moderator_portfolio_has_next=(
            has_next_page
        ),
    )

    if not visible_items:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=(
                t(
                    "super_admin_ro_moderator_portfolio_title",
                    language,
                ).format(
                    page=page + 1,
                    count=0,
                )
                + "\n\n"
                + t(
                    "admin_no_pending_portfolio",
                    language,
                )
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_moderator_back_btn",
                                language,
                            ),
                            callback_data=(
                                "SA_RO_MOD_HOME"
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
        return

    await show_super_admin_read_only_portfolio_item(
        callback,
        state,
        index=0,
    )


@admin_portfolio_router.callback_query(
    F.data.startswith("SA_RO_MOD_PORT_VIEW:")
)
async def super_admin_read_only_moderator_portfolio_view(
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

    await show_super_admin_read_only_portfolio_item(
        callback,
        state,
        index=index,
    )


async def show_super_admin_read_only_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    portfolio_ids = data.get(
        "super_admin_impersonation_moderator_portfolio_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
        or not portfolio_ids
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    index = max(0, min(index, len(portfolio_ids) - 1))

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        item_id = UUID(portfolio_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_portfolio_page"
        ) or 0
    )
    has_next_page = bool(
        data.get(
            "super_admin_impersonation_moderator_portfolio_has_next"
        )
    )

    try:
        async with get_session() as session:
            view = await AdminPortfolioService(
                session
            ).get_impersonated_pending_item(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                item_id=item_id,
                page=page,
                page_size=(
                    MODERATOR_PORTFOLIO_PAGE_SIZE
                ),
                language=language,
            )
    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not view:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    text = (
        t(
            "super_admin_ro_moderator_portfolio_title",
            language,
        ).format(
            page=page + 1,
            count=len(portfolio_ids),
        )
        + "\n\n"
        + format_portfolio_moderation_card(
            view,
            index=index,
            page=page,
            language=language,
        )
    )
    keyboard = (
        super_admin_read_only_moderator_portfolio_keyboard(
            index=index,
            total=len(portfolio_ids),
            page=page,
            has_next_page=has_next_page,
            signed_url=view.signed_url,
            language=language,
        )
    )

    if (
        view.storage_object.file_type
        == "photo"
    ):
        await replace_admin_photo_screen(
            callback=callback,
            state=state,
            photo=view.signed_url,
            caption=text,
            reply_markup=keyboard,
        )
    else:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=text,
            reply_markup=keyboard,
        )


@admin_portfolio_router.callback_query(
    (F.data == "ADM_PORTFOLIO")
    | F.data.startswith("ADM_PORT_QUEUE:")
)
async def list_pending_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    if callback.data == "ADM_PORTFOLIO":
        page = 0
    else:
        try:
            page = max(
                0,
                int((callback.data or "").split(":", 1)[1]),
            )
        except (TypeError, ValueError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    try:
        async with get_session() as session:
            items = await AdminPortfolioService(
                session
            ).list_pending_items(
                platform_user_id=(
                    callback.from_user.id
                ),
                page=page,
                page_size=(
                    MODERATOR_PORTFOLIO_PAGE_SIZE
                ),
                language=language,
            )
    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[:MODERATOR_PORTFOLIO_PAGE_SIZE]
    has_next = len(items) > MODERATOR_PORTFOLIO_PAGE_SIZE

    await state.update_data(
        admin_portfolio_ids=[
            str(view.item.id)
            for view in visible_items
        ],
        admin_portfolio_page=page,
        admin_portfolio_has_next=has_next,
    )

    if not visible_items:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_pending_portfolio",
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
                                "ADM_PANEL"
                            ),
                        )
                    ]
                ]
            ),
        )
        return

    await show_pending_portfolio_item(
        callback,
        state,
        index=0,
    )


async def show_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    ids = (
        data.get(
            "admin_portfolio_ids"
        )
        or []
    )

    page = int(
        data.get(
            "admin_portfolio_page"
        )
        or 0
    )
    has_next_page = bool(
        data.get(
            "admin_portfolio_has_next"
        )
    )

    if not ids:
        await callback.answer(
            t(
                "admin_no_pending_portfolio",
                language,
            ),
            show_alert=True,
        )
        return

    index = max(
        0,
        min(
            int(index),
            len(ids) - 1,
        ),
    )

    try:
        item_id = UUID(
            ids[index]
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
            view = await AdminPortfolioService(
                session
            ).get_pending_item(
                platform_user_id=(
                    callback.from_user.id
                ),
                item_id=item_id,
                page=page,
                page_size=(
                    MODERATOR_PORTFOLIO_PAGE_SIZE
                ),
                language=language,
            )
    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        logger.warning(
            "moderator_portfolio_load_failed "
            "telegram_id=%s item_id=%s "
            "error=%s",
            callback.from_user.id,
            item_id,
            exc,
        )
        await callback.answer(
            t(
                "moderator_portfolio_load_failed",
                language,
            ),
            show_alert=True,
        )
        return

    if not view:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    text = format_portfolio_moderation_card(
        view,
        index=index,
        page=page,
        language=language,
    )

    keyboard = portfolio_moderation_keyboard(
        index=index,
        total=len(ids),
        page=page,
        has_next_page=has_next_page,
        signed_url=view.signed_url,
        language=language,
    )

    if (
        view.storage_object.file_type
        == "photo"
    ):
        await replace_admin_photo_screen(
            callback=callback,
            state=state,
            photo=view.signed_url,
            caption=text,
            reply_markup=keyboard,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=text,
        reply_markup=keyboard,
    )


@admin_portfolio_router.callback_query(F.data == "ADM_PORTFOLIO_REJECTED")
async def list_rejected_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            items = await AdminPortfolioService(
                session
            ).list_rejected_items(
                platform_user_id=(
                    callback.from_user.id
                ),
                limit=50,
                language=language,
            )
    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not items:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_rejected_portfolio",
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
                            callback_data="ADM_PANEL",
                        )
                    ]
                ]
            ),
        )
        return

    await state.update_data(
        admin_rejected_portfolio_ids=[
            str(view.item.id)
            for view in items
        ]
    )

    await show_rejected_portfolio_item(
        callback,
        state,
        index=0,
    )


async def show_rejected_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_rejected_portfolio_ids") or []

    if not ids:
        await callback.answer(
            t("admin_no_rejected_portfolio", language),
            show_alert=True,
        )
        return

    index = max(0, min(int(index), len(ids) - 1))
    item_id = UUID(ids[index])

    try:
        async with get_session() as session:
            view = await AdminPortfolioService(
                session
            ).get_rejected_item(
                platform_user_id=(
                    callback.from_user.id
                ),
                item_id=item_id,
                limit=50,
                language=language,
            )
    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not view:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = 0

    text = format_portfolio_moderation_card(
        view,
        index=index,
        page=page,
        language=language,
    )

    keyboard = rejected_portfolio_keyboard(
        index=index,
        total=len(ids),
        signed_url=view.signed_url,
        language=language,
    )

    if (
        view.storage_object.file_type
        == "photo"
    ):
        await replace_admin_photo_screen(
            callback=callback,
            state=state,
            photo=view.signed_url,
            caption=text,
            reply_markup=keyboard,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=text,
        reply_markup=keyboard,
    )


@admin_portfolio_router.callback_query(
    F.data.startswith("ADM_PORT_REJECTED_VIEW:")
)
async def view_rejected_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    try:
        index = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t(
                "admin_item_not_found",
                normalize_language(callback.from_user.language_code),
            ),
            show_alert=True,
        )
        return

    await show_rejected_portfolio_item(
        callback,
        state,
        index=index,
    )


@admin_portfolio_router.callback_query(
    F.data.startswith("ADM_PORT_RESTORE:")
)
async def restore_rejected_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_rejected_portfolio_ids") or []

    try:
        index = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    item_id = UUID(ids[index])

    try:
        async with get_session() as session:
            action = await AdminPortfolioService(
                session
            ).restore_rejected_item(
                platform_user_id=(
                    callback.from_user.id
                ),
                item_id=item_id,
            )

        item = action.result
        roles = set(action.actor.roles)

    except (
        AdminPortfolioAccessError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    remaining_ids = [
        stored_id
        for stored_id in ids
        if stored_id != str(item_id)
    ]

    await state.update_data(
        admin_rejected_portfolio_ids=remaining_ids
    )

    portfolio_status = t(
        f"portfolio_status_{item.status}",
        language,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_portfolio_updated",
            language,
        ).format(
            status=portfolio_status,
        ),
        reply_markup=admin_panel_keyboard(
            language,
            roles,
        ),
    )


@admin_portfolio_router.callback_query(F.data.startswith("ADM_PORT_VIEW:"))
async def view_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    index = int(callback.data.split(":", 1)[1])

    await show_pending_portfolio_item(
        callback,
        state,
        index=index,
    )


@admin_portfolio_router.callback_query(
    F.data.startswith("ADM_PORT_APPROVE:")
    | F.data.startswith("ADM_PORT_REJECT:")
)
async def ask_portfolio_moderation_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        prefix, raw_index = (
            callback.data or ""
        ).split(":", 1)
        index = int(raw_index)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    item_ids = data.get("admin_portfolio_ids") or []

    if index < 0 or index >= len(item_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminPortfolioService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminPortfolioAccessError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if prefix == "ADM_PORT_REJECT":
        await state.update_data(
            moderator_portfolio_item_id=(
                item_ids[index]
            ),
            moderator_portfolio_index=index,
            moderator_portfolio_decision=None,
        )

        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "moderator_portfolio_reject_type_prompt",
                language,
            ),
            reply_markup=portfolio_reject_type_keyboard(
                language=language,
            ),
        )
        return

    await state.update_data(
        moderator_portfolio_item_id=item_ids[index],
        moderator_portfolio_decision="approved",
        moderator_portfolio_index=index,
    )
    await state.set_state(
        AdminPortfolioFSM
        .entering_portfolio_moderation_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_portfolio_reason_prompt",
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
                            "ADM_PORT_DECISION_CANCEL:"
                            f"{index}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_portfolio_router.callback_query(
    F.data.in_(
        {
            "ADM_PORT_REJECT_REGULAR",
            "ADM_PORT_REJECT_FORBIDDEN",
        }
    )
)
async def choose_portfolio_reject_type(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    item_id = data.get(
        "moderator_portfolio_item_id"
    )
    index = int(
        data.get("moderator_portfolio_index")
        or 0
    )

    if not item_id:
        await state.set_state(None)
        await state.update_data(
            moderator_portfolio_item_id=None,
            moderator_portfolio_decision=None,
            moderator_portfolio_reason=None,
        )
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    decision = (
        "forbidden"
        if callback.data
        == "ADM_PORT_REJECT_FORBIDDEN"
        else "rejected"
    )

    await state.update_data(
        moderator_portfolio_decision=decision,
    )
    await state.set_state(
        AdminPortfolioFSM
        .entering_portfolio_moderation_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_portfolio_reason_prompt",
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
                            "ADM_PORT_DECISION_CANCEL:"
                            f"{index}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_portfolio_router.callback_query(
    F.data == "ADM_PORT_REJECT_CANCEL"
)
async def cancel_portfolio_reject_type(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    index = int(
        data.get("moderator_portfolio_index")
        or 0
    )

    await state.set_state(None)
    await state.update_data(
        moderator_portfolio_item_id=None,
        moderator_portfolio_index=None,
        moderator_portfolio_decision=None,
        moderator_portfolio_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_portfolio_cancelled",
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
                            f"ADM_PORT_VIEW:{index}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_portfolio_router.message(
    AdminPortfolioFSM.entering_portfolio_moderation_reason
)
async def receive_portfolio_moderation_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    item_id = data.get(
        "moderator_portfolio_item_id"
    )
    decision = data.get(
        "moderator_portfolio_decision"
    )
    index = int(
        data.get("moderator_portfolio_index")
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
                        "ADM_PORT_DECISION_CANCEL:"
                        f"{index}"
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
                f"{t('moderator_portfolio_reason_prompt', language)}"
            ),
            reply_markup=cancel_keyboard,
        )
        return

    if (
        not item_id
        or decision
        not in {
            "approved",
            "rejected",
            "forbidden",
        }
    ):
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
                                "moderator_back_btn",
                                language,
                            ),
                            callback_data="ADM_PORTFOLIO",
                        )
                    ]
                ]
            ),
        )
        await state.set_state(None)
        await state.update_data(
            moderator_portfolio_item_id=None,
            moderator_portfolio_index=None,
            moderator_portfolio_decision=None,
            moderator_portfolio_reason=None,
        )
        return

    await state.update_data(
        moderator_portfolio_reason=reason,
    )
    await state.set_state(
        AdminPortfolioFSM
        .confirming_portfolio_moderation
    )

    if decision == "approved":
        confirmation_key = (
            "moderator_portfolio_approve_confirmation"
        )
    elif decision == "forbidden":
        confirmation_key = (
            "moderator_portfolio_forbidden_confirmation"
        )
    else:
        confirmation_key = (
            "moderator_portfolio_reject_confirmation"
        )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            confirmation_key,
            language,
        ).format(
            reason=reason
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_portfolio_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_PORT_DECISION_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_portfolio_edit_reason_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_PORT_DECISION_EDIT"
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
                            "ADM_PORT_DECISION_CANCEL:"
                            f"{index}"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_portfolio_router.callback_query(
    F.data == "ADM_PORT_DECISION_EDIT"
)
async def edit_portfolio_moderation_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    item_id = data.get(
        "moderator_portfolio_item_id"
    )
    index = int(
        data.get("moderator_portfolio_index")
        or 0
    )

    if not item_id:
        await state.set_state(None)
        await state.update_data(
            moderator_portfolio_item_id=None,
            moderator_portfolio_index=None,
            moderator_portfolio_decision=None,
            moderator_portfolio_reason=None,
        )
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_portfolio_reason=None,
    )
    await state.set_state(
        AdminPortfolioFSM
        .entering_portfolio_moderation_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_portfolio_reason_prompt",
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
                            "ADM_PORT_DECISION_CANCEL:"
                            f"{index}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_portfolio_router.callback_query(
    F.data.startswith("ADM_PORT_DECISION_CANCEL:")
)
async def cancel_portfolio_moderation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = max(
            0,
            int(
                (callback.data or "").split(
                    ":",
                    1,
                )[1]
            ),
        )
    except (TypeError, ValueError):
        index = 0

    await state.set_state(None)
    await state.update_data(
        moderator_portfolio_item_id=None,
        moderator_portfolio_index=None,
        moderator_portfolio_decision=None,
        moderator_portfolio_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_portfolio_cancelled",
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
                            f"ADM_PORT_VIEW:{index}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_portfolio_router.callback_query(
    F.data == "ADM_PORT_DECISION_CONFIRM"
)
async def confirm_portfolio_moderation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    item_id = data.get(
        "moderator_portfolio_item_id"
    )
    decision = data.get(
        "moderator_portfolio_decision"
    )
    reason = (
        data.get("moderator_portfolio_reason")
        or ""
    ).strip()
    item_ids = data.get(
        "admin_portfolio_ids"
    ) or []

    if (
        not item_id
        or decision
        not in {
            "approved",
            "rejected",
            "forbidden",
        }
        or len(reason) < 3
    ):
        await state.set_state(None)
        await state.update_data(
            moderator_portfolio_item_id=None,
            moderator_portfolio_index=None,
            moderator_portfolio_decision=None,
            moderator_portfolio_reason=None,
        )
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await AdminPortfolioService(
                session
            ).moderate_item(
                platform_user_id=(
                    callback.from_user.id
                ),
                item_id=UUID(item_id),
                decision=decision,
                reason=reason,
            )

        item = action.result
        moderator_user_id = (
            action.actor.user_id
        )

    except (
        AdminPortfolioAccessError,
        AdminPortfolioDecisionError,
        PortfolioServiceError,
        ValueError,
    ) as exc:
        logger.warning(
            "moderator_portfolio_decision_failed "
            "telegram_id=%s item_id=%s "
            "decision=%s error=%s",
            callback.from_user.id,
            item_id,
            decision,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "moderator_portfolio_decision_completed "
        "telegram_id=%s moderator_user_id=%s "
        "item_id=%s decision=%s status=%s",
        callback.from_user.id,
        moderator_user_id,
        item_id,
        decision,
        item.status,
    )

    remaining_ids = [
        stored_id
        for stored_id in item_ids
        if stored_id != str(item_id)
    ]

    await state.set_state(None)
    await state.update_data(
        admin_portfolio_ids=remaining_ids,
        moderator_portfolio_item_id=None,
        moderator_portfolio_index=None,
        moderator_portfolio_decision=None,
        moderator_portfolio_reason=None,
    )

    result_text_key = (
        "moderator_portfolio_approved"
        if decision == "approved"
        else "moderator_portfolio_rejected"
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            result_text_key,
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
                        callback_data="ADM_PORTFOLIO",
                    )
                ]
            ]
        ),
    )
