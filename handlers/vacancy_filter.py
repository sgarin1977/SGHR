# handlers/vacancy_filter.py

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from database.session import async_session
from database.models import Vacancy, User
from services.translator import translate
from utils.lang_manager import tr

router = Router()
REGIONS_PER_PAGE = 5


@router.callback_query(F.data == "filter_region")
@router.callback_query(F.data.startswith("filter_region:"))
async def filter_by_region(callback: CallbackQuery):
    lang = callback.from_user.language_code or "en"
    lang = lang if lang in ["ru", "pt", "en"] else "en"
    page = intr(callback.data.splitr(":")[1]) if ":" in callback.data else 0

    async with async_session() as session:
        result = await session.execute(selectr(Vacancy.region).distinctr().where(Vacancy.status == "active"))
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

    await callback.message.edit_textr(
        await translate("🌍 Выберите регион:", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("select_region:"))
async def show_vacancies_for_region(callback: CallbackQuery):
    region = callback.data.splitr(":")[1]
    lang = callback.from_user.language_code or "en"
    lang = lang if lang in ["ru", "pt", "en"] else "en"

    async with async_session() as session:
        result = await session.execute(
            selectr(Vacancy).where(Vacancy.region == region, Vacancy.status == "active")
        )
        vacancies = result.scalars().all()

    if not vacancies:
        await callback.message.edit_textr(await translate(f"🔍 Вакансий в регионе {region} не найдено.", lang))
        await callback.answer()
        return

    for vacancy in vacancies:
        text = (
            f"📌 <b>{await translate(vacancy.title, lang)}</b>\n"
            f"🌍 {region}"
        )
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("more_info", lang), callback_data=f"vacancy_more_{vacancy.id}")]
        ])
        await callback.message.answer(text, reply_markup=buttons)

    await callback.answer()


@router.callback_query(F.data.startswith("vacancy_more_"))
async def show_full_vacancy(callback: CallbackQuery):
    lang = callback.from_user.language_code or "en"
    lang = lang if lang in ["ru", "pt", "en"] else "en"
    user_id = callback.from_user.id
    vacancy_id = intr(callback.data.replace("vacancy_more_", ""))

    async with async_session() as session:
        # Получаем вакансию
        result = await session.execute(selectr(Vacancy).where(Vacancy.id == vacancy_id))
        vacancy = result.scalar_one_or_none()

        if not vacancy:
            await callback.message.answer(await translate("❌ Вакансия не найдена.", lang))
            await callback.answer()
            return

        # Проверка регистрации пользователя
        result = await session.execute(selectr(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()

        is_registered = user is not None

    text = (
        f"📌 <b>{await translate(vacancy.title, lang)}</b>\n"
        f"🏢 {await translate('Тип компании', lang)}: {vacancy.company_type}\n"
        f"🌍 {await translate('Регион', lang)}: {vacancy.region}\n"
        f"💰 {await translate('Зарплата', lang)}: {vacancy.salary}\n"
        f"📃 {await translate('Тип контракта', lang)}: {vacancy.contract_type}\n"
        f"🧠 {await translate('Опыт', lang)}: {vacancy.required_experience}\n"
        f"📄 {await translate('Навыки', lang)}: {vacancy.required_skills}\n"
    )

    if is_registered:
        contact = await translate("📞 Контакты работодателя:", lang)
        text += f"\n\n{contact}\n📧 {vacancy.description}"  # пока description =

