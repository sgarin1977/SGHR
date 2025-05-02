from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from database.session import async_session
from database.models import Vacancy
from ui.texts import t
from services.translator import translate

router = Router()
REGIONS_PER_PAGE = 5


@router.callback_query(F.data == "find_job")
async def handle_find_job(callback: CallbackQuery):
    lang = callback.from_user.language_code or "en"
    lang = lang if lang in ["ru", "pt", "en"] else "en"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=await translate("📋 Последние вакансии", lang), callback_data="latest_vacancies")],
        [InlineKeyboardButton(text=await translate("🔎 Поиск по фильтру", lang), callback_data="filter_region")]
    ])
    await callback.message.answer(await translate("Выберите способ просмотра вакансий:", lang), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "latest_vacancies")
async def show_latest_vacancies(callback: CallbackQuery):
    lang = callback.from_user.language_code or "en"
    lang = lang if lang in ["ru", "pt", "en"] else "en"

    async with async_session() as session:
        result = await session.execute(
            select(Vacancy).where(Vacancy.status == "active").order_by(Vacancy.created_at.desc()).limit(10)
        )
        vacancies = result.scalars().all()

    if not vacancies:
        await callback.message.answer(await translate("🔍 Вакансии не найдены.", lang))
        await callback.answer()
        return

    for vacancy in vacancies:
        text = (
            f"📌 <b>{vacancy.title}</b>\n"
            f"🏢 {await translate('Тип компании', lang)}: {vacancy.company_type}\n"
            f"🌍 {await translate('Регион', lang)}: {vacancy.region}\n"
            f"💰 {await translate('Зарплата', lang)}: {vacancy.salary}\n"
            f"📃 {await translate('Тип контракта', lang)}: {vacancy.contract_type}\n"
            f"🧠 {await translate('Опыт', lang)}: {vacancy.required_experience}\n"
            f"📄 {await translate('Навыки', lang)}: {vacancy.required_skills}"
        )
        await callback.message.answer(text)

    await callback.answer()


@router.callback_query(F.data.startswith("filter_region"))
async def filter_by_region(callback: CallbackQuery):
    lang = callback.from_user.language_code or "en"
    lang = lang if lang in ["ru", "pt", "en"] else "en"
    page = int(callback.data.split(":")[1]) if ":" in callback.data else 0

    async with async_session() as session:
        result = await session.execute(select(Vacancy.region).distinct().where(Vacancy.status == "active"))
        all_regions = sorted({r[0] for r in result.fetchall() if r[0]})

    start = page * REGIONS_PER_PAGE
    end = start + REGIONS_PER_PAGE
    regions_page = all_regions[start:end]

    keyboard = [
        [InlineKeyboardButton(text=region, callback_data=f"select_region:{region}")]
        for region in regions_page
    ]

    nav_buttons = []
    if start > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"filter_region:{page - 1}"))
    if end < len(all_regions):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"filter_region:{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    await callback.message.edit_text(
        await translate("🌍 Выберите регион:", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
