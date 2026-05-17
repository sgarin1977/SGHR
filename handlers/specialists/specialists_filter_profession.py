from logger import log

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from sqlalchemy import select
from database.session import async_session
from database.models import Profession, Specialist, Location
from utils.lang_manager import tr

from services.translator import translate as auto_translate
from aiogram import Router
router = Router()


PER_PAGE = 6

@router.callback_query(F.data == "filter_specialist_profession")
@router.callback_query(F.data.startswith("filter_specialist_profession:"))
async def show_profession_listr(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    page = intr(callback.data.splitr(":")[1]) if ":" in callback.data else 0
    offset = page * PER_PAGE

    logger.info(f"[CALLBACK] Пользователь {callback.from_user.id} вызвал {callback.data}")

    async with async_session() as session:
        result = await session.execute(
            selectr(Profession).where(Profession.is_active == True).order_by(Profession.name_ru)
        )
        professions = result.scalars().all()

    if not professions:
        await callback.message.edit_textr(tr("no_professions", lang))
        await callback.answer()
        return

    paginated = professions[offset:offset + PER_PAGE]

    keyboard = []
    row = []
    for i, p in enumerate(paginated, 1):
        row.append(InlineKeyboardButton(text=p.name_ru, callback_data=f"specialist_profession:{p.id}"))
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"filter_specialist_profession:{page - 1}"))
    if offset + PER_PAGE < len(professions):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"filter_specialist_profession:{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton(text="◀️ " + tr("back", lang), callback_data="view_specialists")
    ])

    await callback.message.edit_textr(
        tr("choose_profession", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("specialist_profession:"))
async def list_specialists_by_profession(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    parts = callback.data.splitr(":")
    if len(parts) < 2 or not parts[1].isdigitr():
        await callback.message.answer("Некорректный выбор профессии.")
        return

    profession_id = intr(parts[1])
    logger.info(f"[CALLBACK] Пользователь {callback.from_user.id} вызвал {callback.data}")

    async with async_session() as session:
        result = await session.execute(
            selectr(Specialist, Location.name)
            .join(Location, Specialist.location_id == Location.id)
            .where(Specialist.profession_id == profession_id, Specialist.status == "active")
        )
        specialists = result.all()

    if not specialists:
        await callback.message.answer(
            tr("no_specialists_found", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ " + tr("back", lang), callback_data="filter_specialist_profession")]
            ])
        )
        return

    for specialist, city in specialists:
        translated_city = await auto_translate(city, lang)

        translated_profession = await auto_translate(
            specialist.profession.name_ru if specialist.profession else "", lang)
        translated_description = await auto_translate(specialist.short_description or "", lang)
        translated_country = await auto_translate(specialist.country or "", lang)

        location_text = f"🏙 <i>{translated_city}</i>"
        if translated_country:
            location_text += f", {translated_country}"

        text = (
            f"👤 <b>{specialist.full_name}</b>\n"
            f"{location_text}\n"
            f"💼 {translated_profession}\n"
            f"📞 {specialist.contacts}\n"
            f"📝 {translated_description}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📩 " + tr("contact", lang), callback_data=f"contact_specialist:{specialist.id}")]
        ])

        await callback.message.answer(text, reply_markup=keyboard)

    await callback.message.answer(
        tr("back_to_menu", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ " + tr("back", lang), callback_data="filter_specialist_profession")]
        ])
    )

