from collections.abc import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

async def edit_or_replace_menu_message(
    *,
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    try:
        return await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return callback.message

    try:
        await callback.message.delete()
    except (
        TelegramBadRequest,
        TelegramForbiddenError,
    ):
        pass

    return await callback.message.answer(
        text,
        reply_markup=reply_markup,
    )

async def replace_callback_menu_message(
    *,
    callback: CallbackQuery,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup
        | ReplyKeyboardMarkup
        | ReplyKeyboardRemove
        | None
    ) = None,
) -> Message:
    try:
        await callback.message.delete()
    except (
        TelegramBadRequest,
        TelegramForbiddenError,
    ):
        pass

    return await callback.message.answer(
        text,
        reply_markup=reply_markup,
    )

async def edit_or_replace_tracked_menu_message(
    *,
    message: Message,
    menu_message_id: int | None,
    delete_source_message: bool = False,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup
        | ReplyKeyboardMarkup
        | ReplyKeyboardRemove
        | None
    ) = None,
) -> int:
    if delete_source_message:
        try:
            await message.delete()
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
        ):
            pass
    requires_replacement = isinstance(
        reply_markup,
        (
            ReplyKeyboardMarkup,
            ReplyKeyboardRemove,
        ),
    )

    if menu_message_id and not requires_replacement:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=menu_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            return menu_message_id
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return menu_message_id

    if menu_message_id:
        try:
            await message.bot.delete_message(
                chat_id=message.chat.id,
                message_id=menu_message_id,
            )
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
        ):
            pass

    menu_message = await message.answer(
        text,
        reply_markup=reply_markup,
    )

    return menu_message.message_id

async def delete_telegram_messages(
    *,
    bot: Bot,
    chat_id: int,
    message_ids: Iterable[int],
) -> None:
    unique_message_ids = {
        message_id
        for message_id in message_ids
        if isinstance(message_id, int) and message_id > 0
    }

    for message_id in unique_message_ids:
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            continue

def split_telegram_text(
    text: str,
    *,
    limit: int = 4000,
) -> list[str]:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return []

    chunks: list[str] = []
    current_chunk = ""

    for paragraph in normalized_text.split("\n\n"):
        remaining = paragraph.strip()

        while remaining:
            available_length = (
                limit
                if not current_chunk
                else limit - len(current_chunk) - 2
            )

            if len(remaining) <= available_length:
                current_chunk = (
                    remaining
                    if not current_chunk
                    else f"{current_chunk}\n\n{remaining}"
                )
                remaining = ""
                continue

            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
                continue

            split_position = remaining.rfind(
                "\n",
                0,
                available_length + 1,
            )
            if split_position <= 0:
                split_position = remaining.rfind(
                    " ",
                    0,
                    available_length + 1,
                )
            if split_position <= 0:
                split_position = available_length

            chunks.append(
                remaining[:split_position].strip()
            )
            remaining = remaining[split_position:].strip()

    if current_chunk:
        chunks.append(current_chunk)

    return chunks
async def send_telegram_attachment(
    *,
    bot: Bot,
    chat_id: int,
    attachment: dict,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    attachment_type = attachment.get("type")
    file_id = attachment.get("file_id")

    if not file_id:
        return

    normalized_caption = (
        str(caption or "").strip()[:1000]
        or None
    )

    if attachment_type == "photo":
        return await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=normalized_caption,
            reply_markup=reply_markup,
        )

    if attachment_type == "document":
        return await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=normalized_caption,
            reply_markup=reply_markup,
        )