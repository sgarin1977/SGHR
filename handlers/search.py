import logging
from uuid import UUID
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from services.user import UserService
from database.repositories.contact import ContactChatRepository
from database.repositories.geo_repository import GeoRepository
from database.repositories.search import SpecialistSearchRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.moderation import ModerationRepository
from database.repositories.translation import TranslationRepository
from database.session import get_session
from handlers.start import send_global_main_menu
from services.contact_chat import ContactChatError, ContactChatService
from services.geo_search import (
    EmptySearchEvent,
    GeoSearchService,
    SearchResultsViewedEvent,
    SpecialistPublicCard,
    PublicCardViewEvent,
)
from services.geo_service import GeoService, GeoServiceError
from services.moderation import ModerationError, ModerationService
from services.rate_limit import RateLimitError
from services.specialist import (
    SpecialistSearchSelectionService,
    SpecialistSearchTextService,
)
from services.translation import TranslationError, TranslationService
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_menu_message,
    edit_or_replace_tracked_menu_message,
    replace_callback_menu_message,
    send_telegram_attachment,
    split_telegram_text,
)
from database.repositories.favorites import FavoriteRepository
from services.favorites import FavoriteService
from database.repositories.portfolio import PortfolioRepository
from database.repositories.reviews import ReviewRepository
from services.reviews import ReviewService, ReviewServiceError
from services.portfolio import PortfolioService, PortfolioServiceError
search_router = Router()
logger = logging.getLogger(__name__)

PER_PAGE = 5
DEFAULT_RADIUS_KM = 25
CATEGORY_PAGE_SIZE = 8
PUBLIC_REVIEW_PAGE_SIZE = 5

class SpecialistSearchFSM(StatesGroup):
    choosing_category = State()
    choosing_profession = State()
    entering_text_query = State()
    entering_location_query = State()
    choosing_geo_place = State()
    waiting_geo = State()
    choosing_filters = State()
    viewing_results = State()
    entering_thread_message = State()
    entering_report_comment = State()
    confirming_report = State()
    choosing_review_rating = State()
    entering_review_text = State()

def normalize_language(language: str | None) -> str:
    return language if language in {"ru", "en", "pt", "uk"} else "ru"

async def get_interface_language(
    telegram_id: int | str,
    fallback_language: str | None,
) -> str:
    language = normalize_language(fallback_language)

    async with get_session() as session:
        user = await UserService(
            session
        ).get_user_by_telegram_id(
            telegram_id
        )

        if not user:
            return language

        resolved_language = await TranslationService(
            TranslationRepository(session)
        ).resolve_interface_language(
            user_id=user.id,
            fallback_language=user.language_code,
        )

    return normalize_language(resolved_language)

async def get_search_language(state: FSMContext, event: CallbackQuery | Message) -> str:
    data = await state.get_data()
    stored_language = data.get("user_language")

    if stored_language in {"ru", "en", "pt"}:
        return stored_language

    fallback_language = event.from_user.language_code if event.from_user else None
    telegram_id = event.from_user.id if event.from_user else None

    if telegram_id is None:
        return normalize_language(fallback_language)

    language = await get_interface_language(telegram_id, fallback_language)
    await state.update_data(user_language=language)
    return language

def item_name(item, language: str = "ru") -> str:
    localized = getattr(item, f"name_{language}", None)
    return localized or getattr(item, "name_ru", None) or getattr(item, "name", None) or str(item.id)


def work_format_label(value: str | None, language: str) -> str:
    labels = {
        None: t("search_filter_any", language),
        "at_client": t("search_work_at_client", language),
        "at_specialist": t("search_work_at_specialist", language),
        "remote": t("search_work_remote", language),
        "mixed": t("search_work_mixed", language),
    }
    return labels.get(value, value or t("search_filter_any", language))


def language_filter_label(value: str | None, language: str) -> str:
    labels = {
        None: t("search_filter_any", language),
        "ru": t("search_language_ru", language),
        "pt": t("search_language_pt", language),
        "en": t("search_language_en", language),
    }
    return labels.get(value, value or t("search_filter_any", language))


def sort_label(value: str | None, language: str) -> str:
    labels = {
        "distance": t("search_sort_distance", language),
        "relevance": t("search_sort_relevance", language),
    }
    return labels.get(value or "distance", value or "distance")


def geo_candidate_label(candidate: dict, index: int) -> str:
    name = candidate.get("name") or candidate.get("display_name") or "-"
    country = candidate.get("country_name") or candidate.get("country_code") or "-"
    place_type = candidate.get("place_type") or candidate.get("osm_type") or "place"
    return f"{index + 1}. {name}, {country} - {place_type}"

def dedupe_geo_candidate_states(candidates: list[dict], limit: int = 8) -> list[dict]:
    unique_candidates = []
    seen = set()

    for candidate in candidates:
        name = str(candidate.get("name") or "").strip().lower()
        country_code = str(candidate.get("country_code") or "").strip().upper()
        country_name = str(candidate.get("country_name") or "").strip().lower()
        place_type = str(candidate.get("place_type") or "").strip().lower()
        display_name = str(candidate.get("display_name") or "").strip().lower()

        human_key = (
            name,
            country_code or country_name,
            place_type,
        )

        fallback_key = (
            display_name,
            place_type,
        )

        dedupe_key = human_key if name else fallback_key

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        unique_candidates.append(candidate)

        if len(unique_candidates) >= limit:
            break

    return unique_candidates

async def show_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
) -> Message:
    return await edit_or_replace_menu_message(
        callback=callback,
        text=text,
        reply_markup=reply_markup,
    )

async def collapse_search_results_to_callback_message(
    *,
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    current_message_id = (
        callback.message.message_id
    )

    stale_message_ids = [
        int(message_id)
        for message_id in (
            data.get(
                "last_search_result_message_ids"
            )
            or []
        )
        if (
            message_id
            and int(message_id)
            != current_message_id
        )
    ]

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=stale_message_ids,
    )

    await state.update_data(
        last_search_result_message_ids=[],
        last_menu_message_id=current_message_id,
    )



async def get_requester_context(
    platform_user_id: int | str,
) -> tuple[UUID | None, UUID | None]:
    async with get_session() as session:
        context = await UserService(
            session
        ).get_requester_context(
            platform_user_id
        )

    if not context:
        return None, None

    return (
        context.user_id,
        context.tenant_id,
    )

async def store_post_auth_action(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    action: str,
    language: str,
):
    await state.update_data(
        post_auth_action=action
    )

    menu_message = await show_callback_message(
        callback,
        t(
            "auth_required_start",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

    await callback.answer()

async def resume_public_portfolio_after_auth(
    *,
    message: Message,
    state: FSMContext,
    language: str,
    tenant_id: UUID,
    user_id: UUID,
    specialist_id: str,
) -> None:
    data = await state.get_data()
    results_page = int(
        data.get("results_page") or 0
    )

    try:
        async with get_session() as session:
            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_active_items_for_viewer(
                tenant_id=tenant_id,
                specialist_id=UUID(specialist_id),
                viewer_user_id=user_id,
                page=0,
            )
    except (
        PortfolioServiceError,
        ValueError,
    ) as exc:
        logger.warning(
            "post_auth_public_portfolio_failed "
            "specialist_id=%s error=%s",
            specialist_id,
            exc,
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "public_portfolio_load_error",
                    language,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_back",
                                    language,
                                ),
                                callback_data=(
                                    f"search_results_page:"
                                    f"{results_page}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data="search_menu",
                            )
                        ],
                    ]
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=menu_message_id,
        )
        return

    if not items:
        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "public_portfolio_empty",
                    language,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_back",
                                    language,
                                ),
                                callback_data=(
                                    f"search_result_back_to_card:"
                                    f"{results_page}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data="search_menu",
                            )
                        ],
                    ]
                ),
            )
        )

        await state.set_state(
            SpecialistSearchFSM.viewing_results
        )
        await state.update_data(
            public_portfolio_page=0,
            public_portfolio_item_ids=[],
            last_menu_message_id=menu_message_id,
        )
        return

    view = items[0]

    keyboard = public_portfolio_keyboard(
        signed_url=view.signed_url,
        language=language,
        page=0,
        has_previous=False,
        has_next=len(items) > 1,
        results_page=results_page,
    )
    caption = public_portfolio_caption(
        view,
        language,
    )

    stale_message_ids = [
        int(message_id)
        for message_id in (
            data.get(
                "last_search_result_message_ids"
            )
            or []
        )
        if (
            message_id
            and str(message_id)
            != str(
                data.get("last_menu_message_id")
            )
        )
    ]

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=stale_message_ids,
    )

    menu_message_id: int

    if view.storage_object.file_type == "photo":
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                data.get("last_menu_message_id")
            ],
        )

        try:
            portfolio_message = (
                await message.bot.send_photo(
                    chat_id=message.chat.id,
                    photo=view.signed_url,
                    caption=caption,
                    reply_markup=keyboard,
                )
            )
            menu_message_id = (
                portfolio_message.message_id
            )
        except Exception as exc:
            logger.warning(
                "post_auth_portfolio_photo_send_failed "
                "item_id=%s error=%s",
                view.item.id,
                exc,
            )
            menu_message_id = (
                await edit_or_replace_tracked_menu_message(
                    message=message,
                    menu_message_id=None,
                    text=caption,
                    reply_markup=keyboard,
                )
            )
    else:
        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=caption,
                reply_markup=keyboard,
            )
        )

    await state.set_state(
        SpecialistSearchFSM.viewing_results
    )
    await state.update_data(
        public_portfolio_page=0,
        public_portfolio_item_ids=[
            str(item.item.id)
            for item in items
        ],
        pending_report_target_type=(
            "portfolio_item"
        ),
        pending_report_target_id=str(
            view.item.id
        ),
        last_search_result_message_ids=[],
        last_menu_message_id=menu_message_id,
    )



async def resume_public_reviews_after_auth(
    *,
    message: Message,
    state: FSMContext,
    language: str,
    tenant_id: UUID,
    user_id: UUID,
    specialist_id: str,
) -> None:
    data = await state.get_data()
    results_page = int(
        data.get("results_page") or 0
    )

    try:
        async with get_session() as session:
            review_page = await ReviewService(
                ReviewRepository(session)
            ).list_public_reviews_for_viewer(
                tenant_id=tenant_id,
                specialist_id=UUID(specialist_id),
                viewer_user_id=user_id,
                page=0,
                page_size=PUBLIC_REVIEW_PAGE_SIZE,
            )
    except (
        ReviewServiceError,
        ValueError,
    ) as exc:
        logger.warning(
            "post_auth_public_reviews_failed "
            "specialist_id=%s error=%s",
            specialist_id,
            exc,
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "public_reviews_load_error",
                    language,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_back",
                                    language,
                                ),
                                callback_data=(
                                    f"search_results_page:"
                                    f"{results_page}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data="search_menu",
                            )
                        ],
                    ]
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=menu_message_id,
        )
        return

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                data.get(
                    "last_search_result_message_ids"
                )
                or []
            )
            if (
                message_id
                and str(message_id)
                != str(
                    data.get("last_menu_message_id")
                )
            )
        ],
    )

    await state.update_data(
        public_reviews_page=review_page.page,
        public_review_ids=[
            str(review.id)
            for review in review_page.reviews
        ],
        last_search_result_message_ids=[],
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=format_public_reviews(
                review_page,
                language,
            ),
            reply_markup=public_reviews_keyboard(
                language=language,
                page=review_page.page,
                has_previous=(
                    review_page.has_previous
                ),
                has_next=review_page.has_next,
                reviews_count=len(
                    review_page.reviews
                ),
                results_page=results_page,
            ),
        )
    )

    await state.set_state(
        SpecialistSearchFSM.viewing_results
    )
    await state.update_data(
        last_menu_message_id=menu_message_id,
    )



async def resume_post_auth_action(
    *,
    message: Message,
    state: FSMContext,
    language: str,
) -> bool:
    data = await state.get_data()
    action = data.get("post_auth_action")
    specialist_id = data.get("selected_specialist_id")
    profession_id = data.get("profession_id")

    if not action or not specialist_id:
        return False

    user_id, tenant_id = await get_requester_context(
        message.from_user.id,
    )
    if not user_id or not tenant_id:
        return False

    await state.update_data(post_auth_action=None)

    if action == "contact":
        try:
            async with get_session() as session:
                chat = await ContactChatService(
                    ContactChatRepository(session)
                ).open_contact_chat(
                    tenant_id=tenant_id,
                    from_user_id=user_id,
                    specialist_id=UUID(specialist_id),
                    profession_id=(
                        UUID(profession_id)
                        if profession_id
                        else None
                    ),
                    system_message=t(
                        "contact_chat_first_prompt",
                        language,
                    ),
                    original_language=language,
                )
        except (
            ContactChatError,
            ValueError,
        ) as exc:
            logger.warning(
                "post_auth_contact_chat_open_failed "
                "telegram_id=%s specialist_id=%s "
                "error=%s",
                message.from_user.id,
                specialist_id,
                exc,
            )

            menu_message_id = (
                await edit_or_replace_tracked_menu_message(
                    message=message,
                    menu_message_id=data.get(
                        "last_menu_message_id"
                    ),
                    text=t(
                        "contact_chat_error",
                        language,
                    ),
                    reply_markup=search_start_keyboard(
                        language
                    ),
                )
            )

            await state.update_data(
                last_menu_message_id=menu_message_id
            )
            return True

        await state.update_data(
            active_contact_request_id=str(
                chat.contact_request_id
            ),
            active_thread_id=str(chat.thread_id),
            active_thread_role="client",
            pending_contact_message=None,
        )
        await state.set_state(
            SpecialistSearchFSM.entering_thread_message,
        )

        messages_to_delete = [
            data.get("last_menu_message_id"),
            *(
                data.get(
                    "last_search_result_message_ids"
                )
                or []
            ),
        ]

        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                int(message_id)
                for message_id in messages_to_delete
                if message_id
            ],
        )

        await state.update_data(
            last_menu_message_id=None,
            last_search_result_message_ids=[],
        )

        await show_client_contact_chat(
            message=message,
            state=state,
            thread_id=str(chat.thread_id),
            user_id=user_id,
            language=language,
        )
        return True
    if action == "favorite":
        try:
            async with get_session() as session:
                is_saved = await FavoriteService(
                    FavoriteRepository(session)
                ).toggle_specialist(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    specialist_id=UUID(
                        specialist_id
                    ),
                )

            text_key = (
                "favorite_saved"
                if is_saved
                else "favorite_removed"
            )
            result_text = t(
                text_key,
                language,
            )
        except Exception as exc:
            logger.exception(
                "post_auth_favorite_toggle_failed "
                "telegram_id=%s specialist_id=%s",
                message.from_user.id,
                specialist_id,
            )
            result_text = t(
                "favorite_action_error",
                language,
            )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=result_text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_back_to_filters_btn",
                                    language,
                                ),
                                callback_data="search_filters",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data="search_menu",
                            )
                        ],
                    ]
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=menu_message_id
        )
        return True

    if action == "report":
        await state.set_state(
            SpecialistSearchFSM.viewing_results
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "complaint_reason_prompt",
                    language,
                ),
                reply_markup=complaint_reason_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=menu_message_id
        )
        return True
    if action == "portfolio":
        await resume_public_portfolio_after_auth(
            message=message,
            state=state,
            language=language,
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )
        return True
    if action == "reviews":
        await resume_public_reviews_after_auth(
            message=message,
            state=state,
            language=language,
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )
        return True
    return False

def callback_index(callback: CallbackQuery) -> int | None:
    try:
        return int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None

async def load_search_profession_options(
    *,
    category_id: UUID | None,
    language: str,
):
    async with get_session() as session:
        return await (
            SpecialistSearchSelectionService(
                SpecialistRepository(session)
            ).list_profession_options(
                category_id=category_id,
                language=language,
                limit=100,
            )
        )

def telegram_chat_id(platform_user_id: str | int | None) -> int | None:
    if platform_user_id is None:
        return None

    try:
        return int(str(platform_user_id).strip())
    except (TypeError, ValueError):
        return None

def paged_keyboard(
    *,
    items,
    item_prefix: str,
    page_prefix: str,
    page: int,
    language: str,
    back_callback: str = "search_filters",
    back_text_key: str = "search_back_to_filters_btn",
    page_size: int = PER_PAGE,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    selected_item_id: str | None = None,
) -> InlineKeyboardMarkup:
    start = page * page_size
    end = start + page_size

    rows = []
    for index, item in enumerate(
        items[start:end],
        start=start,
    ):
        item_id = str(item.id)
        marker = (
            "✓ "
            if item_id == selected_item_id
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker}"
                        f"{item_name(item, language)}"
                    ),
                    callback_data=f"{item_prefix}:{index}",
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t(
                    "search_previous_categories",
                    language,
                ),
                callback_data=(
                    f"{page_prefix}:{page - 1}"
                ),
            )
        )
    if end < len(items):
        nav.append(
            InlineKeyboardButton(
                text=t(
                    "search_more_categories",
                    language,
                ),
                callback_data=(
                    f"{page_prefix}:{page + 1}"
                ),
            )
        )
    if nav:
        rows.append(nav)

    if extra_rows:
        rows.extend(extra_rows)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(back_text_key, language),
                callback_data=back_callback,
            )
        ]
    )
    rows.append([InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def category_selection_text(
    language: str,
    selected_names: list[str],
) -> str:
    screen_text = t(
        "search_choose_category",
        language,
    )

    if not selected_names:
        return screen_text

    selected_count_text = t(
        "search_selected_professions_count",
        language,
    ).format(
        count=len(selected_names),
    )

    return (
        f"{screen_text}\n\n"
        f"{selected_count_text}\n"
        f"{', '.join(selected_names)}"
    )


def category_selection_rows(
    language: str,
    selected_ids: list[str],
) -> list[list[InlineKeyboardButton]] | None:
    if not selected_ids:
        return None

    return [
        [
            InlineKeyboardButton(
                text=t(
                    "search_show_specialists_btn",
                    language,
                ),
                callback_data="search_professions_apply",
            )
        ]
    ]

def profession_keyboard(
    *,
    professions,
    page: int,
    language: str,
    selected_ids: set[str] | None = None,
    show_filters_back: bool = False,
) -> InlineKeyboardMarkup:
    selected_ids = selected_ids or set()
    page_size = PER_PAGE
    start = page * page_size
    end = start + page_size

    rows = [
        [
            InlineKeyboardButton(
                text=t("search_all_professions", language),
                callback_data="search_professions_select_all",
            )
        ]
    ]

    for index, profession in enumerate(professions[start:end], start=start):
        profession_id = str(profession.id)
        marker = "☑" if profession_id in selected_ids else "☐"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker} {item_name(profession, language)}",
                    callback_data=f"search_profession_toggle:{index}",
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t(
                    "search_previous_professions",
                    language,
                ),
                callback_data=(
                    f"search_professions_page:{page - 1}"
                ),
            )
        )
    if end < len(professions):
        nav.append(
            InlineKeyboardButton(
                text=t(
                    "search_more_professions",
                    language,
                ),
                callback_data=(
                    f"search_professions_page:{page + 1}"
                ),
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_reset_directions_btn", language),
                callback_data="search_professions_reset",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_show_specialists_btn", language),
                callback_data="search_professions_apply",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "search_back_to_categories_btn",
                    language,
                ),
                callback_data="search_filter_category",
            )
        ]
    )
    if show_filters_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "search_back_to_filters_btn",
                        language,
                    ),
                    callback_data="search_filters",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "search_menu",
                    language,
                ),
                callback_data="search_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )

def profession_selection_text(
    language: str,
    selected_ids: list[str] | set[str] | None,
) -> str:
    selected_count = len(selected_ids or [])

    return (
        f"{t('search_choose_profession', language)}\n\n"
        f"{t('search_selected_professions_count', language).format(count=selected_count)}"
    )



def result_card_keyboard(
    index: int,
    language: str,
    *,
    is_saved: bool = False,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_result_details_btn", language),
                    callback_data=f"search_result:{index}",
                ),
                InlineKeyboardButton(
                    text=t("search_result_message_btn", language),
                    callback_data=f"search_result_contact:{index}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_result_saved_btn"
                        if is_saved
                        else "search_result_save_btn",
                        language,
                    ),
                    callback_data=f"search_result_favorite:{index}",
                )
            ],
        ]
    )


def results_navigation_keyboard(
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if has_next:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("search_next_specialists", language),
                    callback_data=f"search_results_page:{page + 1}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_filters_btn", language),
                callback_data="search_filters",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="search_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def card_keyboard(
    language: str,
    results_page: int = 0,
    *,
    is_saved: bool = False,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "contact",
                        language,
                    ),
                    callback_data="search_contact_pending",
                ),
                InlineKeyboardButton(
                    text=t(
                        (
                            "search_result_saved_btn"
                            if is_saved
                            else "search_result_save_btn"
                        ),
                        language,
                    ),
                    callback_data="search_favorite_pending",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "specialist_profile_portfolio_btn",
                        language,
                    ),
                    callback_data="search_portfolio_pending",
                ),
                InlineKeyboardButton(
                    text=t(
                        "specialist_profile_reviews_btn",
                        language,
                    ),
                    callback_data="search_reviews_pending",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_back",
                        language,
                    ),
                    callback_data=(
                        f"search_results_page:"
                        f"{results_page}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="search_menu",
                )
            ],
        ]
    )

def public_portfolio_caption(view, language: str) -> str:
    is_photo = view.storage_object.file_type == "photo"
    label = t(
        (
            "portfolio_photo_label"
            if is_photo
            else "portfolio_pdf_label"
        ),
        language,
    )

    title = view.item.title or label
    description = (view.item.description or "").strip()

    lines = [
        t("public_portfolio_title", language),
        f"{label}: {title}",
    ]

    if description:
        lines.append(description)

    return "\n".join(lines)


def public_portfolio_keyboard(
    *,
    signed_url: str,
    language: str,
    page: int,
    has_previous: bool,
    has_next: bool,
    results_page: int,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ]
    ]

    nav = []
    if has_previous:
        nav.append(
            InlineKeyboardButton(
                text=t("prev_btn", language),
                callback_data=f"search_portfolio_page:{page - 1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text=t("next_btn", language),
                callback_data=f"search_portfolio_page:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("public_portfolio_report_btn", language),
                callback_data="search_portfolio_report",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_back", language),
                callback_data=f"search_result_back_to_card:{results_page}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="search_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_public_reviews(review_page, language: str) -> str:
    if review_page.reputation and review_page.reputation.review_count:
        score = float(review_page.reputation.score or 0)
        rating_line = t("public_reviews_summary", language).format(
            rating=f"{score:.1f}",
            count=review_page.reputation.review_count,
        )
    else:
        rating_line = t("public_reviews_summary", language).format(
            rating=t("search_no_reviews", language),
            count=0,
        )

    lines = [
        t("public_reviews_title", language),
        rating_line,
        "",
    ]

    if not review_page.reviews:
        lines.append(t("public_reviews_empty", language))
        return "\n".join(lines)

    start_number = review_page.page * review_page.page_size + 1

    for index, review in enumerate(review_page.reviews, start=start_number):
        text = (review.text or "").strip() or t("public_review_without_text", language)
        lines.append(
            t("public_review_item", language).format(
                number=index,
                rating=review.rating,
                text=text,
            )
        )

        if review.specialist_reply:
            lines.append(
                t("public_review_specialist_reply", language).format(
                    reply=review.specialist_reply,
                )
            )

        lines.append("")

    return "\n".join(lines).strip()


def public_reviews_keyboard(
    *,
    language: str,
    page: int,
    has_previous: bool,
    has_next: bool,
    reviews_count: int,
    results_page: int,
) -> InlineKeyboardMarkup:
    rows = []

    nav = []
    if has_previous:
        nav.append(
            InlineKeyboardButton(
                text=t("prev_btn", language),
                callback_data=f"search_reviews_page:{page - 1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text=t("next_btn", language),
                callback_data=f"search_reviews_page:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    for index in range(reviews_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("public_review_report_btn", language).format(
                        number=index + 1,
                    ),
                    callback_data=f"search_review_report:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_back", language),
                callback_data=f"search_result_back_to_card:{results_page}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="search_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def render_public_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int = 0,
) -> None:
    data = await state.get_data()
    language = await get_search_language(state, callback)

    specialist_id = data.get("selected_specialist_id")
    if not specialist_id:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    requester_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not requester_user_id or not tenant_id:
        await store_post_auth_action(
            callback=callback,
            state=state,
            action="portfolio",
            language=language,
        )
        return

    await callback.answer()

    try:
        async with get_session() as session:
            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_active_items_for_viewer(
                tenant_id=tenant_id,
                specialist_id=UUID(specialist_id),
                viewer_user_id=requester_user_id,
                page=page,
            )

    except PortfolioServiceError as exc:
        logger.warning(
            "public_portfolio_load_failed "
            "specialist_id=%s error=%s",
            specialist_id,
            exc,
        )
        menu_message = await show_callback_message(
            callback,
            t(
                "public_portfolio_load_error",
                language,
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_back",
                                language,
                            ),
                            callback_data=(
                                "search_result_back_to_card:"
                                f"{int(data.get('results_page') or 0)}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_menu",
                                language,
                            ),
                            callback_data="search_menu",
                        )
                    ],
                ]
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    if not items:
        await state.update_data(
            public_portfolio_page=0,
            public_portfolio_item_ids=[],
        )
        await show_callback_message(
            callback,
            t("public_portfolio_empty", language),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("search_back", language),
                            callback_data=f"search_result_back_to_card:{int(data.get('results_page') or 0)}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t("search_menu", language),
                            callback_data="search_menu",
                        )
                    ],
                ]
            ),
        )
        return

    normalized_page = max(0, min(int(page), len(items) - 1))
    view = items[normalized_page]

    await state.update_data(
        public_portfolio_page=normalized_page,
        public_portfolio_item_ids=[str(item.item.id) for item in items],
        pending_report_target_type="portfolio_item",
        pending_report_target_id=str(view.item.id),
    )

    keyboard = public_portfolio_keyboard(
        signed_url=view.signed_url,
        language=language,
        page=normalized_page,
        has_previous=normalized_page > 0,
        has_next=normalized_page + 1 < len(items),
        results_page=int(data.get("results_page") or 0),
    )

    caption = public_portfolio_caption(view, language)

    if view.storage_object.file_type == "photo":
        await delete_telegram_messages(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            message_ids=[
                callback.message.message_id
            ],
        )

        try:
            menu_message = await callback.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=view.signed_url,
                caption=caption,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning(
                "public_portfolio_photo_send_failed "
                "item_id=%s error=%s",
                view.item.id,
                exc,
            )
            menu_message = await show_callback_message(
                callback,
                caption,
                keyboard,
            )
    else:
        menu_message = await show_callback_message(
            callback,
            caption,
            keyboard,
        )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

async def store_complaint_target_summary(
    state: FSMContext,
    language: str,
) -> None:
    data = await state.get_data()
    specialist_id = data.get("selected_specialist_id")
    target_type = data.get("pending_report_target_type") or "specialist"

    target_labels = {
        "specialist": t("complaint_target_specialist", language),
        "review": t("complaint_target_review", language),
        "portfolio_item": t("complaint_target_portfolio", language),
        "thread": t("complaint_target_dialog", language),
        "message": t("complaint_target_message", language),
    }
    target_label = target_labels.get(target_type, target_type)

    specialist_name = None

    if specialist_id:
        async with get_session() as session:
            card = await GeoSearchService(
                SpecialistSearchRepository(session)
            ).get_public_card(
                specialist_id=UUID(specialist_id),
                log_event=False,
                language=language,
            )

        if card:
            specialist_name = card.display_name

    target_summary = (
        f"{target_label}: {specialist_name}"
        if specialist_name
        else target_label
    )

    await state.update_data(
        pending_report_target_summary=target_summary,
    )

def complaint_reason_label(reason: str, language: str) -> str:
    labels = {
        "fake": t("complaint_reason_fake", language),
        "contact": t("complaint_reason_contact", language),
        "abuse": t("complaint_reason_abuse", language),
        "other": t("complaint_reason_other", language),
    }
    return labels.get(reason, reason)


def complaint_draft_text(data: dict, language: str) -> str:
    target_type = data.get("pending_report_target_type") or "specialist"
    target_labels = {
        "specialist": t("complaint_target_specialist", language),
        "review": t("complaint_target_review", language),
        "portfolio_item": t("complaint_target_portfolio", language),
        "thread": t("complaint_target_dialog", language),
        "message": t("complaint_target_message", language),
    }

    reason = data.get("pending_report_reason") or "-"
    comment = data.get("pending_report_comment") or t(
        "complaint_comment_not_set",
        language,
    )

    target_summary = (
        data.get("pending_report_target_summary")
        or target_labels.get(target_type, target_type)
    )

    return t("complaint_draft", language).format(
        target=target_summary,
        reason=complaint_reason_label(reason, language),
        comment=comment,
    )


def complaint_draft_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("complaint_add_comment_btn", language),
                    callback_data="search_report_comment",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_send_btn", language),
                    callback_data="search_report_send",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_cancel_btn", language),
                    callback_data="search_report_cancel",
                )
            ],
        ]
    )

def complaint_comment_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "complaint_cancel_btn",
                        language,
                    ),
                    callback_data="search_report_cancel",
                )
            ],
        ]
    )

def complaint_reason_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_fake", language),
                    callback_data="search_report_reason:fake",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_contact", language),
                    callback_data="search_report_reason:contact",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_abuse", language),
                    callback_data="search_report_reason:abuse",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_other", language),
                    callback_data="search_report_reason:other",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back", language),
                    callback_data="search_report_cancel",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )


def contact_thread_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_attach_btn",
                        language,
                    ),
                    callback_data="CONTACT_ATTACH_FILE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_finish_btn",
                        language,
                    ),
                    callback_data="SPEC_THREAD_COMPLETE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_report_btn",
                        language,
                    ),
                    callback_data="search_report_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_back_btn",
                        language,
                    ),
                    callback_data="CLIENT_DIALOGS",
                )
            ],
        ]
    )


def contact_thread_keyboard_for_role(
    language: str,
    role: str | None,
) -> InlineKeyboardMarkup:
    if role == "specialist":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "contact_chat_attach_btn",
                            language,
                        ),
                        callback_data="CONTACT_ATTACH_FILE",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "contact_chat_finish_btn",
                            language,
                        ),
                        callback_data="SPEC_THREAD_COMPLETE",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "contact_chat_report_btn",
                            language,
                        ),
                        callback_data="SPEC_THREAD_REPORT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "contact_chat_back_btn",
                            language,
                        ),
                        callback_data="SPEC_DIALOGS",
                    )
                ],
            ]
        )

    return contact_thread_keyboard(language)

def contact_completed_keyboard(contact_request_id: str, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("review_leave_btn", language),
                    callback_data=f"review_start:{contact_request_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back_to_filters_btn", language),
                    callback_data="search_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )


def review_rating_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="review_rating:1"),
                InlineKeyboardButton(text="2", callback_data="review_rating:2"),
                InlineKeyboardButton(text="3", callback_data="review_rating:3"),
                InlineKeyboardButton(text="4", callback_data="review_rating:4"),
                InlineKeyboardButton(text="5", callback_data="review_rating:5"),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )


def review_skip_text_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("review_skip_text_btn", language),
                    callback_data="review_text_skip",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )

def review_completed_keyboard(
    *,
    language: str,
    role: str,
) -> InlineKeyboardMarkup:
    back_callback = (
        "CLIENT_DIALOGS"
        if role == "client"
        else "SPEC_DIALOGS"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("contact_chat_back_btn", language),
                    callback_data=back_callback,
                )
            ]
        ]
    )

def format_chat_message_body(
    item,
    language: str,
) -> str:
    text = str(item.text or "").strip()
    attachment = (
        item.attachment
        if isinstance(item.attachment, dict)
        else None
    )

    if not attachment:
        return text

    attachment_type = attachment.get("type")
    file_name = str(
        attachment.get("file_name") or ""
    ).strip()

    if file_name:
        attachment_label = file_name
    elif attachment_type == "photo":
        attachment_label = t(
            "contact_attachment_photo_label",
            language,
        )
    else:
        attachment_label = t(
            "contact_attachment_file_label",
            language,
        )

    fallback_texts = {
        "📎 Attachment",
    }
    if file_name:
        fallback_texts.add(file_name)

    lines: list[str] = []

    if text and text not in fallback_texts:
        lines.append(text)

    lines.append(f"📎 {attachment_label}")

    return "\n".join(lines)

def contact_chat_status_text(
    status: str | None,
    *,
    viewer_role: str,
    language: str,
) -> str:
    if status in {"completed", "closed"}:
        return t(
            "messages_card_status_completed",
            language,
        )

    waiting_for_viewer = (
        "waiting_client"
        if viewer_role == "client"
        else "waiting_specialist"
    )

    if status == waiting_for_viewer:
        return t(
            "messages_card_status_waiting_you",
            language,
        )

    if status in {
        "waiting_client",
        "waiting_specialist",
    }:
        return t(
            "messages_card_status_waiting_other",
            language,
        )

    return t(
        "messages_card_status_in_progress",
        language,
    )


def format_contact_chat_text(
    detail,
    *,
    viewer_role: str,
    language: str,
) -> str:
    counterpart_name = (
        detail.specialist_name
        if viewer_role == "client"
        else detail.client_name
    )
    history_lines = []

    for item in detail.messages:
        if item.is_system:
            history_lines.append(
                format_chat_message_body(
                    item,
                    language,
                )
            )
            continue

        sender_name = (
            t("contact_chat_you_label", language)
            if item.is_sent_by_viewer
            else counterpart_name
        )
        sent_at = item.created_at.strftime(
            "%d.%m %H:%M"
        )

        history_lines.append(
            f"{sender_name} · {sent_at}\n"
            f"{format_chat_message_body(item, language)}"
        )

    lines = [f"💬 {counterpart_name}"]

    if detail.profession_name:
        lines.append(
            f"💼 {detail.profession_name}"
        )

    lines.append(
        contact_chat_status_text(
            detail.thread_status,
            viewer_role=viewer_role,
            language=language,
        )
    )

    if history_lines:
        lines.extend(
            [
                "",
                "\n\n".join(history_lines),
            ]
        )

    return "\n".join(lines)


def format_client_contact_chat_text(
    detail,
    language: str,
) -> str:
    return format_contact_chat_text(
        detail,
        viewer_role="client",
        language=language,
    )


def format_specialist_contact_chat_text(
    detail,
    language: str,
) -> str:
    return format_contact_chat_text(
        detail,
        viewer_role="specialist",
        language=language,
    )


def client_contact_chat_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_attach_btn",
                        language,
                    ),
                    callback_data="CONTACT_ATTACH_FILE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_chat_finish_btn", language),
                    callback_data="SPEC_THREAD_COMPLETE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_chat_report_btn", language),
                    callback_data="search_report_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_chat_back_btn", language),
                    callback_data="CLIENT_DIALOGS",
                )
            ],
        ]
    )

@search_router.callback_query(
    F.data == "CONTACT_ATTACH_FILE"
)
async def prompt_contact_attachment(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(
        state,
        callback,
    )
    data = await state.get_data()

    if not data.get("active_thread_id"):
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        SpecialistSearchFSM.entering_thread_message
    )

    menu_message = await show_callback_message(
        callback,
        t(
            "contact_chat_attach_prompt",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

    await callback.answer()

async def show_contact_chat_screen(
    *,
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    data = await state.get_data()

    current_menu_message_id = data.get(
        "last_menu_message_id"
    )

    messages_to_delete = [
        *(
            data.get(
                "last_contact_chat_message_ids"
            )
            or []
        ),
        (
            message.message_id
            if (
                message.from_user
                and not message.from_user.is_bot
            )
            else None
        ),
    ]

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in messages_to_delete
            if (
                message_id
                and str(message_id)
                != str(current_menu_message_id)
            )
        ],
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=current_menu_message_id,
            text=text,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_contact_chat_message_ids=[
            menu_message_id
        ],
        last_menu_message_id=menu_message_id,
    )



async def show_contact_chat(
    *,
    message: Message,
    state: FSMContext,
    thread_id: str,
    user_id: UUID,
    viewer_role: str,
    language: str,
    include_attachments: bool = True,
    notice: str | None = None,
) -> None:
    normalized_role = (
        "specialist"
        if viewer_role == "specialist"
        else "client"
    )

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_thread_detail(
                thread_id=UUID(thread_id),
                user_id=user_id,
                language=language,
            )
    except ContactChatError:
        logger.exception(
            "contact_chat_open_failed thread_id=%s",
            thread_id,
        )

        await show_contact_chat_screen(
            message=message,
            state=state,
            text=t(
                "contact_chat_error",
                language,
            ),
            reply_markup=(
                contact_thread_keyboard_for_role(
                    language,
                    normalized_role,
                )
            ),
        )
        return

    if not detail:
        await show_contact_chat_screen(
            message=message,
            state=state,
            text=t(
                "contact_chat_error",
                language,
            ),
            reply_markup=(
                contact_thread_keyboard_for_role(
                    language,
                    normalized_role,
                )
            ),
        )
        return

    state_data = await state.get_data()

    messages_to_delete = [
        *(
            state_data.get(
                "last_contact_chat_message_ids"
            )
            or []
        ),
        *(
            state_data.get(
                "last_search_result_message_ids"
            )
            or []
        ),
        state_data.get("last_menu_message_id"),
        message.message_id,
    ]

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in messages_to_delete
            if message_id
        ],
    )

    await state.update_data(
        last_contact_chat_message_ids=[],
        last_search_result_message_ids=[],
        last_menu_message_id=None,
    )

    rendered_message_ids: list[int] = []

    counterpart_name = (
        detail.client_name
        if normalized_role == "specialist"
        else detail.specialist_name
    )
    keyboard = contact_thread_keyboard_for_role(
        language,
        normalized_role,
    )
    attachment_items = (
        [
            item
            for item in detail.messages
            if item.attachment
        ]
        if include_attachments
        else []
    )
    chat_text = format_contact_chat_text(
        detail,
        viewer_role=normalized_role,
        language=language,
    )

    if notice:
        chat_text = (
            f"{chat_text}\n\n"
            f"{notice}"
        )

    chat_chunks = split_telegram_text(
        chat_text
    )

    for index, chunk in enumerate(chat_chunks):
        is_last_chunk = (
            index == len(chat_chunks) - 1
        )

        chat_message = await message.answer(
            chunk,
            reply_markup=(
                keyboard
                if (
                    is_last_chunk
                    and not attachment_items
                )
                else None
            ),
        )

        rendered_message_ids.append(
            chat_message.message_id
        )

    for index, item in enumerate(
        attachment_items
    ):
        is_last_attachment = (
            index == len(attachment_items) - 1
        )
        sender_name = (
            t("contact_chat_you_label", language)
            if item.is_sent_by_viewer
            else counterpart_name
        )
        sent_at = item.created_at.strftime(
            "%d.%m %H:%M"
        )
        attachment_caption = (
            f"{sender_name} · {sent_at}\n"
            f"{format_chat_message_body(item, language)}"
        )

        attachment_message = (
            await send_telegram_attachment(
                bot=message.bot,
                chat_id=message.chat.id,
                attachment=item.attachment,
                caption=attachment_caption,
                reply_markup=(
                    keyboard
                    if is_last_attachment
                    else None
                ),
            )
        )

        if attachment_message:
            rendered_message_ids.append(
                attachment_message.message_id
            )

    await state.update_data(
        last_contact_chat_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=(
            rendered_message_ids[-1]
            if rendered_message_ids
            else None
        ),
    )


async def show_client_contact_chat(
    *,
    message: Message,
    state: FSMContext,
    thread_id: str,
    user_id: UUID,
    language: str,
) -> None:
    await show_contact_chat(
        message=message,
        state=state,
        thread_id=thread_id,
        user_id=user_id,
        viewer_role="client",
        language=language,
    )

async def translate_message_for_notification(
    *,
    session,
    message_id: UUID,
    receiver_user_id: UUID,
) -> tuple[str, bool, str]:
    result = await TranslationService(
        TranslationRepository(session)
    ).translate_notification_message(
        message_id=message_id,
        receiver_user_id=receiver_user_id,
    )

    return (
        result.display_text,
        result.used_translation,
        result.translation_status,
    )

def format_search_filters_summary(data: dict, language: str) -> str:
    category = (
        data.get("category_name")
        or t("search_filter_category_not_selected", language)
    )
    professions = (
        data.get("profession_name")
        or ", ".join(data.get("selected_profession_names") or [])
        or t("search_filter_professions_not_selected", language)
    )

    if data.get("location_state") == "without":
        location = t("search_location_without", language)
    else:
        location = (
            data.get("city_name")
            or t("search_location_without", language)
        )

    if data.get("country_wide"):
        radius = t("search_radius_country", language)
    else:
        radius = f"{data.get('radius_km') or DEFAULT_RADIUS_KM} km"

    return "\n".join(
        [
            t("search_filters_title", language),
            "",
            f"{t('search_filter_category_label', language)}: {category}",
            f"{t('search_filter_profession_label', language)}: {professions}",
            f"{t('search_filter_location_label', language)}: {location}",
            f"{t('search_filter_radius_label', language)}: {radius}",
            (
                f"{t('search_filter_sort_label', language)}: "
                f"{sort_label(data.get('sort_by'), language)}"
            ),
        ]
    )


def search_start_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_choose_category_btn", language),
                    callback_data="search_filter_category",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_history_btn", language),
                    callback_data="SEARCH_HISTORY",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_favorites_btn", language),
                    callback_data="CAB_FAVORITES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back", language),
                    callback_data="GLOBAL_MAIN_MENU",
                ),
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                ),
            ],
        ]
    )
def search_filters_keyboard(data: dict, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_filter_category", language),
                    callback_data="search_filter_category",
                ),
                InlineKeyboardButton(
                    text=t("search_filter_profession", language),
                    callback_data="search_filter_profession",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_location", language),
                    callback_data="search_filter_location",
                ),
                InlineKeyboardButton(
                    text=t("search_filter_radius", language),
                    callback_data="search_filter_radius",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_sort", language),
                    callback_data="search_filter_sort",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_show_specialists_btn",
                        language,
                    ),
                    callback_data="search_professions_apply",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_reset_filters", language),
                    callback_data="search_reset_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )

def search_advanced_filters_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_filter_radius", language),
                    callback_data="search_filter_radius",
                ),
                InlineKeyboardButton(
                    text=t("search_filter_work_format", language),
                    callback_data="search_filter_work_format",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_language", language),
                    callback_data="search_filter_language",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_availability", language),
                    callback_data="search_filter_availability",
                ),
                InlineKeyboardButton(
                    text=t("search_filter_verified_label", language),
                    callback_data="search_filter_verified",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_rating_label", language),
                    callback_data="search_filter_rating",
                ),
                InlineKeyboardButton(
                    text=t("search_filter_sort", language),
                    callback_data="search_filter_sort",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_reset_filters", language),
                    callback_data="search_reset_filters",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back_to_filters_btn", language),
                    callback_data="search_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )

def search_location_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_location_geo", language), callback_data="search_location_geo")],
            [InlineKeyboardButton(text=t("search_location_city", language), callback_data="search_location_city")],
            [InlineKeyboardButton(text=t("search_location_without", language), callback_data="search_location_without")],
            [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def search_radius_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="5 km", callback_data="search_radius:5"),
                InlineKeyboardButton(text="10 km", callback_data="search_radius:10"),
                InlineKeyboardButton(text="25 km", callback_data="search_radius:25"),
            ],
            [
                InlineKeyboardButton(text="50 km", callback_data="search_radius:50"),
                InlineKeyboardButton(text="100 km", callback_data="search_radius:100"),
                InlineKeyboardButton(text=t("search_radius_country", language), callback_data="search_radius:country"),
            ],
            [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_work_format_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_filter_any", language), callback_data="search_work:any")],
            [InlineKeyboardButton(text=t("search_work_at_client", language), callback_data="search_work:at_client")],
            [InlineKeyboardButton(text=t("search_work_at_specialist", language), callback_data="search_work:at_specialist")],
            [InlineKeyboardButton(text=t("search_work_remote", language), callback_data="search_work:remote")],
            [InlineKeyboardButton(text=t("search_work_mixed", language), callback_data="search_work:mixed")],
            [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_language_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("search_filter_any", language), callback_data="search_lang:any"),
                InlineKeyboardButton(text=t("search_language_ru", language), callback_data="search_lang:ru"),
            ],
            [
                InlineKeyboardButton(text=t("search_language_pt", language), callback_data="search_lang:pt"),
                InlineKeyboardButton(text=t("search_language_en", language), callback_data="search_lang:en"),
            ],
            [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_availability_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_filter_any", language),
                    callback_data="search_availability:any",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_available_now", language),
                    callback_data="search_availability:now",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back_to_filters_btn", language),
                    callback_data="search_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )


def search_verified_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_filter_verified_all", language),
                    callback_data="search_verified:any",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_verified_only", language),
                    callback_data="search_verified:only",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back_to_filters_btn", language),
                    callback_data="search_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )


def search_rating_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_filter_rating_any", language),
                    callback_data="search_rating:any",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_filter_rating_4", language),
                    callback_data="search_rating:4",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back_to_filters_btn", language),
                    callback_data="search_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )

def search_sort_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_sort_distance", language), callback_data="search_sort:distance")],
            [InlineKeyboardButton(text=t("search_sort_relevance", language), callback_data="search_sort:relevance")],
            [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def search_history_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_write_query_btn", language),
                    callback_data="SEARCH_AI",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_choose_category_btn", language),
                    callback_data="search_filter_category",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back", language),
                    callback_data="search_start",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )


def format_search_history_item(payload: dict, language: str) -> str:
    parts = []

    for key in (
        "search_text_query",
        "category_name",
        "profession_name",
        "city_name",
    ):
        value = payload.get(key)
        if value:
            parts.append(str(value))

    if payload.get("location_state") == "without":
        parts.append(t("search_location_without", language))

    return " • ".join(parts) or t("search_history_generic_item", language)

def search_geo_candidates_keyboard(candidates: list[dict], language: str) -> InlineKeyboardMarkup:
    rows = []
    for index, candidate in enumerate(candidates[:8]):
        rows.append(
            [
                InlineKeyboardButton(
                    text=geo_candidate_label(candidate, index),
                    callback_data=f"search_geo_place:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_geo_other", language),
                callback_data="search_geo_other",
            ),
            InlineKeyboardButton(
                text=t("search_geo_retry", language),
                callback_data="search_geo_retry",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")])
    rows.append([InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def search_geo_empty_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_geo_retry", language),
                    callback_data="search_geo_retry",
                ),
                InlineKeyboardButton(
                    text=t("search_location_without", language),
                    callback_data="search_location_without",
                ),
            ],
            [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def next_empty_radius_suggestion(data: dict) -> tuple[int | None, bool]:
    if data.get("country_wide"):
        return None, True

    radius = int(data.get("radius_km") or DEFAULT_RADIUS_KM)

    if radius < 25:
        return 25, False

    if radius < 50:
        return 50, False

    if radius < 100:
        return 100, False

    return None, True


def empty_results_keyboard(data: dict, language: str) -> InlineKeyboardMarkup:
    rows = []

    if data.get("search_text_query"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("search_write_query_btn", language),
                    callback_data="SEARCH_AI",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_choose_category_btn", language),
                callback_data="search_filter_category",
            )
        ]
    )

    has_location_filter = (
        data.get("location_state") != "without"
        and (
            data.get("city_id")
            or data.get("latitude") is not None
            or data.get("country_wide")
        )
    )

    if has_location_filter:
        next_radius, next_country_wide = next_empty_radius_suggestion(data)

        if next_radius is not None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("search_empty_increase_radius_to", language).format(
                            radius=next_radius,
                        ),
                        callback_data="search_empty_increase_radius",
                    )
                ]
            )
        elif next_country_wide and not data.get("country_wide"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t("search_empty_increase_radius_country", language),
                        callback_data="search_empty_increase_radius",
                    )
                ]
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=t("search_location_without", language),
                    callback_data="search_location_without",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("search_back_to_filters_btn", language),
                    callback_data="search_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_empty_reset_all", language),
                    callback_data="search_reset_filters",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_empty_results_text(data: dict, language: str) -> str:
    return (
        f"{t('search_empty_summary', language)}\n\n"
        f"{format_search_filters_summary(data, language)}"
    )

def format_results_header(
    *,
    data: dict,
    language: str,
    page: int,
    visible_count: int,
    total_count: int,
) -> str:
    start = page * PER_PAGE + 1 if visible_count else 0
    end = page * PER_PAGE + visible_count
    shown_range = f"{start}–{end}"

    return t("search_results_header", language).format(
        found=total_count,
        range=shown_range,
        context="",
    )

def search_result_badge(specialist) -> str:
    metadata = specialist.extra_metadata or {}

    if metadata.get("boost_enabled") or metadata.get("is_boosted"):
        return "🔥"

    if specialist.is_premium:
        return "⭐"

    if metadata.get("partner") or metadata.get("is_partner"):
        return "🤝"

    return ""


def compact_rating(specialist, language: str) -> str:
    reviews_count = specialist.reviews_count or 0

    if reviews_count > 0 and specialist.rating is not None:
        return f"⭐{float(specialist.rating):.1f}"

    return f"⭐{t('search_no_reviews', language)}"


def compact_distance(result, language: str) -> str:
    specialist = result.specialist

    if getattr(specialist, "work_format", None) == "remote":
        return f"📍{work_format_label('remote', language)}"

    if result.distance_km is not None:
        return f"📍{result.distance_km:.0f} км"

    return f"📍{result.city_name or t('search_filter_not_set', language)}"

def public_safe_description(text: str | None, limit: int = 300) -> str:
    if not text:
        return ""

    clean = " ".join(str(text).split())
    lowered = clean.lower()
    test_description_markers = (
        "beta testing",
        "seed",
        "test automation",
        "тестовый профиль",
        "ручной проверки",
    )
    if any(marker in lowered for marker in test_description_markers):
        return ""
    if (
        "http://" in lowered
        or "https://" in lowered
        or "www." in lowered
        or "@" in clean
    ):
        return ""

    digits_count = sum(1 for char in clean if char.isdigit())
    if digits_count >= 7:
        return ""

    return clean[:limit].rstrip()

def format_specialist_result(result, index: int, language: str) -> str:
    specialist = result.specialist
    profession = result.profession_name

    if specialist.reviews_count and specialist.rating is not None:
        rating = f"⭐ {float(specialist.rating):.1f}"
    else:
        rating = f"⭐ {t('search_no_reviews', language)}"

    is_remote = getattr(specialist, "work_format", None) == "remote"
    location_parts = []

    if is_remote:
        location_parts.append(work_format_label("remote", language))
    else:
        if result.city_name:
            location_parts.append(result.city_name)
        if result.distance_km is not None:
            location_parts.append(f"{result.distance_km:.0f} км")

    availability = (
        t("search_filter_available_now", language)
        if getattr(specialist, "is_available", False)
        else t("search_unavailable_now", language)
    )

    languages = [
        language_filter_label(language_code, language)
        for language_code in (result.languages or [])
    ]
    description = public_safe_description(specialist.short_description)

    lines = [
        f"👤 {specialist.display_name}",
        rating,
    ]

    if location_parts:
        lines.append(f"📍 {' • '.join(location_parts)}")

    lines.append(f"🟢 {availability}")

    if profession:
        lines.append(f"💼 {profession}")

    if languages:
        lines.append(f"🌍 {', '.join(languages)}")

    if description:
        lines.extend(["", description])

    return "\n".join(lines)

def format_public_card(card: SpecialistPublicCard, language: str) -> str:
    labels = []
    if card.is_verified:
        labels.append(f"✅ {t('search_verified_label', language)}")
    if card.is_available:
        labels.append(t("search_filter_available_now", language))
    if card.is_premium:
        labels.append(t("search_premium_label", language))

    label_text = f" ({', '.join(labels)})" if labels else ""
    is_remote = card.work_format == "remote"

    lines = [
        t("search_profile_photo_placeholder", language),
        f"{card.display_name}{label_text}",
        "",
    ]

    if card.category_name:
        lines.append(f"{t('search_filter_category_label', language)}: {card.category_name}")

    if card.profession_name:
        lines.append(f"{t('search_filter_profession_label', language)}: {card.profession_name}")

    if is_remote:
        lines.append(
            f"{t('search_filter_location_label', language)}: "
            f"{work_format_label('remote', language)}"
        )
    elif card.city_name:
        location = card.city_name
        if card.distance_km is not None:
            location = f"{location}\n{t('search_distance', language)}: {card.distance_km:.1f} km"
        lines.append(f"{t('search_filter_location_label', language)}: {location}")

    work_format = work_format_label(card.work_format, language)
    if work_format:
        lines.append(f"{t('search_filter_work_label', language)}: {work_format}")

    if card.experience_years is not None:
        lines.append(
            t("search_experience_years", language).format(
                years=card.experience_years
            )
        )

    if card.service_titles:
        lines.append(
            f"{t('search_services_label', language)}: "
            f"{', '.join(card.service_titles)}"
        )
    if card.skill_names:
        lines.append(
            f"{t('search_skills_label', language)}: "
            f"{', '.join(card.skill_names)}"
        )

    if card.languages:
        lines.append(
            f"{t('search_filter_language_label', language)}: "
            f"{', '.join(card.languages)}"
        )

    if card.reviews_count > 0:
        rating = f"{float(card.rating):.1f} ({card.reviews_count})"
    else:
        rating = t("search_no_reviews", language)
    lines.append(f"{t('search_rating', language)}: {rating}")

    description = public_safe_description(card.short_description)

    if description:
        lines.extend(["", description])

    return "\n".join(lines)

async def show_filters(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        search_category_source="filters",
    )
    data = await state.get_data()
    language = await get_search_language(state, callback)

    await show_callback_message(
        callback,
        format_search_filters_summary(data, language),
        search_filters_keyboard(data, language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_filters)
    await callback.answer()


async def render_results(
    *,
    event: CallbackQuery | Message,
    state: FSMContext,
    page: int,
):
    data = await state.get_data()
    language = await get_search_language(state, event)

    if isinstance(event, CallbackQuery):
        await event.answer()

        processing_message = await event.message.answer(
            t(
                "search_searching_specialists",
                language,
            )
        )
    else:
        processing_message = await event.answer(
            t("search_searching_specialists", language)
        )
    source_message = (
        event.message
        if isinstance(event, CallbackQuery)
        else event
    )

    messages_to_delete = [
        *(
            data.get(
                "last_search_result_message_ids"
            )
            or []
        ),
        data.get("last_menu_message_id"),
        source_message.message_id,
    ]

    await delete_telegram_messages(
        bot=source_message.bot,
        chat_id=source_message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in messages_to_delete
            if message_id
        ],
    )

    await state.update_data(
        last_search_result_message_ids=[],
        last_menu_message_id=None,
    )

    category_id = UUID(data["category_id"]) if data.get("category_id") else None
    profession_id = UUID(data["profession_id"]) if data.get("profession_id") else None
    selected_profession_ids = [
        UUID(item)
        for item in (data.get("selected_profession_ids") or [])
    ]
    city_id = UUID(data["city_id"]) if data.get("city_id") else None
    country_id = UUID(data["country_id"]) if data.get("country_id") else None
    has_geo = data.get("latitude") is not None and data.get("longitude") is not None
    without_location = (
        data.get("location_state") == "without"
        or data.get("work_format") == "remote"
    )

    if not city_id and not has_geo and not without_location:
        await state.update_data(location_state="without")
        data["location_state"] = "without"
        without_location = True
    country_wide = bool(data.get("country_wide"))
    language_code = data.get("language_code")
    verified_only = bool(data.get("verified_only"))
    available_only = bool(data.get("available_only"))
    premium_only = bool(data.get("premium_only"))
    work_format = data.get("work_format")
    remote_only = work_format == "remote"
    rating_min = data.get("rating_min")
    sort_by = data.get("sort_by") or "distance"
    requester_user_id = None
    tenant_id = None
    platform_user_id = event.from_user.id if event.from_user else None
    if platform_user_id is not None:
        requester_user_id, tenant_id = await get_requester_context(platform_user_id)

    async with get_session() as session:
        service = GeoSearchService(SpecialistSearchRepository(session))

        if remote_only:
            results = await service.search_without_location(
                category_id=category_id,
                profession_id=profession_id,
                profession_ids=selected_profession_ids,
                interface_language=language,
                language_code=language_code,
                verified_only=verified_only,
                premium_only=premium_only,
                available_only=available_only,
                work_format=work_format,
                rating_min=rating_min,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
                requester_user_id=requester_user_id,
                tenant_id=tenant_id,
                log_event=True,
                sort_by=sort_by,
            )

        elif has_geo:
            results = await service.search_by_radius(
                latitude=float(data["latitude"]),
                longitude=float(data["longitude"]),
                radius_km=float(data.get("radius_km") or DEFAULT_RADIUS_KM),
                category_id=category_id,
                country_id=country_id,
                country_wide=country_wide,
                interface_language=language,
                profession_id=profession_id,
                profession_ids=selected_profession_ids,
                language_code=language_code,
                verified_only=verified_only,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
                requester_user_id=requester_user_id,
                tenant_id=tenant_id,
                log_event=True,
                premium_only=premium_only,
                work_format=work_format,
                rating_min=rating_min,
                sort_by=sort_by,
            )
        elif city_id:
            results = await service.search_by_city(
                city_id=city_id,
                category_id=category_id,
                profession_id=profession_id,
                profession_ids=selected_profession_ids,
                country_id=country_id,
                interface_language=language,
                language_code=language_code,
                verified_only=verified_only,
                premium_only=premium_only,
                available_only=available_only,
                work_format=work_format,
                rating_min=rating_min,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
                requester_user_id=requester_user_id,
                tenant_id=tenant_id,
                log_event=True,
                sort_by=sort_by,
            )
        elif without_location:
            results = await service.search_without_location(
                category_id=category_id,
                profession_id=profession_id,
                profession_ids=selected_profession_ids,
                interface_language=language,
                language_code=language_code,
                verified_only=verified_only,
                premium_only=premium_only,
                available_only=available_only,
                work_format=work_format,
                rating_min=rating_min,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
                requester_user_id=requester_user_id,
                tenant_id=tenant_id,
                log_event=True,
                sort_by=sort_by,
            )
        else:
            results = []

    total_results = results

    if len(results) >= PER_PAGE + 1 or page > 0:
        async with get_session() as session:
            total_service = GeoSearchService(SpecialistSearchRepository(session))

            if remote_only:
                total_results = await total_service.search_without_location(
                    category_id=category_id,
                    profession_id=profession_id,
                    profession_ids=selected_profession_ids,
                    interface_language=language,
                    language_code=language_code,
                    verified_only=verified_only,
                    premium_only=premium_only,
                    work_format=work_format,
                    rating_min=rating_min,
                    limit=200,
                    offset=0,
                    requester_user_id=requester_user_id,
                    tenant_id=tenant_id,
                    log_event=False,
                    sort_by=sort_by,
                )
            elif has_geo:
                total_results = await total_service.search_by_radius(
                    latitude=float(data["latitude"]),
                    longitude=float(data["longitude"]),
                    radius_km=float(data.get("radius_km") or DEFAULT_RADIUS_KM),
                    category_id=category_id,
                    country_id=country_id,
                    country_wide=country_wide,
                    interface_language=language,
                    profession_id=profession_id,
                    profession_ids=selected_profession_ids,
                    language_code=language_code,
                    verified_only=verified_only,
                    limit=200,
                    offset=0,
                    requester_user_id=requester_user_id,
                    tenant_id=tenant_id,
                    log_event=False,
                    premium_only=premium_only,
                    work_format=work_format,
                    rating_min=rating_min,
                    sort_by=sort_by,
                )
            elif city_id:
                total_results = await total_service.search_by_city(
                    city_id=city_id,
                    category_id=category_id,
                    profession_id=profession_id,
                    profession_ids=selected_profession_ids,
                    country_id=country_id,
                    interface_language=language,
                    language_code=language_code,
                    verified_only=verified_only,
                    premium_only=premium_only,
                    work_format=work_format,
                    rating_min=rating_min,
                    limit=200,
                    offset=0,
                    requester_user_id=requester_user_id,
                    tenant_id=tenant_id,
                    log_event=False,
                    sort_by=sort_by,
                )
            elif without_location:
                total_results = await total_service.search_without_location(
                    category_id=category_id,
                    profession_id=profession_id,
                    profession_ids=selected_profession_ids,
                    interface_language=language,
                    language_code=language_code,
                    verified_only=verified_only,
                    premium_only=premium_only,
                    work_format=work_format,
                    rating_min=rating_min,
                    limit=200,
                    offset=0,
                    requester_user_id=requester_user_id,
                    tenant_id=tenant_id,
                    log_event=False,
                    sort_by=sort_by,
                )

    total_count = len(total_results)

    logger.info(
        "search_results_rendered telegram_id=%s results=%s page=%s has_geo=%s city_id=%s category_id=%s profession_id=%s sort_by=%s",
        platform_user_id,
        len(results),
        page,
        has_geo,
        city_id,
        category_id,
        profession_id,
        sort_by,
    )

    has_next = (page + 1) * PER_PAGE < total_count
    visible_results = results[:PER_PAGE]

    saved_specialist_ids: set[UUID] = set()

    if (
        requester_user_id
        and tenant_id
        and visible_results
    ):
        async with get_session() as session:
            saved_specialist_ids = await FavoriteService(
                FavoriteRepository(session)
            ).list_saved_specialist_ids(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                specialist_ids=[
                    result.specialist.id
                    for result in visible_results
                ],
            )

    if requester_user_id and tenant_id:
        async with get_session() as session:
            await GeoSearchService(
                SpecialistSearchRepository(session)
            ).record_results_viewed(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                event=SearchResultsViewedEvent(
                    platform_user_id=(
                        str(platform_user_id)
                        if platform_user_id is not None
                        else None
                    ),
                    page=page,
                    visible_count=len(visible_results),
                    has_next=has_next,
                    category_id=data.get("category_id"),
                    profession_id=data.get(
                        "profession_id"
                    ),
                    city_id=data.get("city_id"),
                    location_state=data.get(
                        "location_state"
                    ),
                    radius_km=data.get("radius_km"),
                    country_wide=bool(
                        data.get("country_wide")
                    ),
                    sort_by=data.get("sort_by"),
                    category_name=data.get(
                        "category_name"
                    ),
                    profession_name=data.get(
                        "profession_name"
                    ),
                    city_name=data.get("city_name"),
                    search_text_query=data.get(
                        "search_text_query"
                    ),
                ),
            )

    await state.update_data(
        results_page=page,
        result_specialist_ids=[str(item.specialist.id) for item in visible_results],
        result_distances=[item.distance_km for item in visible_results],
    )

    if not visible_results:
        if requester_user_id and tenant_id:
            async with get_session() as session:
                await GeoSearchService(
                    SpecialistSearchRepository(session)
                ).record_empty_search(
                    tenant_id=tenant_id,
                    user_id=requester_user_id,
                    event=EmptySearchEvent(
                        page=page,
                        category_id=data.get(
                            "category_id"
                        ),
                        profession_id=data.get(
                            "profession_id"
                        ),
                        city_id=data.get("city_id"),
                        location_state=data.get(
                            "location_state"
                        ),
                        radius_km=data.get("radius_km"),
                        country_wide=bool(
                            data.get("country_wide")
                        ),
                        language_code=data.get(
                            "language_code"
                        ),
                        work_format=data.get(
                            "work_format"
                        ),
                    ),
                )

        text = format_empty_results_text(data, language)
        keyboard = empty_results_keyboard(data, language)
    else:
        start_number = page * PER_PAGE + 1
        header = format_results_header(
            data=data,
            language=language,
            page=page,
            visible_count=len(visible_results),
            total_count=total_count,
        )
        text = header
        keyboard = results_navigation_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        )

    await state.set_state(SpecialistSearchFSM.viewing_results)

    rendered_message_ids: list[int] = []

    if isinstance(event, CallbackQuery):
        if visible_results:
            header_message = await event.message.answer(text)
            rendered_message_ids.append(
                header_message.message_id,
            )

            for index, result in enumerate(visible_results):
                card_message = await event.message.answer(
                    format_specialist_result(
                        result,
                        start_number + index,
                        language,
                    ),
                reply_markup=result_card_keyboard(
                    index,
                    language,
                    is_saved=(
                        result.specialist.id
                        in saved_specialist_ids
                    ),
                ),
                )
                rendered_message_ids.append(
                    card_message.message_id,
                )

            navigation_message = await event.message.answer(
                t("search_results_navigation", language),
                reply_markup=keyboard,
            )
            rendered_message_ids.append(
                navigation_message.message_id,
            )
        else:
            empty_message = await show_callback_message(
                event,
                text,
                keyboard,
            )
            rendered_message_ids.append(
                empty_message.message_id,
            )

    else:
        if visible_results:
            header_message = await event.answer(text)
            rendered_message_ids.append(
                header_message.message_id,
            )

            for index, result in enumerate(visible_results):
                card_message = await event.answer(
                    format_specialist_result(
                        result,
                        start_number + index,
                        language,
                    ),
                reply_markup=result_card_keyboard(
                    index,
                    language,
                    is_saved=(
                        result.specialist.id
                        in saved_specialist_ids
                    ),
                ),
                )
                rendered_message_ids.append(
                    card_message.message_id,
                )

            navigation_message = await event.answer(
                t("search_results_navigation", language),
                reply_markup=keyboard,
            )
            rendered_message_ids.append(
                navigation_message.message_id,
            )
        else:
            empty_message = await event.answer(
                text,
                reply_markup=keyboard,
            )
            rendered_message_ids.append(
                empty_message.message_id,
            )

    await state.update_data(
        last_search_result_message_ids=rendered_message_ids,
    )
    try:
        await processing_message.delete()
    except TelegramBadRequest:
        pass


@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))
async def start_search(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    language = await get_interface_language(callback.from_user.id, callback.from_user.language_code)
    requester_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if requester_user_id and tenant_id:
        async with get_session() as session:
            await GeoSearchService(
                SpecialistSearchRepository(session)
            ).record_search_opened(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                source=callback.data,
            )
    await state.update_data(
        user_language=language,
        category_id=None,
        category_name=None,
        country_wide=False,
        profession_id=None,
        profession_name=None,
        country_id=None,
        city_id=None,
        city_name=None,
        latitude=None,
        longitude=None,
        radius_km=DEFAULT_RADIUS_KM,
        work_format=None,
        language_code=None,
        location_state=None,
        sort_by="distance",
        page=0,
        search_category_source="start",
    )

    menu_message = await show_callback_message(
        callback,
        t(
            "search_start_screen",
            language,
        ),
        search_start_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await state.set_state(
        SpecialistSearchFSM.entering_text_query
    )
    await callback.answer()

@search_router.callback_query(F.data == "SEARCH_AI")
async def ask_text_search_query(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)

    menu_message = await show_callback_message(
        callback,
        t(
            "search_text_query_prompt",
            language,
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("search_choose_category_btn", language),
                        callback_data="search_filter_category",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="search_menu",
                    )
                ],
            ]
        ),
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_text_query)
    await callback.answer()

@search_router.callback_query(F.data == "SEARCH_HISTORY")
async def show_search_history(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)
    requester_user_id, tenant_id = await get_requester_context(callback.from_user.id)

    if not requester_user_id or not tenant_id:
        await callback.answer()

        menu_message = await show_callback_message(
            callback,
            t(
                "search_history_empty",
                language,
            ),
            search_history_keyboard(
                language
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    async with get_session() as session:
        history_items = await GeoSearchService(
            SpecialistSearchRepository(session)
        ).list_recent_search_history(
            tenant_id=tenant_id,
            user_id=requester_user_id,
            limit=5,
        )

    if not history_items:
        await callback.answer()

        menu_message = await show_callback_message(
            callback,
            t(
                "search_history_empty",
                language,
            ),
            search_history_keyboard(
                language
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    lines = [t("search_history_title", language), ""]

    for index, payload in enumerate(
        history_items,
        start=1,
    ):
        lines.append(
            t(
                "search_history_item",
                language,
            ).format(
                number=index,
                query=format_search_history_item(
                    payload,
                    language,
                ),
            )
        )

    await callback.answer()

    menu_message = await show_callback_message(
        callback,
        "\n".join(lines),
        search_history_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@search_router.message(SpecialistSearchFSM.entering_text_query)
async def receive_text_search_query(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(
        state,
        message,
    )
    query = (
        message.text
        or ""
    ).strip()

    if len(query) < 2:
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "search_text_query_too_short",
                    language,
                ),
                reply_markup=search_start_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    async with get_session() as session:
        search_result = await SpecialistSearchTextService(
            SpecialistRepository(session)
        ).search(
            query,
            language=language,
            limit=10,
        )

    parsed_query = search_result.parsed_query
    professions = list(search_result.professions)

    if not professions:
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "search_text_query_no_matches",
                    language,
                ).format(
                    query=query
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_choose_category_btn",
                                    language,
                                ),
                                callback_data=(
                                    "search_filter_category"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data="search_menu",
                            )
                        ],
                    ]
                ),
            )
        )

        await state.set_state(
            SpecialistSearchFSM.choosing_filters
        )
        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    await state.update_data(
        search_text_query=query,
        profession_ids=[str(profession.id) for profession in professions],
        selected_profession_ids=[],
        selected_profession_names=[],
        city_id=str(parsed_query.city_id) if parsed_query.city_id else None,
        city_name=parsed_query.city_name,
        country_id=str(parsed_query.country_id) if parsed_query.country_id else None,
        country_name=parsed_query.country_name,
        location_state="city" if parsed_query.city_id else "without",
        profession_page=0,
    )

    if len(professions) == 1:
        profession = professions[0]

        await state.update_data(
            category_id=str(profession.category_id),
            profession_id=str(profession.id),
            profession_name=item_name(profession, language),
            selected_profession_ids=[str(profession.id)],
            selected_profession_names=[item_name(profession, language)],
            location_state="city" if parsed_query.city_id else "without",
            page=0,
        )
        await render_results(event=message, state=state, page=0)
        return

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id
        ],
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=t(
                "search_text_query_matches",
                language,
            ).format(
                query=query
            ),
            reply_markup=profession_keyboard(
                professions=professions,
                page=0,
                language=language,
                selected_ids=set(),
            ),
        )
    )

    await state.set_state(
        SpecialistSearchFSM.choosing_profession
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message_id
        ),
    )

@search_router.callback_query(F.data == "search_filters")
async def back_to_search_filters(callback: CallbackQuery, state: FSMContext):
    await show_filters(callback, state)

@search_router.callback_query(F.data == "search_advanced_filters")
async def open_advanced_search_filters(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)

    await show_callback_message(
        callback,
        t("search_advanced_filters", language),
        search_advanced_filters_keyboard(language),
    )
    await callback.answer()

@search_router.callback_query(F.data == "search_filter_category")
async def open_category_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    selected_ids = list(
        data.get("selected_profession_ids") or []
    )
    selected_names = list(
        data.get("selected_profession_names") or []
    )
    async with get_session() as session:
        categories = await (
            SpecialistSearchSelectionService(
                SpecialistRepository(session)
            ).list_active_categories(
                language=language,
                limit=100,
            )
        )

    if not categories:
        await callback.answer()

        menu_message = await show_callback_message(
            callback,
            t(
                "search_categories_missing",
                language,
            ),
            (
                search_filters_keyboard(
                    data,
                    language,
                )
                if data.get(
                    "search_category_source"
                ) == "filters"
                else search_start_keyboard(
                    language
                )
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    await state.update_data(
        category_ids=[str(category.id) for category in categories],
        category_page=0,
    )

    await show_callback_message(
        callback,
        category_selection_text(
            language,
            selected_names,
        ),
        paged_keyboard(
            items=categories,
            item_prefix="search_category",
            page_prefix="search_categories_page",
            page=0,
            language=language,
            back_callback=(
                "search_filters"
                if data.get("search_category_source")
                == "filters"
                else "search_start"
            ),
            back_text_key=(
                "search_back_to_filters_btn"
                if data.get("search_category_source")
                == "filters"
                else "search_back"
            ),
            page_size=CATEGORY_PAGE_SIZE,
            extra_rows=category_selection_rows(
                language,
                selected_ids,
            ),
            selected_item_id=data.get("category_id"),
        ),
    )
    await state.set_state(SpecialistSearchFSM.choosing_category)
    await callback.answer()


@search_router.callback_query(
    F.data.startswith("search_categories_page:")
)
async def paginate_categories(
    callback: CallbackQuery,
    state: FSMContext,
):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return

    data = await state.get_data()
    language = await get_search_language(
        state,
        callback,
    )
    selected_ids = list(
        data.get("selected_profession_ids") or []
    )
    selected_names = list(
        data.get("selected_profession_names") or []
    )

    async with get_session() as session:
        categories = await (
            SpecialistSearchSelectionService(
                SpecialistRepository(session)
            ).list_active_categories(
                language=language,
                limit=100,
            )
        )

    await state.update_data(
        category_ids=[
            str(category.id)
            for category in categories
        ],
        category_page=page,
    )

    await show_callback_message(
        callback,
        category_selection_text(
            language,
            selected_names,
        ),
        paged_keyboard(
            items=categories,
            item_prefix="search_category",
            page_prefix="search_categories_page",
            page=page,
            language=language,
            back_callback=(
                "search_filters"
                if data.get("search_category_source")
                == "filters"
                else "search_start"
            ),
            back_text_key=(
                "search_back_to_filters_btn"
                if data.get("search_category_source")
                == "filters"
                else "search_back"
            ),
            page_size=CATEGORY_PAGE_SIZE,
            extra_rows=category_selection_rows(
                language,
                selected_ids,
            ),
            selected_item_id=data.get("category_id"),
        ),
    )
    await callback.answer()

@search_router.callback_query(F.data.startswith("search_category:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    category_ids = data.get("category_ids") or []

    if index is None or index >= len(category_ids):
        await callback.answer()
        return

    requester_user_id, tenant_id = (
        await get_requester_context(
            callback.from_user.id
        )
    )

    async with get_session() as session:
        selection = await SpecialistSearchSelectionService(
            SpecialistRepository(session)
        ).select_category(
            category_id=UUID(category_ids[index]),
            language=language,
            tenant_id=tenant_id,
            user_id=requester_user_id,
        )

    if not selection:
        await callback.answer(
            t(
                "search_category_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        category_id=str(selection.category_id),
        category_name=selection.category_name,
        profession_id=None,
        profession_name=None,
        location_state="without",
        page=0,
    )

    await open_profession_filter(callback, state)


@search_router.callback_query(
    F.data == "search_filter_profession"
)
async def open_profession_filter(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(
        state,
        callback,
    )
    category_id = (
        UUID(data["category_id"])
        if data.get("category_id")
        else None
    )

    professions = (
        await load_search_profession_options(
            category_id=category_id,
            language=language,
        )
    )

    if not professions:
        await callback.answer(
            t(
                "search_professions_missing",
                language,
            ),
            show_alert=True,
        )
        return

    selected_ids = list(
        data.get("selected_profession_ids") or []
    )
    selected_names = list(
        data.get("selected_profession_names") or []
    )

    await state.update_data(
        profession_ids=[
            str(profession.id)
            for profession in professions
        ],
        selected_profession_ids=selected_ids,
        selected_profession_names=selected_names,
        profession_page=0,
    )

    await show_callback_message(
        callback,
        profession_selection_text(
            language,
            selected_ids,
        ),
        profession_keyboard(
            professions=professions,
            page=0,
            language=language,
            selected_ids=set(selected_ids),
            show_filters_back=(
                data.get("search_category_source")
                == "filters"
            ),
        ),
    )
    await state.set_state(
        SpecialistSearchFSM.choosing_profession
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_professions_page:"))
async def paginate_professions(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return

    data = await state.get_data()
    language = await get_search_language(state, callback)
    category_id = UUID(data["category_id"]) if data.get("category_id") else None

    professions = (
        await load_search_profession_options(
            category_id=category_id,
            language=language,
        )
    )
    await state.update_data(
        profession_ids=[str(item.id) for item in professions],
        profession_page=page,
    )

    await show_callback_message(
        callback,
        profession_selection_text(
            language,
            data.get("selected_profession_ids") or [],
        ),
        profession_keyboard(
            professions=professions,
            page=page,
            language=language,
            selected_ids=set(
                data.get("selected_profession_ids") or []
            ),
            show_filters_back=(
                data.get("search_category_source")
                == "filters"
            ),
        ),
    )
    await callback.answer()


@search_router.callback_query(
    F.data == "search_professions_select_all"
)
async def select_all_professions(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(
        state,
        callback,
    )
    category_id = (
        UUID(data["category_id"])
        if data.get("category_id")
        else None
    )
    page = int(
        data.get("profession_page") or 0
    )

    professions = (
        await load_search_profession_options(
            category_id=category_id,
            language=language,
        )
    )

    selected_ids = list(
        data.get("selected_profession_ids") or []
    )
    selected_names = list(
        data.get("selected_profession_names") or []
    )

    for profession in professions:
        profession_id = str(profession.id)

        if profession_id in selected_ids:
            continue

        selected_ids.append(profession_id)
        selected_names.append(
            item_name(
                profession,
                language,
            )
        )

    await state.update_data(
        profession_ids=[
            str(profession.id)
            for profession in professions
        ],
        selected_profession_ids=selected_ids,
        selected_profession_names=selected_names,
        profession_page=page,
    )

    await show_callback_message(
        callback,
        profession_selection_text(
            language,
            selected_ids,
        ),
        profession_keyboard(
            professions=professions,
            page=page,
            language=language,
            selected_ids=set(selected_ids),
            show_filters_back=(
                data.get("search_category_source")
                == "filters"
            ),
        ),
    )
    await callback.answer()

@search_router.callback_query(F.data.startswith("search_profession_toggle:"))
async def toggle_profession(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    profession_ids = data.get("profession_ids") or []

    if index is None or index >= len(profession_ids):
        await callback.answer()
        return

    profession_id = profession_ids[index]
    selected_ids = list(data.get("selected_profession_ids") or [])
    selected_names = list(data.get("selected_profession_names") or [])

    category_id = (
        UUID(data["category_id"])
        if data.get("category_id")
        else None
    )

    async with get_session() as session:
        selection = await (
            SpecialistSearchSelectionService(
                SpecialistRepository(session)
            ).select_profession(
                profession_id=UUID(
                    profession_id
                ),
                category_id=category_id,
                language=language,
            )
        )

    if not selection:
        await callback.answer(
            t(
                "search_profession_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    profession_name = (
        selection.profession_name
    )

    if profession_id in selected_ids:
        remove_index = selected_ids.index(profession_id)
        selected_ids.pop(remove_index)
        if remove_index < len(selected_names):
            selected_names.pop(remove_index)
    else:
        selected_ids.append(profession_id)
        selected_names.append(profession_name)

    await state.update_data(
        selected_profession_ids=selected_ids,
        selected_profession_names=selected_names,
        profession_page=int(data.get("profession_page") or 0),
    )

    page = int(
        data.get("profession_page") or 0
    )

    professions = (
        await load_search_profession_options(
            category_id=category_id,
            language=language,
        )
    )

    await show_callback_message(
        callback,
        profession_selection_text(language, selected_ids),
        profession_keyboard(
            professions=professions,
            page=page,
            language=language,
            selected_ids=set(selected_ids),
            show_filters_back=(
                data.get("search_category_source")
                == "filters"
            ),
        ),
    )
    await callback.answer()


@search_router.callback_query(F.data == "search_professions_reset")
async def reset_selected_professions(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    category_id = UUID(data["category_id"]) if data.get("category_id") else None
    page = int(data.get("profession_page") or 0)

    await state.update_data(
        selected_profession_ids=[],
        selected_profession_names=[],
        profession_id=None,
        profession_name=None,
    )

    professions = (
        await load_search_profession_options(
            category_id=category_id,
            language=language,
        )
    )

    await show_callback_message(
        callback,
        profession_selection_text(language, []),
        profession_keyboard(
            professions=professions,
            page=page,
            language=language,
            selected_ids=set(),
            show_filters_back=(
                data.get("search_category_source")
                == "filters"
            ),
        ),
    )
    await callback.answer()


@search_router.callback_query(F.data == "search_professions_apply")
async def apply_selected_professions(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_ids = list(data.get("selected_profession_ids") or [])
    selected_names = list(data.get("selected_profession_names") or [])
    language = await get_search_language(state, callback)

    if not selected_ids:
        await callback.answer(
            t("search_selected_directions_required", language),
            show_alert=True,
        )
        return

    await state.update_data(
        profession_id=selected_ids[0],
        profession_name=", ".join(selected_names),
        selected_profession_ids=selected_ids,
        selected_profession_names=selected_names,
        location_state="without",
        page=0,
    )
    await render_results(event=callback, state=state, page=0)

@search_router.callback_query(F.data.startswith("search_profession:"))
async def choose_profession(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    profession_ids = data.get("profession_ids") or []

    if index is None or index >= len(profession_ids):
        await callback.answer()
        return

    category_id = UUID(data["category_id"]) if data.get("category_id") else None

    requester_user_id, tenant_id = (
        await get_requester_context(
            callback.from_user.id
        )
    )

    async with get_session() as session:
        selection = await SpecialistSearchSelectionService(
            SpecialistRepository(session)
        ).select_profession(
            profession_id=UUID(profession_ids[index]),
            category_id=category_id,
            language=language,
            tenant_id=tenant_id,
            user_id=requester_user_id,
        )

    if not selection:
        await callback.answer(
            t(
                "search_profession_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        profession_id=str(selection.profession_id),
        profession_name=selection.profession_name,
        location_state="without",
        page=0,
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_filter_location")
async def open_location_filter(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)

    requester_user_id, tenant_id = (
        await get_requester_context(
            callback.from_user.id
        )
    )

    if requester_user_id and tenant_id:
        async with get_session() as session:
            await GeoSearchService(
                SpecialistSearchRepository(session)
            ).record_location_opened(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                source="search_filter",
            )

    await show_callback_message(
        callback,
        t("search_location_prompt", language),
        search_location_keyboard(language),
    )
    await callback.answer()

@search_router.callback_query(F.data == "search_location_city")
async def start_location_city_search(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    await show_callback_message(
        callback,
        t("search_location_city_prompt", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_location_query)
    await callback.answer()

@search_router.callback_query(F.data == "search_location_without")
async def choose_search_without_location(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)

    await state.update_data(
        location_state="without",
        country_id=None,
        city_id=None,
        city_name=None,
        latitude=None,
        longitude=None,
        country_wide=False,
        page=0,
    )

    await render_results(event=callback, state=state, page=0)

@search_router.message(SpecialistSearchFSM.entering_location_query)
async def receive_location_query(message: Message, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, message)
    query = (message.text or "").strip()

    if len(query) < 2:
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "search_location_query_too_short",
                    language,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_back_to_filters_btn",
                                    language,
                                ),
                                callback_data="search_filters",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data="search_menu",
                            )
                        ],
                    ]
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    try:
        async with get_session() as session:
            candidates = await GeoService(
                GeoRepository(session)
            ).search_places(
                query=query,
                language=language,
                limit=8,
            )

        logger.info(
            "search_geo_query_completed telegram_id=%s candidates=%s",
            message.from_user.id,
            len(candidates),
        )
    except GeoServiceError as exc:
        logger.warning(
            "search_geo_query_failed telegram_id=%s error=%s",
            message.from_user.id,
            exc,
        )

        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "search_geo_provider_error",
                    language,
                ),
                reply_markup=search_geo_empty_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    if not candidates:
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "search_geo_candidates_not_found",
                    language,
                ),
                reply_markup=search_geo_empty_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    candidate_state = dedupe_geo_candidate_states(
        [
            candidate.to_state()
            for candidate in candidates
        ],
        limit=8,
    )

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id
        ],
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=t(
                "search_geo_candidates_prompt",
                language,
            ),
            reply_markup=search_geo_candidates_keyboard(
                candidate_state,
                language,
            ),
        )
    )

    await state.update_data(
        search_geo_candidates=candidate_state,
        last_menu_message_id=(
            menu_message_id
        ),
    )
    await state.set_state(
        SpecialistSearchFSM.choosing_geo_place
    )


@search_router.callback_query(F.data == "search_location_geo")
async def start_location_geo_search(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(
        state,
        callback,
    )
    await callback.answer()

    menu_message = await replace_callback_menu_message(
        callback=callback,
        text=t(
            "search_geo_prompt",
            language,
        ),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=t(
                            "search_send_geo_btn",
                            language,
                        ),
                        request_location=True,
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )

    await state.set_state(
        SpecialistSearchFSM.waiting_geo
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@search_router.message(SpecialistSearchFSM.waiting_geo)
async def receive_geo(message: Message, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, message)

    if not message.location:
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "search_geo_required",
                    language,
                ),
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[
                        [
                            KeyboardButton(
                                text=t(
                                    "search_send_geo_btn",
                                    language,
                                ),
                                request_location=True,
                            )
                        ]
                    ],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    try:
        async with get_session() as session:
            candidates = await GeoService(
                GeoRepository(session)
            ).nearby_places(
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                language=language,
                limit=4,
            )

        logger.info(
            "search_geo_nearby_completed telegram_id=%s candidates=%s",
            message.from_user.id,
            len(candidates),
        )
    except GeoServiceError as exc:
        logger.warning(
            "search_geo_nearby_failed telegram_id=%s error=%s",
            message.from_user.id,
            exc,
        )

        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        error_text = (
            f"{t('search_geo_provider_error', language)}"
            "\n\n"
            f"{t('search_location_prompt', language)}"
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=error_text,
                reply_markup=ReplyKeyboardRemove(),
            )
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=menu_message_id,
                text=error_text,
                reply_markup=search_geo_empty_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    candidate_state = dedupe_geo_candidate_states(
        [
            candidate.to_state()
            for candidate in candidates
        ],
        limit=4,
    )

    if not candidate_state:
        await delete_telegram_messages(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[
                message.message_id
            ],
        )

        empty_text = (
            f"{t('search_geo_candidates_not_found', language)}"
            "\n\n"
            f"{t('search_location_prompt', language)}"
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=empty_text,
                reply_markup=ReplyKeyboardRemove(),
            )
        )

        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=menu_message_id,
                text=empty_text,
                reply_markup=search_geo_empty_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message_id
            ),
        )
        return

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id
        ],
    )

    candidates_text = (
        f"{t('search_geo_candidates_prompt', language)}"
        "\n\n"
        f"{t('search_geo_nearby_prompt', language)}"
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=candidates_text,
            reply_markup=ReplyKeyboardRemove(),
        )
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=menu_message_id,
            text=candidates_text,
            reply_markup=search_geo_candidates_keyboard(
                candidate_state,
                language,
            ),
        )
    )

    await state.update_data(
        search_geo_candidates=candidate_state,
        last_menu_message_id=(
            menu_message_id
        ),
    )
    await state.set_state(
        SpecialistSearchFSM.choosing_geo_place
    )

@search_router.callback_query(F.data == "search_geo_other")
async def search_geo_other_options(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)

    await show_callback_message(
        callback,
        t("search_location_city_prompt", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_back_to_filters_btn", language), callback_data="search_filters")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_location_query)
    await callback.answer()


@search_router.callback_query(F.data == "search_geo_retry")
async def search_geo_retry(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)

    await show_callback_message(
        callback,
        t("search_location_prompt", language),
        search_location_keyboard(language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_filters)
    await callback.answer()

@search_router.callback_query(F.data.startswith("search_geo_place:"))
async def choose_search_geo_place(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    candidates = data.get("search_geo_candidates") or []

    if index is None or index >= len(candidates):
        await callback.answer(t("search_geo_candidate_not_found", language), show_alert=True)
        return

    candidate = candidates[index]

    try:
        actor_user_id, tenant_id = (
            await get_requester_context(
                callback.from_user.id
            )
        )

        async with get_session() as session:
            place = await GeoService(
                GeoRepository(session)
            ).confirm_search_place(
                candidate,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                source="search_filter",
            )

        logger.info(
            "search_geo_place_confirmed telegram_id=%s city_id=%s country_id=%s",
            callback.from_user.id,
            place.city_id,
            place.country_id,
        )

    except RateLimitError as exc:
        logger.warning(
            "search_geo_change_rate_limited telegram_id=%s error=%s",
            callback.from_user.id,
            exc,
        )
        await callback.answer(t("error_rate_limited", language), show_alert=True)
        return
    
    except GeoServiceError as exc:
        logger.warning(
            "search_geo_place_confirm_failed telegram_id=%s error=%s",
            callback.from_user.id,
            exc,
        )
        await callback.answer(
            t(
                "search_geo_provider_error",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        country_id=str(place.country_id),
        city_id=str(place.city_id),
        city_name=place.city_name,
        latitude=place.latitude,
        longitude=place.longitude,
        location_state="selected",
        radius_km=data.get("radius_km") or DEFAULT_RADIUS_KM,
        search_geo_candidates=[],
        page=0,
    )
    await render_results(event=callback, state=state, page=0)

async def log_search_filters_changed(
    callback: CallbackQuery,
    *,
    filter_name: str,
    value: str | int | float | bool | None,
) -> None:
    actor_user_id, tenant_id = (
        await get_requester_context(
            callback.from_user.id
        )
    )

    if not actor_user_id or not tenant_id:
        return

    async with get_session() as session:
        await GeoSearchService(
            SpecialistSearchRepository(session)
        ).record_filter_changed(
            tenant_id=tenant_id,
            user_id=actor_user_id,
            filter_name=filter_name,
            value=value,
        )

@search_router.callback_query(F.data == "search_filter_radius")
async def open_radius_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    await show_callback_message(callback, t("search_radius_prompt", language), search_radius_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_radius:"))
async def choose_radius(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "country":
        await state.update_data(country_wide=True, page=0)
        await log_search_filters_changed(
            callback,
            filter_name="radius",
            value="country",
        )
        await render_results(event=callback, state=state, page=0)
        return
    try:
        radius_km = int(value)
    except ValueError:
        await callback.answer()
        return

    radius_km = max(5, min(radius_km, 100))
    await state.update_data(radius_km=radius_km, country_wide=False, page=0)
    await log_search_filters_changed(
        callback,
        filter_name="radius",
        value=radius_km,
    )
    await render_results(event=callback, state=state, page=0)

@search_router.callback_query(F.data == "search_filter_work_format")
async def open_work_format_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    await show_callback_message(callback, t("search_work_prompt", language), search_work_format_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_work:"))
async def choose_work_format_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]
    work_format = None if value == "any" else value

    if work_format not in {None, "at_client", "at_specialist", "remote", "mixed"}:
        await callback.answer()
        return

    if work_format == "remote":
        await state.update_data(
            work_format=work_format,
            location_state="without",
            country_id=None,
            country_name=None,
            city_id=None,
            city_name=None,
            latitude=None,
            longitude=None,
            radius_km=None,
            country_wide=False,
            page=0,
        )
    else:
        await state.update_data(
            work_format=work_format,
            page=0,
        )
    await log_search_filters_changed(
        callback,
        filter_name="work_format",
        value=work_format or "any",
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_filter_language")
async def open_language_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    await show_callback_message(callback, t("search_language_prompt", language), search_language_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_lang:"))
async def choose_language_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]
    language_code = None if value == "any" else value

    if language_code not in {None, "ru", "pt", "en"}:
        await callback.answer()
        return

    await state.update_data(language_code=language_code, page=0)
    await log_search_filters_changed(
        callback,
        filter_name="language",
        value=language_code or "any",
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_filter_availability")
async def open_availability_filter(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)
    await show_callback_message(
        callback,
        t("search_availability_prompt", language),
        search_availability_keyboard(language),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_availability:"))
async def choose_availability_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "any":
        available_only = False
    elif value == "now":
        available_only = True
    else:
        await callback.answer()
        return

    await state.update_data(available_only=available_only, page=0)
    await log_search_filters_changed(
        callback,
        filter_name="availability",
        value="available_now" if available_only else "any",
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_filter_verified")
async def open_verified_filter(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)
    await show_callback_message(
        callback,
        t("search_verified_prompt", language),
        search_verified_keyboard(language),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_verified:"))
async def choose_verified_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "any":
        verified_only = False
    elif value == "only":
        verified_only = True
    else:
        await callback.answer()
        return

    await state.update_data(verified_only=verified_only, page=0)
    await log_search_filters_changed(
        callback,
        filter_name="verified_profile",
        value="only" if verified_only else "any",
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_filter_rating")
async def open_rating_filter(callback: CallbackQuery, state: FSMContext):
    language = await get_search_language(state, callback)
    await show_callback_message(
        callback,
        t("search_rating_prompt", language),
        search_rating_keyboard(language),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_rating:"))
async def choose_rating_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "any":
        rating_min = None
    elif value == "4":
        rating_min = 4
    else:
        await callback.answer()
        return

    await state.update_data(rating_min=rating_min, page=0)
    await log_search_filters_changed(
        callback,
        filter_name="rating",
        value=rating_min or "any",
    )
    await render_results(event=callback, state=state, page=0)

@search_router.callback_query(F.data == "search_filter_sort")
async def open_sort_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    await show_callback_message(callback, t("search_sort_prompt", language), search_sort_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_sort:"))
async def choose_sort_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value not in {"distance", "relevance"}:
        await callback.answer()
        return

    await state.update_data(sort_by=value, page=0)
    await log_search_filters_changed(
        callback,
        filter_name="sort",
        value=value,
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_reset_filters")
async def reset_search_filters(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    await state.update_data(
        user_language=language,
        category_id=None,
        category_name=None,
        profession_id=None,
        profession_name=None,
        country_id=None,
        country_wide=False,
        city_id=None,
        city_name=None,
        latitude=None,
        longitude=None,
        radius_km=DEFAULT_RADIUS_KM,
        work_format=None,
        language_code=None,
        verified_only=False,
        available_only=False,
        rating_min=None,
        sort_by="distance",
        page=0,
    )
    await log_search_filters_changed(
        callback,
        filter_name="reset",
        value="all",
    )
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_empty_increase_radius")
async def empty_increase_radius(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    next_radius, next_country_wide = next_empty_radius_suggestion(data)

    if next_radius is not None:
        await state.update_data(
            radius_km=next_radius,
            country_wide=False,
            page=0,
        )
    elif next_country_wide and not data.get("country_wide"):
        await state.update_data(
            country_wide=True,
            page=0,
        )
    else:
        await callback.answer()
        return

    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_empty_reset_profession")
async def empty_reset_profession(callback: CallbackQuery, state: FSMContext):
    await state.update_data(profession_id=None, profession_name=None, page=0)
    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_show_results")
async def show_filtered_results(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    has_location = (
        data.get("location_state") == "without"
        or data.get("work_format") == "remote"
        or data.get("city_id")
        or data.get("latitude") is not None
        or data.get("country_wide")
    )

    if not has_location:
        await state.update_data(location_state="without")

    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data.startswith("search_results_page:"))
async def paginate_results(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return
    await render_results(event=callback, state=state, page=page)

@search_router.callback_query(F.data.startswith("search_result_contact:"))
async def contact_from_result(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    specialist_ids = data.get("result_specialist_ids") or []
    distances = data.get("result_distances") or []

    if index is None or index >= len(specialist_ids):
        await callback.answer()
        return

    distance_km = distances[index] if index < len(distances) else None

    await state.update_data(
        selected_specialist_id=specialist_ids[index],
        selected_specialist_distance=distance_km,
        selected_result_index=index,
    )

    await contact_start(callback, state)

@search_router.callback_query(F.data.startswith("search_result_favorite:"))
async def favorite_from_result(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    specialist_ids = data.get("result_specialist_ids") or []
    distances = data.get("result_distances") or []

    if index is None or index >= len(specialist_ids):
        await callback.answer()
        return

    distance_km = distances[index] if index < len(distances) else None

    await state.update_data(
        selected_specialist_id=specialist_ids[index],
        selected_specialist_distance=distance_km,
        selected_result_index=index,
    )

    await favorite_pending(callback, state)


@search_router.callback_query(F.data.startswith("search_result_report:"))
async def report_from_result(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(state, callback)
    specialist_ids = data.get("result_specialist_ids") or []
    distances = data.get("result_distances") or []

    if index is None or index >= len(specialist_ids):
        await callback.answer()
        return

    distance_km = distances[index] if index < len(distances) else None

    await state.update_data(
        selected_specialist_id=specialist_ids[index],
        selected_specialist_distance=distance_km,
    )

    await report_pending(callback, state)

@search_router.callback_query(
    F.data.startswith("search_result:")
)
async def show_specialist_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    index = callback_index(callback)
    data = await state.get_data()
    language = await get_search_language(
        state,
        callback,
    )
    specialist_ids = (
        data.get("result_specialist_ids") or []
    )
    distances = (
        data.get("result_distances") or []
    )

    if (
        index is None
        or index < 0
        or index >= len(specialist_ids)
    ):
        await callback.answer()
        return

    await callback.answer()

    distance_km = (
        distances[index]
        if index < len(distances)
        else None
    )
    results_page = int(
        data.get("results_page") or 0
    )

    requester_user_id, tenant_id = (
        await get_requester_context(
            callback.from_user.id
        )
    )

    async with get_session() as session:
        card = await GeoSearchService(
            SpecialistSearchRepository(session)
        ).get_public_card_for_viewer(
            specialist_id=UUID(
                specialist_ids[index]
            ),
            viewer_user_id=requester_user_id,
            tenant_id=tenant_id,
            event=PublicCardViewEvent(
                source="search_results",
                results_page=results_page,
                result_index=index,
                distance_km=distance_km,
            ),
            language=language,
        )

    if not card:
        return

    await state.update_data(
        selected_specialist_id=(
            specialist_ids[index]
        ),
        selected_specialist_distance=distance_km,
        selected_result_index=index,
    )

    await collapse_search_results_to_callback_message(
        callback=callback,
        state=state,
    )

    menu_message = await show_callback_message(
        callback,
        format_public_card(
            card,
            language,
        ),
        card_keyboard(
            language,
            results_page,
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@search_router.callback_query(F.data == "search_portfolio_pending")
async def show_selected_specialist_portfolio(callback: CallbackQuery, state: FSMContext):
    await render_public_portfolio(callback, state, page=0)


@search_router.callback_query(F.data.startswith("search_portfolio_page:"))
async def show_selected_specialist_portfolio_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        page = 0

    await render_public_portfolio(callback, state, page=page)

@search_router.callback_query(F.data == "search_portfolio_report")
async def report_public_portfolio_item(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    item_ids = data.get("public_portfolio_item_ids") or []
    page = int(data.get("public_portfolio_page") or 0)

    if page < 0 or page >= len(item_ids):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await state.update_data(
        pending_report_target_type="portfolio_item",
        pending_report_target_id=item_ids[page],
    )
    await store_complaint_target_summary(
        state,
        language,
    )

    await show_callback_message(
        callback,
        t("complaint_reason_prompt", language),
        complaint_reason_keyboard(language),
    )
    await callback.answer()

async def render_selected_specialist_reviews(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int = 0,
):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    specialist_id = data.get("selected_specialist_id")
    if not specialist_id:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    requester_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not requester_user_id or not tenant_id:
        await store_post_auth_action(
            callback=callback,
            state=state,
            action="reviews",
            language=language,
        )
        return

    await callback.answer()

    try:
        async with get_session() as session:
            review_page = await ReviewService(
                ReviewRepository(session)
            ).list_public_reviews_for_viewer(
                tenant_id=tenant_id,
                specialist_id=UUID(specialist_id),
                viewer_user_id=requester_user_id,
                page=page,
                page_size=PUBLIC_REVIEW_PAGE_SIZE,
            )

    except ReviewServiceError as exc:
        logger.warning(
            "public_reviews_load_failed "
            "specialist_id=%s error=%s",
            specialist_id,
            exc,
        )
        menu_message = await show_callback_message(
            callback,
            t(
                "public_reviews_load_error",
                language,
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_back",
                                language,
                            ),
                            callback_data=(
                                "search_result_back_to_card:"
                                f"{int(data.get('results_page') or 0)}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_menu",
                                language,
                            ),
                            callback_data="search_menu",
                        )
                    ],
                ]
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    await state.update_data(
        public_reviews_page=review_page.page,
        public_review_ids=[str(review.id) for review in review_page.reviews],
    )

    menu_message = await show_callback_message(
        callback,
        format_public_reviews(
            review_page,
            language,
        ),
        public_reviews_keyboard(
            language=language,
            page=review_page.page,
            has_previous=review_page.has_previous,
            has_next=review_page.has_next,
            reviews_count=len(review_page.reviews),
            results_page=int(data.get("results_page") or 0),
        ),
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@search_router.callback_query(F.data == "search_reviews_pending")
async def show_selected_specialist_reviews(callback: CallbackQuery, state: FSMContext):
    await render_selected_specialist_reviews(callback, state, page=0)


@search_router.callback_query(F.data.startswith("search_reviews_page:"))
async def show_selected_specialist_reviews_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        page = 0

    await render_selected_specialist_reviews(callback, state, page=page)

@search_router.callback_query(F.data.startswith("search_review_report:"))
async def report_public_review(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer()
        return

    review_ids = data.get("public_review_ids") or []
    if index < 0 or index >= len(review_ids):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await state.update_data(
        pending_report_target_type="review",
        pending_report_target_id=review_ids[index],
    )
    await store_complaint_target_summary(
        state,
        language,
    )

    await show_callback_message(
        callback,
        t("complaint_reason_prompt", language),
        complaint_reason_keyboard(language),
    )
    await callback.answer()

@search_router.callback_query(F.data.startswith("search_result_back_to_card:"))
async def back_to_selected_specialist_card(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    specialist_id = data.get("selected_specialist_id")
    if not specialist_id:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    async with get_session() as session:
        card = await GeoSearchService(
            SpecialistSearchRepository(session)
        ).get_public_card(
            specialist_id=UUID(specialist_id),
        )

    if not card:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    try:
        results_page = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        results_page = int(data.get("results_page") or 0)

    await show_callback_message(
        callback,
        format_public_card(card, language),
        card_keyboard(language, results_page),
    )
    await callback.answer()

@search_router.callback_query(
    F.data == "search_contact_pending"
)
async def contact_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    specialist_id = data.get("selected_specialist_id")
    profession_id = data.get("profession_id")

    if not specialist_id:
        await callback.answer(
            t("search_contact_no_specialist", language),
            show_alert=True,
        )
        return

    requester_user_id, tenant_id = await get_requester_context(
        callback.from_user.id,
    )
    if not requester_user_id or not tenant_id:
        await store_post_auth_action(
            callback=callback,
            state=state,
            action="contact",
            language=language,
        )
        return
    
    await callback.answer()

    try:
        async with get_session() as session:
            chat = await ContactChatService(
                ContactChatRepository(session)
            ).open_contact_chat(
                tenant_id=tenant_id,
                from_user_id=requester_user_id,
                specialist_id=UUID(specialist_id),
                profession_id=(
                    UUID(profession_id)
                    if profession_id
                    else None
                ),
                system_message=t(
                    "contact_chat_first_prompt",
                    language,
                ),
                original_language=language,
            )
    except (ContactChatError, ValueError) as exc:
        logger.warning(
            "contact_chat_open_failed "
            "telegram_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        menu_message = await show_callback_message(
            callback,
            t(
                "contact_chat_error",
                language,
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_back_to_filters_btn",
                                language,
                            ),
                            callback_data="search_filters",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_menu",
                                language,
                            ),
                            callback_data="search_menu",
                        )
                    ],
                ]
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    await state.update_data(
        active_contact_request_id=str(
            chat.contact_request_id
        ),
        active_thread_id=str(chat.thread_id),
        active_thread_role="client",
        pending_contact_message=None,
    )
    await state.set_state(
        SpecialistSearchFSM.entering_thread_message,
    )

    await show_client_contact_chat(
        message=callback.message,
        state=state,
        thread_id=str(chat.thread_id),
        user_id=requester_user_id,
        language=language,
    )


@search_router.callback_query(F.data == "search_contact_cancel")
async def cancel_contact_flow(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("results_page") or 0)

    await state.update_data(
        pending_contact_message=None,
        pending_report_reason=None,
    )

    logger.info(
        "contact_flow_cancelled telegram_id=%s page=%s",
        callback.from_user.id,
        page,
    )

    await render_results(
        event=callback,
        state=state,
        page=page,
    )

def callback_token(callback: CallbackQuery) -> str | None:
    try:
        return (callback.data or "").split(":", 1)[1]
    except (IndexError, TypeError):
        return None


@search_router.callback_query(
    F.data.startswith("contact_accept:")
)
@search_router.callback_query(
    F.data.startswith("contact_reject:")
)
async def block_legacy_contact_request_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(state, callback)

    await callback.answer(
        t("legacy_contact_request_unavailable", language),
        show_alert=True,
    )

@search_router.message(SpecialistSearchFSM.entering_thread_message)
async def receive_thread_message(message: Message, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, message)
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await show_contact_chat_screen(
            message=message,
            state=state,
            text=t(
                "contact_thread_not_found",
                language,
            ),
            reply_markup=search_start_keyboard(
                language
            ),
        )
        await state.set_state(
            SpecialistSearchFSM.viewing_results
        )
        return

    sender_user_id, tenant_id = await get_requester_context(message.from_user.id)
    if not sender_user_id or not tenant_id:
        await show_contact_chat_screen(
            message=message,
            state=state,
            text=t(
                "search_contact_user_not_found",
                language,
            ),
            reply_markup=(
                contact_thread_keyboard_for_role(
                    language,
                    data.get("active_thread_role"),
                )
            ),
        )
        return

    message_text = (
        message.text
        or message.caption
        or ""
    ).strip()
    attachment: dict | None = None

    if message.photo:
        photo = message.photo[-1]
        attachment = {
            "type": "photo",
            "file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "file_name": None,
            "mime_type": "image/jpeg",
            "file_size": photo.file_size,
        }
    elif message.document:
        document = message.document
        attachment = {
            "type": "document",
            "file_id": document.file_id,
            "file_unique_id": document.file_unique_id,
            "file_name": document.file_name,
            "mime_type": document.mime_type,
            "file_size": document.file_size,
        }
    elif not message_text:
        await show_contact_chat_screen(
            message=message,
            state=state,
            text=t(
                "contact_attachment_unsupported",
                language,
            ),
            reply_markup=(
                contact_thread_keyboard_for_role(
                    language,
                    data.get("active_thread_role"),
                )
            ),
        )
        return

    receiver_platform_user_id = None
    receiver_language = language
    receiver_notification_message = message_text
    receiver_used_translation = False
    receiver_translation_status = "not_needed"

    try:
        async with get_session() as session:
            result = await ContactChatService(
                ContactChatRepository(session)
            ).send_thread_message(
                thread_id=UUID(thread_id),
                sender_user_id=sender_user_id,
                text=message_text,
                original_language=language,
                attachment=attachment,
            )

            delivery_context = await UserService(
                session
            ).get_telegram_delivery_context(
                user_id=result.receiver_user_id,
            )

            if delivery_context.language_code:
                receiver_language = (
                    normalize_language(
                        delivery_context.language_code
                    )
                )

            receiver_platform_user_id = (
                delivery_context.platform_user_id
            )

            receiver_notification_message, receiver_used_translation, receiver_translation_status = await translate_message_for_notification(
                session=session,
                message_id=result.message_id,
                receiver_user_id=result.receiver_user_id,
            )

        logger.info(
            "contact_thread_message_sent telegram_id=%s thread_id=%s message_id=%s receiver_user_id=%s",
            message.from_user.id,
            result.thread_id,
            result.message_id,
            result.receiver_user_id,
        )

    except ContactChatError as exc:
        error_text = str(exc)

        logger.warning(
            "contact_thread_message_failed telegram_id=%s thread_id=%s error=%s",
            message.from_user.id,
            thread_id,
            exc,
        )

        if "read-only for blacklisted users" in error_text:
            await show_contact_chat_screen(
                message=message,
                state=state,
                text=t(
                    "contact_thread_read_only_blacklisted",
                    language,
                ),
                reply_markup=(
                    contact_thread_keyboard_for_role(
                        language,
                        data.get("active_thread_role"),
                    )
                ),
            )
            await state.set_state(
                SpecialistSearchFSM.entering_thread_message
            )
            return
        if "Attachment is too large." in error_text:
            await show_contact_chat_screen(
                message=message,
                state=state,
                text=t(
                    "contact_attachment_too_large",
                    language,
                ),
                reply_markup=(
                    contact_thread_keyboard_for_role(
                        language,
                        data.get("active_thread_role"),
                    )
                ),
            )
            await state.set_state(
                SpecialistSearchFSM.entering_thread_message
            )
            return

        if (
            "Unsupported attachment type." in error_text
            or "Attachment file is missing." in error_text
            or "Invalid attachment size." in error_text
        ):
            await show_contact_chat_screen(
                message=message,
                state=state,
                text=t(
                    "contact_attachment_unsupported",
                    language,
                ),
                reply_markup=(
                    contact_thread_keyboard_for_role(
                        language,
                        data.get("active_thread_role"),
                    )
                ),
            )
            await state.set_state(
                SpecialistSearchFSM.entering_thread_message
            )
            return

        await show_contact_chat_screen(
            message=message,
            state=state,
            text=t(
                "contact_chat_error",
                language,
            ),
            reply_markup=(
                contact_thread_keyboard_for_role(
                    language,
                    data.get("active_thread_role"),
                )
            ),
        )
        await state.set_state(
            SpecialistSearchFSM.entering_thread_message
        )
        return


    receiver_chat_id = telegram_chat_id(receiver_platform_user_id)

    receiver_notification_key = (
        "contact_translated_message_received"
        if receiver_used_translation
        else "contact_thread_message_received"
    )
    if receiver_translation_status == "failed":
        receiver_notification_key = "contact_translation_failed_original_shown"

    if receiver_chat_id:
        receiver_notification_text = t(
            receiver_notification_key,
            receiver_language,
        ).format(
            message=receiver_notification_message,
        )

        if attachment:
            attachment_caption = (
                receiver_notification_text[:1000]
            )

            if attachment["type"] == "photo":
                await message.bot.send_photo(
                    chat_id=receiver_chat_id,
                    photo=attachment["file_id"],
                    caption=attachment_caption,
                    reply_markup=contact_thread_keyboard(
                        receiver_language
                    ),
                )
            else:
                await message.bot.send_document(
                    chat_id=receiver_chat_id,
                    document=attachment["file_id"],
                    caption=attachment_caption,
                    reply_markup=contact_thread_keyboard(
                        receiver_language
                    ),
                )
        else:
            await message.bot.send_message(
                chat_id=receiver_chat_id,
                text=receiver_notification_text,
                reply_markup=contact_thread_keyboard(
                    receiver_language
                ),
            )
    await state.update_data(
        active_thread_id=str(result.thread_id)
    )
    await state.set_state(
        SpecialistSearchFSM.entering_thread_message,
    )

    await show_contact_chat(
        message=message,
        state=state,
        thread_id=str(result.thread_id),
        user_id=sender_user_id,
        viewer_role=(
            data.get("active_thread_role")
            or "client"
        ),
        language=language,
        include_attachments=(
            attachment is not None
        ),
        notice=(
            t(
                "contact_detection_warning",
                language,
            )
            if result.message_masked
            else None
        ),
    )

@search_router.callback_query(
    F.data == "search_favorite_pending"
)
async def favorite_pending(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(
        state,
        callback,
    )
    specialist_id = data.get(
        "selected_specialist_id"
    )

    if not specialist_id:
        await callback.answer(
            t(
                "search_contact_no_specialist",
                language,
            ),
            show_alert=True,
        )
        return

    user_id, tenant_id = await get_requester_context(
        callback.from_user.id
    )
    if not user_id or not tenant_id:
        await store_post_auth_action(
            callback=callback,
            state=state,
            action="favorite",
            language=language,
        )
        return
    
    await callback.answer()

    try:
        async with get_session() as session:
            is_saved = await FavoriteService(
                FavoriteRepository(session)
            ).toggle_specialist(
                tenant_id=tenant_id,
                user_id=user_id,
                specialist_id=UUID(
                    specialist_id
                ),
            )
    except ValueError as exc:
        logger.warning(
            "favorite_toggle_failed "
            "telegram_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        menu_message = await show_callback_message(
            callback,
            t(
                "favorite_action_error",
                language,
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_back_to_filters_btn",
                                language,
                            ),
                            callback_data="search_filters",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_menu",
                                language,
                            ),
                            callback_data="search_menu",
                        )
                    ],
                ]
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    logger.info(
        "favorite_toggled "
        "telegram_id=%s user_id=%s "
        "specialist_id=%s is_saved=%s",
        callback.from_user.id,
        user_id,
        specialist_id,
        is_saved,
    )

    await state.update_data(
        selected_specialist_is_saved=is_saved,
    )

    try:
        if callback.data == "search_favorite_pending":
            results_page = int(
                data.get("results_page") or 0
            )

            await callback.message.edit_reply_markup(
                reply_markup=card_keyboard(
                    language,
                    results_page,
                    is_saved=is_saved,
                )
            )
        else:
            result_index = data.get(
                "selected_result_index"
            )

            if isinstance(result_index, int):
                await callback.message.edit_reply_markup(
                    reply_markup=result_card_keyboard(
                        result_index,
                        language,
                        is_saved=is_saved,
                    )
                )
    except TelegramBadRequest:
        pass

@search_router.callback_query(
    F.data == "search_report_pending"
)
async def report_pending(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(
        state,
        callback,
    )
    specialist_id = data.get(
        "selected_specialist_id"
    )

    if not specialist_id:
        await callback.answer(
            t(
                "search_contact_no_specialist",
                language,
            ),
            show_alert=True,
        )
        return

    await callback.answer()

    await state.update_data(
        pending_report_target_type="specialist",
        pending_report_target_id=specialist_id,
        pending_report_reason=None,
        pending_report_comment=None,
    )

    await store_complaint_target_summary(
        state,
        language,
    )

    await collapse_search_results_to_callback_message(
        callback=callback,
        state=state,
    )

    menu_message = await show_callback_message(
        callback,
        t(
            "complaint_reason_prompt",
            language,
        ),
        complaint_reason_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@search_router.callback_query(F.data.startswith("search_report_reason:"))
async def choose_report_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    reason = callback.data.split(":", 1)[1]

    if reason not in {"fake", "contact", "abuse", "other"}:
        await callback.answer()
        return

    if not data.get("selected_specialist_id"):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await state.update_data(
        pending_report_reason=reason,
        pending_report_comment=None,
    )
    await state.set_state(SpecialistSearchFSM.confirming_report)

    data = await state.get_data()
    await show_callback_message(
        callback,
        complaint_draft_text(data, language),
        complaint_draft_keyboard(language),
    )
    await callback.answer()

@search_router.callback_query(
    F.data == "search_report_comment"
)
async def ask_report_comment(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(
        state,
        callback,
    )

    await callback.answer()

    await state.set_state(
        SpecialistSearchFSM.entering_report_comment
    )

    menu_message = await show_callback_message(
        callback,
        t(
            "complaint_comment_prompt",
            language,
        ),
        complaint_comment_keyboard(language),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@search_router.callback_query(F.data == "search_report_send")
async def send_report(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    reason = data.get("pending_report_reason")
    comment = data.get("pending_report_comment")

    if not reason:
        await callback.answer(
            t("complaint_reason_required", language),
            show_alert=True,
        )
        return

    if reason == "other" and not comment:
        await callback.answer(
            t("complaint_other_comment_required", language),
            show_alert=True,
        )
        return

    await create_search_complaint(
        event=callback,
        state=state,
        reason=reason,
        comment=comment,
    )

@search_router.callback_query(F.data == "search_report_cancel")
async def cancel_report(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(state, callback)

    await state.update_data(
        pending_report_reason=None,
        pending_report_comment=None,
        pending_report_target_type=None,
        pending_report_target_id=None,
        pending_report_target_summary=None,
    )

    if data.get("active_thread_id"):
        await state.set_state(
            SpecialistSearchFSM.entering_thread_message
        )
        back_callback = (
            "SPEC_DIALOGS"
            if data.get("active_thread_role") == "specialist"
            else "CLIENT_DIALOGS"
        )
        back_text = t(
            "contact_back_to_dialogs_btn",
            language,
        )
    else:
        await state.set_state(
            SpecialistSearchFSM.viewing_results
        )
        back_callback = "search_filters"
        back_text = t(
            "search_back_to_filters_btn",
            language,
        )

    await callback.answer()

    menu_message = await show_callback_message(
        callback,
        t(
            "complaint_cancelled",
            language,
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=back_text,
                        callback_data=back_callback,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="search_menu",
                    )
                ],
            ]
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@search_router.message(
    SpecialistSearchFSM.entering_report_comment
)
async def receive_report_comment(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    language = await get_search_language(
        state,
        message,
    )
    comment = (message.text or "").strip()

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[message.message_id],
    )

    if len(comment) < 3:
        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=(
                    f"{t('complaint_comment_too_short', language)}\n\n"
                    f"{t('complaint_comment_prompt', language)}"
                ),
                reply_markup=complaint_comment_keyboard(
                    language
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=menu_message_id
        )
        return

    await state.update_data(
        pending_report_comment=comment
    )
    await state.set_state(
        SpecialistSearchFSM.confirming_report
    )

    data = await state.get_data()

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=complaint_draft_text(
                data,
                language,
            ),
            reply_markup=complaint_draft_keyboard(
                language
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=menu_message_id
    )

async def create_search_complaint(
    *,
    event: CallbackQuery | Message,
    state: FSMContext,
    reason: str,
    comment: str | None,
):
    data = await state.get_data()
    language = normalize_language(
        data.get("user_language")
        or event.from_user.language_code
    )
    specialist_id = data.get("selected_specialist_id")
    target_type = data.get("pending_report_target_type") or "specialist"
    target_id = data.get("pending_report_target_id") or specialist_id

    if not target_id:
        if isinstance(event, CallbackQuery):
            await event.answer(t("search_contact_no_specialist", language), show_alert=True)
        else:
            await event.answer(t("search_contact_no_specialist", language))
        return

    reporter_user_id, tenant_id = (
        await get_requester_context(
            event.from_user.id
        )
    )

    if not reporter_user_id or not tenant_id:
        if isinstance(event, CallbackQuery):
            await store_post_auth_action(
                callback=event,
                state=state,
                action="report",
                language=language,
            )
        else:
            await state.update_data(
                post_auth_action="report"
            )

            menu_message_id = (
                await edit_or_replace_tracked_menu_message(
                    message=event,
                    menu_message_id=data.get(
                        "last_menu_message_id"
                    ),
                    text=t(
                        "auth_required_start",
                        language,
                    ),
                    reply_markup=search_start_keyboard(
                        language
                    ),
                )
            )

            await state.update_data(
                last_menu_message_id=menu_message_id
            )
        return

    try:
        async with get_session() as session:
            moderation_service = ModerationService(
                ModerationRepository(session)
            )
            complaint = await moderation_service.create_complaint(
                tenant_id=tenant_id,
                reporter_user_id=reporter_user_id,
                target_type=target_type,
                target_id=UUID(target_id),
                reason=reason,
                comment=comment,
            )
            await moderation_service.confirm_complaint(
                reporter_user_id=reporter_user_id,
                complaint_id=complaint.id,
            )

        complaint_number = str(complaint.id).split("-", 1)[0]

        logger.info(
            "complaint_created telegram_id=%s reporter_user_id=%s target_type=%s target_id=%s reason=%s",
            event.from_user.id,
            reporter_user_id,
            target_type,
            target_id,
            reason,
        )
    except ModerationError as exc:
        technical_error = str(exc)
        error_text = t(
            "complaint_create_error",
            language,
        )

        if (
            "active complaint with this reason already exists"
            in technical_error.lower()
        ):
            error_text = t(
                "complaint_duplicate_active",
                language,
            )
        logger.warning(
            "complaint_create_failed telegram_id=%s target_type=%s target_id=%s reason=%s error=%s",
            event.from_user.id,
            target_type,
            target_id,
            reason,
            exc,
        )
        if isinstance(event, CallbackQuery):
            await event.answer(
                error_text,
                show_alert=True,
            )
        else:
            await event.answer(error_text)
        return

    await state.update_data(
        pending_report_reason=None,
        pending_report_comment=None,
        pending_report_target_type=None,
        pending_report_target_id=None,
        pending_report_target_summary=None,
        page=data.get("page") or 0,
    )
    await state.set_state(SpecialistSearchFSM.viewing_results)
    confirmation_text = t(
        "complaint_confirmed",
        language,
    ).format(
        complaint_number=complaint_number,
    )

    confirmation_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "support_my_tickets_btn",
                        language,
                    ),
                    callback_data="SUPPORT_MY_TICKETS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="search_menu",
                )
            ],
        ]
    )

    if isinstance(event, CallbackQuery):
        menu_message = await show_callback_message(
            event,
            confirmation_text,
            confirmation_keyboard,
        )
        menu_message_id = menu_message.message_id
        await event.answer()
    else:
        menu_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=event,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=confirmation_text,
                reply_markup=confirmation_keyboard,
            )
        )

    await state.update_data(
        last_menu_message_id=menu_message_id
    )


@search_router.callback_query(
    F.data == "search_menu"
)
async def back_to_main_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(
        state,
        callback,
    )

    await send_global_main_menu(
        callback=callback,
        state=state,
        language=language,
    )


@search_router.callback_query(F.data == "contact_show_original")
async def show_original_message(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = await get_search_language(state, callback)
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    viewer_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not viewer_user_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            original = await TranslationService(
                TranslationRepository(session)
            ).get_original_message_for_thread(
                thread_id=UUID(thread_id),
                viewer_user_id=viewer_user_id,
            )
    except TranslationError as exc:
        logger.warning(
            "contact_show_original_failed telegram_id=%s thread_id=%s error=%s",
            callback.from_user.id,
            thread_id,
            exc,
        )
        await callback.answer(
            t(
                "contact_original_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    original_text_key = (
        "contact_translation_failed_original_shown"
        if original.translation_status == "failed"
        else "contact_original_message"
    )

    await callback.answer()

    menu_message = await show_callback_message(
        callback,
        t(
            original_text_key,
            language,
        ).format(
            message=original.original_text,
        ),
        contact_thread_keyboard(language),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@search_router.callback_query(
    (F.data == "ORDER_CREATE_FROM_THREAD")
    | (F.data == "ORDER_FORM_CANCEL")
    | F.data.startswith("ORDER_CONFIRM:")
    | F.data.startswith("ORDER_COMPLETE:")
    | F.data.startswith("review_start_order:")
)
async def block_legacy_order_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(
        state,
        callback,
    )

    await state.set_state(None)

    await callback.answer(
        t(
            "order_actions_unavailable",
            language,
        ),
        show_alert=True,
    )

def localized_review_error(error: Exception | str, language: str) -> str:
    error_text = str(error)

    mapping = {
        "invalid rating": "review_invalid_rating",
        "missing review data": "review_missing_data",
    }

    return t(mapping.get(error_text, "review_error"), language).format(
        error=error_text,
    )

@search_router.callback_query(
    F.data == "contact_finish"
)
async def finish_contact_thread(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(
        state,
        callback,
    )

    await callback.answer(
        t(
            "contact_thread_completion_not_available",
            language,
        ),
        show_alert=True,
    )

@search_router.callback_query(F.data.startswith("review_start:"))
async def start_contact_review(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(state, callback)
    contact_request_id = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if not contact_request_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.update_data(
        review_contact_request_id=contact_request_id,
        review_rating=None,
        review_text=None,
        review_thread_id=data.get("review_thread_id"),
        review_thread_role=data.get(
            "review_thread_role"
        ) or "client",
    )
    await state.set_state(
        SpecialistSearchFSM.choosing_review_rating,
    )

    await callback.answer()

    menu_message = await show_callback_message(
        callback,
        t(
            "review_rating_prompt",
            language,
        ),
        review_rating_keyboard(language),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@search_router.callback_query(
    F.data.startswith("review_rating:")
)
async def choose_review_rating(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_search_language(
        state,
        callback,
    )

    try:
        rating = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            localized_review_error(
                "invalid rating",
                language,
            ),
            show_alert=True,
        )
        return

    if rating < 1 or rating > 5:
        await callback.answer(
            localized_review_error(
                "invalid rating",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        review_rating=rating
    )
    await state.set_state(
        SpecialistSearchFSM.entering_review_text
    )

    await callback.answer()

    menu_message = await show_callback_message(
        callback,
        t(
            "review_text_prompt",
            language,
        ),
        review_skip_text_keyboard(language),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@search_router.callback_query(
    F.data == "review_text_skip"
)
async def skip_review_text(
    callback: CallbackQuery,
    state: FSMContext,
):
    await create_review_from_state(
        callback,
        state,
        text=None,
    )


@search_router.message(
    SpecialistSearchFSM.entering_review_text
)
async def receive_review_text(
    message: Message,
    state: FSMContext,
):
    text = (message.text or "").strip()

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[message.message_id],
    )

    await create_review_from_state(
        message,
        state,
        text=text,
    )


async def show_review_flow_screen(
    *,
    event: CallbackQuery | Message,
    state: FSMContext,
    menu_message_id: int | str | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(event, CallbackQuery):
        menu_message = await show_callback_message(
            event,
            text,
            reply_markup,
        )
        current_message_id = menu_message.message_id
    else:
        current_message_id = (
            await edit_or_replace_tracked_menu_message(
                message=event,
                menu_message_id=menu_message_id,
                text=text,
                reply_markup=reply_markup,
            )
        )

    await state.update_data(
        last_menu_message_id=current_message_id
    )

async def create_review_from_state(
    event: CallbackQuery | Message,
    state: FSMContext,
    text: str | None,
):
    data = await state.get_data()
    language = await get_search_language(state, event)
    contact_request_id = data.get(
        "review_contact_request_id"
    )
    review_thread_id = data.get(
        "review_thread_id"
    )
    review_thread_role = data.get(
        "review_thread_role"
    ) or "client"
    rating = data.get("review_rating")

    if not contact_request_id or not rating:
        if isinstance(event, CallbackQuery):
            await event.answer()

        await show_review_flow_screen(
            event=event,
            state=state,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=localized_review_error(
                "missing review data",
                language,
            ),
        )
        return

    reviewer_user_id, tenant_id = (
        await get_requester_context(
            event.from_user.id,
        )
    )

    if not reviewer_user_id or not tenant_id:
        if isinstance(event, CallbackQuery):
            await event.answer()

        await show_review_flow_screen(
            event=event,
            state=state,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=t(
                "search_contact_user_not_found",
                language,
            ),
        )
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    try:
        async with get_session() as session:
            review_service = ReviewService(
                ReviewRepository(session)
            )

            await review_service.create_contact_review(
                tenant_id=tenant_id,
                reviewer_user_id=reviewer_user_id,
                contact_request_id=UUID(
                    contact_request_id
                ),
                rating=int(rating),
                text=text,
            )

            if review_thread_id:
                await ContactChatService(
                    ContactChatRepository(session)
                ).archive_thread_after_review(
                    thread_id=UUID(
                        review_thread_id
                    ),
                    user_id=reviewer_user_id,
                )
    except (
        ContactChatError,
        ReviewServiceError,
    ) as exc:

        await show_review_flow_screen(
            event=event,
            state=state,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=localized_review_error(
                exc,
                language,
            ),
        )
        return

    if contact_request_id and review_thread_id:
        result_text = t(
            "review_created_archived",
            language,
        )
        result_keyboard = review_completed_keyboard(
            language=language,
            role=review_thread_role,
        )
    else:
        result_text = t(
            "review_created",
            language,
        )
        result_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_back_to_filters_btn",
                            language,
                        ),
                        callback_data="search_filters",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="search_menu",
                    )
                ],
            ]
        )

    await state.clear()

    await show_review_flow_screen(
        event=event,
        state=state,
        menu_message_id=data.get(
            "last_menu_message_id"
        ),
        text=result_text,
        reply_markup=result_keyboard,
    )