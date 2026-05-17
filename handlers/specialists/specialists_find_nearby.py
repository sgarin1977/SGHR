from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from sqlalchemy import select
from database.session import async_session
from database.models import Specialist, Location, Profession
from utils.lang_manager import tr

from math import radians, cos, sin, asin, sqrt
from services.translator import translate as auto_translate

from logger import log
from aiogram import Router
router = Router()

DEFAULT_RADIUS = 10
PER_PAGE = 6

user_search_state = {}

radius_buttons = [
    [
        InlineKeyboardButton(text="3 км", callback_data="set_radius:3"),
        InlineKeyboardButton(text="10 км", callback_data="set_radius:10"),
        InlineKeyboardButton(text="15 км", callback_data="set_radius:15"),
        InlineKeyboardButton(text="300 км", callback_data="set_radius:300")
    ],
    [InlineKeyboardButton(text="◀️ Назад", callback_data="find_specialist")]
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

@router.callback_query(F.data == "find_nearby_specialists")
async def choose_category(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    log.info(f"[CALLBACK] Пользователь {callback.from_user.id} вызвал find_nearby_specialists")

    async with async_session() as session:
        result = await session.execute(
            select(Profession).where(Profession.is_active == True).order_by(Profession.name_ru)
        )
        professions = result.scalars().all()

    keyboard = []
    row = []
    for i, p in enumerate(professions, 1):
        translated_name = await auto_translate(getattr(p, "name_ru"), lang)
        row.append(InlineKeyboardButton(text=translated_name, callback_data=f"select_category:{p.id}"))
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard += radius_buttons

    await callback.message.edit_text(tr("choose_profession", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@router.callback_query(F.data.startswith("select_category:"))
async def request_location(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    profession_id = int(callback.data.split(":")[1])
    user_search_state[callback.from_user.id] = {"profession_id": profession_id, "radius": DEFAULT_RADIUS}
    log.info(f"[CALLBACK] Пользователь {callback.from_user.id} выбрал категорию {profession_id}")

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=tr("send_location", lang), request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await callback.message.delete()
    await callback.message.answer(tr("request_location_prompt", lang), reply_markup=kb)
    await callback.answer()

@router.message(F.location)
async def handle_location(message: Message):
    lat, lon = message.location.latitude, message.location.longitude
    lang = message.from_user.language_code or "ru"
    user_id = message.from_user.id
    state = user_search_state.get(user_id)

    log.info(f"[LOCATION] Пользователь {user_id} отправил геопозицию: {lat}, {lon}")

    if not state:
        await message.answer("Ошибка: не выбрана категория для поиска.")
        return

    state["location"] = (lat, lon)
    await show_nearby_specialists(message, lang, user_id)

@router.callback_query(F.data.startswith("set_radius:"))
async def update_radius(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    radius = int(callback.data.split(":")[1])
    state = user_search_state.get(callback.from_user.id)

    if state:
        state["radius"] = radius
        log.info(f"[RADIUS] Пользователь {callback.from_user.id} выбрал радиус {radius} км")

    await callback.message.delete()
    await show_nearby_specialists(callback.message, lang, callback.from_user.id)
    await callback.answer()

async def show_nearby_specialists(message, lang, user_id):
    state = user_search_state.get(user_id)
    if not state or "location" not in state:
        await message.answer("Ошибка: отсутствуют координаты.")
        return

    lat, lon = state["location"]
    radius = state.get("radius", DEFAULT_RADIUS)
    profession_id = state["profession_id"]
    log.info(f"[SHOW] Поиск специалистов в радиусе {radius} км по профессии {profession_id} для пользователя {user_id}")

    async with async_session() as session:
        result = await session.execute(
            select(Specialist, Profession.name_ru, Location.name)
            .join(Profession, Specialist.profession_id == Profession.id)
            .join(Location, Specialist.location_id == Location.id)
            .where(Specialist.latitude.isnot(None), Specialist.longitude.isnot(None), Specialist.status == "active")
            .where(Specialist.profession_id == profession_id)
        )
        all_specialists = result.all()

    distances = []
    for specialist, prof, loc in all_specialists:
        dist = haversine(lat, lon, specialist.latitude, specialist.longitude)
        if dist <= radius:
            distances.append((dist, specialist, prof, loc))

    distances.sort(key=lambda x: x[0])
    top = distances[:6]

    text_blocks = []
    if not top:
        text_blocks.append(tr("no_specialists_found", lang))
    else:
        for dist, specialist, prof, loc in top:
            translated_prof = await auto_translate(prof or "", lang)
            translated_loc = await auto_translate(loc or "", lang)
            translated_desc = await auto_translate(specialist.short_description or "", lang)

            text_blocks.append(
                f"👤 <b>{specialist.full_name}</b>\n"
                f"🏙 {translated_loc}\n"
                f"💼 {translated_prof}\n"
                f"📞 {specialist.contacts}\n"
                f"📝 {translated_desc}\n"
                f"📏 {dist:.1f} км"
            )

    full_text = "\n\n".join(text_blocks)
    await message.answer(full_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=radius_buttons))

