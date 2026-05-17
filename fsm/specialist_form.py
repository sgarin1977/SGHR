from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from database.session import async_session
from database.models import Direction, Profession, Specialist, User
from ui.buttons.menu import main_menu
from ui.ui_buttons import back_button
from utils.geocode_utils import get_location_by_coords, get_coords_by_city
from utils.translate_utils import tr
from logger import log
from datetime import datetime

specialist_form_router = Router()

class SpecialistForm(StatesGroup):
    choosing_direction = State()
    choosing_profession = State()
    choosing_location_mode = State()
    manual_country = State()
    manual_city = State()
    geo_location = State()
    entering_name = State()
    entering_description = State()
    entering_contact = State()
    confirming = State()

PER_PAGE = 6

def paginate_buttons(items, prefix, page, lang):
    start = page * PER_PAGE
    end = start + PER_PAGE
    buttons = []
    row = []
    for i, item in enumerate(items[start:end], start=1):
        row.append(InlineKeyboardButton(text=item.name_ru, callback_data=f"{prefix}:{item.id}"))
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_page:{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_page:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([back_button])
    log.debug(f"[REG_SPEC] Построена пагинация для {prefix}, страница {page}, всего элементов: {len(items)}")
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@specialist_form_router.callback_query(F.data == "register_specialist")
async def register_specialist(call: CallbackQuery, state: FSMContext):
    lang = call.from_user.language_code or "ru"
    log.info(f"[REG_SPEC] Начало регистрации специалиста: {call.from_user.id}")
    await state.clear()
    async with async_session() as session:
        existing = await session.execute(select(Specialist).where(Specialist.user_id == call.from_user.id))
        if existing.scalars().first():
            log.info(f"[REG_SPEC] Уже зарегистрирован: {call.from_user.id}")
            await call.message.edit_text(tr("already_registered", lang))
            return
        directions = (await session.execute(select(Direction))).scalars().all()
    await state.update_data(directions=[d.id for d in directions], direction_page=0)
    keyboard = paginate_buttons(directions, "direction", 0, lang)
    await call.message.edit_text(tr("choose_direction", lang), reply_markup=keyboard)
    await state.set_state(SpecialistForm.choosing_direction)

@specialist_form_router.callback_query(F.data.startswith("direction:"))
async def choose_direction(call: CallbackQuery, state: FSMContext):
    direction_id = int(call.data.split(":" )[1])
    log.info(f"[REG_SPEC] Выбран направление: {direction_id} пользователем {call.from_user.id}")
    lang = call.from_user.language_code or "ru"
    await state.update_data(direction_id=direction_id)
    async with async_session() as session:
        professions = (await session.execute(select(Profession).where(Profession.direction_id == direction_id))).scalars().all()
    await state.update_data(professions=[p.id for p in professions], profession_page=0)
    keyboard = paginate_buttons(professions, "profession", 0, lang)
    await call.message.edit_text(tr("choose_profession", lang), reply_markup=keyboard)
    await state.set_state(SpecialistForm.choosing_profession)

@specialist_form_router.callback_query(F.data.startswith("direction_page:"))
async def paginate_directions(call: CallbackQuery, state: FSMContext):
    page = int(call.data.split(":" )[1])
    log.info(f"[REG_SPEC] Пагинация направлений: страница {page}")
    lang = call.from_user.language_code or "ru"
    async with async_session() as session:
        directions = (await session.execute(select(Direction))).scalars().all()
    keyboard = paginate_buttons(directions, "direction", page, lang)
    await call.message.edit_text(tr("choose_direction", lang), reply_markup=keyboard)

@specialist_form_router.callback_query(F.data.startswith("profession_page:"))
async def paginate_professions(call: CallbackQuery, state: FSMContext):
    page = int(call.data.split(":" )[1])
    data = await state.get_data()
    direction_id = data.get("direction_id")
    log.info(f"[REG_SPEC] Пагинация профессий по направлению {direction_id}: страница {page}")
    lang = call.from_user.language_code or "ru"
    async with async_session() as session:
        professions = (await session.execute(select(Profession).where(Profession.direction_id == direction_id))).scalars().all()
    keyboard = paginate_buttons(professions, "profession", page, lang)
    await call.message.edit_text(tr("choose_profession", lang), reply_markup=keyboard)

@specialist_form_router.callback_query(F.data.startswith("profession:"))
async def choose_profession(call: CallbackQuery, state: FSMContext):
    profession_id = int(call.data.split(":" )[1])
    log.info(f"[REG_SPEC] Выбран профессия: {profession_id}")
    lang = call.from_user.language_code or "ru"
    await state.update_data(profession_id=profession_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("send_location", lang), callback_data="geo_location")],
        [InlineKeyboardButton(text=tr("enter_manually", lang), callback_data="manual_location")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="register_specialist")]
    ])
    await call.message.edit_text(tr("choose_location_mode", lang), reply_markup=keyboard)
    await state.set_state(SpecialistForm.choosing_location_mode)

@specialist_form_router.callback_query(F.data == "manual_location")
async def manual_location(call: CallbackQuery, state: FSMContext):
    log.info(f"[REG_SPEC] Пользователь выбрал ручной ввод локации.")
    lang = call.from_user.language_code or "ru"

    current = await state.get_state()
    log.debug(f"[FSM] BEFORE manual_country → текущее состояние: {current}")
    await state.set_state(SpecialistForm.manual_country)
    current = await state.get_state()
    log.debug(f"[FSM] AFTER manual_country → новое состояние: {current}")
    await call.message.edit_text(tr("enter_country", lang))
    await call.answer()

@specialist_form_router.callback_query(F.data == "geo_location")
async def geo_location_prompt(call: CallbackQuery, state: FSMContext):
    log.info(f"[REG_SPEC] Пользователь выбрал отправку геолокации.")
    lang = call.from_user.language_code or "ru"
    await state.set_state(SpecialistForm.geo_location)
    current = await state.get_state()
    log.debug(f"[FSM] AFTER geo_location → новое состояние: {current}")
    await call.message.edit_text(tr("send_geo", lang))

@specialist_form_router.message(SpecialistForm.manual_country)
async def manual_country(msg: Message, state: FSMContext):
    log.info(f"[FSM-DEBUG] manual_country HANDLER CALLED")
    print("[FSM-DEBUG] manual_country HANDLER CALLED")
    log.info(f"[REG_SPEC] Введена страна: {msg.text}")
    await state.update_data(country=msg.text)
    await msg.answer(tr("enter_city", msg.from_user.language_code))
    await state.set_state(SpecialistForm.manual_city)

@specialist_form_router.message(SpecialistForm.manual_city)
async def manual_city(msg: Message, state: FSMContext):
    data = await state.get_data()
    log.info(f"[FSM-DEBUG] manual_city HANDLER CALLED")
    print("[FSM-DEBUG] manual_city HANDLER CALLED")
    log.info(f"[FSM-DEBUG] ДО get_coords_by_city: country={data.get('country')} city={msg.text}")
    print(f"[FSM-DEBUG] ДО get_coords_by_city: country={data.get('country')} city={msg.text}")
    try:
        coords = await get_coords_by_city(data['country'], msg.text)
        log.info(f"[FSM-DEBUG] ПОСЛЕ get_coords_by_city: coords={coords}")
        print(f"[FSM-DEBUG] ПОСЛЕ get_coords_by_city: coords={coords}")
    except Exception as e:
        log.error(f"[FSM-DEBUG] Ошибка при get_coords_by_city: {e}")
        print(f"[FSM-DEBUG] Ошибка при get_coords_by_city: {e}")
        coords = None
    if coords:
        log.info(f"[REG_SPEC] Введен город: {msg.text}, координаты: {coords}")
        await state.update_data(city=msg.text, latitude=coords[0], longitude=coords[1])
        await msg.answer(tr("enter_name", msg.from_user.language_code))
        await state.set_state(SpecialistForm.entering_name)
    else:
        log.warning(f"[REG_SPEC] Город не найден: {msg.text}")
        await msg.answer(tr("location_not_found", msg.from_user.language_code))

@specialist_form_router.message(SpecialistForm.geo_location)
async def geo_location(msg: Message, state: FSMContext):
    log.info(f"[FSM-DEBUG] geo_location HANDLER CALLED")
    print("[FSM-DEBUG] geo_location HANDLER CALLED")
    if not msg.location:
        await msg.answer(tr("send_geo", msg.from_user.language_code))
        return
    lat, lon = msg.location.latitude, msg.location.longitude
    log.info(f"[FSM-DEBUG] ДО get_location_by_coords: lat={lat} lon={lon}")
    print(f"[FSM-DEBUG] ДО get_location_by_coords: lat={lat} lon={lon}")
    try:
        country, city = await get_location_by_coords(lat, lon)
        log.info(f"[FSM-DEBUG] ПОСЛЕ get_location_by_coords: country={country}, city={city}")
        print(f"[FSM-DEBUG] ПОСЛЕ get_location_by_coords: country={country}, city={city}")
    except Exception as e:
        log.error(f"[FSM-DEBUG] Ошибка при get_location_by_coords: {e}")
        print(f"[FSM-DEBUG] Ошибка при get_location_by_coords: {e}")
        country, city = None, None
    if country and city:
        log.info(f"[REG_SPEC] Получена геолокация: {country}, {city}, ({lat}, {lon})")
        await state.update_data(
            country=country,
            city=city,
            latitude=lat,
            longitude=lon
        )
        await msg.answer(tr("enter_name", msg.from_user.language_code))
        await state.set_state(SpecialistForm.entering_name)
    else:
        log.warning("[REG_SPEC] Геолокация не распознана.")
        await msg.answer(tr("location_not_found", msg.from_user.language_code))

@specialist_form_router.message(SpecialistForm.entering_name)
async def enter_name(msg: Message, state: FSMContext):
    log.info(f"[REG_SPEC] Имя: {msg.text}")
    await state.update_data(full_name=msg.text)
    await msg.answer(tr("enter_description", msg.from_user.language_code))
    await state.set_state(SpecialistForm.entering_description)

@specialist_form_router.message(SpecialistForm.entering_description)
async def enter_description(msg: Message, state: FSMContext):
    log.info(f"[REG_SPEC] Описание: {msg.text}")
    await state.update_data(description=msg.text)
    await msg.answer(tr("enter_contact", msg.from_user.language_code))
    await state.set_state(SpecialistForm.entering_contact)

@specialist_form_router.message(SpecialistForm.entering_contact)
async def enter_contact(msg: Message, state: FSMContext):
    log.info(f"[REG_SPEC] Контакт: {msg.text}")
    await state.update_data(contacts=msg.text)
    data = await state.get_data()
    lang = msg.from_user.language_code or "ru"

    summary = (
        f"\U0001F464 <b>{data['full_name']}</b>\n"
        f"\U0001F30D {data.get('country', '')}, {data.get('city', '')}\n"
        f"\U0001F4DE {data['contacts']}\n"
        f"\U0001F4DD {data['description']}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("confirm_data", lang), callback_data="confirm_specialist")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="register_specialist")]
    ])
    await msg.answer(summary, reply_markup=keyboard)
    await state.set_state(SpecialistForm.confirming)

@specialist_form_router.callback_query(F.data == "confirm_specialist")
async def confirm_specialist(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    async with async_session() as session:
        user_obj = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
        user = user_obj.scalar_one_or_none()
        if not user:
            user = User(
                telegram_id=call.from_user.id,
                full_name=call.from_user.full_name or "",
                language=call.from_user.language_code or "ru",
                last_login=datetime.utcnow()
            )
            session.add(user)
            await session.flush()

        specialist = Specialist(
            user_id=user.id,
            direction_id=data.get("direction_id"),
            profession_id=data.get("profession_id"),
            full_name=data.get("full_name"),
            description=data.get("description"),
            contacts=data.get("contacts"),
            region=f"{data.get('country', '')}, {data.get('city', '')}",
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            location_updated_at=datetime.utcnow(),
            status="active",
            imported=False
        )
        session.add(specialist)
        await session.commit()

    log.info(f"[REG_SPEC] Успешно зарегистрирован: {call.from_user.id}")
    lang = call.from_user.language_code or "ru"
    await call.message.edit_text(tr("registration_success_specialist", lang))
    await state.clear()
    # Показываем главное меню после регистрации
    reply_markup = main_menu(lang)
    await call.message.answer(tr("choose_section", lang), reply_markup=reply_markup)

