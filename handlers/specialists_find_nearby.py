
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram import F
from sqlalchemy import select
from database.session import async_session
from database.models import Specialist, Location, Profession
from ui.texts import t
from specialists.specialists import router
from math import radians, cos, sin, asin, sqrt

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

@router.callback_query(F.data == "find_nearby_specialists")
async def request_location(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton(text=t("send_location", lang), request_location=True))
    await callback.message.answer(t("request_location_prompt", lang), reply_markup=kb)
    await callback.answer()

@router.message(F.location)
async def handle_location(message: Message):
    lat, lon = message.location.latitude, message.location.longitude
    lang = message.from_user.language_code or "ru"

    async with async_session() as session:
        result = await session.execute(
            select(Specialist, Profession.name_ru, Location.name)
            .join(Profession, Specialist.profession_id == Profession.id)
            .join(Location, Specialist.location_id == Location.id)
            .where(Specialist.latitude.isnot(None), Specialist.longitude.isnot(None), Specialist.status == "active")
        )
        all_specialists = result.all()

    distances = []
    for specialist, prof, loc in all_specialists:
        dist = haversine(lat, lon, specialist.latitude, specialist.longitude)
        distances.append((dist, specialist, prof, loc))

    distances.sort(key=lambda x: x[0])
    top = distances[:5]

    if not top:
        await message.answer(t("no_specialists_found", lang))
        return

    for dist, specialist, prof, loc in top:
        text = f"👤 <b>{specialist.full_name}</b>\n🛠 {prof}\n🏙 {loc}\n📞 {specialist.contacts}\n📏 {dist:.1f} км"
        await message.answer(text)
