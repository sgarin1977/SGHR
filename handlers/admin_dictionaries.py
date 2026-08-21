from aiogram import Bot, F, Router
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
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_dictionaries import (
    AdminDictionariesAccessError,
    AdminDictionariesService,
)
from services.dictionaries import (
    DictionaryServiceError,
)
from services.specialist import (
    MAX_PROFESSIONS_PER_CATEGORY,
)
from ui.texts import t


admin_dictionaries_router = Router()
normalize_language = normalize_admin_language

admin_dictionaries_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_dictionaries_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


ADMIN_CATEGORIES_PAGE_SIZE = 5
ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE = 5
ADMIN_CITIES_PAGE_SIZE = 5
ADMIN_COUNTRIES_PAGE_SIZE = 5
ADMIN_LANGUAGES_PAGE_SIZE = 5
ADMIN_MOVE_CATEGORIES_PAGE_SIZE = 5
ADMIN_MOVE_PROFESSIONS_PAGE_SIZE = 5
ADMIN_PROFESSIONS_PAGE_SIZE = 5
ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE = 5
ADMIN_SKILLS_PAGE_SIZE = 5


class AdminDictionariesFSM(StatesGroup):
    choosing_admin_move_mode = State()
    confirming_admin_multi_move = State()
    confirming_admin_skill_merge = State()
    entering_admin_category_create = State()
    entering_admin_category_number = State()
    entering_admin_category_rename = State()
    entering_admin_category_sort_order = State()
    entering_admin_category_specialist_move_numbers = State()
    entering_admin_city_create = State()
    entering_admin_city_geo_update = State()
    entering_admin_city_import = State()
    entering_admin_city_number = State()
    entering_admin_city_update = State()
    entering_admin_country_create = State()
    entering_admin_country_import = State()
    entering_admin_country_number = State()
    entering_admin_country_update = State()
    entering_admin_language_create = State()
    entering_admin_language_number = State()
    entering_admin_language_rename = State()
    entering_admin_move_target_professions = State()
    entering_admin_profession_create = State()
    entering_admin_profession_move = State()
    entering_admin_profession_number = State()
    entering_admin_profession_rename = State()
    entering_admin_skill_create = State()
    entering_admin_skill_merge = State()
    entering_admin_skill_number = State()
    entering_admin_skill_rename = State()
    entering_admin_specialist_move_numbers = State()


def admin_dictionaries_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_categories_btn", language),
                    callback_data="ADM_DICT_CATEGORIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_professions_btn", language),
                    callback_data="ADM_DICT_PROFESSIONS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skills_btn", language),
                    callback_data="ADM_DICT_SKILLS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_languages_btn", language),
                    callback_data="ADM_DICT_LANGUAGES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_geo_btn", language),
                    callback_data="ADM_DICT_GEO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
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


@admin_dictionaries_router.callback_query(F.data == "ADM_DICT")
async def admin_dictionaries_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_menu_title",
            language,
        ),
        reply_markup=admin_dictionaries_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_DICT_CATEGORIES")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_DICT_CATEGORIES:"))
async def admin_categories_dictionary(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_categories(
                platform_user_id=(
                    callback.from_user.id
                ),
                language=language,
                page=page,
                page_size=ADMIN_CATEGORIES_PAGE_SIZE,
            )
    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    visible_items = list(result.items)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        admin_category_ids=[
            str(item.category_id)
            for item in visible_items
        ],
        admin_category_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_categories_list(
            visible_items,
            language,
            page=page,
        ),
        reply_markup=admin_categories_list_keyboard(
            language,
            page=page,
            has_next=has_next,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_CREATE")
async def admin_category_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_category_create
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_category_create_prompt",
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
                            "ADM_DICT_CATEGORIES"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_category_create)
async def admin_category_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).create_category(
                platform_user_id=(
                    message.from_user.id
                ),
                title=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
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

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_category_create_prompt',
                    language,
                )}"
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
                                "ADM_DICT_CATEGORIES"
                            ),
                        )
                    ]
                ]
            ),
        )
        return

    await state.update_data(
        admin_selected_category_id=str(
            item.category_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_category_create_done',
                language,
            )}\n\n"
            f"{format_admin_category_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_category_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_OPEN_STUB")
async def admin_category_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_ids = data.get("admin_category_ids") or []
    category_page = (
        data.get("admin_category_page")
        or 0
    )
    if not category_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_category_number
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{callback.message.text or ''}\n\n"
            f"{t(
                'admin_dict_category_open_prompt',
                language,
            ).format(
                count=len(category_ids),
            )}"
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
                            "ADM_DICT_CATEGORIES:"
                            f"{category_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data.in_(
        {
            "ADM_CAT_CREATE_STUB",
            "ADM_CAT_REORDER_STUB",
        }
    )
)
async def admin_category_action_stub(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    await callback.answer(
        t("feature_disabled_beta_message", language),
        show_alert=True,
    )


def format_admin_categories_list(
    items,
    language: str,
    *,
    page: int = 0,
) -> str:
    if not items:
        return t("admin_dict_categories_empty", language)

    lines = [
        t("admin_dict_categories_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_category_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                status=item.status,
                sort_order=item.sort_order,
                professions=item.professions_count,
                specialists=item.specialists_count,
                release=item.release or "-",
            )
        )

    return "\n\n".join(lines)


def format_admin_professions_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_professions_empty", language)

    lines = [
        t("admin_dict_professions_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_profession_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                category=item.category_name,
                status=item.status,
                sort_order=item.sort_order,
                specialists=item.specialists_count,
                release=item.release or "-",
            )
        )

    return "\n\n".join(lines)


def format_admin_countries_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_countries_empty", language)

    lines = [
        t("admin_dict_countries_title", language).format(
            count=len(items)
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_country_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                status=item.status,
                cities=item.cities_count,
                professional_cabinets=(
                item.professional_cabinets_count
            ),
            )
        )

    return "\n\n".join(lines)


def admin_countries_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_country_create_btn", language),
                callback_data="ADM_COUNTRY_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_country_import_btn", language),
                callback_data="ADM_COUNTRY_IMPORT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_country_open_btn", language),
                callback_data="ADM_COUNTRY_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_DICT_GEO:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_GEO:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def read_admin_csv_payload_from_message(
    message: Message,
    state: FSMContext,
    bot: Bot,
    language: str,
) -> str | None:
    if message.text:
        return message.text

    if not message.document:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_import_file_required",
                language,
            ),
        )
        return None

    file_name = (
        message.document.file_name
        or ""
    )

    if not file_name.lower().endswith(
        ".csv"
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_import_file_invalid",
                language,
            ),
        )
        return None

    file = await bot.get_file(
        message.document.file_id
    )
    buffer = await bot.download_file(
        file.file_path
    )

    if buffer is None:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_import_file_encoding_error",
                language,
            ),
        )
        return None

    try:
        return buffer.read().decode(
            "utf-8-sig"
        )
    except UnicodeDecodeError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_import_file_encoding_error",
                language,
            ),
        )
        return None


def format_admin_dictionary_import_result(
    result,
    language: str,
) -> str:
    errors = "\n".join(
        f"- {error}"
        for error in result.errors[:10]
    )

    if not errors:
        errors = "-"

    return t("admin_dict_import_done", language).format(
        created=result.created_count,
        updated=result.updated_count,
        skipped=result.skipped_count,
        errors=errors,
    )


def format_admin_country_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_country_card", language).format(
        title=item.title,
        code=item.code,
        status=item.status,
        default_language=item.default_language or "-",
        default_currency=item.default_currency or "-",
        phone_code=item.phone_code or "-",
        cities=item.cities_count,
        professional_cabinets=(
        item.professional_cabinets_count
            ),
    )


def admin_country_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_country_cities_btn", language),
                    callback_data="ADM_COUNTRY_CITIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_create_btn", language),
                    callback_data="ADM_CITY_CREATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_import_btn", language),
                    callback_data="ADM_CITY_IMPORT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_country_update_btn", language),
                    callback_data="ADM_COUNTRY_UPDATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_country_toggle_btn", language),
                    callback_data="ADM_COUNTRY_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_GEO",
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


def format_admin_cities_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_cities_empty", language)

    lines = [
        t("admin_dict_cities_title", language).format(
            count=len(items)
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_city_row", language).format(
                number=index,
                title=item.title,
                status=item.status,
                timezone=item.timezone or "-",
                professional_cabinets=(
                item.professional_cabinets_count
            ),
            )
        )

    return "\n\n".join(lines)


def admin_cities_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_city_open_btn", language),
                callback_data="ADM_CITY_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_COUNTRY_CITIES:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_COUNTRY_CITIES:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_GEO",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_city_card(
    item,
    language: str,
) -> str:
    coordinates = "-"
    if item.latitude is not None and item.longitude is not None:
        coordinates = f"{item.latitude}, {item.longitude}"

    return t("admin_dict_city_card", language).format(
        title=item.title,
        country=item.country_name,
        status=item.status,
        timezone=item.timezone or "-",
        coordinates=coordinates,
        professional_cabinets=(
        item.professional_cabinets_count
    ),
    )


def admin_city_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_update_btn", language),
                    callback_data="ADM_CITY_UPDATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_geo_update_btn", language),
                    callback_data="ADM_CITY_GEO_UPDATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_toggle_btn", language),
                    callback_data="ADM_CITY_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_COUNTRY_CITIES",
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


def format_admin_languages_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_languages_empty", language)

    lines = [
        t("admin_dict_languages_title", language).format(
            count=len(items)
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_language_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                native_name=item.native_name or "-",
                status=item.status,
                specialist_links=item.specialist_links_count,
            )
        )

    return "\n\n".join(lines)


def admin_languages_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_language_create_btn", language),
                callback_data="ADM_LANGUAGE_CREATE",
                )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_language_open_btn", language),
                callback_data="ADM_LANGUAGE_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_DICT_LANGUAGES:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_LANGUAGES:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_language_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_language_card", language).format(
        title=item.title,
        code=item.code,
        native_name=item.native_name or "-",
        status=item.status,
        specialist_links=item.specialist_links_count,
    )


def admin_language_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_language_rename_btn", language),
                    callback_data="ADM_LANGUAGE_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_language_toggle_btn", language),
                    callback_data="ADM_LANGUAGE_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_LANGUAGES",
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


def format_admin_skills_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_skills_empty", language)

    lines = [
        t("admin_dict_skills_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_skill_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                status=item.status,
                profession_links=item.profession_links_count,
                cabinet_links=item.cabinet_links_count,
                vacancy_links=item.vacancy_links_count,
            )
        )

    return "\n\n".join(lines)


def admin_skills_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_skill_create_btn", language),
                callback_data="ADM_SKILL_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_skill_open_btn", language),
                callback_data="ADM_SKILL_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_DICT_SKILLS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_SKILLS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_skill_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_skill_card", language).format(
        title=item.title,
        code=item.code,
        status=item.status,
        profession_links=item.profession_links_count,
        cabinet_links=item.cabinet_links_count,
        vacancy_links=item.vacancy_links_count,
    )


def admin_skill_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_rename_btn", language),
                    callback_data="ADM_SKILL_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_toggle_btn", language),
                    callback_data="ADM_SKILL_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_merge_btn", language),
                    callback_data="ADM_SKILL_MERGE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_SKILLS",
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


def admin_skill_merge_prompt_keyboard(
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
                    callback_data=(
                        "ADM_SKILL_MERGE_CANCEL"
                    ),
                )
            ],
        ]
    )


def admin_skill_merge_confirm_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_skill_merge_confirm_btn",
                        language,
                    ),
                    callback_data=(
                        "ADM_SKILL_MERGE_CONFIRM"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_cancel",
                        language,
                    ),
                    callback_data=(
                        "ADM_SKILL_MERGE_CANCEL"
                    ),
                )
            ],
        ]
    )


def admin_professions_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_profession_create_btn", language),
                callback_data="ADM_PROF_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_profession_open_btn", language),
                callback_data="ADM_PROF_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_DICT_PROFESSIONS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_PROFESSIONS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_profession_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_profession_card", language).format(
        title=item.title,
        code=item.code,
        category=item.category_name,
        status=item.status,
        sort_order=item.sort_order,
        specialists=item.specialists_count,
        release=item.release or "-",
    )


def admin_profession_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_rename_btn", language),
                    callback_data="ADM_PROF_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_move_btn", language),
                    callback_data="ADM_PROF_MOVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_toggle_btn", language),
                    callback_data="ADM_PROF_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_archive_btn", language),
                    callback_data="ADM_PROF_ARCHIVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_specialists_btn", language),
                    callback_data="ADM_PROF_SPECIALISTS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_PROFESSIONS",
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


def admin_categories_list_keyboard(
    language: str,
    *,
    page: int = 0,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_category_create_btn", language),
                callback_data="ADM_CAT_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_category_open_btn", language),
                callback_data="ADM_CAT_OPEN_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_category_reorder_btn", language),
                callback_data="ADM_CAT_REORDER_STUB",
            )
        ],
    ]

    paging_row = []
    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_DICT_CATEGORIES:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_CATEGORIES:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_dictionaries_router.callback_query(F.data == "ADM_DICT_PROFESSIONS")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_DICT_PROFESSIONS:"))
async def admin_professions_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_professions(
                platform_user_id=(
                    callback.from_user.id
                ),
                language=language,
                page=page,
                page_size=ADMIN_PROFESSIONS_PAGE_SIZE,
            )
    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    visible_items = list(result.items)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        admin_profession_ids=[
            str(item.profession_id)
            for item in visible_items
        ],
        admin_profession_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_professions_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_professions_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_OPEN")
async def admin_profession_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_ids = data.get("admin_profession_ids") or []
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )
    if not profession_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_profession_number
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{callback.message.text or ''}\n\n"
            f"{t(
                'admin_dict_profession_open_prompt',
                language,
            ).format(
                count=len(profession_ids),
            )}"
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
                            "ADM_DICT_PROFESSIONS:"
                            f"{profession_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_profession_number)
async def admin_profession_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    profession_ids = (
        data.get("admin_profession_ids")
        or []
    )
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_PROFESSIONS:"
                        f"{profession_page}"
                    ),
                )
            ]
        ]
    )

    prompt_text = t(
        "admin_dict_profession_open_prompt",
        language,
    ).format(
        count=len(profession_ids),
    )

    try:
        index = (
            int(
                (message.text or "").strip()
            )
            - 1
        )
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_profession_open_bad_number',
                    language,
                ).format(
                    count=len(profession_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        index < 0
        or index >= len(profession_ids)
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_profession_open_bad_number',
                    language,
                ).format(
                    count=len(profession_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    profession_id = profession_ids[index]

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).get_profession(
                platform_user_id=(
                    message.from_user.id
                ),
                profession_id=profession_id,
                language=language,
            )
    except AdminDictionariesAccessError:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not item:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_profession_id=profession_id,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_profession_card(
            item,
            language,
        ),
        reply_markup=admin_profession_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_CREATE")
async def admin_profession_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    category_page = (
        data.get("admin_category_page")
        or 0
    )
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )

    if category_id:
        prompt_text = t(
            "admin_dict_profession_create_prompt_in_category",
            language,
        )
        back_callback = (
            "ADM_DICT_CATEGORIES:"
            f"{category_page}"
        )
    else:
        prompt_text = t(
            "admin_dict_profession_create_prompt_with_category",
            language,
        )
        back_callback = (
            "ADM_DICT_PROFESSIONS:"
            f"{profession_page}"
        )

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_profession_create
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=prompt_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data=back_callback,
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_profession_create)
async def admin_profession_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    category_id = data.get(
        "admin_selected_category_id"
    )
    category_page = (
        data.get("admin_category_page")
        or 0
    )
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )

    if category_id:
        prompt_text = t(
            "admin_dict_profession_create_prompt_in_category",
            language,
        )
        back_callback = (
            "ADM_DICT_CATEGORIES:"
            f"{category_page}"
        )
    else:
        prompt_text = t(
            "admin_dict_profession_create_prompt_with_category",
            language,
        )
        back_callback = (
            "ADM_DICT_PROFESSIONS:"
            f"{profession_page}"
        )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=back_callback,
                )
            ]
        ]
    )

    raw_text = message.text or ""
    category_code = None
    title = raw_text

    if not category_id:
        parts = raw_text.split("|", 1)

        if len(parts) != 2:
            await replace_admin_input_screen(
                message=message,
                state=state,
                text=(
                    f"{t(
                        'admin_dict_profession_create_format_error',
                        language,
                    )}\n\n"
                    f"{prompt_text}"
                ),
                reply_markup=back_keyboard,
            )
            return

        category_code = parts[0].strip()
        title = parts[1].strip()

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).create_profession(
                platform_user_id=(
                    message.from_user.id
                ),
                category_id=category_id,
                category_code=category_code,
                title=title,
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_profession_id=str(
            item.profession_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_profession_create_done',
                language,
            )}\n\n"
            f"{format_admin_profession_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_profession_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_RENAME")
async def admin_profession_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )
    if not data.get("admin_selected_profession_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_profession_rename
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_profession_rename_prompt",
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
                            "ADM_DICT_PROFESSIONS:"
                            f"{profession_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_profession_rename)
async def admin_profession_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    profession_id = data.get(
        "admin_selected_profession_id"
    )
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_PROFESSIONS:"
                        f"{profession_page}"
                    ),
                )
            ]
        ]
    )

    if not profession_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).rename_profession(
                platform_user_id=(
                    message.from_user.id
                ),
                profession_id=profession_id,
                title=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_profession_rename_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_profession_id=str(
            item.profession_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_profession_rename_done',
                language,
            )}\n\n"
            f"{format_admin_profession_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_profession_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_MOVE")
async def admin_profession_move_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )
    if not data.get("admin_selected_profession_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_profession_move
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_profession_move_prompt",
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
                            "ADM_DICT_PROFESSIONS:"
                            f"{profession_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_profession_move)
async def admin_profession_move_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    profession_id = data.get(
        "admin_selected_profession_id"
    )
    profession_page = (
        data.get("admin_profession_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_PROFESSIONS:"
                        f"{profession_page}"
                    ),
                )
            ]
        ]
    )

    if not profession_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).move_profession_to_category(
                platform_user_id=(
                    message.from_user.id
                ),
                profession_id=profession_id,
                category_code=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_profession_move_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_profession_id=str(
            item.profession_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_profession_move_done',
                language,
            )}\n\n"
            f"{format_admin_profession_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_profession_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_TOGGLE")
async def admin_profession_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_profession_visibility(
                platform_user_id=(
                    callback.from_user.id
                ),
                profession_id=profession_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )

    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                'admin_dict_profession_visibility_done',
                language,
            )}\n\n"
            f"{format_admin_profession_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_profession_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_ARCHIVE")
async def admin_profession_archive(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).toggle_profession_archive(
                platform_user_id=(
                    callback.from_user.id
                ),
                profession_id=profession_id,
                language=language,
            )

        item = result.item
        done_text_key = (
            "admin_dict_profession_archive_done"
            if result.archived
            else (
                "admin_dict_profession_unarchive_done"
            )
        )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(done_text_key, language)}\n\n"
            f"{format_admin_profession_card(item, language)}"
        ),
        reply_markup=admin_profession_card_keyboard(language),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_SPEC_MOVE_ALL")
async def admin_specialist_move_all(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            specialist_ids = list(
                await AdminDictionariesService(
                    session
                ).list_profession_specialist_ids(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    profession_id=profession_id,
                )
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_specialist_move_ids=specialist_ids,
        admin_move_source_type="profession",
        admin_move_source_id=profession_id,
        admin_move_specialist_ids=specialist_ids,
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        edit=True,
    )
    await callback.answer()


@admin_dictionaries_router.callback_query(F.data == "ADM_SPEC_MOVE_SELECT")
async def admin_specialist_move_select_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    specialist_ids = data.get("admin_profession_specialist_ids") or []
    page = int(
        data.get(
            "admin_profession_specialists_page"
        )
        or 0
    )
    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_specialist_move_numbers
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_specialist_move_select_prompt",
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
                            "ADM_PROF_SPECIALISTS:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_specialist_move_numbers)
async def admin_specialist_move_numbers_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    data = await state.get_data()
    specialist_ids = data.get("admin_profession_specialist_ids") or []
    page = int(
        data.get(
            "admin_profession_specialists_page"
        )
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_PROF_SPECIALISTS:"
                        f"{page}"
                    ),
                )
            ]
        ]
    )
    if not specialist_ids:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_specialist_move_empty",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    raw_numbers = [
        item.strip()
        for item in (message.text or "").replace(";", ",").split(",")
        if item.strip()
    ]

    selected_indexes = []

    try:
        selected_indexes = [
            int(item) - 1
            for item in raw_numbers
        ]
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_specialist_move_bad_numbers",
                language,
            ).format(
                count=len(specialist_ids),
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        not selected_indexes
        or any(index < 0 or index >= len(specialist_ids) for index in selected_indexes)
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_specialist_move_bad_numbers",
                language,
            ).format(
                count=len(specialist_ids),
            ),
            reply_markup=back_keyboard,
        )
        return

    selected_specialist_ids = [
        specialist_ids[index]
        for index in dict.fromkeys(selected_indexes)
    ]

    await state.update_data(
        admin_selected_specialist_move_ids=(
            selected_specialist_ids
        ),
        admin_move_source_type="profession",
        admin_move_source_id=data.get(
            "admin_selected_profession_id"
        ),
        admin_move_specialist_ids=(
            selected_specialist_ids
        ),
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        message,
        state,
        language,
        platform_user_id=message.from_user.id,
    )


def admin_multi_move_confirm_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_confirm_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="ADM_MULTI_BACK_MODE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MOVE_CANCEL",
                )
            ],
        ]
    )


def admin_multi_move_mode_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_move_mode_replace_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MODE:replace",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_move_mode_add_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MODE:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="ADM_MULTI_BACK_PROF",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MOVE_CANCEL",
                )
            ],
        ]
    )


def admin_multi_move_profession_keyboard(
    items,
    selected_ids: list[str],
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    selected_set = set(selected_ids)
    rows = []

    for index, item in enumerate(items):
        item_id = str(item.profession_id)
        marker = "✓ " if item_id in selected_set else ""

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{item.title}",
                    callback_data=f"ADM_MULTI_PROF:{index}",
                )
            ]
        )

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_MULTI_PROF_PAGE:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_MULTI_PROF_PAGE:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_move_professions_done_btn",
                    language,
                ),
                callback_data="ADM_MULTI_PROF_DONE",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_move_back_categories_btn",
                    language,
                ),
                callback_data="ADM_MULTI_BACK_CAT",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_specialist_move_cancel_btn",
                    language,
                ),
                callback_data="ADM_MULTI_MOVE_CANCEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin_multi_move_professions(
    message: Message,
    state: FSMContext,
    language: str,
    *,
    platform_user_id: int | str,
    page: int = 0,
    edit: bool = False,
):
    data = await state.get_data()
    category_id = data.get(
        "admin_move_target_category_id"
    )
    selected_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []
    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_MULTI_MOVE_CANCEL"
                    ),
                )
            ]
        ]
    )
    if not category_id:
        await message.edit_text(
            t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=cancel_keyboard,
        )
        await state.update_data(
            last_menu_message_id=message.message_id,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).get_move_target_professions(
                platform_user_id=platform_user_id,
                category_id=category_id,
                language=language,
                page=page,
                page_size=(
                    ADMIN_MOVE_PROFESSIONS_PAGE_SIZE
                ),
            )
    except AdminDictionariesAccessError:
        await state.set_state(None)
        await message.edit_text(
            t(
                "admin_access_denied",
                language,
            ),
            reply_markup=cancel_keyboard,
        )
        await state.update_data(
            last_menu_message_id=(
                message.message_id
            ),
        )
        return
    except DictionaryServiceError as exc:
        await message.edit_text(
            t(exc.text_key, language),
            reply_markup=cancel_keyboard,
        )
        await state.update_data(
            last_menu_message_id=(
                message.message_id
            ),
        )
        return

    category = result.category
    professions = list(result.professions)
    visible_professions = list(
        result.visible_professions
    )
    page = result.page
    has_next = result.has_next

    if not category:
        await message.edit_text(
            t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=cancel_keyboard,
        )
        await state.update_data(
            last_menu_message_id=message.message_id,
        )
        return

    await state.update_data(
        admin_move_available_profession_ids=[
            str(profession.profession_id)
            for profession in visible_professions
        ],
        admin_move_professions_page=page,
    )
    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_move_target_professions
    )

    selected_titles = [
        profession.title
        for profession in professions
        if str(profession.profession_id)
        in set(selected_ids)
    ]

    if selected_titles:
        selected_text = t(
            "admin_dict_move_selected_professions",
            language,
        ).format(
            items=", ".join(selected_titles),
        )
    else:
        selected_text = t(
            "admin_dict_move_selected_professions_empty",
            language,
        )

    category_text = t(
        "admin_dict_move_selected_category",
        language,
    ).format(
        category=category.title,
    )

    screen_text = (
        f"{t('admin_dict_move_choose_professions', language)}"
        f"\n\n{category_text}"
        f"\n{selected_text}"
    )
    keyboard = admin_multi_move_profession_keyboard(
        visible_professions,
        selected_ids,
        page=page,
        has_next=has_next,
        language=language,
    )

    if edit:
        await message.edit_text(
            screen_text,
            reply_markup=keyboard,
        )
    else:
        await message.edit_text(
            screen_text,
            reply_markup=keyboard,
        )

    await state.update_data(
        last_menu_message_id=message.message_id,
    )


@admin_dictionaries_router.callback_query(
    F.data.startswith("ADM_MULTI_PROF_PAGE:")
)
async def admin_multi_move_profession_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        page=page,
        edit=True,
    )
    await callback.answer()


@admin_dictionaries_router.callback_query(
    F.data.startswith("ADM_MULTI_PROF:")
)
async def admin_multi_move_profession_toggle(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    profession_ids = data.get(
        "admin_move_available_profession_ids"
    ) or []
    selected_ids = list(
        data.get(
            "admin_move_selected_profession_ids"
        ) or []
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
        profession_id = profession_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if profession_id in selected_ids:
        selected_ids = [
            item
            for item in selected_ids
            if item != profession_id
        ]
    else:
        if (
            len(selected_ids)
            >= MAX_PROFESSIONS_PER_CATEGORY
        ):
            await callback.answer(
                t(
                    "spec_profession_limit_per_category",
                    language,
                ),
                show_alert=True,
            )
            return

        selected_ids.append(profession_id)

    await state.update_data(
        admin_move_selected_profession_ids=selected_ids
    )

    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        page=int(
            data.get("admin_move_professions_page") or 0
        ),
        edit=True,
    )
    await callback.answer()


@admin_dictionaries_router.callback_query(
    F.data == "ADM_MULTI_PROF_DONE"
)
async def admin_multi_move_professions_done(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    selected_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []

    if not selected_ids:
        await callback.answer(
            t("spec_profession_select_one", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.choosing_admin_move_mode
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_move_mode_prompt",
            language,
        ),
        reply_markup=admin_multi_move_mode_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_MULTI_BACK_CAT"
)
async def admin_multi_move_back_categories(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        edit=True,
    )
    await callback.answer()


@admin_dictionaries_router.callback_query(
    F.data == "ADM_MULTI_BACK_MODE"
)
async def admin_multi_move_back_mode(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(
        AdminDictionariesFSM.choosing_admin_move_mode
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_move_mode_prompt",
            language,
        ),
        reply_markup=admin_multi_move_mode_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_MULTI_CONFIRM"
)
async def admin_multi_move_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).execute_multi_move(
                platform_user_id=(
                    callback.from_user.id
                ),
                source_type=data.get(
                    "admin_move_source_type"
                ),
                source_id=data.get(
                    "admin_move_source_id"
                ),
                target_category_id=data.get(
                    "admin_move_target_category_id"
                ),
                target_profession_ids=data.get(
                    "admin_move_selected_profession_ids"
                ) or [],
                specialist_ids=data.get(
                    "admin_move_specialist_ids"
                ) or [],
                mode=data.get("admin_move_mode"),
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    mode_key = (
        "admin_dict_move_mode_replace_label"
        if result.mode == "replace"
        else "admin_dict_move_mode_add_label"
    )

    await clear_admin_multi_move_state(
        state
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_multi_move_done",
            language,
        ).format(
            target_category=result.target_category.title,
            target_professions=", ".join(
                profession.title
                for profession
                in result.target_professions
            ),
            mode=t(mode_key, language),
            specialists_count=(
                result.requested_specialists_count
            ),
            created_count=result.created_cabinets_count,
            reactivated_count=(
                result.reactivated_cabinets_count
            ),
            existing_count=result.existing_cabinets_count,
            archived_count=(
                result.archived_old_cabinets_count
            ),
            synchronized_count=(
                result.synchronized_primary_count
            ),
            missing_count=(
                result.missing_specialists_count
            ),
        ),
        reply_markup=admin_dictionaries_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data.startswith("ADM_MULTI_MODE:")
)
async def admin_multi_move_mode_selected(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    mode = (callback.data or "").split(":", 1)[1]

    if mode not in {"replace", "add"}:
        await callback.answer(
            t("admin_dict_move_mode_invalid", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    source_type = data.get("admin_move_source_type")
    source_id = data.get("admin_move_source_id")
    category_id = data.get(
        "admin_move_target_category_id"
    )
    profession_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []
    specialist_ids = data.get(
        "admin_move_specialist_ids"
    ) or []

    try:
        async with get_session() as session:
            preview = await AdminDictionariesService(
                session
            ).preview_multi_move(
                platform_user_id=(
                    callback.from_user.id
                ),
                source_type=source_type,
                source_id=source_id,
                target_category_id=category_id,
                target_profession_ids=profession_ids,
                specialist_ids=specialist_ids,
                mode=mode,
                language=language,
            )
    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_move_mode=mode
    )
    await state.set_state(
        AdminDictionariesFSM.confirming_admin_multi_move
    )

    mode_key = (
        "admin_dict_move_mode_replace_label"
        if mode == "replace"
        else "admin_dict_move_mode_add_label"
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_multi_move_preview",
            language,
        ).format(
            source=preview.source_title,
            target_category=(
                preview.target_category.title
            ),
            target_professions=", ".join(
                profession.title
                for profession
                in preview.target_professions
            ),
            mode=t(
                mode_key,
                language,
            ),
            specialists_count=len(
                preview.selected_specialists
            ),
        ),
        reply_markup=admin_multi_move_confirm_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_MULTI_BACK_PROF"
)
async def admin_multi_move_back_professions(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        edit=True,
    )
    await callback.answer()


def admin_multi_move_category_keyboard(
    items,
    *,
    selected_category_id: str | None,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items):
        item_id = str(item.category_id)
        marker = (
            "✓ "
            if item_id == selected_category_id
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{item.title}",
                    callback_data=f"ADM_MULTI_CAT:{index}",
                )
            ]
        )

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_MULTI_CAT_PAGE:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_MULTI_CAT_PAGE:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_specialist_move_cancel_btn",
                    language,
                ),
                callback_data="ADM_MULTI_MOVE_CANCEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin_multi_move_categories(
    message: Message,
    state: FSMContext,
    language: str,
    *,
    platform_user_id: int | str,
    page: int = 0,
    edit: bool = False,
):
    data = await state.get_data()
    selected_category_id = data.get(
        "admin_move_target_category_id"
    )

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_move_target_categories(
                platform_user_id=platform_user_id,
                language=language,
                page=page,
                page_size=(
                    ADMIN_MOVE_CATEGORIES_PAGE_SIZE
                ),
            )
    except AdminDictionariesAccessError:
        await state.set_state(None)
        access_text = t(
            "admin_access_denied",
            language,
        )
        access_keyboard = (
            admin_dictionaries_keyboard(
                language
            )
        )

        if edit:
            await message.edit_text(
                access_text,
                reply_markup=access_keyboard,
            )
            await state.update_data(
                last_menu_message_id=(
                    message.message_id
                ),
            )
        else:
            await replace_admin_input_screen(
                message=message,
                state=state,
                text=access_text,
                reply_markup=access_keyboard,
            )
        return

    visible_categories = list(result.items)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        admin_move_available_category_ids=[
            str(category.category_id)
            for category in visible_categories
        ],
        admin_move_categories_page=page,
    )
    await state.set_state(
        AdminDictionariesFSM.choosing_admin_move_mode
    )

    keyboard = admin_multi_move_category_keyboard(
        visible_categories,
        selected_category_id=selected_category_id,
        page=page,
        has_next=has_next,
        language=language,
    )
    screen_text = t(
        "admin_dict_move_choose_category",
        language,
    )

    if edit:
        await message.edit_text(
            screen_text,
            reply_markup=keyboard,
        )
        await state.update_data(
            last_menu_message_id=(
                message.message_id
            ),
        )
        return

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=screen_text,
        reply_markup=keyboard,
    )


@admin_dictionaries_router.callback_query(
    F.data.startswith("ADM_MULTI_CAT_PAGE:")
)
async def admin_multi_move_category_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        page=page,
        edit=True,
    )
    await callback.answer()


@admin_dictionaries_router.callback_query(
    F.data.startswith("ADM_MULTI_CAT:")
)
async def admin_multi_move_category_selected(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    previous_category_id = data.get(
        "admin_move_target_category_id"
    )
    previous_selected_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []
    category_ids = data.get(
        "admin_move_available_category_ids"
    ) or []

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
        category_id = category_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            target = await AdminDictionariesService(
                session
            ).get_move_target_professions(
                platform_user_id=(
                    callback.from_user.id
                ),
                category_id=category_id,
                language=language,
                page=0,
                page_size=(
                    ADMIN_MOVE_PROFESSIONS_PAGE_SIZE
                ),
            )
            professions = list(
                target.professions
            )
    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    selected_profession_ids = (
        previous_selected_ids
        if previous_category_id == category_id
        else []
    )

    await state.update_data(
        admin_move_target_category_id=category_id,
        admin_move_available_profession_ids=[
            str(profession.profession_id)
            for profession in professions
        ],
        admin_move_selected_profession_ids=(
            selected_profession_ids
        ),
    )
    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        page=0,
        edit=True,
    )
    await callback.answer()


async def clear_admin_multi_move_state(
    state: FSMContext,
) -> None:
    await state.set_state(None)
    await state.update_data(
        admin_selected_category_specialist_move_ids=[],
        admin_selected_specialist_move_ids=[],
        admin_move_source_type=None,
        admin_move_source_id=None,
        admin_move_specialist_ids=[],
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_available_category_ids=[],
        admin_move_categories_page=0,
        admin_move_target_profession_ids=[],
        admin_move_target_professions=[],
        admin_move_available_profession_ids=[],
        admin_move_selected_profession_ids=[],
        admin_move_professions_page=0,
        admin_move_mode=None,
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_MULTI_MOVE_CANCEL"
)
async def admin_multi_move_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await clear_admin_multi_move_state(
        state
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_cancelled",
            language,
        ),
        reply_markup=admin_dictionaries_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_PROF_SPECIALISTS")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_PROF_SPECIALISTS:"))
async def admin_profession_specialists(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_profession_specialists(
                platform_user_id=(
                    callback.from_user.id
                ),
                profession_id=profession_id,
                page=page,
                page_size=(
                    ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    page = result.page
    has_next = result.has_next
    visible_items = list(result.items)

    await state.update_data(
        admin_profession_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        admin_profession_specialists_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_profession_specialists_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_profession_specialists_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_DICT_GEO")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_DICT_GEO:"))
async def admin_geo_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_countries(
                platform_user_id=(
                    callback.from_user.id
                ),
                language=language,
                page=page,
                page_size=(
                    ADMIN_COUNTRIES_PAGE_SIZE
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    page = result.page
    has_next = result.has_next
    visible_items = list(result.items)

    await state.update_data(
        admin_country_ids=[
            str(item.country_id)
            for item in visible_items
        ],
        admin_country_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_countries_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_countries_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_COUNTRY_CREATE")
async def admin_country_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    country_page = (
        data.get("admin_country_page")
        or 0
    )

    await state.set_state(
        AdminDictionariesFSM.entering_admin_country_create
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_country_create_prompt",
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
                            "ADM_DICT_GEO:"
                            f"{country_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_country_create)
async def admin_country_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    country_page = (
        data.get("admin_country_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_GEO:"
                        f"{country_page}"
                    ),
                )
            ]
        ]
    )

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).create_country(
                platform_user_id=(
                    message.from_user.id
                ),
                payload=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_country_create_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_country_id=str(
            item.country_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_country_create_done',
                language,
            )}\n\n"
            f"{format_admin_country_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_country_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_COUNTRY_IMPORT")
async def admin_country_import_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    country_page = (
        data.get("admin_country_page")
        or 0
    )

    await state.set_state(
        AdminDictionariesFSM.entering_admin_country_import
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_country_import_prompt",
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
                            "ADM_DICT_GEO:"
                            f"{country_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_country_import)
async def admin_country_import_receive(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    country_page = (
        data.get("admin_country_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_GEO:"
                        f"{country_page}"
                    ),
                )
            ]
        ]
    )

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    message.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    payload = (
        await read_admin_csv_payload_from_message(
            message,
            state,
            bot,
            language,
        )
    )

    if payload is None:
        return

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).import_countries(
                platform_user_id=(
                    message.from_user.id
                ),
                payload=payload,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_country_import_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_dictionary_import_result(
            result,
            language,
        ),
        reply_markup=back_keyboard,
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_COUNTRY_OPEN")
async def admin_country_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    country_ids = data.get("admin_country_ids") or []
    country_page = (
        data.get("admin_country_page")
        or 0
    )
    if not country_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_country_number
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{callback.message.text or ''}\n\n"
            f"{t(
                'admin_dict_country_open_prompt',
                language,
            ).format(
                count=len(country_ids),
            )}"
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
                            "ADM_DICT_GEO:"
                            f"{country_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_country_number)
async def admin_country_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    country_ids = (
        data.get("admin_country_ids")
        or []
    )
    country_page = (
        data.get("admin_country_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_GEO:"
                        f"{country_page}"
                    ),
                )
            ]
        ]
    )

    prompt_text = t(
        "admin_dict_country_open_prompt",
        language,
    ).format(
        count=len(country_ids),
    )

    try:
        selected_index = (
            int(
                (message.text or "").strip()
            )
            - 1
        )
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_country_open_bad_number',
                    language,
                ).format(
                    count=len(country_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        selected_index < 0
        or selected_index >= len(
            country_ids
        )
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_country_open_bad_number',
                    language,
                ).format(
                    count=len(country_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    selected_country_id = country_ids[
        selected_index
    ]

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).get_country(
                platform_user_id=(
                    message.from_user.id
                ),
                country_id=selected_country_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not item:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_country_id=str(
            item.country_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_country_card(
            item,
            language,
        ),
        reply_markup=admin_country_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_COUNTRY_UPDATE")
async def admin_country_update_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")
    country_page = (
        data.get("admin_country_page")
        or 0
    )
    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_country_update
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_country_update_prompt",
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
                            "ADM_DICT_GEO:"
                            f"{country_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_country_update)
async def admin_country_update_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    country_id = data.get(
        "admin_selected_country_id"
    )
    country_page = (
        data.get("admin_country_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_GEO:"
                        f"{country_page}"
                    ),
                )
            ]
        ]
    )

    if not country_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).update_country(
                platform_user_id=(
                    message.from_user.id
                ),
                country_id=country_id,
                payload=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_country_update_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_country_id=str(
            item.country_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_country_update_done',
                language,
            )}\n\n"
            f"{format_admin_country_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_country_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_COUNTRY_TOGGLE")
async def admin_country_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_country_visibility(
                platform_user_id=(
                    callback.from_user.id
                ),
                country_id=country_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(
                exc.text_key,
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_country_id=str(
            item.country_id
        ),
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                'admin_dict_country_visibility_done',
                language,
            )}\n\n"
            f"{format_admin_country_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_country_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CITY_CREATE")
async def admin_city_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")
    city_page = (
        data.get("admin_city_page")
        or 0
    )
    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_city_create
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_city_create_prompt",
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
                            "ADM_COUNTRY_CITIES:"
                            f"{city_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_city_create)
async def admin_city_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    country_id = data.get(
        "admin_selected_country_id"
    )
    city_page = (
        data.get("admin_city_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_COUNTRY_CITIES:"
                        f"{city_page}"
                    ),
                )
            ]
        ]
    )

    if not country_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).create_city(
                platform_user_id=(
                    message.from_user.id
                ),
                country_id=country_id,
                payload=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_city_create_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_city_id=str(
            item.city_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_city_create_done',
                language,
            )}\n\n"
            f"{format_admin_city_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_city_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CITY_IMPORT")
async def admin_city_import_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")
    city_page = (
        data.get("admin_city_page")
        or 0
    )
    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_city_import
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_city_import_prompt",
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
                            "ADM_COUNTRY_CITIES:"
                            f"{city_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_city_import)
async def admin_city_import_receive(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    country_id = data.get(
        "admin_selected_country_id"
    )
    city_page = (
        data.get("admin_city_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_COUNTRY_CITIES:"
                        f"{city_page}"
                    ),
                )
            ]
        ]
    )

    if not country_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    message.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    payload = (
        await read_admin_csv_payload_from_message(
            message,
            state,
            bot,
            language,
        )
    )

    if payload is None:
        return

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).import_cities(
                platform_user_id=(
                    message.from_user.id
                ),
                country_id=country_id,
                payload=payload,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_city_import_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_country_id=country_id,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_dictionary_import_result(
            result,
            language,
        ),
        reply_markup=back_keyboard,
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_COUNTRY_CITIES")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_COUNTRY_CITIES:"))
async def admin_country_cities(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_cities(
                platform_user_id=(
                    callback.from_user.id
                ),
                country_id=country_id,
                language=language,
                page=page,
                page_size=(
                    ADMIN_CITIES_PAGE_SIZE
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    page = result.page
    has_next = result.has_next
    visible_items = list(result.items)

    await state.update_data(
        admin_city_ids=[
            str(item.city_id)
            for item in visible_items
        ],
        admin_city_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_cities_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_cities_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CITY_UPDATE")
async def admin_city_update_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")
    city_page = (
        data.get("admin_city_page")
        or 0
    )
    if not city_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_city_update
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_city_update_prompt",
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
                            "ADM_COUNTRY_CITIES:"
                            f"{city_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_city_update)
async def admin_city_update_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    city_id = data.get(
        "admin_selected_city_id"
    )
    city_page = (
        data.get("admin_city_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_COUNTRY_CITIES:"
                        f"{city_page}"
                    ),
                )
            ]
        ]
    )

    if not city_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).update_city(
                platform_user_id=(
                    message.from_user.id
                ),
                city_id=city_id,
                payload=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_city_update_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_city_id=str(
            item.city_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_city_update_done',
                language,
            )}\n\n"
            f"{format_admin_city_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_city_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CITY_GEO_UPDATE")
async def admin_city_geo_update_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")
    city_page = (
        data.get("admin_city_page")
        or 0
    )
    if not city_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_city_geo_update
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_city_geo_update_prompt",
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
                            "ADM_COUNTRY_CITIES:"
                            f"{city_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_city_geo_update)
async def admin_city_geo_update_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    city_id = data.get(
        "admin_selected_city_id"
    )
    city_page = (
        data.get("admin_city_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_COUNTRY_CITIES:"
                        f"{city_page}"
                    ),
                )
            ]
        ]
    )

    if not city_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).update_city_geo(
                platform_user_id=(
                    message.from_user.id
                ),
                city_id=city_id,
                payload=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_city_geo_update_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_city_id=str(
            item.city_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_city_geo_update_done',
                language,
            )}\n\n"
            f"{format_admin_city_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_city_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CITY_TOGGLE")
async def admin_city_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")

    if not city_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_city_visibility(
                platform_user_id=(
                    callback.from_user.id
                ),
                city_id=city_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(
                exc.text_key,
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_city_id=str(
            item.city_id
        ),
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                'admin_dict_city_visibility_done',
                language,
            )}\n\n"
            f"{format_admin_city_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_city_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CITY_OPEN")
async def admin_city_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    city_ids = data.get("admin_city_ids") or []
    city_page = (
        data.get("admin_city_page")
        or 0
    )
    if not city_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_city_number
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{callback.message.text or ''}\n\n"
            f"{t(
                'admin_dict_city_open_prompt',
                language,
            ).format(
                count=len(city_ids),
            )}"
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
                            "ADM_COUNTRY_CITIES:"
                            f"{city_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_city_number)
async def admin_city_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    city_ids = data.get("admin_city_ids") or []
    city_page = (
        data.get("admin_city_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_COUNTRY_CITIES:"
                        f"{city_page}"
                    ),
                )
            ]
        ]
    )

    prompt_text = t(
        "admin_dict_city_open_prompt",
        language,
    ).format(
        count=len(city_ids),
    )

    try:
        selected_index = (
            int(
                (message.text or "").strip()
            )
            - 1
        )
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_city_open_bad_number',
                    language,
                ).format(
                    count=len(city_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        selected_index < 0
        or selected_index >= len(city_ids)
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_city_open_bad_number',
                    language,
                ).format(
                    count=len(city_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    selected_city_id = city_ids[selected_index]

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).get_city(
                platform_user_id=(
                    message.from_user.id
                ),
                city_id=selected_city_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not item:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_city_id=str(
            item.city_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_city_card(
            item,
            language,
        ),
        reply_markup=admin_city_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_DICT_LANGUAGES")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_DICT_LANGUAGES:"))
async def admin_languages_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_languages(
                platform_user_id=(
                    callback.from_user.id
                ),
                page=page,
                page_size=(
                    ADMIN_LANGUAGES_PAGE_SIZE
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    page = result.page
    has_next = result.has_next
    visible_items = list(result.items)

    await state.update_data(
        admin_language_codes=[
            item.code
            for item in visible_items
        ],
        admin_language_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_languages_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_languages_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_LANGUAGE_CREATE")
async def admin_language_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    language_page = (
        data.get("admin_language_page")
        or 0
    )
    await state.set_state(
        AdminDictionariesFSM.entering_admin_language_create
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_language_create_prompt",
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
                            "ADM_DICT_LANGUAGES:"
                            f"{language_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_language_create)
async def admin_language_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    language_page = (
        data.get("admin_language_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_LANGUAGES:"
                        f"{language_page}"
                    ),
                )
            ]
        ]
    )

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).create_language(
                platform_user_id=(
                    message.from_user.id
                ),
                payload=message.text or "",
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_language_create_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_language_code=item.code,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_language_create_done',
                language,
            )}\n\n"
            f"{format_admin_language_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_language_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_LANGUAGE_TOGGLE")
async def admin_language_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    language_code = data.get("admin_selected_language_code")

    if not language_code:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_language_visibility(
                platform_user_id=(
                    callback.from_user.id
                ),
                code=language_code,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(
                exc.text_key,
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_language_code=item.code,
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                'admin_dict_language_visibility_done',
                language,
            )}\n\n"
            f"{format_admin_language_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_language_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_LANGUAGE_RENAME")
async def admin_language_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    language_code = data.get("admin_selected_language_code")
    language_page = (
        data.get("admin_language_page")
        or 0
    )
    if not language_code:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_language_rename
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_language_rename_prompt",
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
                            "ADM_DICT_LANGUAGES:"
                            f"{language_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_language_rename)
async def admin_language_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    language_code = data.get(
        "admin_selected_language_code"
    )
    language_page = (
        data.get("admin_language_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_LANGUAGES:"
                        f"{language_page}"
                    ),
                )
            ]
        ]
    )

    if not language_code:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).rename_language(
                platform_user_id=(
                    message.from_user.id
                ),
                code=language_code,
                payload=message.text or "",
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_language_rename_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_language_code=item.code,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_language_rename_done',
                language,
            )}\n\n"
            f"{format_admin_language_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_language_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_LANGUAGE_OPEN")
async def admin_language_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    language_codes = data.get("admin_language_codes") or []
    language_page = (
        data.get("admin_language_page")
        or 0
    )
    if not language_codes:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_language_number
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{callback.message.text or ''}\n\n"
            f"{t(
                'admin_dict_language_open_prompt',
                language,
            ).format(
                count=len(language_codes),
            )}"
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
                            "ADM_DICT_LANGUAGES:"
                            f"{language_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_language_number)
async def admin_language_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    language_codes = (
        data.get("admin_language_codes")
        or []
    )
    language_page = (
        data.get("admin_language_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_LANGUAGES:"
                        f"{language_page}"
                    ),
                )
            ]
        ]
    )

    prompt_text = t(
        "admin_dict_language_open_prompt",
        language,
    ).format(
        count=len(language_codes),
    )

    try:
        selected_index = (
            int(
                (message.text or "").strip()
            )
            - 1
        )
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_language_open_bad_number',
                    language,
                ).format(
                    count=len(language_codes),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        selected_index < 0
        or selected_index >= len(
            language_codes
        )
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_language_open_bad_number',
                    language,
                ).format(
                    count=len(language_codes),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    selected_code = language_codes[
        selected_index
    ]

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).get_language(
                platform_user_id=(
                    message.from_user.id
                ),
                code=selected_code,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not item:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_language_code=item.code,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_language_card(
            item,
            language,
        ),
        reply_markup=admin_language_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_DICT_SKILLS")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_DICT_SKILLS:"))
async def admin_skills_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_skills(
                platform_user_id=(
                    callback.from_user.id
                ),
                language=language,
                page=page,
                page_size=(
                    ADMIN_SKILLS_PAGE_SIZE
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    page = result.page
    has_next = result.has_next
    visible_items = list(result.items)

    await state.update_data(
        admin_skill_ids=[
            str(item.skill_id)
            for item in visible_items
        ],
        admin_skill_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_skills_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_skills_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_SKILL_OPEN")
async def admin_skill_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_ids = data.get("admin_skill_ids") or []
    skill_page = (
        data.get("admin_skill_page")
        or 0
    )
    if not skill_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_skill_number
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{callback.message.text or ''}\n\n"
            f"{t(
                'admin_dict_skill_open_prompt',
                language,
            ).format(
                count=len(skill_ids),
            )}"
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
                            "ADM_DICT_SKILLS:"
                            f"{skill_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_skill_number)
async def admin_skill_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    skill_ids = data.get("admin_skill_ids") or []
    skill_page = (
        data.get("admin_skill_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_SKILLS:"
                        f"{skill_page}"
                    ),
                )
            ]
        ]
    )

    prompt_text = t(
        "admin_dict_skill_open_prompt",
        language,
    ).format(
        count=len(skill_ids),
    )

    try:
        index = (
            int(
                (message.text or "").strip()
            )
            - 1
        )
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_skill_open_bad_number',
                    language,
                ).format(
                    count=len(skill_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    if index < 0 or index >= len(skill_ids):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_skill_open_bad_number',
                    language,
                ).format(
                    count=len(skill_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    skill_id = skill_ids[index]

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).get_skill(
                platform_user_id=(
                    message.from_user.id
                ),
                skill_id=skill_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not item:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_skill_id=skill_id,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_skill_card(
            item,
            language,
        ),
        reply_markup=admin_skill_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_SKILL_CREATE")
async def admin_skill_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    skill_page = (
        data.get("admin_skill_page")
        or 0
    )
    await state.set_state(
        AdminDictionariesFSM.entering_admin_skill_create
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_skill_create_prompt",
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
                            "ADM_DICT_SKILLS:"
                            f"{skill_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_skill_create)
async def admin_skill_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    skill_page = (
        data.get("admin_skill_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_SKILLS:"
                        f"{skill_page}"
                    ),
                )
            ]
        ]
    )

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).create_skill(
                platform_user_id=(
                    message.from_user.id
                ),
                title=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_skill_create_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_skill_id=str(
            item.skill_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_skill_create_done',
                language,
            )}\n\n"
            f"{format_admin_skill_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_skill_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_SKILL_RENAME")
async def admin_skill_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_page = (
        data.get("admin_skill_page")
        or 0
    )
    if not data.get("admin_selected_skill_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_skill_rename
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_skill_rename_prompt",
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
                            "ADM_DICT_SKILLS:"
                            f"{skill_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_skill_rename)
async def admin_skill_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    skill_id = data.get(
        "admin_selected_skill_id"
    )
    skill_page = (
        data.get("admin_skill_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_SKILLS:"
                        f"{skill_page}"
                    ),
                )
            ]
        ]
    )

    if not skill_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).rename_skill(
                platform_user_id=(
                    message.from_user.id
                ),
                skill_id=skill_id,
                title=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_skill_rename_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_skill_id=str(
            item.skill_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_skill_rename_done',
                language,
            )}\n\n"
            f"{format_admin_skill_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_skill_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_SKILL_TOGGLE")
async def admin_skill_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_id = data.get("admin_selected_skill_id")

    if not skill_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_skill_visibility(
                platform_user_id=(
                    callback.from_user.id
                ),
                skill_id=skill_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_skill_id=str(item.skill_id),
    )

    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                'admin_dict_skill_visibility_done',
                language,
            )}\n\n"
            f"{format_admin_skill_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_skill_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_SKILL_MERGE"
)
async def admin_skill_merge_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    skill_id = data.get(
        "admin_selected_skill_id"
    )

    if not skill_id:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_skill_merge
    )
    await state.update_data(
        admin_skill_merge_target_value=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_skill_merge_prompt",
            language,
        ),
        reply_markup=(
            admin_skill_merge_prompt_keyboard(
                language
            )
        ),
    )


@admin_dictionaries_router.message(
    AdminDictionariesFSM
    .entering_admin_skill_merge
)
async def admin_skill_merge_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    skill_id = data.get(
        "admin_selected_skill_id"
    )

    if not skill_id:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
        )
        await state.set_state(None)
        await state.update_data(
            admin_skill_merge_target_value=None,
        )
        return

    try:
        async with get_session() as session:
            preview = await AdminDictionariesService(
                session
            ).preview_skill_merge(
                platform_user_id=(
                    message.from_user.id
                ),
                source_skill_id=skill_id,
                target_skill_value=(
                    message.text or ""
                ),
                language=language,
            )

    except AdminDictionariesAccessError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        await state.set_state(None)
        await state.update_data(
            admin_skill_merge_target_value=None,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t('admin_dict_skill_merge_prompt', language)}"
            ),
            reply_markup=(
                admin_skill_merge_prompt_keyboard(
                    language
                )
            ),
        )
        return

    await state.update_data(
        admin_skill_merge_target_value=(
            message.text or ""
        ),
    )
    await state.set_state(
        AdminDictionariesFSM
        .confirming_admin_skill_merge
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_dict_skill_merge_confirm_text",
            language,
        ).format(
            source_title=(
                preview.source_skill.title
            ),
            source_code=(
                preview.source_skill.code
            ),
            target_title=(
                preview.target_skill.title
            ),
            target_code=(
                preview.target_skill.code
            ),
            source_profession_links=(
                preview
                .source_skill
                .profession_links_count
            ),
            source_cabinet_links=(
                preview
                .source_skill
                .cabinet_links_count
            ),
        ),
        reply_markup=(
            admin_skill_merge_confirm_keyboard(
                language
            )
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_SKILL_MERGE_CANCEL"
)
async def admin_skill_merge_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    skill_id = data.get(
        "admin_selected_skill_id"
    )

    await state.set_state(None)
    await state.update_data(
        admin_skill_merge_target_value=None,
    )

    if not skill_id:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_cancelled",
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
                                "ADM_DICT_SKILLS"
                            ),
                        )
                    ],
                ]
            ),
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_cancelled",
            language,
        ),
        reply_markup=(
            admin_skill_card_keyboard(
                language
            )
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_SKILL_MERGE_CONFIRM"
)
async def admin_skill_merge_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    skill_id = data.get(
        "admin_selected_skill_id"
    )
    target_value = data.get(
        "admin_skill_merge_target_value"
    )

    if not skill_id or not target_value:
        await state.set_state(None)
        await state.update_data(
            admin_skill_merge_target_value=None,
        )
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
            result = await AdminDictionariesService(
                session
            ).merge_skills(
                platform_user_id=(
                    callback.from_user.id
                ),
                source_skill_id=skill_id,
                target_skill_value=target_value,
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)
        await state.update_data(
            admin_skill_merge_target_value=None,
        )
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await state.set_state(None)
        await state.update_data(
            admin_skill_merge_target_value=None,
        )

        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t('admin_dict_skill_merge_prompt', language)}"
            ),
            reply_markup=(
                admin_skill_merge_prompt_keyboard(
                    language
                )
            ),
        )
        return

    await state.update_data(
        admin_selected_skill_id=str(
            result.target_skill.skill_id
        ),
        admin_skill_merge_target_value=None,
    )
    await state.set_state(None)

    result_text = t(
        "admin_dict_skill_merge_done",
        language,
    ).format(
        moved_profession_links=(
            result.moved_profession_links
        ),
        removed_duplicate_profession_links=(
            result.removed_duplicate_profession_links
        ),
        moved_cabinet_links=(
            result.moved_cabinet_links
        ),
        removed_duplicate_cabinet_links=(
            result.removed_duplicate_cabinet_links
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{result_text}\n\n"
            f"{format_admin_skill_card(result.target_skill, language)}"
        ),
        reply_markup=(
            admin_skill_card_keyboard(
                language
            )
        ),
    )


def format_admin_category_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_category_card", language).format(
        title=item.title,
        code=item.code,
        status=item.status,
        sort_order=item.sort_order,
        professions=item.professions_count,
        specialists=item.specialists_count,
        release=item.release or "-",
    )


def format_admin_category_specialists_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_category_specialists_empty", language)

    lines = [
        t("admin_dict_category_specialists_title", language).format(
            count=len(items),
        )
    ]

    start_number = page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1

    for index, item in enumerate(items, start=start_number):
        verified = "yes" if item.is_verified else "no"
        available = "yes" if item.is_available else "no"

        lines.append(
            t("admin_dict_category_specialist_row", language).format(
                number=index,
                name=item.display_name,
                status=item.status,
                professions=item.profession_names,
                verified=verified,
                available=available,
            )
        )

    return "\n\n".join(lines)


def format_admin_profession_specialists_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_profession_specialists_empty", language)

    lines = [
        t("admin_dict_profession_specialists_title", language).format(
            page=page + 1,
            count=len(items),
        )
    ]

    start_number = page * ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE + 1

    for index, item in enumerate(items, start=start_number):
        verified = "yes" if item.is_verified else "no"
        available = "yes" if item.is_available else "no"

        lines.append(
            t("admin_dict_category_specialist_row", language).format(
                number=index,
                name=item.display_name,
                status=item.status,
                professions=item.profession_names,
                verified=verified,
                available=available,
            )
        )

    return "\n\n".join(lines)


def admin_profession_specialists_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_specialist_move_btn", language),
                callback_data="ADM_SPEC_MOVE_SELECT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_specialist_move_all_btn", language),
                callback_data="ADM_SPEC_MOVE_ALL",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_PROF_SPECIALISTS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PROF_SPECIALISTS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_PROFESSIONS",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_category_specialists_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_specialist_move_btn",
                    language,
                ),
                callback_data="ADM_CAT_SPEC_MOVE_SELECT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_category_specialist_move_all_btn",
                    language,
                ),
                callback_data="ADM_CAT_SPEC_MOVE_ALL",
            )
        ],
    ]
    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_CAT_SPECIALISTS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_CAT_SPECIALISTS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_CATEGORIES",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_category_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_rename_btn", language),
                    callback_data="ADM_CAT_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_toggle_btn", language),
                    callback_data="ADM_CAT_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_archive_btn", language),
                    callback_data="ADM_CAT_ARCHIVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_reorder_btn", language),
                    callback_data="ADM_CAT_REORDER",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_create_btn", language),
                    callback_data="ADM_PROF_CREATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_specialists_btn", language),
                    callback_data="ADM_CAT_SPECIALISTS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_CATEGORIES",
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


@admin_dictionaries_router.callback_query(
    F.data == "ADM_CAT_SPEC_MOVE_SELECT"
)
async def admin_category_specialist_move_select_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    specialist_ids = data.get(
        "admin_category_specialist_ids"
    ) or []

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )
    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM
        .entering_admin_category_specialist_move_numbers
    )
    page = int(
        data.get("admin_category_specialists_page") or 0
    )
    start_number = (
        page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1
    )
    end_number = start_number + len(specialist_ids) - 1

    example_numbers = list(
        range(
            start_number,
            min(end_number, start_number + 2) + 1,
        )
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_specialist_move_select_page_prompt",
            language,
        ).format(
            start=start_number,
            end=end_number,
            example=",".join(
                str(number)
                for number in example_numbers
            ),
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
                            "ADM_CAT_SPECIALISTS:"
                            f"{page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.callback_query(
    F.data == "ADM_CAT_SPEC_MOVE_ALL"
)
async def admin_category_specialist_move_all(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    category_id = data.get(
        "admin_selected_category_id"
    )

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            specialist_ids = await AdminDictionariesService(
                session
            ).list_category_specialist_ids(
                platform_user_id=(
                    callback.from_user.id
                ),
                category_id=category_id,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_category_specialist_move_ids=(
            specialist_ids
        ),
        admin_move_source_type="category",
        admin_move_source_id=category_id,
        admin_move_specialist_ids=specialist_ids,
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
        platform_user_id=callback.from_user.id,
        edit=True,
    )
    await callback.answer()


@admin_dictionaries_router.message(
    AdminDictionariesFSM
    .entering_admin_category_specialist_move_numbers
)
async def admin_category_specialist_move_numbers_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    data = await state.get_data()

    specialist_ids = data.get(
        "admin_category_specialist_ids"
    ) or []
    page = int(
        data.get("admin_category_specialists_page") or 0
    )
    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_CAT_SPECIALISTS:"
                        f"{page}"
                    ),
                )
            ]
        ]
    )
    try:
        async with get_session() as session:
            await AdminDictionariesService(
                session
            ).require_actor(
                platform_user_id=(
                    message.from_user.id
                ),
            )
    except AdminDictionariesAccessError:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not specialist_ids:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_specialist_move_empty",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    start_number = (
        page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1
    )
    end_number = start_number + len(specialist_ids) - 1

    raw_numbers = [
        item.strip()
        for item in (
            message.text or ""
        ).replace(";", ",").split(",")
        if item.strip()
    ]

    try:
        entered_numbers = [
            int(item)
            for item in raw_numbers
        ]
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_specialist_move_bad_page_numbers",
                language,
            ).format(
                start=start_number,
                end=end_number,
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        not entered_numbers
        or any(
            number < start_number
            or number > end_number
            for number in entered_numbers
        )
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_dict_specialist_move_bad_page_numbers",
                language,
            ).format(
                start=start_number,
                end=end_number,
            ),
            reply_markup=back_keyboard,
        )
        return

    selected_specialist_ids = [
        specialist_ids[number - start_number]
        for number in dict.fromkeys(entered_numbers)
    ]

    await state.update_data(
        admin_selected_category_specialist_move_ids=(
            selected_specialist_ids
        ),
        admin_move_source_type="category",
        admin_move_source_id=data.get(
            "admin_selected_category_id"
        ),
        admin_move_specialist_ids=(
            selected_specialist_ids
        ),
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        message,
        state,
        language,
        platform_user_id=message.from_user.id,
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_SPECIALISTS")
@admin_dictionaries_router.callback_query(F.data.startswith("ADM_CAT_SPECIALISTS:"))
async def admin_category_specialists(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await AdminDictionariesService(
                session
            ).list_category_specialists(
                platform_user_id=(
                    callback.from_user.id
                ),
                category_id=category_id,
                page=page,
                page_size=(
                    ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE
                ),
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    visible_items = list(result.items)
    page = result.page
    has_next = result.has_next

    await state.update_data(
        admin_category_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        admin_category_specialists_page=page,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_admin_category_specialists_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_category_specialists_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_category_number)
async def admin_category_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    category_ids = (
        data.get("admin_category_ids")
        or []
    )
    category_page = (
        data.get("admin_category_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_CATEGORIES:"
                        f"{category_page}"
                    ),
                )
            ]
        ]
    )

    prompt_text = t(
        "admin_dict_category_open_prompt",
        language,
    ).format(
        count=len(category_ids),
    )

    try:
        index = (
            int(
                (message.text or "").strip()
            )
            - 1
        )
    except ValueError:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_category_open_bad_number',
                    language,
                ).format(
                    count=len(category_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    if (
        index < 0
        or index >= len(category_ids)
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(
                    'admin_dict_category_open_bad_number',
                    language,
                ).format(
                    count=len(category_ids),
                )}\n\n"
                f"{prompt_text}"
            ),
            reply_markup=back_keyboard,
        )
        return

    category_id = category_ids[index]

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).get_category(
                platform_user_id=(
                    message.from_user.id
                ),
                category_id=category_id,
                language=language,
            )
    except AdminDictionariesAccessError:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    if not item:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_category_id=category_id,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_admin_category_card(
            item,
            language,
        ),
        reply_markup=admin_category_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_RENAME")
async def admin_category_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_page = (
        data.get("admin_category_page")
        or 0
    )
    if not data.get("admin_selected_category_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_category_rename
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_category_rename_prompt",
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
                            "ADM_DICT_CATEGORIES:"
                            f"{category_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_category_rename)
async def admin_category_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    category_id = data.get(
        "admin_selected_category_id"
    )
    category_page = (
        data.get("admin_category_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_CATEGORIES:"
                        f"{category_page}"
                    ),
                )
            ]
        ]
    )

    if not category_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).rename_category(
                platform_user_id=(
                    message.from_user.id
                ),
                category_id=category_id,
                title=message.text or "",
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_category_rename_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_category_id=str(
            item.category_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_category_rename_done',
                language,
            )}\n\n"
            f"{format_admin_category_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_category_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_TOGGLE")
async def admin_category_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_category_visibility(
                platform_user_id=(
                    callback.from_user.id
                ),
                category_id=category_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                'admin_dict_category_visibility_done',
                language,
            )}\n\n"
            f"{format_admin_category_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_category_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_ARCHIVE")
async def admin_category_archive(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).toggle_category_archive(
                platform_user_id=(
                    callback.from_user.id
                ),
                category_id=category_id,
                language=language,
            )

    except AdminDictionariesAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    if item is None:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    done_text_key = (
        "admin_dict_category_archive_done"
        if item.status_code == "archived"
        else "admin_dict_category_unarchive_done"
    )

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            f"{t(
                done_text_key,
                language,
            )}\n\n"
            f"{format_admin_category_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_category_card_keyboard(
            language
        ),
    )


@admin_dictionaries_router.callback_query(F.data == "ADM_CAT_REORDER")
async def admin_category_sort_order_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_page = (
        data.get("admin_category_page")
        or 0
    )
    if not data.get("admin_selected_category_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminDictionariesFSM.entering_admin_category_sort_order
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_dict_category_sort_order_prompt",
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
                            "ADM_DICT_CATEGORIES:"
                            f"{category_page}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_dictionaries_router.message(AdminDictionariesFSM.entering_admin_category_sort_order)
async def admin_category_sort_order_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    data = await state.get_data()
    category_id = data.get(
        "admin_selected_category_id"
    )
    category_page = (
        data.get("admin_category_page")
        or 0
    )

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data=(
                        "ADM_DICT_CATEGORIES:"
                        f"{category_page}"
                    ),
                )
            ]
        ]
    )

    if not category_id:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    try:
        async with get_session() as session:
            item = await AdminDictionariesService(
                session
            ).update_category_sort_order(
                platform_user_id=(
                    message.from_user.id
                ),
                category_id=category_id,
                sort_order_text=(
                    message.text or ""
                ),
                language=language,
            )

    except AdminDictionariesAccessError:
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=back_keyboard,
        )
        return

    except DictionaryServiceError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t(exc.text_key, language)}\n\n"
                f"{t(
                    'admin_dict_category_sort_order_prompt',
                    language,
                )}"
            ),
            reply_markup=back_keyboard,
        )
        return

    await state.update_data(
        admin_selected_category_id=str(
            item.category_id
        ),
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=(
            f"{t(
                'admin_dict_category_sort_order_done',
                language,
            )}\n\n"
            f"{format_admin_category_card(
                item,
                language,
            )}"
        ),
        reply_markup=admin_category_card_keyboard(
            language
        ),
    )
