from aiogram.fsm.state import StatesGroup, State


class SeekerRegistration(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_profession = State()
    waiting_for_region = State()
    waiting_for_language = State()
    waiting_for_status = State()


class EmployerRegistration(StatesGroup):
    waiting_for_company_name = State()
    waiting_for_company_type = State()
    waiting_for_region = State()
    waiting_for_contact = State()
    waiting_for_language = State()