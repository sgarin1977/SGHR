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
    MODERATOR_PROFILE_PAGE_SIZE,
    READ_ONLY_MODERATION_TARGET_ROLES,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_reviews import (
    AdminReviewsAccessError,
    AdminReviewsDecisionError,
    AdminReviewsService,
)
from services.reviews import (
    ReviewModerationCard,
    ReviewServiceError,
)
from ui.texts import t


admin_reviews_router = Router()


admin_reviews_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_reviews_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


normalize_language = normalize_admin_language


class AdminReviewsFSM(StatesGroup):
    entering_review_hide_reason = State()


def review_moderation_error_text(
    error: Exception,
    language: str,
) -> str:
    if "no longer pending moderation" in str(error).lower():
        return t(
            "admin_review_already_processed",
            language,
        )

    return t(
        "admin_item_not_found",
        language,
    )


def review_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_show_review", language),
                callback_data=f"ADM_RV_APPROVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_hide_review", language),
                callback_data=f"ADM_RV_HIDE:{index}",
            ),
        ]
    ]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_RV_VIEW:{index - 1}",
            )
        )
    elif page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_REVIEWS_PAGE:{page - 1}",
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_RV_VIEW:{index + 1}",
            )
        )
    elif has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_REVIEWS_PAGE:{page + 1}",
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


def review_reason_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_cancel",
                        language,
                    ),
                    callback_data="ADM_REVIEWS",
                )
            ],
        ]
    )


def review_result_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data="ADM_REVIEWS",
                )
            ],
        ]
    )


def format_review_card(
    card: ReviewModerationCard,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    review = card.review
    review_text = (
        review.text
        or t(
            "admin_no_comment",
            language,
        )
    )
    target_name = (
        card.target_name
        or t(
            "admin_review_target_unavailable",
            language,
        )
    )

    title = t(
        "admin_review_title",
        language,
    ).format(
        index=index + 1,
        total=total,
    )

    return (
        f"{title}\n\n"
        f"{t('admin_review_rating', language)}: "
        f"{review.rating}/5\n"
        f"{t('admin_review_author', language)}: "
        f"{card.author_label}\n"
        f"{t('admin_review_target', language)}: "
        f"{target_name}\n"
        f"{t('admin_review_cabinet', language)}: "
        f"{card.cabinet_title}\n"
        f"{t('admin_review_profession', language)}: "
        f"{card.profession_name}\n\n"
        f"{t('admin_review_text', language)}:\n"
        f"{review_text}"
    )


def super_admin_read_only_moderator_reviews_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_REV_VIEW:{index - 1}",
            )
        )
    elif page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_MOD_REVIEWS_PAGE:{page - 1}"
                ),
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_REV_VIEW:{index + 1}",
            )
        )
    elif has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_MOD_REVIEWS_PAGE:{page + 1}"
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
                        "super_admin_ro_moderator_back_to_reviews_btn",
                        language,
                    ),
                    callback_data=(
                        f"SA_RO_MOD_REVIEWS_PAGE:{page}"
                    ),
                )
            ],
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


@admin_reviews_router.callback_query(F.data == "SA_RO_MOD_REVIEWS")
@admin_reviews_router.callback_query(
    F.data.startswith("SA_RO_MOD_REVIEWS_PAGE:")
)
async def super_admin_read_only_moderator_reviews(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    if callback.data == "SA_RO_MOD_REVIEWS":
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
            results = await AdminReviewsService(
                session
            ).list_impersonated_pending_reviews(
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
        AdminReviewsAccessError,
        ReviewServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    reviews = results[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(results) > MODERATOR_PROFILE_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_moderator_review_ids=[
            str(review.id)
            for review in reviews
        ],
        super_admin_impersonation_moderator_review_page=page,
        super_admin_impersonation_moderator_review_has_next=(
            has_next
        ),
    )

    if not reviews:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=(
                t(
                    "super_admin_ro_moderator_reviews_title",
                    language,
                ).format(
                    page=page + 1,
                    count=0,
                )
                + "\n\n"
                + t(
                    "admin_no_pending_reviews",
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

    await show_super_admin_read_only_review(
        callback,
        state,
        index=0,
    )


@admin_reviews_router.callback_query(
    F.data.startswith("SA_RO_MOD_REV_VIEW:")
)
async def super_admin_read_only_moderator_review_view(
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

    await show_super_admin_read_only_review(
        callback,
        state,
        index=index,
    )


async def show_super_admin_read_only_review(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    review_ids = data.get(
        "super_admin_impersonation_moderator_review_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
        or not review_ids
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    index = max(0, min(index, len(review_ids) - 1))

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        review_id = UUID(review_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_review_page"
        ) or 0
    )
    has_next = bool(
        data.get(
            "super_admin_impersonation_moderator_review_has_next"
        )
    )

    try:
        async with get_session() as session:
            card = await AdminReviewsService(
                session
            ).get_impersonated_pending_review(
                platform_user_id=(
                    callback.from_user.id
                ),
                effective_moderator_user_id=(
                    target_user_id
                ),
                review_id=review_id,
                language=language,
            )
    except (
        AdminReviewsAccessError,
        ReviewServiceError,
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
            t(
                "super_admin_ro_moderator_reviews_title",
                language,
            ).format(
                page=page + 1,
                count=len(review_ids),
            )
            + "\n\n"
            + format_review_card(
                card,
                index=index,
                total=len(review_ids),
                language=language,
            )
        ),
        reply_markup=(
            super_admin_read_only_moderator_reviews_keyboard(
                index=index,
                total=len(review_ids),
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )


@admin_reviews_router.callback_query(
    F.data == "ADM_REVIEWS"
)
async def list_pending_reviews(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.set_state(None)
    await state.update_data(
        admin_review_action_id=None,
        admin_review_action_status=None,
        admin_review_source_chat_id=None,
        admin_review_source_message_id=None,
    )

    await open_pending_reviews_page(
        callback,
        state,
        page=0,
    )


@admin_reviews_router.callback_query(F.data.startswith("ADM_REVIEWS_PAGE:"))
async def change_pending_reviews_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    try:
        page = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        page = 0

    await open_pending_reviews_page(
        callback,
        state,
        page=max(page, 0),
    )


async def open_pending_reviews_page(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            results = await AdminReviewsService(
                session
            ).list_pending_reviews(
                platform_user_id=(
                    callback.from_user.id
                ),
                page=page,
                page_size=5,
            )

    except (
        AdminReviewsAccessError,
        ReviewServiceError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    reviews = results[:5]
    has_next = len(results) > 5

    if not reviews:
        await state.update_data(
            admin_review_ids=[],
            admin_review_page=page,
            admin_review_has_next=False,
        )

        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_pending_reviews",
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
                                "ADM_PANEL"
                            ),
                        )
                    ],
                ]
            ),
        )
        return

    await state.update_data(
        admin_review_ids=[
            str(review.id)
            for review in reviews
        ],
        admin_review_page=page,
        admin_review_has_next=has_next,
    )

    await show_review(
        callback,
        state,
        index=0,
    )


async def show_review(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_review_ids") or []
    page = int(data.get("admin_review_page") or 0)
    has_next = bool(data.get("admin_review_has_next"))
    if not ids:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_pending_reviews",
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
                                "ADM_PANEL"
                            ),
                        )
                    ],
                ]
            ),
        )
        return

    index = max(0, min(int(index), len(ids) - 1))

    try:
        async with get_session() as session:
            card = await AdminReviewsService(
                session
            ).get_pending_review(
                platform_user_id=(
                    callback.from_user.id
                ),
                review_id=UUID(
                    ids[index]
                ),
                language=language,
            )

    except (
        AdminReviewsAccessError,
        ReviewServiceError,
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
        text=format_review_card(
            card,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=review_keyboard(
            index=index,
            total=len(ids),
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_reviews_router.callback_query(F.data.startswith("ADM_RV_VIEW:"))
async def view_pending_review(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_review(callback, state, index=index)


@admin_reviews_router.callback_query(
    F.data.startswith(
        "ADM_RV_APPROVE:"
    )
)
async def approve_pending_review(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    index = int(
        callback.data.split(
            ":",
            1,
        )[1]
    )
    ids = data.get(
        "admin_review_ids"
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

    try:
        async with get_session() as session:
            await AdminReviewsService(
                session
            ).moderate_review(
                platform_user_id=(
                    callback.from_user.id
                ),
                review_id=UUID(
                    ids[index]
                ),
                status="published",
                reason="shown by moderator",
            )

    except (
        AdminReviewsAccessError,
        AdminReviewsDecisionError,
        ReviewServiceError,
        ValueError,
    ) as exc:
        error_text = (
            review_moderation_error_text(
                exc,
                language,
            )
            if isinstance(
                exc,
                ReviewServiceError,
            )
            else str(exc)
        )
        await callback.answer(
            error_text,
            show_alert=True,
        )
        return

    await open_pending_reviews_page(
        callback,
        state,
        page=0,
    )


@admin_reviews_router.callback_query(F.data.startswith("ADM_RV_HIDE:"))
async def ask_hide_review_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_review_moderation_reason(
        callback,
        state,
        status="hidden",
        state_name=AdminReviewsFSM.entering_review_hide_reason,
    )


async def prepare_review_moderation_reason(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
    state_name: State,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    index = int(
        callback.data.split(
            ":",
            1,
        )[1]
    )
    ids = data.get(
        "admin_review_ids"
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

    try:
        async with get_session() as session:
            await AdminReviewsService(
                session
            ).require_moderator_actor(
                platform_user_id=(
                    callback.from_user.id
                )
            )
    except AdminReviewsAccessError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_review_action_id=ids[index],
        admin_review_action_status=status,
        admin_review_source_chat_id=None,
        admin_review_source_message_id=None,
    )
    await state.set_state(
        state_name
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_reason_prompt",
            language,
        ),
        reply_markup=(
            review_reason_keyboard(
                language
            )
        ),
    )


@admin_reviews_router.message(
    AdminReviewsFSM
    .entering_review_hide_reason
)
async def receive_review_moderation_reason(
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
    review_id = data.get(
        "admin_review_action_id"
    )
    status = data.get(
        "admin_review_action_status"
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
                review_reason_keyboard(
                    language
                )
            ),
        )
        return

    if status != "hidden" or not review_id:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=(
                review_result_keyboard(
                    language
                )
            ),
        )
        await state.set_state(None)
        await state.update_data(
            admin_review_action_id=None,
            admin_review_action_status=None,
        )
        return

    try:
        parsed_review_id = UUID(
            str(review_id)
        )

        async with get_session() as session:
            await AdminReviewsService(
                session
            ).moderate_review(
                platform_user_id=(
                    message.from_user.id
                ),
                review_id=parsed_review_id,
                status="hidden",
                reason=reason,
            )

    except (
        AdminReviewsAccessError,
        AdminReviewsDecisionError,
        ReviewServiceError,
        ValueError,
    ) as exc:
        if isinstance(
            exc,
            AdminReviewsAccessError,
        ):
            error_text = t(
                "admin_access_denied",
                language,
            )
        elif isinstance(
            exc,
            ReviewServiceError,
        ):
            error_text = (
                review_moderation_error_text(
                    exc,
                    language,
                )
            )
        else:
            error_text = t(
                "admin_item_not_found",
                language,
            )

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=error_text,
            reply_markup=(
                review_result_keyboard(
                    language
                )
            ),
        )
        await state.set_state(None)
        await state.update_data(
            admin_review_action_id=None,
            admin_review_action_status=None,
        )
        return

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_review_updated",
            language,
        ).format(
            status=t(
                "admin_review_status_hidden",
                language,
            ),
        ),
        reply_markup=(
            review_result_keyboard(
                language
            )
        ),
    )
    await state.set_state(None)
    await state.update_data(
        admin_review_action_id=None,
        admin_review_action_status=None,
    )
