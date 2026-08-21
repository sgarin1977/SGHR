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
    ADMIN_SPECIALIST_PAGE_SIZE,
    MODERATOR_PROFILE_PAGE_SIZE,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_specialists import (
    AdminSpecialistsAccessError,
    AdminSpecialistsService,
)
from services.moderation import ModerationError
from ui.texts import t


admin_specialists_router = Router()
normalize_language = normalize_admin_language
logger = logging.getLogger(__name__)


class AdminSpecialistsFSM(StatesGroup):
    entering_specialist_decision_reason = State()
    confirming_specialist_decision = State()
    entering_specialist_visibility_reason = State()
    confirming_specialist_visibility = State()
    entering_specialist_changes_reason = State()
    confirming_specialist_changes = State()


def format_pending_profiles_header(
    *,
    page: int,
    count: int,
    language: str,
) -> str:
    return t("moderator_profiles_header", language).format(
        page=page + 1,
        count=count,
    )


def format_pending_profile_queue_item(
    item,
    *,
    number: int,
    language: str,
) -> str:
    city = item.city_name or t("moderator_city_not_set", language)
    created_at = (
        item.created_at.strftime("%Y-%m-%d")
        if item.created_at
        else "-"
    )

    return t("moderator_profile_queue_item", language).format(
        number=number,
        name=item.display_name,
        profession=item.profession_name,
        city=city,
        date=created_at,
    )


def pending_profiles_queue_keyboard(
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
                callback_data=f"ADM_SP_QUEUE:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SP_QUEUE:{page + 1}",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_pending_profiles_screen(
    items,
    *,
    page: int,
    language: str,
) -> str:
    parts = [
        format_pending_profiles_header(
            page=page,
            count=len(items),
            language=language,
        )
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


def pending_profiles_screen_keyboard(
    *,
    items_count: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{index + 1}. "
                    f"{t('moderator_open_btn', language)}"
                ),
                callback_data=(
                    f"ADM_SP_OPEN:{index}"
                ),
            )
        ]
        for index in range(items_count)
    ]

    rows.extend(
        pending_profiles_queue_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ).inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def pending_specialist_keyboard(
    *,
    index: int,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_approve", language),
                    callback_data=f"ADM_SP_APPROVE:{index}",
                ),
                InlineKeyboardButton(
                    text=t("admin_reject", language),
                    callback_data=f"ADM_SP_REJECT:{index}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_request_changes_btn", language),
                    callback_data=f"ADM_SP_CHANGES:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_scoped_blacklist_btn", language),
                    callback_data=f"ADM_SP_SCOPED_BLOCK:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_back_to_profiles_btn", language),
                    callback_data=f"ADM_SP_QUEUE:{page}",
                )
            ],
        ]
    )


def format_pending_specialist_card(
    card,
    *,
    language: str,
) -> str:
    city = (
        card.city_name
        or t(
            "moderator_city_not_set",
            language,
        )
    )

    services = (
        "\n".join(
            f"- {title}"
            for title in card.service_titles
        )
        if card.service_titles
        else t(
            "moderator_no_services",
            language,
        )
    )

    activity = t(
        (
            "admin_professional_cabinet_active"
            if card.is_active
            else "admin_professional_cabinet_archived"
        ),
        language,
    )

    text = t(
        "moderator_profile_card",
        language,
    ).format(
        name=card.display_name,
        profession=card.profession_name,
        city=city,
        status=card.status,
        description=card.description,
        contact=card.masked_contact,
        complaints=card.complaints_count,
        risk_flags=(
            card.open_risk_flags_count
        ),
        services=services,
    )

    return (
        f"{text}\n\n"
        f"{activity}"
    )


def admin_specialist_card_keyboard(
    *,
    index: int,
    moderation_status: str,
    list_status: str,
    is_active: bool,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if is_active:
        if (
            moderation_status
            == "pending_moderation"
        ):
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_approve",
                                language,
                            ),
                            callback_data=(
                                f"ADM_SP_APPROVE:{index}"
                            ),
                        ),
                        InlineKeyboardButton(
                            text=t(
                                "admin_reject",
                                language,
                            ),
                            callback_data=(
                                f"ADM_SP_REJECT:{index}"
                            ),
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "moderator_request_changes_btn",
                                language,
                            ),
                            callback_data=(
                                f"ADM_SP_CHANGES:{index}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "moderator_scoped_blacklist_btn",
                                language,
                            ),
                            callback_data=(
                                "ADM_SP_SCOPED_BLOCK:"
                                f"{index}"
                            ),
                        )
                    ],
                ]
            )
        elif moderation_status == "approved":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_hide_specialist_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_HIDE:{index}"
                        ),
                    )
                ]
            )
        elif moderation_status == "hidden":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_restore_specialist_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_RESTORE:{index}"
                        ),
                    )
                ]
            )

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_specialist_read_only_btn",
                    language,
                ),
                callback_data=(
                    "ADMIN_SPECIALIST_READ_ONLY"
                ),
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_panel_back",
                    language,
                ),
                callback_data=(
                    "ADM_ADMIN_SPECIALISTS:"
                    f"{list_status}:{page}"
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def format_admin_specialist_item(
    item,
    *,
    number: int,
    language: str,
) -> str:
    city = (
        item.city_name
        or t(
            "admin_specialist_city_not_set",
            language,
        )
    )
    created_at = (
        item.created_at.strftime(
            "%Y-%m-%d"
        )
        if item.created_at
        else "-"
    )
    activity = t(
        (
            "admin_professional_cabinet_active"
            if item.is_active
            else "admin_professional_cabinet_archived"
        ),
        language,
    )

    text = t(
        "admin_specialist_item",
        language,
    ).format(
        number=number,
        name=item.display_name,
        profession=item.profession_name,
        city=city,
        status=item.status,
        date=created_at,
    )

    return (
        f"{text}\n"
        f"{activity}"
    )


def admin_specialists_keyboard(
    *,
    status: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_specialist_filter_btn", language),
                callback_data="ADM_ADMIN_SPECIALIST_FILTER",
            )
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_ADMIN_SPECIALISTS:{status}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_ADMIN_SPECIALISTS:{status}:{page + 1}"
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


def format_admin_professional_cabinets_screen(
    items,
    *,
    status: str,
    page: int,
    language: str,
) -> str:
    parts = [
        t(
            "admin_specialists_header",
            language,
        ).format(
            status=status,
            page=page + 1,
            count=len(items),
        )
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


def admin_professional_cabinets_screen_keyboard(
    *,
    items_count: int,
    status: str,
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
                    text=(
                        f"{number}. "
                        f"{t('admin_user_open_btn', language)}"
                    ),
                    callback_data=(
                        "ADM_ADMIN_SPECIALIST_OPEN:"
                        f"{index}"
                    ),
                )
            ]
        )

    rows.extend(
        admin_specialists_keyboard(
            status=status,
            page=page,
            has_next=has_next,
            language=language,
        ).inline_keyboard
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def admin_specialist_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    statuses = (
        (
            "all",
            "admin_specialist_filter_all",
        ),
        (
            "approved",
            "admin_specialist_filter_approved",
        ),
        (
            "pending_moderation",
            "admin_specialist_filter_pending",
        ),
        (
            "draft",
            "admin_specialist_filter_draft",
        ),
        (
            "hidden",
            "admin_specialist_filter_hidden",
        ),
        (
            "rejected",
            "admin_specialist_filter_rejected",
        ),
        (
            "archived",
            "admin_specialist_filter_archived",
        ),
    )

    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    text_key,
                    language,
                ),
                callback_data=(
                    "ADM_ADMIN_SPECIALISTS:"
                    f"{status}:0"
                ),
            )
        ]
        for status, text_key in statuses
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_panel_back",
                    language,
                ),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


@admin_specialists_router.callback_query(
    (F.data == "ADM_ADMIN_SPECIALISTS")
    | F.data.startswith(
        "ADM_ADMIN_SPECIALISTS:"
    )
)
async def open_admin_specialists_list(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    status = "approved"
    page = 0

    if (
        callback.data
        != "ADM_ADMIN_SPECIALISTS"
    ):
        parts = (
            callback.data or ""
        ).split(":")

        if len(parts) != 3:
            await callback.answer(
                t(
                    "admin_item_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

        status = parts[1]

        try:
            page = max(
                int(parts[2]),
                0,
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
            cabinet_page = await AdminSpecialistsService(
                session
            ).open_admin_specialists(
                platform_user_id=(
                    callback.from_user.id
                ),
                status=status,
                page=page,
                page_size=(
                    ADMIN_SPECIALIST_PAGE_SIZE
                ),
            )
    except (
        AdminSpecialistsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_professional_cabinet_ids=[
            str(
                item.professional_cabinet_id
            )
            for item in cabinet_page.items
        ],
        admin_specialist_ids=[
            str(item.specialist_id)
            for item in cabinet_page.items
        ],
        admin_specialist_status=(
            cabinet_page.status
        ),
        admin_specialist_page=(
            cabinet_page.page
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            format_admin_professional_cabinets_screen(
                cabinet_page.items,
                status=cabinet_page.status,
                page=cabinet_page.page,
                language=language,
            )
        ),
        reply_markup=(
            admin_professional_cabinets_screen_keyboard(
                items_count=len(
                    cabinet_page.items
                ),
                status=cabinet_page.status,
                page=cabinet_page.page,
                has_next=(
                    cabinet_page.has_next
                ),
                language=language,
            )
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith(
        "ADM_ADMIN_SPECIALIST_OPEN:"
    )
)
async def open_admin_specialist_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    cabinet_ids = (
        data.get(
            "admin_professional_cabinet_ids"
        )
        or []
    )
    specialist_ids = (
        data.get(
            "admin_specialist_ids"
        )
        or []
    )
    status = (
        data.get(
            "admin_specialist_status"
        )
        or "approved"
    )
    page = int(
        data.get(
            "admin_specialist_page"
        )
        or 0
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        TypeError,
        ValueError,
        IndexError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    if (
        index < 0
        or index >= len(cabinet_ids)
        or index >= len(specialist_ids)
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
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
            card = await AdminSpecialistsService(
                session
            ).get_specialist_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
    except (
        AdminSpecialistsAccessError,
        ModerationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_pending_professional_cabinet_ids=(
            cabinet_ids
        ),
        admin_pending_specialist_ids=(
            specialist_ids
        ),
        admin_pending_specialist_page=page,
        moderator_selected_professional_cabinet_id=(
            str(card.professional_cabinet_id)
        ),
        moderator_selected_specialist_id=(
            str(card.specialist_id)
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=(
            admin_specialist_card_keyboard(
                index=index,
                moderation_status=(
                    card.status
                ),
                list_status=status,
                is_active=card.is_active,
                page=page,
                language=language,
            )
        ),
    )


@admin_specialists_router.callback_query(
    (F.data == "ADM_PENDING")
    | F.data.startswith("ADM_SP_QUEUE:")
)
async def list_pending_profiles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    if callback.data == "ADM_PENDING":
        page = 0
    else:
        try:
            page = max(
                0,
                int(
                    (callback.data or "").split(
                        ":",
                        1,
                    )[1]
                ),
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
            items = await AdminSpecialistsService(
                session
            ).open_pending_specialists(
                platform_user_id=(
                    callback.from_user.id
                ),
                page=page,
                page_size=(
                    MODERATOR_PROFILE_PAGE_SIZE
                ),
            )
    except (
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
        admin_pending_professional_cabinet_ids=[
            str(
                item.professional_cabinet_id
            )
            for item in visible_items
        ],
        admin_pending_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        admin_pending_specialist_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_pending_profiles_screen(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=(
            pending_profiles_screen_keyboard(
                items_count=len(
                    visible_items
                ),
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )


async def show_pending_specialist(
    callback: CallbackQuery,
    state: FSMContext,
    index: int,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    cabinet_ids = (
        data.get(
            "admin_pending_professional_cabinet_ids"
        )
        or []
    )
    page = int(
        data.get(
            "admin_pending_specialist_page"
        )
        or 0
    )

    if (
        index < 0
        or index >= len(cabinet_ids)
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return


    try:
        professional_cabinet_id = UUID(
            cabinet_ids[index]
        )

        async with get_session() as session:
            card = await AdminSpecialistsService(
                session
            ).get_specialist_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
    except (
        AdminSpecialistsAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_selected_professional_cabinet_id=(
            str(card.professional_cabinet_id)
        ),
        moderator_selected_specialist_id=(
            str(card.specialist_id)
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=pending_specialist_keyboard(
            index=index,
            page=page,
            language=language,
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith("ADM_SP_APPROVE:")
    | F.data.startswith("ADM_SP_REJECT:")
)
async def ask_specialist_decision_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        callback_prefix, raw_index = (
            callback.data or ""
        ).split(
            ":",
            1,
        )
        index = int(raw_index)
    except (TypeError, ValueError):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    cabinet_ids = (
        data.get(
            "admin_pending_professional_cabinet_ids"
        )
        or []
    )
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

    if (
        index < 0
        or index >= len(cabinet_ids)
        or index >= len(specialist_ids)
    ):
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
            await AdminSpecialistsService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminSpecialistsAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return


    decision = (
        "approved"
        if callback_prefix
        == "ADM_SP_APPROVE"
        else "rejected"
    )

    await state.update_data(
        moderator_decision_professional_cabinet_id=(
            cabinet_ids[index]
        ),
        moderator_decision_specialist_id=(
            specialist_ids[index]
        ),
        moderator_decision=decision,
        moderator_decision_page=page,
    )
    await state.set_state(
        AdminSpecialistsFSM
        .entering_specialist_decision_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_decision_reason_prompt",
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
                            "ADM_SP_DECISION_CANCEL:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith("ADM_SP_HIDE:")
    | F.data.startswith(
        "ADM_SP_RESTORE:"
    )
)
async def ask_specialist_visibility_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        callback_prefix, raw_index = (
            callback.data or ""
        ).split(
            ":",
            1,
        )
        index = int(raw_index)
    except (TypeError, ValueError):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    action_by_callback = {
        "ADM_SP_HIDE": "hide",
        "ADM_SP_RESTORE": "restore",
    }
    action = action_by_callback.get(
        callback_prefix
    )

    cabinet_ids = (
        data.get(
            "admin_professional_cabinet_ids"
        )
        or []
    )
    specialist_ids = (
        data.get(
            "admin_specialist_ids"
        )
        or []
    )
    status = (
        data.get(
            "admin_specialist_status"
        )
        or "approved"
    )
    page = int(
        data.get(
            "admin_specialist_page"
        )
        or 0
    )

    if (
        action is None
        or index < 0
        or index >= len(cabinet_ids)
        or index >= len(specialist_ids)
    ):
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
            await AdminSpecialistsService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminSpecialistsAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return


    await state.update_data(
        admin_specialist_visibility_action=(
            action
        ),
        admin_specialist_visibility_professional_cabinet_id=(
            cabinet_ids[index]
        ),
        admin_specialist_visibility_specialist_id=(
            specialist_ids[index]
        ),
        admin_specialist_visibility_status=(
            status
        ),
        admin_specialist_visibility_page=(
            page
        ),
    )
    await state.set_state(
        AdminSpecialistsFSM
        .entering_specialist_visibility_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_decision_reason_prompt",
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
                            "ADM_SP_VISIBILITY_CANCEL:"
                            f"{status}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.message(
    AdminSpecialistsFSM
    .entering_specialist_visibility_reason
)
async def receive_specialist_visibility_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    action = data.get(
        "admin_specialist_visibility_action"
    )
    professional_cabinet_id = data.get(
        "admin_specialist_visibility_professional_cabinet_id"
    )
    specialist_id = data.get(
        "admin_specialist_visibility_specialist_id"
    )
    status = (
        data.get(
            "admin_specialist_visibility_status"
        )
        or "approved"
    )
    page = int(
        data.get(
            "admin_specialist_visibility_page"
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
                        "ADM_SP_VISIBILITY_CANCEL:"
                        f"{status}:{page}"
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
                f"{t('moderator_decision_reason_prompt', language)}"
            ),
            reply_markup=cancel_keyboard,
        )
        return

    if (
        not professional_cabinet_id
        or not specialist_id
        or action
        not in {
            "hide",
            "restore",
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
                                "admin_panel_back",
                                language,
                            ),
                            callback_data=(
                                "ADM_ADMIN_SPECIALISTS:"
                                f"{status}:{page}"
                            ),
                        )
                    ]
                ]
            ),
        )
        await state.set_state(None)
        return

    confirmation_key = (
        "moderator_hide_specialist_confirmation"
        if action == "hide"
        else "moderator_restore_specialist_confirmation"
    )

    await state.update_data(
        admin_specialist_visibility_reason=reason,
    )
    await state.set_state(
        AdminSpecialistsFSM
        .confirming_specialist_visibility
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
                            "moderator_decision_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_VISIBILITY_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_decision_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_VISIBILITY_EDIT"
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
                            "ADM_SP_VISIBILITY_CANCEL:"
                            f"{status}:{page}"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data == "ADM_SP_VISIBILITY_EDIT"
)
async def edit_specialist_visibility_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    action = data.get(
        "admin_specialist_visibility_action"
    )
    professional_cabinet_id = data.get(
        "admin_specialist_visibility_professional_cabinet_id"
    )
    specialist_id = data.get(
        "admin_specialist_visibility_specialist_id"
    )
    status = (
        data.get(
            "admin_specialist_visibility_status"
        )
        or "approved"
    )
    page = int(
        data.get(
            "admin_specialist_visibility_page"
        )
        or 0
    )

    if (
        not professional_cabinet_id
        or not specialist_id
        or action
        not in {
            "hide",
            "restore",
        }
    ):
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
                                "admin_panel_back",
                                language,
                            ),
                            callback_data=(
                                "ADM_ADMIN_SPECIALISTS:"
                                f"{status}:{page}"
                            ),
                        )
                    ]
                ]
            ),
        )
        await state.set_state(None)
        return

    await state.set_state(
        AdminSpecialistsFSM
        .entering_specialist_visibility_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_decision_reason_prompt",
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
                            "ADM_SP_VISIBILITY_CANCEL:"
                            f"{status}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith(
        "ADM_SP_VISIBILITY_CANCEL:"
    )
)
async def cancel_specialist_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, status, raw_page = (
            callback.data or ""
        ).split(
            ":",
            2,
        )
        page = max(
            int(raw_page),
            0,
        )
    except (TypeError, ValueError):
        status = "approved"
        page = 0

    allowed_statuses = {
        "all",
        "draft",
        "pending_moderation",
        "approved",
        "rejected",
        "hidden",
        "archived",
    }

    if status not in allowed_statuses:
        status = "approved"

    await state.set_state(None)
    await state.update_data(
        admin_specialist_visibility_action=None,
        admin_specialist_visibility_professional_cabinet_id=None,
        admin_specialist_visibility_specialist_id=None,
        admin_specialist_visibility_reason=None,
        admin_specialist_visibility_status=None,
        admin_specialist_visibility_page=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_decision_cancelled",
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
                        callback_data=(
                            "ADM_ADMIN_SPECIALISTS:"
                            f"{status}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data == "ADM_SP_VISIBILITY_CONFIRM"
)
async def confirm_specialist_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    action = data.get(
        "admin_specialist_visibility_action"
    )
    professional_cabinet_id = data.get(
        "admin_specialist_visibility_professional_cabinet_id"
    )
    specialist_id = data.get(
        "admin_specialist_visibility_specialist_id"
    )
    reason = (
        data.get(
            "admin_specialist_visibility_reason"
        )
        or ""
    ).strip()
    status = (
        data.get(
            "admin_specialist_visibility_status"
        )
        or "approved"
    )
    page = int(
        data.get(
            "admin_specialist_visibility_page"
        )
        or 0
    )

    if (
        not professional_cabinet_id
        or not specialist_id
        or action
        not in {
            "hide",
            "restore",
        }
        or len(reason) < 3
    ):
        await state.set_state(None)
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
                                "admin_panel_back",
                                language,
                            ),
                            callback_data=(
                                "ADM_ADMIN_SPECIALISTS:"
                                f"{status}:{page}"
                            ),
                        )
                    ]
                ]
            ),
        )
        return


    admin_user_id = None

    try:
        cabinet_id = UUID(
            professional_cabinet_id
        )

        async with get_session() as session:
            service = AdminSpecialistsService(
                session
            )

            if action == "hide":
                action_result = await (
                    service
                    .hide_professional_cabinet(
                        platform_user_id=(
                            callback.from_user.id
                        ),
                        professional_cabinet_id=(
                            cabinet_id
                        ),
                        reason=reason,
                    )
                )
            else:
                action_result = await (
                    service
                    .restore_professional_cabinet(
                        platform_user_id=(
                            callback.from_user.id
                        ),
                        professional_cabinet_id=(
                            cabinet_id
                        ),
                        reason=reason,
                    )
                )

            result = action_result.result
            admin_user_id = (
                action_result.actor.user_id
            )

    except (
        AdminSpecialistsAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        logger.warning(
            "professional_cabinet_visibility_failed "
            "telegram_id=%s "
            "professional_cabinet_id=%s "
            "specialist_id=%s action=%s error=%s",
            callback.from_user.id,
            professional_cabinet_id,
            specialist_id,
            action,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    result_text_key = (
        "moderator_specialist_hidden"
        if action == "hide"
        else "moderator_specialist_restored"
    )

    logger.info(
        "professional_cabinet_visibility_completed "
        "telegram_id=%s admin_user_id=%s "
        "professional_cabinet_id=%s "
        "specialist_id=%s action=%s status=%s",
        callback.from_user.id,
        admin_user_id,
        professional_cabinet_id,
        specialist_id,
        action,
        result.status,
    )

    await state.set_state(None)
    await state.update_data(
        admin_specialist_visibility_action=None,
        admin_specialist_visibility_professional_cabinet_id=None,
        admin_specialist_visibility_specialist_id=None,
        admin_specialist_visibility_reason=None,
        admin_specialist_visibility_status=None,
        admin_specialist_visibility_page=None,
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
                            "admin_panel_back",
                            language,
                        ),
                        callback_data=(
                            "ADM_ADMIN_SPECIALISTS:"
                            f"{status}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.message(
    AdminSpecialistsFSM
    .entering_specialist_decision_reason
)
async def receive_specialist_decision_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    professional_cabinet_id = data.get(
        "moderator_decision_professional_cabinet_id"
    )
    specialist_id = data.get(
        "moderator_decision_specialist_id"
    )
    decision = data.get(
        "moderator_decision"
    )
    page = int(
        data.get(
            "moderator_decision_page"
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
                        "ADM_SP_DECISION_CANCEL:"
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
                f"{t('moderator_decision_reason_prompt', language)}"
            ),
            reply_markup=cancel_keyboard,
        )
        return

    if (
        not professional_cabinet_id
        or not specialist_id
        or decision
        not in {
            "approved",
            "rejected",
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
                                "admin_panel_back",
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
        return

    await state.update_data(
        moderator_decision_reason=reason,
    )
    await state.set_state(
        AdminSpecialistsFSM
        .confirming_specialist_decision
    )

    confirmation_key = (
        "moderator_approve_confirmation"
        if decision == "approved"
        else "moderator_reject_confirmation"
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
                            "moderator_decision_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_DECISION_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_decision_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_DECISION_EDIT"
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
                            "ADM_SP_DECISION_CANCEL:"
                            f"{page}"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data == "ADM_SP_DECISION_EDIT"
)
async def edit_specialist_decision_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    professional_cabinet_id = data.get(
        "moderator_decision_professional_cabinet_id"
    )
    specialist_id = data.get(
        "moderator_decision_specialist_id"
    )
    decision = data.get(
        "moderator_decision"
    )
    page = int(
        data.get(
            "moderator_decision_page"
        )
        or 0
    )

    if (
        not professional_cabinet_id
        or not specialist_id
        or decision
        not in {
            "approved",
            "rejected",
        }
    ):
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
                                "admin_panel_back",
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
        return

    await state.set_state(
        AdminSpecialistsFSM
        .entering_specialist_decision_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_decision_reason_prompt",
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
                            "ADM_SP_DECISION_CANCEL:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith(
        "ADM_SP_DECISION_CANCEL:"
    )
)
async def cancel_specialist_decision(
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
                (callback.data or "").split(
                    ":",
                    1,
                )[1]
            ),
        )
    except (TypeError, ValueError):
        page = 0

    await state.set_state(None)
    await state.update_data(
        moderator_decision_professional_cabinet_id=None,
        moderator_decision_specialist_id=None,
        moderator_decision=None,
        moderator_decision_reason=None,
        moderator_decision_page=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_decision_cancelled",
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


@admin_specialists_router.callback_query(
    F.data == "ADM_SP_DECISION_CONFIRM"
)
async def confirm_specialist_decision(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    professional_cabinet_id = data.get(
        "moderator_decision_professional_cabinet_id"
    )
    specialist_id = data.get(
        "moderator_decision_specialist_id"
    )
    decision = data.get(
        "moderator_decision"
    )
    reason = (
        data.get(
            "moderator_decision_reason"
        )
        or ""
    ).strip()
    page = int(
        data.get(
            "moderator_decision_page"
        )
        or 0
    )

    if (
        not professional_cabinet_id
        or not specialist_id
        or decision
        not in {
            "approved",
            "rejected",
        }
        or len(reason) < 3
    ):
        await state.set_state(None)
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
        return


    moderator_user_id = None

    try:
        cabinet_id = UUID(
            professional_cabinet_id
        )

        async with get_session() as session:
            service = AdminSpecialistsService(
                session
            )

            if decision == "approved":
                action_result = (
                    await service.approve_specialist(
                        platform_user_id=(
                            callback.from_user.id
                        ),
                        professional_cabinet_id=(
                            cabinet_id
                        ),
                        reason=reason,
                    )
                )
            else:
                action_result = (
                    await service.reject_specialist(
                        platform_user_id=(
                            callback.from_user.id
                        ),
                        professional_cabinet_id=(
                            cabinet_id
                        ),
                        reason=reason,
                    )
                )

            result = action_result.result
            moderator_user_id = (
                action_result.actor.user_id
            )

    except (
        AdminSpecialistsAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        logger.warning(
            "moderator_cabinet_decision_failed "
            "telegram_id=%s "
            "professional_cabinet_id=%s "
            "specialist_id=%s decision=%s "
            "error=%s",
            callback.from_user.id,
            professional_cabinet_id,
            specialist_id,
            decision,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "moderator_cabinet_decision_completed "
        "telegram_id=%s moderator_user_id=%s "
        "professional_cabinet_id=%s "
        "specialist_id=%s decision=%s status=%s",
        callback.from_user.id,
        moderator_user_id,
        professional_cabinet_id,
        specialist_id,
        decision,
        result.status,
    )

    result_text_key = (
        "moderator_decision_approved"
        if decision == "approved"
        else "moderator_decision_rejected"
    )

    await state.set_state(None)
    await state.update_data(
        moderator_decision_professional_cabinet_id=None,
        moderator_decision_specialist_id=None,
        moderator_decision=None,
        moderator_decision_reason=None,
        moderator_decision_page=None,
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
                        callback_data=(
                            f"ADM_SP_QUEUE:{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith("ADM_SP_CHANGES:")
)
async def ask_specialist_changes_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
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
    except (TypeError, ValueError):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    cabinet_ids = (
        data.get(
            "admin_pending_professional_cabinet_ids"
        )
        or []
    )
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

    if (
        index < 0
        or index >= len(cabinet_ids)
        or index >= len(specialist_ids)
    ):
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
            await AdminSpecialistsService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminSpecialistsAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return


    await state.update_data(
        moderator_changes_professional_cabinet_id=(
            cabinet_ids[index]
        ),
        moderator_changes_specialist_id=(
            specialist_ids[index]
        ),
        moderator_changes_page=page,
    )
    await state.set_state(
        AdminSpecialistsFSM
        .entering_specialist_changes_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_changes_reason_prompt",
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
                            "ADM_SP_CHANGES_CANCEL:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.message(
    AdminSpecialistsFSM
    .entering_specialist_changes_reason
)
async def receive_specialist_changes_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    professional_cabinet_id = data.get(
        "moderator_changes_professional_cabinet_id"
    )
    specialist_id = data.get(
        "moderator_changes_specialist_id"
    )
    page = int(
        data.get(
            "moderator_changes_page"
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
                        "ADM_SP_CHANGES_CANCEL:"
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
                f"{t('moderator_changes_reason_prompt', language)}"
            ),
            reply_markup=cancel_keyboard,
        )
        return

    if (
        not professional_cabinet_id
        or not specialist_id
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
        return

    await state.update_data(
        moderator_changes_reason=reason,
    )
    await state.set_state(
        AdminSpecialistsFSM
        .confirming_specialist_changes
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_changes_confirmation",
            language,
        ).format(
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_CHANGES_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_CHANGES_EDIT"
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
                            "ADM_SP_CHANGES_CANCEL:"
                            f"{page}"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data == "ADM_SP_CHANGES_EDIT"
)
async def edit_specialist_changes_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    professional_cabinet_id = data.get(
        "moderator_changes_professional_cabinet_id"
    )
    specialist_id = data.get(
        "moderator_changes_specialist_id"
    )
    page = int(
        data.get(
            "moderator_changes_page"
        )
        or 0
    )

    if (
        not professional_cabinet_id
        or not specialist_id
    ):
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
        return

    await state.set_state(
        AdminSpecialistsFSM
        .entering_specialist_changes_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_changes_reason_prompt",
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
                            "ADM_SP_CHANGES_CANCEL:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_specialists_router.callback_query(
    F.data.startswith(
        "ADM_SP_CHANGES_CANCEL:"
    )
)
async def cancel_specialist_changes(
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
                (callback.data or "").split(
                    ":",
                    1,
                )[1]
            ),
        )
    except (TypeError, ValueError):
        page = 0

    await state.set_state(None)
    await state.update_data(
        moderator_changes_professional_cabinet_id=None,
        moderator_changes_specialist_id=None,
        moderator_changes_reason=None,
        moderator_changes_page=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_changes_cancelled",
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


@admin_specialists_router.callback_query(
    F.data == "ADM_SP_CHANGES_CONFIRM"
)
async def confirm_specialist_changes(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    professional_cabinet_id = data.get(
        "moderator_changes_professional_cabinet_id"
    )
    specialist_id = data.get(
        "moderator_changes_specialist_id"
    )
    reason = (
        data.get(
            "moderator_changes_reason"
        )
        or ""
    ).strip()
    page = int(
        data.get(
            "moderator_changes_page"
        )
        or 0
    )

    if (
        not professional_cabinet_id
        or not specialist_id
        or len(reason) < 3
    ):
        await state.set_state(None)
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
        return


    moderator_user_id = None

    try:
        cabinet_id = UUID(
            professional_cabinet_id
        )

        async with get_session() as session:
            action_result = await AdminSpecialistsService(
                session
            ).request_specialist_changes(
                platform_user_id=(
                    callback.from_user.id
                ),
                professional_cabinet_id=(
                    cabinet_id
                ),
                reason=reason,
            )

            result = action_result.result
            moderator_user_id = (
                action_result.actor.user_id
            )

    except (
        AdminSpecialistsAccessError,
        ModerationError,
        ValueError,
    ) as exc:
        logger.warning(
            "moderator_cabinet_changes_failed "
            "telegram_id=%s "
            "professional_cabinet_id=%s "
            "specialist_id=%s error=%s",
            callback.from_user.id,
            professional_cabinet_id,
            specialist_id,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "moderator_cabinet_changes_requested "
        "telegram_id=%s moderator_user_id=%s "
        "professional_cabinet_id=%s "
        "specialist_id=%s status=%s",
        callback.from_user.id,
        moderator_user_id,
        professional_cabinet_id,
        specialist_id,
        result.status,
    )

    await state.set_state(None)
    await state.update_data(
        moderator_changes_professional_cabinet_id=None,
        moderator_changes_specialist_id=None,
        moderator_changes_reason=None,
        moderator_changes_page=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_changes_submitted",
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
