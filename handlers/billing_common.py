from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_tracked_menu_message,
)


from aiogram.types import CallbackQuery
async def replace_billing_input_screen(
    *,
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup | None
    ) = None,
) -> None:
    data = await state.get_data()

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
            text=text,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_menu_message_id=menu_message_id,
    )


async def clear_cross_feature_messages(
    *,
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()

    tracked_message_ids = [
        *(
            data.get(
                "support_list_message_ids"
            )
            or []
        ),
        *(
            data.get(
                "last_search_result_message_ids"
            )
            or []
        ),
        *(
            data.get(
                "last_contact_chat_message_ids"
            )
            or []
        ),
        *(
            data.get(
                "dialog_list_message_ids"
            )
            or []
        ),
        *(
            data.get(
                "cabinet_favorite_message_ids"
            )
            or []
        ),
        *(
            data.get(
                "owner_portfolio_message_ids"
            )
            or []
        ),
        data.get("last_menu_message_id"),
    ]

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            int(message_id)
            for message_id in tracked_message_ids
            if message_id
        ],
    )

    await state.update_data(
        support_list_message_ids=[],
        last_search_result_message_ids=[],
        last_contact_chat_message_ids=[],
        dialog_list_message_ids=[],
        cabinet_favorite_message_ids=[],
        owner_portfolio_message_ids=[],
        last_menu_message_id=None,
    )
