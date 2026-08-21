import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.session import get_session
from handlers.specialist_cabinet_common import (
    show_specialist_cabinet,
)
from handlers.start import normalize_language
from services.specialist import (
    ProfessionalCabinetAlreadyExistsError,
    ProfessionalCabinetOption,
    SpecialistRegistrationError,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
    SpecialistCabinetsService,
)
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_menu_message,
)


specialist_cabinets_router = Router()
logger = logging.getLogger(__name__)

SPECIALIST_CABINET_EDITOR_PAGE_SIZE = 5


class SpecialistProfessionalCabinetsFSM(StatesGroup):
    adding_cabinet_category = State()
    adding_cabinet_profession = State()


async def replace_billing_callback_screen(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
    callback_answered: bool = False,
) -> Message:
    if not callback_answered:
        await callback.answer()

    data = await state.get_data()
    current_message_id = (
        callback.message.message_id
    )
    tracked_message_id = data.get(
        "last_menu_message_id"
    )

    if (
        tracked_message_id
        and tracked_message_id
        != current_message_id
    ):
        await delete_telegram_messages(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            message_ids=[
                tracked_message_id,
            ],
        )

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=text,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

    return menu_message


def format_professional_cabinets_text(
    options: tuple[
        ProfessionalCabinetOption,
        ...,
    ],
    language: str,
) -> str:
    if not options:
        return t(
            "professional_cabinets_empty",
            language,
        )

    return t(
        "professional_cabinets_title",
        language,
    )


def professional_cabinets_keyboard(
    options: tuple[
        ProfessionalCabinetOption,
        ...,
    ],
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[
        list[InlineKeyboardButton]
    ] = []

    for option in options:
        marker = (
            "✅ "
            if option.is_selected
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker}"
                        f"{option.profession_name}"
                    ),
                    callback_data=(
                        "SPEC_PRO_CABINET_SWITCH:"
                        f"{option.id}"
                    ),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "professional_cabinet_add_btn",
                    language,
                ),
                callback_data=(
                    "SPEC_PRO_CABINET_ADD"
                ),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "billing_back",
                    language,
                ),
                callback_data="GLOBAL_MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def professional_cabinet_add_selection_keyboard(
    *,
    items,
    language: str,
    page: int,
    select_callback_prefix: str,
    page_callback_prefix: str,
    back_callback_data: str,
) -> InlineKeyboardMarkup:
    page = max(0, page)
    start = (
        page
        * SPECIALIST_CABINET_EDITOR_PAGE_SIZE
    )
    end = (
        start
        + SPECIALIST_CABINET_EDITOR_PAGE_SIZE
    )
    page_items = items[start:end]
    rows: list[list[InlineKeyboardButton]] = []

    for index, item in enumerate(
        page_items,
        start=start,
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=item.name,
                    callback_data=(
                        f"{select_callback_prefix}:"
                        f"{index}"
                    ),
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=(
                    f"{page_callback_prefix}:"
                    f"{page - 1}"
                ),
            )
        )

    if end < len(items):
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"{page_callback_prefix}:"
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
                    "billing_back",
                    language,
                ),
                callback_data=back_callback_data,
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


@specialist_cabinets_router.callback_query(
    F.data == "SPEC_PRO_CABINETS"
)
async def show_professional_cabinets(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).list_cabinets(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistCabinetsAccessError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    options = action.result
    language = action.actor.language

    await replace_billing_callback_screen(
        callback=callback,
        state=state,
        text=format_professional_cabinets_text(
            options,
            language,
        ),
        reply_markup=(
            professional_cabinets_keyboard(
                options,
                language,
            )
        ),
        callback_answered=(
            callback_answered
        ),
    )


@specialist_cabinets_router.callback_query(
    F.data.startswith(
        "SPEC_PRO_CABINET_SWITCH:"
    )
)
async def switch_professional_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        professional_cabinet_id = (
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except IndexError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).switch_cabinet(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    professional_cabinet_id=(
                        professional_cabinet_id
                    ),
                )
            )
    except (
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
    ):
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    changed = action.result
    language = action.actor.language

    await callback.answer(
        (
            t(
                "professional_cabinet_switched",
                language,
            )
            if changed
            else None
        )
    )

    await show_specialist_cabinet(
        callback,
        state,
        callback_answered=True,
    )


@specialist_cabinets_router.callback_query(
    F.data == "SPEC_PRO_CABINET_ADD"
)
async def start_professional_cabinet_creation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).list_categories(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=50,
                )
            )
    except SpecialistCabinetsAccessError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except Exception:
        logger.exception(
            "professional_cabinet_categories_failed "
            "telegram_id=%s",
            callback.from_user.id,
        )
        await callback.answer(
            t(
                "professional_cabinet_add_failed",
                language,
            ),
            show_alert=True,
        )
        return

    categories = action.result
    language = action.actor.language

    await state.set_state(
        SpecialistProfessionalCabinetsFSM.adding_cabinet_category
    )
    await state.update_data(
        professional_cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
        professional_cabinet_category_page=0,
    )

    await replace_billing_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "professional_cabinet_choose_category",
            language,
        ),
        reply_markup=(
            professional_cabinet_add_selection_keyboard(
                items=categories,
                language=language,
                page=0,
                select_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_CAT"
                ),
                page_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_CAT_PAGE"
                ),
                back_callback_data=(
                    "SPEC_PRO_CABINETS"
                ),
            )
        ),
    )


@specialist_cabinets_router.callback_query(
    StateFilter(
        SpecialistProfessionalCabinetsFSM.adding_cabinet_category
    ),
    F.data.startswith(
        "SPEC_PRO_CABINET_ADD_CAT_PAGE:"
    ),
)
async def change_professional_cabinet_category_page(
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
                (callback.data or "").rsplit(
                    ":",
                    1,
                )[1]
            ),
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).list_categories(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=50,
                )
            )
    except SpecialistCabinetsAccessError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    categories = action.result
    language = action.actor.language

    await replace_billing_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "professional_cabinet_choose_category",
            language,
        ),
        reply_markup=(
            professional_cabinet_add_selection_keyboard(
                items=categories,
                language=language,
                page=page,
                select_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_CAT"
                ),
                page_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_CAT_PAGE"
                ),
                back_callback_data=(
                    "SPEC_PRO_CABINETS"
                ),
            )
        ),
    )

    await state.update_data(
        professional_cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
        professional_cabinet_category_page=page,
    )


@specialist_cabinets_router.callback_query(
    StateFilter(
        SpecialistProfessionalCabinetsFSM.adding_cabinet_category
    ),
    F.data.startswith(
        "SPEC_PRO_CABINET_ADD_CAT:"
    ),
)
async def select_professional_cabinet_category(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    category_ids = (
        data.get(
            "professional_cabinet_category_ids"
        )
        or []
    )

    try:
        index = int(
            (callback.data or "").rsplit(
                ":",
                1,
            )[1]
        )
        category_id = category_ids[index]
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

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).list_professions(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    category_id=category_id,
                    limit=50,
                )
            )
    except SpecialistCabinetsAccessError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    professions = action.result
    language = action.actor.language

    if not professions:
        await callback.answer(
            t(
                "professional_cabinet_add_failed",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(
        SpecialistProfessionalCabinetsFSM.adding_cabinet_profession
    )
    await state.update_data(
        professional_cabinet_category_id=(
            str(category_id)
        ),
        professional_cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        professional_cabinet_profession_page=0,
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "professional_cabinet_choose_profession",
            language,
        ),
        reply_markup=(
            professional_cabinet_add_selection_keyboard(
                items=professions,
                language=language,
                page=0,
                select_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_PROF"
                ),
                page_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_PROF_PAGE"
                ),
                back_callback_data=(
                    "SPEC_PRO_CABINET_ADD"
                ),
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@specialist_cabinets_router.callback_query(
    StateFilter(
        SpecialistProfessionalCabinetsFSM.adding_cabinet_profession
    ),
    F.data.startswith(
        "SPEC_PRO_CABINET_ADD_PROF_PAGE:"
    ),
)
async def change_professional_cabinet_profession_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        page = max(
            0,
            int(
                (callback.data or "").rsplit(
                    ":",
                    1,
                )[1]
            ),
        )
        category_id = data[
            "professional_cabinet_category_id"
        ]
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).list_professions(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    category_id=category_id,
                    limit=50,
                )
            )
    except SpecialistCabinetsAccessError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    professions = action.result
    language = action.actor.language

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "professional_cabinet_choose_profession",
            language,
        ),
        reply_markup=(
            professional_cabinet_add_selection_keyboard(
                items=professions,
                language=language,
                page=page,
                select_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_PROF"
                ),
                page_callback_prefix=(
                    "SPEC_PRO_CABINET_ADD_PROF_PAGE"
                ),
                back_callback_data=(
                    "SPEC_PRO_CABINET_ADD"
                ),
            )
        ),
    )

    await state.update_data(
        professional_cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        professional_cabinet_profession_page=page,
        last_menu_message_id=menu_message.message_id,
    )


@specialist_cabinets_router.callback_query(
    StateFilter(
        SpecialistProfessionalCabinetsFSM.adding_cabinet_profession
    ),
    F.data.startswith(
        "SPEC_PRO_CABINET_ADD_PROF:"
    ),
)
async def create_selected_professional_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    profession_ids = (
        data.get(
            "professional_cabinet_profession_ids"
        )
        or []
    )

    try:
        index = int(
            (callback.data or "").rsplit(
                ":",
                1,
            )[1]
        )
        profession_id = profession_ids[index]
        category_id = data[
            "professional_cabinet_category_id"
        ]
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).create_cabinet(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    category_id=category_id,
                    profession_id=profession_id,
                )
            )

    except SpecialistCabinetsAccessError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    except SpecialistCabinetsSelectionError:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    except ProfessionalCabinetAlreadyExistsError:
        await callback.answer(
            t(
                "professional_cabinet_already_exists",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        SpecialistRegistrationError,
        ValueError,
    ):
        logger.exception(
            "professional_cabinet_creation_rejected "
            "telegram_id=%s",
            callback.from_user.id,
        )
        await callback.answer(
            t(
                "professional_cabinet_add_failed",
                language,
            ),
            show_alert=True,
        )
        return

    except Exception:
        logger.exception(
            "professional_cabinet_creation_failed "
            "telegram_id=%s",
            callback.from_user.id,
        )
        await callback.answer(
            t(
                "professional_cabinet_add_failed",
                language,
            ),
            show_alert=True,
        )
        return

    language = action.actor.language

    await callback.answer(
        t(
            "professional_cabinet_created",
            language,
        )
    )

    await show_specialist_cabinet(
        callback,
        state,
        callback_answered=True,
    )
