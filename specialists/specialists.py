
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from ui.texts import t

router = Router()

@router.callback_query(F.data == "view_specialists")
async def show_specialist_filters(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("filter_by_profession", lang), callback_data="filter_specialist_profession")],
        [InlineKeyboardButton(text=t("filter_by_city", lang), callback_data="filter_specialist_city")],
        [InlineKeyboardButton(text=t("find_nearby", lang), callback_data="find_nearby_specialists")],
    ])
    await callback.message.edit_text(t("choose_filter", lang), reply_markup=keyboard)
    await callback.answer()
