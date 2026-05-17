from aiogram import Router, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from database.models import Direction, Profession, Specialist, Partner
from database.session import async_session
from utils.translate_utils import tr, get_lang
from ui.buttons.menu import main_menu
from utils.geocode_utils import get_coords_by_city, haversine
from services.user import get_user_by_telegram_id
from logger import log

router = Router()
PER_PAGE = 6

class SpecSearchFSM(StatesGroup):
    choosing_direction = State()
    choosing_profession = State()
    choosing_filter = State()
    entering_country = State()
    entering_city = State()
    awaiting_geo = State()
    showing_specialists = State()
    showing_specialist_card = State()

user_search_state = {}

def chunk_buttons(items, prefix, lang, row_size=2):
    rows = []
    row = []
    for idx, obj in enumerate(items, 1):
        row.append(InlineKeyboardButton(text=obj.name_ru, callback_data=f"{prefix}:{obj.id}"))
        if idx % row_size == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows

@router.callback_query(F.data == "find_specialist")
async def start_spec_search(call: CallbackQuery, state: FSMContext):
    lang = get_lang(call)
    log.info(f"[SPEC_SEARCH] Start for user {call.from_user.id}")
    async with async_session() as session:
        directions = (await session.execute(select(Direction))).scalars().all()
    kb = chunk_buttons(directions, "spec_dir", lang, row_size=2)
    kb.append([InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_menu")])
    await call.message.edit_text(tr("choose_direction", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(SpecSearchFSM.choosing_direction)
    user_search_state[call.from_user.id] = {}

@router.callback_query(F.data.startswith("spec_dir:"))
async def choose_profession(call: CallbackQuery, state: FSMContext):
    direction_id = int(call.data.split(":")[1])
    lang = get_lang(call)
    user_search_state[call.from_user.id]["direction_id"] = direction_id
    async with async_session() as session:
        professions = (await session.execute(select(Profession).where(Profession.direction_id == direction_id))).scalars().all()
    kb = chunk_buttons(professions, "spec_prof", lang, row_size=2)
    kb.append([InlineKeyboardButton(text=tr("all_professions", lang), callback_data="spec_prof:all")])
    kb.append([InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_directions")])
    await call.message.edit_text(tr("choose_profession", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(SpecSearchFSM.choosing_profession)

@router.callback_query(F.data == "back_to_directions")
async def back_to_directions(call: CallbackQuery, state: FSMContext):
    await start_spec_search(call, state)

@router.callback_query(F.data.startswith("spec_prof:"))
async def choose_filter(call: CallbackQuery, state: FSMContext):
    lang = get_lang(call)
    prof_id_raw = call.data.split(":")[1]
    user_search_state[call.from_user.id]["profession_id"] = None if prof_id_raw == "all" else int(prof_id_raw)
    kb = [
        [InlineKeyboardButton(text=tr("show_all", lang), callback_data="spec_filter:all")],
        [InlineKeyboardButton(text=tr("filter_by_country", lang), callback_data="spec_filter:country")],
        [InlineKeyboardButton(text=tr("filter_by_city", lang), callback_data="spec_filter:city")],
        [InlineKeyboardButton(text=tr("find_nearby", lang), callback_data="spec_filter:geo")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_professions")]
    ]
    await call.message.edit_text(tr("choose_filter", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(SpecSearchFSM.choosing_filter)

@router.callback_query(F.data == "back_to_professions")
async def back_to_professions(call: CallbackQuery, state: FSMContext):
    direction_id = user_search_state.get(call.from_user.id, {}).get("direction_id")
    if direction_id:
        call.data = f"spec_dir:{direction_id}"
        await choose_profession(call, state)
    else:
        await start_spec_search(call, state)

@router.callback_query(F.data.startswith("spec_filter:"))
async def process_filter_choice(call: CallbackQuery, state: FSMContext):
    choice = call.data.split(":")[1]
    lang = get_lang(call)
    if choice == "all":
        await show_specialists(call, state, filter_type="all")
    elif choice == "country":
        user_search_state[call.from_user.id]["filter"] = "country"
        await call.message.edit_text(tr("enter_country_prompt", lang))
        await state.set_state(SpecSearchFSM.entering_country)
    elif choice == "city":
        user_search_state[call.from_user.id]["filter"] = "city"
        await call.message.edit_text(tr("enter_city_prompt", lang))
        await state.set_state(SpecSearchFSM.entering_city)
    elif choice == "geo":
        user_search_state[call.from_user.id]["filter"] = "geo"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("send_geo", lang), request_location=True)],
            [InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_professions")]
        ])
        await call.message.edit_text(tr("request_location_prompt", lang), reply_markup=kb)
        await state.set_state(SpecSearchFSM.awaiting_geo)

@router.message(SpecSearchFSM.entering_country)
async def input_country(msg: Message, state: FSMContext):
    user_search_state[msg.from_user.id]["country"] = msg.text.strip()
    await show_specialists(msg, state, filter_type="country")

@router.message(SpecSearchFSM.entering_city)
async def input_city(msg: Message, state: FSMContext):
    user_search_state[msg.from_user.id]["city"] = msg.text.strip()
    await show_specialists(msg, state, filter_type="city")

@router.message(SpecSearchFSM.awaiting_geo)
async def input_geo(msg: Message, state: FSMContext):
    if not msg.location:
        await msg.answer(tr("send_geo", get_lang(msg)))
        return
    user_search_state[msg.from_user.id]["coords"] = (msg.location.latitude, msg.location.longitude)
    await show_specialists(msg, state, filter_type="geo")

async def show_specialists(event, state, filter_type):
    if isinstance(event, Message):
        user_id = event.from_user.id
        lang = get_lang(event)
    else:
        user_id = event.from_user.id
        lang = get_lang(event)
    st = user_search_state.get(user_id, {})
    direction_id = st.get("direction_id")
    profession_id = st.get("profession_id")
    country = st.get("country")
    city = st.get("city")
    coords = st.get("coords")

    async with async_session() as session:
        query = select(Specialist).where(Specialist.direction_id == direction_id, Specialist.status == "active")
        if profession_id:
            query = query.where(Specialist.profession_id == profession_id)
        specialists = (await session.execute(query)).scalars().all()
        partner_ids = (await session.execute(select(Partner.user_id).where(Partner.direction_id == direction_id))).scalars().all()

    filtered = specialists
    if filter_type == "country" and country:
        filtered = [s for s in filtered if s.country and s.country.lower() == country.lower()]
    elif filter_type == "city" and city:
        filtered = [s for s in filtered if s.city and s.city.lower() == city.lower()]
    elif filter_type == "geo" and coords:
        filtered = [s for s in filtered if s.latitude and s.longitude and haversine(coords[0], coords[1], s.latitude, s.longitude) <= 100]

    if not filtered:
        if isinstance(event, Message):
            await event.answer(tr("no_specialists_found", lang))
        else:
            await event.message.edit_text(tr("no_specialists_found", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_professions")],
                [InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_menu")]
            ]))
        return

    partners = [s for s in filtered if s.user_id in partner_ids]
    others = [s for s in filtered if s.user_id not in partner_ids]
    sorted_specs = partners + others

    await render_specialist_page(event, sorted_specs, partner_ids, direction_id, profession_id, 0, lang)

async def render_specialist_page(event, specialists, partner_ids, direction_id, profession_id, page, lang):
    paginated = specialists[page * PER_PAGE:(page + 1) * PER_PAGE]
    buttons = [[
        InlineKeyboardButton(
            text=f"{s.full_name} ✨" if s.user_id in partner_ids else s.full_name,
            callback_data=f"spec_card:{s.user_id}"
        )
    ] for s in paginated]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"page:{direction_id}:{profession_id}:{page-1}"))
    if (page + 1) * PER_PAGE < len(specialists):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"page:{direction_id}:{profession_id}:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_professions")])
    buttons.append([InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_menu")])
    text = tr("select_specialist", lang)
    if isinstance(event, Message):
        await event.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        await event.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("page:"))
async def paginate_specialists(call: CallbackQuery):
    _, direction_id, profession_id, page = call.data.split(":")
    direction_id, profession_id, page = int(direction_id), int(profession_id), int(page)
    lang = get_lang(call)
    st = user_search_state.get(call.from_user.id, {})
    async with async_session() as session:
        query = select(Specialist).where(Specialist.direction_id == direction_id, Specialist.status == "active")
        if profession_id:
            query = query.where(Specialist.profession_id == profession_id)
        specialists = (await session.execute(query)).scalars().all()
        partner_ids = (await session.execute(select(Partner.user_id).where(Partner.direction_id == direction_id))).scalars().all()
    partners = [s for s in specialists if s.user_id in partner_ids]
    others = [s for s in specialists if s.user_id not in partner_ids]
    sorted_specs = partners + others
    await render_specialist_page(call, sorted_specs, partner_ids, direction_id, profession_id, page, lang)

@router.callback_query(F.data.startswith("spec_card:"))
async def show_specialist_card(call: CallbackQuery):
    user_id = int(call.data.split(":")[1])
    lang = get_lang(call)
    async with async_session() as session:
        s = (await session.execute(select(Specialist).where(Specialist.user_id == user_id))).scalar_one_or_none()
        if not s:
            await call.answer(tr("no_specialists_found", lang))
            return
        prof = (await session.execute(select(Profession).where(Profession.id == s.profession_id))).scalar_one_or_none()
        dir = (await session.execute(select(Direction).where(Direction.id == s.direction_id))).scalar_one_or_none()
    card = (
        f"👤 <b>{s.full_name}</b>\n"
        f"🧑‍🔧 <b>{prof.name_ru if prof else '-'}</b>\n"
        f"📂 <b>{dir.name_ru if dir else '-'}</b>\n"
        f"📍 <b>{s.city or ''}, {s.country or ''}</b>\n"
        f"📝 {s.description or '-'}\n"
        f"☎️ {s.contacts or '-'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_professions")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_menu")]
    ])
    await call.message.edit_text(card, reply_markup=kb)

@router.callback_query(F.data == "back_to_menu")
async def back_to_main_menu(call: CallbackQuery):
    lang = get_lang(call)
    async with async_session() as session:
        user = await get_user_by_telegram_id(session, call.from_user.id)
        log.info(f"[BACK_TO_MENU] User {call.from_user.id} returned to main menu.")
        await call.message.answer(tr("choose_section", lang), reply_markup=main_menu(lang))

