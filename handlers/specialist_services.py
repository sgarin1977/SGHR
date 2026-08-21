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

from database.models import (
    SpecialistService as SpecialistServiceModel,
)
from database.session import get_session
from handlers.billing_common import (
    replace_billing_input_screen,
)
from handlers.start import normalize_language
from services.specialist import (
    SpecialistRegistrationError,
)
from services.specialist_services import (
    SpecialistServicesAccessError,
    SpecialistServicesService,
)
from ui.texts import t
from utils.telegram_cleanup import (
    edit_or_replace_menu_message,
)


specialist_services_router = Router()
SPECIALIST_SERVICES_PAGE_SIZE = 5


class SpecialistServicesFSM(StatesGroup):
    entering_service_title = State()
    entering_service_description = State()
    entering_service_price = State()
    confirming_service = State()


async def require_specialist_services_actor(
    *,
    platform_user_id: int | str,
    fallback_language: str | None,
):
    async with get_session() as session:
        return await (
            SpecialistServicesService(
                session
            ).require_actor(
                platform_user_id=(
                    platform_user_id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )


def specialist_service_status_text(status: str | None, language: str) -> str:
    normalized = status or "active"
    key = f"specialist_service_status_{normalized}"
    translated = t(key, language)
    if translated == key:
        return normalized
    return translated


def specialist_service_price_text(service: SpecialistServiceModel, language: str) -> str:
    if service.price_from is None and service.price_to is None:
        return t("specialist_service_price_not_set", language)

    currency = service.currency or "EUR"

    if service.price_from is not None and service.price_to is not None:
        return f"{float(service.price_from):.2f}-{float(service.price_to):.2f} {currency}"

    if service.price_from is not None:
        return f"{float(service.price_from):.2f} {currency}"

    return f"{float(service.price_to):.2f} {currency}"


def format_specialist_services_list(
    services: list[SpecialistServiceModel],
    *,
    page: int,
    total: int,
    language: str,
) -> str:
    lines = [
        t("specialist_services_title", language),
        t("specialist_services_hint", language),
        "",
        (
            f"{page + 1}/"
            f"{max(1, (total + SPECIALIST_SERVICES_PAGE_SIZE - 1) // SPECIALIST_SERVICES_PAGE_SIZE)}"
        ),
        "",
    ]

    if not services:
        lines.append(t("specialist_services_empty", language))
        return "\n".join(lines)

    for index, service in enumerate(services, start=1):
        lines.extend(
            [
                f"{index}. {service.title}",
                f"{t('cabinet_profile_price', language)}: {specialist_service_price_text(service, language)}",
                f"{t('cabinet_profile_status', language)}: {specialist_service_status_text(service.status, language)}",
                "",
            ]
        )

    return "\n".join(lines).strip()


def specialist_services_keyboard(
    *,
    services: list[SpecialistServiceModel],
    page: int,
    total: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("specialist_service_add_btn", language),
                callback_data="SPEC_SERVICE_ADD",
            )
        ]
    ]

    for index, service in enumerate(services):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('specialist_service_edit_btn', language)}",
                    callback_data=f"SPEC_SERVICE_EDIT:{index}",
                ),
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('specialist_service_pause_btn', language)}",
                    callback_data=f"SPEC_SERVICE_PAUSE:{index}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('specialist_service_delete_btn', language)}",
                    callback_data=f"SPEC_SERVICE_DELETE:{index}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="<",
                callback_data=f"SPEC_SERVICES_PAGE:{page - 1}",
            )
        )

    if (page + 1) * SPECIALIST_SERVICES_PAGE_SIZE < total:
        nav_row.append(
            InlineKeyboardButton(
                text=">",
                callback_data=f"SPEC_SERVICES_PAGE:{page + 1}",
            )
        )

    if nav_row:
        rows.append(nav_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_CABINET",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_form_back_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )


def service_price_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("specialist_service_skip_price_btn", language),
                    callback_data="SPEC_SERVICE_PRICE_SKIP",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
        ]
    )


def service_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("confirm", language),
                    callback_data="SPEC_SERVICE_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
        ]
    )


def service_delete_confirm_keyboard(
    service_id: str,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("privacy_confirm_btn", language),
                    callback_data=f"SPEC_SERVICE_DELETE_CONFIRM:{service_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
        ]
    )


def parse_service_price(
    value: str,
) -> tuple[
    float | None,
    float | None,
]:
    return (
        SpecialistServicesService
        .parse_price(value)
    )


def service_preview_text(data: dict, language: str) -> str:
    price_from = data.get("service_price_from")
    price_to = data.get("service_price_to")
    currency = data.get("service_currency") or "EUR"

    if price_from is None and price_to is None:
        price = t("specialist_service_price_not_set", language)
    elif price_from is not None and price_to is not None:
        price = f"{float(price_from):.2f}-{float(price_to):.2f} {currency}"
    elif price_from is not None:
        price = f"{float(price_from):.2f} {currency}"
    else:
        price = f"{float(price_to):.2f} {currency}"

    return t("specialist_service_preview", language).format(
        title=data.get("service_title") or "-",
        description=data.get("service_description") or "-",
        price=price,
        currency=currency,
    )


@specialist_services_router.callback_query(F.data == "SPEC_SERVICES")
@specialist_services_router.callback_query(F.data.startswith("SPEC_SERVICES_PAGE:"))
async def specialist_services_entry(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    page = 0
    if (
        callback.data
        and callback.data.startswith(
            "SPEC_SERVICES_PAGE:"
        )
    ):
        try:
            page = max(
                0,
                int(
                    callback.data.split(
                        ":",
                        1,
                    )[1]
                ),
            )
        except ValueError:
            page = 0

    try:
        async with get_session() as session:
            result = await (
                SpecialistServicesService(
                    session
                ).list_services(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    page=page,
                    page_size=(
                        SPECIALIST_SERVICES_PAGE_SIZE
                    ),
                )
            )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = result.actor.language
    services = result.items
    total = result.total

    await state.update_data(
        specialist_service_ids=[
            str(item.id)
            for item in services
        ],
        specialist_services_page=page,
    )

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_specialist_services_list(
                services,
                page=page,
                total=total,
                language=language,
            ),
            reply_markup=(
                specialist_services_keyboard(
                    services=services,
                    page=page,
                    total=total,
                    language=language,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

    if not callback_answered:
        await callback.answer()


@specialist_services_router.callback_query(F.data == "SPEC_SERVICE_ADD")
async def add_specialist_service(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            actor = await (
                SpecialistServicesService(
                    session
                ).require_actor(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                )
            )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
            show_alert=True,
        )
        return

    language = actor.language

    await state.update_data(
        service_mode="create",
        service_specialist_id=str(
            actor.specialist_id
        ),
        service_tenant_id=str(
            actor.tenant_id
        ),
        service_user_id=str(
            actor.user_id
        ),
        service_category_id=None,
        service_profession_id=None,
        service_currency="EUR",
        service_price_from=None,
        service_price_to=None,
    )
    await state.set_state(
        SpecialistServicesFSM.entering_service_title
    )

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "specialist_service_title_prompt",
                language,
            ),
            reply_markup=(
                service_form_back_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await callback.answer()


@specialist_services_router.callback_query(F.data.startswith("SPEC_SERVICE_EDIT:"))
async def edit_specialist_service(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    service_ids = (
        data.get("specialist_service_ids")
        or []
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
        service_id = UUID(
            service_ids[index]
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "specialist_service_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await (
                SpecialistServicesService(
                    session
                ).get_service_for_editing(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    service_id=service_id,
                )
            )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError:
        await callback.answer(
            t(
                "specialist_service_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    actor = result.actor
    service_data = result.item
    language = actor.language

    await state.update_data(
        service_mode="edit",
        service_id=str(
            service_data.service_id
        ),
        service_specialist_id=str(
            actor.specialist_id
        ),
        service_tenant_id=str(
            actor.tenant_id
        ),
        service_user_id=str(
            actor.user_id
        ),
        service_category_id=(
            str(service_data.category_id)
            if service_data.category_id
            else None
        ),
        service_profession_id=(
            str(service_data.profession_id)
            if service_data.profession_id
            else None
        ),
        service_title=service_data.title,
        service_description=(
            service_data.description
        ),
        service_price_from=(
            service_data.price_from
        ),
        service_price_to=(
            service_data.price_to
        ),
        service_currency=(
            service_data.currency
        ),
    )

    await state.set_state(
        SpecialistServicesFSM.entering_service_title
    )

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "specialist_service_title_prompt",
                language,
            ),
            reply_markup=(
                service_form_back_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await callback.answer()


@specialist_services_router.message(
    SpecialistServicesFSM.entering_service_title
)
async def receive_service_title(
    message: Message,
    state: FSMContext,
):
    fallback_language = normalize_language(
        message.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_services_actor(
                platform_user_id=(
                    message.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                key,
                fallback_language,
            ),
        )
        return

    language = actor.language
    title = (message.text or "").strip()

    if not title:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('specialist_service_title_required', language)}\n\n"
                f"{t('specialist_service_title_prompt', language)}"
            ),
            reply_markup=service_form_back_keyboard(
                language
            ),
        )
        return

    await state.update_data(
        service_title=title
    )
    await state.set_state(
        SpecialistServicesFSM.entering_service_description
    )

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=t(
            "specialist_service_description_prompt",
            language,
        ),
        reply_markup=service_form_back_keyboard(
            language
        ),
    )


@specialist_services_router.message(
    SpecialistServicesFSM.entering_service_description
)
async def receive_service_description(
    message: Message,
    state: FSMContext,
):
    fallback_language = normalize_language(
        message.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_services_actor(
                platform_user_id=(
                    message.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                key,
                fallback_language,
            ),
        )
        return

    language = actor.language
    description = (
        message.text or ""
    ).strip()

    if not description:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('specialist_service_description_required', language)}\n\n"
                f"{t('specialist_service_description_prompt', language)}"
            ),
            reply_markup=service_form_back_keyboard(
                language
            ),
        )
        return

    await state.update_data(
        service_description=description
    )
    await state.set_state(
        SpecialistServicesFSM.entering_service_price
    )

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=t(
            "specialist_service_price_prompt",
            language,
        ),
        reply_markup=service_price_keyboard(
            language
        ),
    )


@specialist_services_router.callback_query(F.data == "SPEC_SERVICE_PRICE_SKIP")
async def skip_service_price(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_services_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
            show_alert=True,
        )
        return

    language = actor.language

    await state.update_data(service_price_from=None, service_price_to=None)
    await state.set_state(SpecialistServicesFSM.confirming_service)

    data = await state.get_data()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=service_preview_text(
            data,
            language,
        ),
        reply_markup=service_confirm_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await callback.answer()


@specialist_services_router.message(
    SpecialistServicesFSM.entering_service_price
)
async def receive_service_price(
    message: Message,
    state: FSMContext,
):
    fallback_language = normalize_language(
        message.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_services_actor(
                platform_user_id=(
                    message.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                key,
                fallback_language,
            ),
        )
        return

    language = actor.language

    try:
        price_from, price_to = parse_service_price(
            message.text or ""
        )
    except (TypeError, ValueError):
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('specialist_service_price_invalid', language)}\n\n"
                f"{t('specialist_service_price_prompt', language)}"
            ),
            reply_markup=service_price_keyboard(
                language
            ),
        )
        return

    await state.update_data(
        service_price_from=price_from,
        service_price_to=price_to,
    )
    await state.set_state(
        SpecialistServicesFSM.confirming_service
    )

    data = await state.get_data()

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=service_preview_text(
            data,
            language,
        ),
        reply_markup=service_confirm_keyboard(
            language
        ),
    )


@specialist_services_router.callback_query(F.data == "SPEC_SERVICE_CONFIRM")
async def confirm_specialist_service(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    title = (
        data.get("service_title")
        or ""
    ).strip()
    description = (
        data.get("service_description")
        or ""
    ).strip()

    if not title:
        await callback.answer(
            t(
                "specialist_service_title_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    if not description:
        await callback.answer(
            t(
                "specialist_service_description_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    mode = (
        data.get("service_mode")
        or "create"
    )
    raw_service_id = (
        data.get("service_id")
        if mode == "edit"
        else None
    )

    if (
        mode == "edit"
        and not raw_service_id
    ):
        await callback.answer(
            t(
                "specialist_service_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        await state.clear()
        return

    try:
        service_id = (
            UUID(str(raw_service_id))
            if raw_service_id
            else None
        )
        category_id = (
            UUID(
                str(
                    data[
                        "service_category_id"
                    ]
                )
            )
            if data.get(
                "service_category_id"
            )
            else None
        )
        profession_id = (
            UUID(
                str(
                    data[
                        "service_profession_id"
                    ]
                )
            )
            if data.get(
                "service_profession_id"
            )
            else None
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "specialist_service_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistServicesService(
                    session
                ).save_service(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    service_id=service_id,
                    category_id=category_id,
                    profession_id=profession_id,
                    title=title,
                    description=description,
                    price_from=data.get(
                        "service_price_from"
                    ),
                    price_to=data.get(
                        "service_price_to"
                    ),
                    currency=(
                        data.get(
                            "service_currency"
                        )
                        or "EUR"
                    ),
                )
            )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
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

    language = action.actor.language

    await callback.answer()
    await state.clear()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "specialist_service_saved",
                language,
            ),
            reply_markup=(
                specialist_services_keyboard(
                    services=[],
                    page=0,
                    total=0,
                    language=language,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@specialist_services_router.callback_query(F.data.startswith("SPEC_SERVICE_PAUSE:"))
async def pause_specialist_service(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    service_ids = (
        data.get("specialist_service_ids")
        or []
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
        service_id = UUID(
            service_ids[index]
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "specialist_service_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistServicesService(
                    session
                ).toggle_service_status(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    service_id=service_id,
                )
            )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
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

    await callback.answer(
        t(
            "specialist_service_status_changed",
            action.actor.language,
        )
    )

    await specialist_services_entry(
        callback,
        state,
        callback_answered=True,
    )


@specialist_services_router.callback_query(F.data.startswith("SPEC_SERVICE_DELETE:"))
async def ask_delete_specialist_service(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_services_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
            show_alert=True,
        )
        return

    language = actor.language
    data = await state.get_data()
    service_ids = data.get("specialist_service_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        service_id = service_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("specialist_service_not_found", language), show_alert=True)
        return

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "specialist_service_delete_confirm",
            language,
        ),
        reply_markup=service_delete_confirm_keyboard(
            service_id,
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@specialist_services_router.callback_query(F.data.startswith("SPEC_SERVICE_DELETE_CONFIRM:"))
async def delete_specialist_service(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        service_id = UUID(
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
                "specialist_service_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistServicesService(
                    session
                ).delete_service(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    service_id=service_id,
                )
            )
    except SpecialistServicesAccessError as exc:
        key = (
            "billing_start_required"
            if exc.reason == "user_not_found"
            else "cabinet_profile_not_found"
        )
        await callback.answer(
            t(key, fallback_language),
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

    await callback.answer(
        t(
            "specialist_service_deleted",
            action.actor.language,
        )
    )

    await specialist_services_entry(
        callback,
        state,
        callback_answered=True,
    )
