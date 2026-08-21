from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from database.session import get_session
from handlers.start import normalize_language
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message, delete_telegram_messages
from services.geo_search import SpecialistPublicCard
from handlers.billing_common import clear_cross_feature_messages
from services.user_favorites import UserFavoritesAccessError, UserFavoritesService


user_favorites_router = Router()
FAVORITES_PAGE_SIZE = 5


def favorites_list_keyboard(
    language: str,
    *,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = []
    navigation_row = []

    if page > 0:
        navigation_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_prev", language),
                callback_data=f"CAB_FAVORITES:{page - 1}",
            )
        )

    if has_next:
        navigation_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_next", language),
                callback_data=f"CAB_FAVORITES:{page + 1}",
            )
        )

    if navigation_row:
        rows.append(navigation_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="search_start",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def favorite_list_card_keyboard(
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_result_details_btn", language),
                    callback_data=f"CAB_FAV_VIEW:{index}",
                ),
                InlineKeyboardButton(
                    text=t("search_result_message_btn", language),
                    callback_data=f"search_result_contact:{index}",
                ),
            ]
        ]
    )


def favorite_card_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "contact",
                        language,
                    ),
                    callback_data=(
                        "search_contact_pending"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_report_cabinet_btn",
                        language,
                    ),
                    callback_data=(
                        "search_report_pending"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_report_user_btn",
                        language,
                    ),
                    callback_data=(
                        "search_report_user_pending"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "favorite_remove_btn",
                        language,
                    ),
                    callback_data="CAB_FAV_REMOVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "billing_back",
                        language,
                    ),
                    callback_data="CAB_FAVORITES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )


def favorite_work_format_label(value: str | None, language: str) -> str:
    labels = {
        None: t("search_filter_any", language),
        "at_client": t("search_work_at_client", language),
        "at_specialist": t("search_work_at_specialist", language),
        "remote": t("search_work_remote", language),
        "mixed": t("search_work_mixed", language),
    }
    return labels.get(value, value or "-")


def format_favorite_card(card: SpecialistPublicCard, language: str) -> str:
    lines = [card.display_name, ""]

    category_parts = [
        part
        for part in [card.category_name, card.profession_name]
        if part
    ]
    if category_parts:
        lines.append(" • ".join(category_parts))

    if card.city_name:
        lines.append(f"{t('search_filter_location_label', language)}: {card.city_name}")
    elif card.work_format == "remote":
        lines.append(
            f"{t('search_filter_location_label', language)}: "
            f"{favorite_work_format_label('remote', language)}"
        )

    work_format = favorite_work_format_label(card.work_format, language)
    if card.work_format:
        lines.append(f"{t('search_filter_work_label', language)}: {work_format}")

    if card.service_titles:
        lines.append(
            f"{t('search_services_label', language)}: "
            f"{', '.join(card.service_titles)}"
        )

    if card.languages:
        lines.append(
            f"{t('search_filter_language_label', language)}: "
            f"{', '.join(card.languages)}"
        )

    if card.reviews_count > 0 and card.rating is not None:
        rating = f"{float(card.rating):.1f} ({card.reviews_count})"
    else:
        rating = t("search_no_reviews", language)

    lines.append(f"{t('search_rating', language)}: {rating}")

    description = " ".join((card.short_description or "").split())
    if description:
        lines.extend(["", description[:300]])

    return "\n".join(lines)


@user_favorites_router.callback_query(
    (F.data == "CAB_FAVORITES")
    | F.data.startswith("CAB_FAVORITES:")
)
async def show_favorites(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    requested_page: int | None = None,
    callback_answered: bool = False,
):
    await clear_cross_feature_messages(
        callback=callback,
        state=state,
    )

    language = normalize_language(
        callback.from_user.language_code
    )

    page = 0

    if requested_page is not None:
        page = max(
            0,
            int(requested_page),
        )
    elif (
        callback.data
        and callback.data.startswith(
            "CAB_FAVORITES:"
        )
    ):
        parts = callback.data.split(":")

        if (
            len(parts) >= 2
            and parts[1].isdigit()
        ):
            page = int(parts[1])

    async with get_session() as session:
        try:
            favorites_page = (
                await UserFavoritesService(
                    session
                ).list_favorites(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    page=page,
                    page_size=FAVORITES_PAGE_SIZE,
                )
            )
        except UserFavoritesAccessError:
            await callback.answer(
                t(
                    "billing_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

    language = favorites_page.actor.language

    cards = favorites_page.cards
    has_next = favorites_page.has_next
    page = favorites_page.page

    specialist_ids = [
        str(card.specialist_id)
        for card in cards
    ]
    professional_cabinet_ids = [
        str(card.professional_cabinet_id)
        for card in cards
    ]

    await state.update_data(
        user_language=language,
        cabinet_favorite_ids=(
            professional_cabinet_ids
        ),
        cabinet_favorites_page=page,
        result_specialist_ids=specialist_ids,
        result_professional_cabinet_ids=(
            professional_cabinet_ids
        ),
        result_distances=[
            None
        ] * len(cards),
        results_page=0,
        profession_id=None,
    )

    rendered_message_ids: list[int] = []

    if not cards:
        empty_message = (
            await callback.message.answer(
                t(
                    "favorites_empty",
                    language,
                ),
                reply_markup=(
                    favorites_list_keyboard(
                        language,
                        page=page,
                        has_next=False,
                    )
                ),
            )
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            cabinet_favorite_message_ids=(
                rendered_message_ids
            ),
        )

        if not callback_answered:
            await callback.answer()

        return

    header_message = (
        await callback.message.answer(
            (
                f"{t('favorites_title', language)}\n"
                f"{t('favorites_hint', language)}"
            )
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    for index, card in enumerate(cards):
        card_message = (
            await callback.message.answer(
                format_favorite_card(
                    card,
                    language,
                ),
                reply_markup=(
                    favorite_list_card_keyboard(
                        index,
                        language,
                    )
                ),
            )
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = (
        await callback.message.answer(
            t(
                "favorites_navigation",
                language,
            ),
            reply_markup=(
                favorites_list_keyboard(
                    language,
                    page=page,
                    has_next=has_next,
                )
            ),
        )
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        cabinet_favorite_message_ids=(
            rendered_message_ids
        ),
    )

    if not callback_answered:
        await callback.answer()


@user_favorites_router.callback_query(
    F.data.startswith("CAB_FAV_VIEW:")
)
async def show_favorite_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    professional_cabinet_ids = (
        data.get("cabinet_favorite_ids")
        or []
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (IndexError, ValueError):
        await callback.answer()
        return

    if (
        index < 0
        or index
        >= len(professional_cabinet_ids)
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    professional_cabinet_id = (
        professional_cabinet_ids[index]
    )

    async with get_session() as session:
        try:
            action = await UserFavoritesService(
                session
            ).get_favorite_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        except UserFavoritesAccessError:
            await callback.answer(
                t(
                    "billing_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

    card = action.result
    language = action.actor.language

    if not card:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                data.get(
                    "cabinet_favorite_message_ids"
                )
                or []
            )
            if (
                message_id
                and int(message_id)
                != callback.message.message_id
            )
        ],
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=format_favorite_card(
            card,
            language,
        ),
        reply_markup=favorite_card_keyboard(
            language
        ),
    )

    await state.update_data(
        selected_specialist_id=str(
            card.specialist_id
        ),
        selected_professional_cabinet_id=str(
            card.professional_cabinet_id
        ),
        selected_specialist_distance=None,
        results_page=0,
        user_language=language,
        cabinet_favorite_message_ids=[],
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@user_favorites_router.callback_query(
    F.data == "CAB_FAV_REMOVE"
)
async def remove_favorite_from_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    page = int(
        data.get("cabinet_favorites_page")
        or 0
    )
    professional_cabinet_id = data.get(
        "selected_professional_cabinet_id"
    )

    if not professional_cabinet_id:
        await callback.answer(
            t(
                "search_contact_no_specialist",
                language,
            ),
            show_alert=True,
        )
        return

    async with get_session() as session:
        try:
            action = await UserFavoritesService(
                session
            ).remove_favorite(
                platform_user_id=(
                    callback.from_user.id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        except UserFavoritesAccessError:
            await callback.answer(
                t(
                    "billing_start_required",
                    language,
                ),
                show_alert=True,
            )
            return

    removed = action.result
    language = action.actor.language

    text_key = (
        "favorite_removed"
        if removed
        else "favorites_not_found"
    )
    await callback.answer(
        t(
            text_key,
            language,
        ),
        show_alert=True,
    )

    await state.update_data(
        selected_specialist_id=None,
        selected_professional_cabinet_id=None,
    )

    await show_favorites(
        callback,
        state,
        requested_page=page,
        callback_answered=True,
    )
