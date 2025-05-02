from aiogram import Router, F
from aiogram.types import CallbackQuery
from ui.texts import t
from services.translator import translate

router = Router()

@router.callback_query(F.data == "find_job")
async def handle_find_job(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"

    text = await translate(
        "🔍 Здесь будут фильтры и список вакансий по вашему региону и профессии.\n"
        "Функционал находится в разработке.", to_lang=lang
    )

    await callback.message.edit_text(text)
    await callback.answer()

