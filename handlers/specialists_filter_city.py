
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from sqlalchemy import select
from database.session import async_session
from database.models import Location, Specialist, Profession
from ui.texts import t
from handlers.specialists import router

PER_PAGE = 10

@router.callback_query(F.data == "filter_specialist_city")
@router.callback_query(F.data.startswith("filter_specialist_city:"))
async def show_city_list(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    page = int(callback.data.split(":")[1]) if ":" in callback.data else 0
    offset = page * PER_PAGE

    async with async_session() as session:
        result = await session.execute(
            select(Location).where(Location.is_active == True).order_by(Location.name_ru)
        )
        cities = result.scalars().all()

    if not cities:
        await callback.message.edit_text(t("no_cities", lang))
        await callback.answer()
        return

    paginated = cities[offset:offset + PER_PAGE]

    keyboard = [
        [InlineKeyboardButton(text=c.name_ru, callback_data=f"specialist_city:{c.id}")]
        for c in paginated
    ]

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"filter_specialist_city:{page - 1}"))
    if offset + PER_PAGE < len(cities):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"filter_specialist_city:{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    await callback.message.edit_text(t("choose_city", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@router.callback_query(F.data.startswith("specialist_city:"))
async def list_specialists_by_city(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    location_id = int(callback.data.split(":")[1])

    async with async_session() as session:
        result = await session.execute(
            select(Specialist, Profession.name_ru)
            .join(Profession, Specialist.profession_id == Profession.id)
            .where(Specialist.location_id == location_id, Specialist.status == "active")
        )
        specialists = result.all()

    if not specialists:
        await callback.message.edit_text(t("no_specialists_found", lang))
        await callback.answer()
        return

    for specialist, profession in specialists:
        text = f"👤 <b>{specialist.full_name}</b>\n🛠 {profession}\n📞 {specialist.contacts}"
        await callback.message.answer(text)

    await callback.answer()
