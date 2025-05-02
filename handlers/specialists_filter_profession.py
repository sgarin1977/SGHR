
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from sqlalchemy import select
from database.session import async_session
from database.models import Profession, Specialist, Location
from ui.texts import t
from handlers.specialists import router

PER_PAGE = 10

@router.callback_query(F.data == "filter_specialist_profession")
@router.callback_query(F.data.startswith("filter_specialist_profession:"))
async def show_profession_list(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    page = int(callback.data.split(":")[1]) if ":" in callback.data else 0
    offset = page * PER_PAGE

    async with async_session() as session:
        result = await session.execute(
            select(Profession).where(Profession.is_active == True).order_by(Profession.name_ru)
        )
        professions = result.scalars().all()

    if not professions:
        await callback.message.edit_text(t("no_professions", lang))
        await callback.answer()
        return

    paginated = professions[offset:offset + PER_PAGE]

    keyboard = [
        [InlineKeyboardButton(text=p.name_ru, callback_data=f"specialist_profession:{p.id}")]
        for p in paginated
    ]

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"filter_specialist_profession:{page - 1}"))
    if offset + PER_PAGE < len(professions):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"filter_specialist_profession:{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    await callback.message.edit_text(t("choose_profession", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@router.callback_query(F.data.startswith("specialist_profession:"))
async def list_specialists_by_profession(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    profession_id = int(callback.data.split(":")[1])

    async with async_session() as session:
        result = await session.execute(
            select(Specialist, Location.name)
            .join(Location, Specialist.location_id == Location.id)
            .where(Specialist.profession_id == profession_id, Specialist.status == "active")
        )
        specialists = result.all()

    if not specialists:
        await callback.message.edit_text(t("no_specialists_found", lang))
        await callback.answer()
        return

    for specialist, city in specialists:
        text = f"👤 <b>{specialist.full_name}</b>\n🏙 <i>{city}</i>\n📞 {specialist.contacts}"
        await callback.message.answer(text)

    await callback.answer()
