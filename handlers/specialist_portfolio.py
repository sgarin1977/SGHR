from __future__ import annotations

from uuid import UUID
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.session import get_session
from handlers.start import normalize_language
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message, delete_telegram_messages
from services.portfolio import PortfolioServiceError
from io import BytesIO
from handlers.billing_common import replace_billing_input_screen
from services.specialist_portfolio import SpecialistPortfolioAccessError, SpecialistPortfolioService
from handlers.billing_common import clear_cross_feature_messages


specialist_portfolio_router = Router()
OWNER_PORTFOLIO_PAGE_SIZE = 5


class SpecialistPortfolioFSM(StatesGroup):
    waiting_portfolio_file = State()
    entering_portfolio_caption = State()
    confirming_portfolio_upload = State()


async def require_specialist_portfolio_actor(
    *,
    platform_user_id: int | str,
    fallback_language: str | None,
):
    async with get_session() as session:
        return await (
            SpecialistPortfolioService(
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


def portfolio_menu_keyboard(
    language: str,
    *,
    page: int = 0,
    total: int = 0,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_upload_button", language),
                callback_data="CAB_PORTFOLIO_UPLOAD",
            )
        ]
    ]

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"CAB_PORTFOLIO_PAGE:{page - 1}",
            )
        )

    if (page + 1) * OWNER_PORTFOLIO_PAGE_SIZE < total:
        nav_row.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"CAB_PORTFOLIO_PAGE:{page + 1}",
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


def portfolio_item_keyboard(
    *,
    item_id: UUID,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("portfolio_open_button", language),
                    url=signed_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("portfolio_delete_button", language),
                    callback_data=f"CAB_PORT_DEL:{item_id}",
                )
            ],
        ]
    )


def portfolio_item_text(view, language: str) -> str:
    status_key = (
        f"portfolio_status_{view.item.status}"
    )

    file_label_key = (
        "portfolio_photo_label"
        if view.storage_object.file_type == "photo"
        else "portfolio_pdf_label"
    )

    file_label = t(file_label_key, language)
    title = view.item.title or file_label
    status = t(status_key, language)

    return f"{file_label}: {title}\n{status}"


async def send_owner_portfolio(
    message: Message,
    *,
    portfolio_page,
    language: str,
) -> list[int]:
    page = portfolio_page.page
    total = portfolio_page.total
    page_items = portfolio_page.items
    rendered_message_ids: list[int] = []

    if total == 0:
        empty_message = await message.answer(
            (
                f"{t('specialist_portfolio_title', language)}\n"
                f"{t('specialist_portfolio_hint', language)}\n\n"
                f"{t('portfolio_empty', language)}"
            ),
            reply_markup=(
                portfolio_menu_keyboard(
                    language,
                    page=page,
                    total=total,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )
        return rendered_message_ids

    header_message = await message.answer(
        (
            f"{t('specialist_portfolio_title', language)}\n"
            f"{t('specialist_portfolio_hint', language)}\n"
            f"{page + 1}/"
            f"{portfolio_page.total_pages}"
        ),
        reply_markup=(
            portfolio_menu_keyboard(
                language,
                page=page,
                total=total,
            )
        ),
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    for view in page_items:
        item_text = portfolio_item_text(
            view,
            language,
        )
        keyboard = portfolio_item_keyboard(
            item_id=view.item.id,
            signed_url=view.signed_url,
            language=language,
        )
        item_message = await message.answer(
            item_text,
            reply_markup=keyboard,
        )
        rendered_message_ids.append(
            item_message.message_id
        )

    return rendered_message_ids


@specialist_portfolio_router.callback_query(
    F.data == "CAB_PORTFOLIO"
)
async def show_owner_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            result = await (
                SpecialistPortfolioService(
                    session
                ).list_owner_items(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    page=0,
                    page_size=(
                        OWNER_PORTFOLIO_PAGE_SIZE
                    ),
                )
            )
    except SpecialistPortfolioAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except PortfolioServiceError as exc:
        await callback.answer(
            t(
                "portfolio_error",
                fallback_language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    language = result.actor.language
    portfolio_page = result.page

    await clear_cross_feature_messages(
        callback=callback,
        state=state,
    )

    rendered_message_ids = (
        await send_owner_portfolio(
            callback.message,
            portfolio_page=portfolio_page,
            language=language,
        )
    )

    await state.set_state(None)
    await state.update_data(
        owner_portfolio_message_ids=(
            rendered_message_ids
        ),
        owner_portfolio_page=(
            portfolio_page.page
        ),
    )

    if not callback_answered:
        await callback.answer()


@specialist_portfolio_router.callback_query(
    F.data.startswith("CAB_PORTFOLIO_PAGE:")
)
async def show_owner_portfolio_page(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    requested_page: int | None = None,
    callback_answered: bool = False,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    if requested_page is None:
        try:
            requested_page = max(
                0,
                int(
                    (
                        callback.data or ""
                    ).split(
                        ":",
                        1,
                    )[1]
                ),
            )
        except (
            IndexError,
            TypeError,
            ValueError,
        ):
            requested_page = 0
    else:
        requested_page = max(
            0,
            int(requested_page),
        )

    try:
        async with get_session() as session:
            result = await (
                SpecialistPortfolioService(
                    session
                ).list_owner_items(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    page=requested_page,
                    page_size=(
                        OWNER_PORTFOLIO_PAGE_SIZE
                    ),
                )
            )
    except SpecialistPortfolioAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except PortfolioServiceError as exc:
        await callback.answer(
            t(
                "portfolio_error",
                fallback_language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    language = result.actor.language
    portfolio_page = result.page

    await clear_cross_feature_messages(
        callback=callback,
        state=state,
    )

    rendered_message_ids = (
        await send_owner_portfolio(
            callback.message,
            portfolio_page=portfolio_page,
            language=language,
        )
    )

    await state.update_data(
        owner_portfolio_message_ids=(
            rendered_message_ids
        ),
        owner_portfolio_page=(
            portfolio_page.page
        ),
    )

    if not callback_answered:
        await callback.answer()


def portfolio_caption_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("portfolio_caption_skip_btn", language),
                    callback_data="CAB_PORTFOLIO_CAPTION_SKIP",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="CAB_PORTFOLIO",
                )
            ],
        ]
    )


def portfolio_upload_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("portfolio_upload_confirm_btn", language),
                    callback_data="CAB_PORTFOLIO_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="CAB_PORTFOLIO",
                )
            ],
        ]
    )


def portfolio_upload_preview_text(data: dict, language: str) -> str:
    caption = (data.get("portfolio_caption") or "").strip() or "-"
    size_bytes = int(data.get("portfolio_size_bytes") or 0)
    size_kb = max(1, round(size_bytes / 1024))

    return t("portfolio_upload_preview", language).format(
        filename=data.get("portfolio_filename") or "-",
        file_type=data.get("portfolio_mime_type") or "-",
        size_kb=size_kb,
        caption=caption,
    )


@specialist_portfolio_router.callback_query(
    F.data == "CAB_PORTFOLIO_UPLOAD"
)
async def ask_portfolio_upload(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_portfolio_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistPortfolioAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    language = actor.language
    data = await state.get_data()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in (
                data.get(
                    "owner_portfolio_message_ids"
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
        text=t(
            "portfolio_upload_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "billing_back",
                            language,
                        ),
                        callback_data=(
                            "CAB_PORTFOLIO"
                        ),
                    )
                ]
            ]
        ),
    )

    await state.set_state(
        SpecialistPortfolioFSM.waiting_portfolio_file
    )
    await state.update_data(
        owner_portfolio_message_ids=[],
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@specialist_portfolio_router.message(
    SpecialistPortfolioFSM.waiting_portfolio_file,
    F.photo | F.document,
)
async def receive_portfolio_file(
    message: Message,
    state: FSMContext,
):
    fallback_language = normalize_language(
        message.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_portfolio_actor(
                platform_user_id=(
                    message.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistPortfolioAccessError:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "billing_start_required",
                fallback_language,
            ),
        )
        return

    language = actor.language
    buffer = BytesIO()

    if message.document:
        telegram_file = message.document
        filename = (
            telegram_file.file_name
            or (
                f"{telegram_file.file_unique_id}"
                ".bin"
            )
        )
        mime_type = telegram_file.mime_type
    else:
        telegram_file = message.photo[-1]
        filename = (
            f"{telegram_file.file_unique_id}.jpg"
        )
        mime_type = "image/jpeg"

    try:
        await message.bot.download(
            telegram_file,
            destination=buffer,
        )
    except Exception as exc:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "portfolio_upload_error",
                language,
            ).format(
                error=str(exc)
            ),
            reply_markup=(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "billing_back",
                                    language,
                                ),
                                callback_data=(
                                    "CAB_PORTFOLIO"
                                ),
                            )
                        ]
                    ]
                )
            ),
        )
        return

    content = buffer.getvalue()

    await state.update_data(
        portfolio_filename=filename,
        portfolio_mime_type=mime_type,
        portfolio_content=content,
        portfolio_size_bytes=len(content),
    )
    await state.set_state(
        SpecialistPortfolioFSM
        .entering_portfolio_caption
    )

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=t(
            "portfolio_caption_prompt",
            language,
        ),
        reply_markup=(
            portfolio_caption_keyboard(
                language
            )
        ),
    )


@specialist_portfolio_router.message(
    SpecialistPortfolioFSM.entering_portfolio_caption
)
async def receive_portfolio_caption(
    message: Message,
    state: FSMContext,
):
    fallback_language = normalize_language(
        message.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_portfolio_actor(
                platform_user_id=(
                    message.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistPortfolioAccessError:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "billing_start_required",
                fallback_language,
            ),
        )
        return

    language = actor.language
    caption = (
        message.text or ""
    ).strip()

    await state.update_data(
        portfolio_caption=caption
    )
    await state.set_state(
        SpecialistPortfolioFSM.confirming_portfolio_upload
    )

    data = await state.get_data()

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=portfolio_upload_preview_text(
            data,
            language,
        ),
        reply_markup=(
            portfolio_upload_confirm_keyboard(
                language
            )
        ),
    )


@specialist_portfolio_router.callback_query(
    F.data == "CAB_PORTFOLIO_CAPTION_SKIP"
)
async def skip_portfolio_caption(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_portfolio_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistPortfolioAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    language = actor.language

    await state.update_data(
        portfolio_caption=""
    )
    await state.set_state(
        SpecialistPortfolioFSM.confirming_portfolio_upload
    )

    data = await state.get_data()
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=portfolio_upload_preview_text(
            data,
            language,
        ),
        reply_markup=(
            portfolio_upload_confirm_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@specialist_portfolio_router.message(
    SpecialistPortfolioFSM.waiting_portfolio_file,
)
async def reject_invalid_portfolio_message(
    message: Message,
    state: FSMContext,
):
    fallback_language = normalize_language(
        message.from_user.language_code
    )

    try:
        actor = await (
            require_specialist_portfolio_actor(
                platform_user_id=(
                    message.from_user.id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
    except SpecialistPortfolioAccessError:
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "billing_start_required",
                fallback_language,
            ),
        )
        return

    language = actor.language

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=(
            f"{t('portfolio_invalid_file', language)}\n\n"
            f"{t('portfolio_upload_prompt', language)}"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "billing_back",
                            language,
                        ),
                        callback_data=(
                            "CAB_PORTFOLIO"
                        ),
                    )
                ]
            ]
        ),
    )


@specialist_portfolio_router.callback_query(
    F.data.startswith("CAB_PORT_DEL:")
)
async def delete_owner_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    page = int(
        data.get(
            "owner_portfolio_page"
        )
        or 0
    )

    try:
        item_id = UUID(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        await callback.answer(
            t(
                "portfolio_error",
                fallback_language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistPortfolioService(
                    session
                ).delete_owner_item(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    item_id=item_id,
                )
            )
    except SpecialistPortfolioAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except (
        ValueError,
        PortfolioServiceError,
    ) as exc:
        await callback.answer(
            t(
                "portfolio_error",
                fallback_language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    await callback.answer(
        t(
            "portfolio_deleted",
            action.actor.language,
        ),
        show_alert=True,
    )

    await show_owner_portfolio_page(
        callback,
        state,
        requested_page=page,
        callback_answered=True,
    )


@specialist_portfolio_router.callback_query(
    F.data == "CAB_PORTFOLIO_CONFIRM"
)
async def confirm_portfolio_upload(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    filename = data.get(
        "portfolio_filename"
    )
    mime_type = data.get(
        "portfolio_mime_type"
    )
    content = data.get(
        "portfolio_content"
    )
    caption = (
        data.get(
            "portfolio_caption"
        )
        or ""
    ).strip()

    if not filename or not content:
        await callback.answer(
            t(
                "portfolio_invalid_file",
                fallback_language,
            ),
            show_alert=True,
        )
        await state.clear()
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistPortfolioService(
                    session
                ).upload_item(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    filename=filename,
                    mime_type=mime_type,
                    content=content,
                    caption=caption,
                )
            )
    except SpecialistPortfolioAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except PortfolioServiceError as exc:
        await callback.answer()

        menu_message = (
            await edit_or_replace_menu_message(
                callback=callback,
                text=t(
                    "portfolio_upload_error",
                    fallback_language,
                ).format(
                    error=str(exc)
                ),
                reply_markup=(
                    InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=t(
                                        "billing_back",
                                        fallback_language,
                                    ),
                                    callback_data=(
                                        "CAB_PORTFOLIO"
                                    ),
                                )
                            ]
                        ]
                    )
                ),
            )
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    language = action.actor.language

    await callback.answer(
        t(
            "portfolio_upload_success",
            language,
        )
    )
    await state.set_state(None)

    await show_owner_portfolio(
        callback,
        state,
        callback_answered=True,
    )

    await state.update_data(
        portfolio_tenant_id=None,
        portfolio_owner_user_id=None,
        portfolio_filename=None,
        portfolio_mime_type=None,
        portfolio_content=None,
        portfolio_size_bytes=None,
        portfolio_caption=None,
    )
