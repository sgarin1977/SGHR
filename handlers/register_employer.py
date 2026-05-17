from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from fsm.register import EmployerRegistration
from aiogram import F, Router

router = Router()

@router.message(EmployerRegistration.waiting_for_company_name)
async def employer_company_name(message: Message, state: FSMContext):
    await state.update_data(company_name=message.text)
    await state.set_state(EmployerRegistration.waiting_for_company_type)
    await message.answer("Укажите тип вашей компании (локальная/международная):")


@router.message(EmployerRegistration.waiting_for_company_type)
async def employer_company_type(message: Message, state: FSMContext):
    await state.update_data(company_type=message.text)
    await state.set_state(EmployerRegistration.waiting_for_region)
    await message.answer("Укажите регион, в котором вы работаете:")


@router.message(EmployerRegistration.waiting_for_region)
async def employer_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await state.set_state(EmployerRegistration.waiting_for_contact)
    await message.answer("Оставьте контакт для связи с вами (email или Telegram):")


@router.message(EmployerRegistration.waiting_for_contact)
async def employer_contactr(message: Message, state: FSMContext):
    await state.update_data(contact=message.text)
    await state.set_state(EmployerRegistration.waiting_for_language)
    await message.answer("Укажите язык общения (например, ru, pt, en):")


@router.message(EmployerRegistration.waiting_for_language)
async def employer_language(message: Message, state: FSMContext):
    await state.update_data(language=message.text)
    data = await state.get_data()
    await message.answer(f"Спасибо! Вы зарегистрированы как работодатель:"
                         f"Компания: {data['company_name']}"
                         f"Тип: {data['company_type']}"
                         f"Регион: {data['region']}"
                         f"Контакт: {data['contact']}"
                         f"Язык: {data['language']}")
    await state.clear()
