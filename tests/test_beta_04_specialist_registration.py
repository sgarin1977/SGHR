import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, select

from database.models import (
    City,
    Country,
    EventLog,
    LegalDocument,
    Profession,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistService,
    User,
    UserAccount,
    UserConsent,
    UserRoleMapping,
)
from database.repositories.legal import LegalRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from services.legal import REQUIRED_SPECIALIST_CONSENTS, LegalService
from services.specialist import (
    SpecialistRegistrationData,
    SpecialistRegistrationError,
    SpecialistService as SpecialistRegistrationService,
)


BETA_CONTACT_NOTE = "Contact inside SGHR beta chat"
LEGAL_TEST_VERSION = "test-beta-0.4"


async def cleanup_test_user(session, platform_user_id: str):
    await session.rollback()

    account_result = await session.execute(
        select(UserAccount).where(
            UserAccount.platform == "telegram",
            UserAccount.platform_user_id == platform_user_id,
        )
    )
    account = account_result.scalar_one_or_none()

    if not account:
        await session.rollback()
        return

    user_id = account.user_id

    specialist_result = await session.execute(
        select(Specialist).where(Specialist.user_id == user_id)
    )
    specialist = specialist_result.scalar_one_or_none()

    if specialist:
        await session.execute(
            delete(SpecialistService).where(SpecialistService.specialist_id == specialist.id)
        )
        await session.execute(
            delete(SpecialistLanguage).where(SpecialistLanguage.specialist_id == specialist.id)
        )
        await session.execute(
            delete(SpecialistLocation).where(SpecialistLocation.specialist_id == specialist.id)
        )
        await session.execute(delete(Specialist).where(Specialist.id == specialist.id))

    await session.execute(delete(UserConsent).where(UserConsent.user_id == user_id))
    await session.execute(delete(EventLog).where(EventLog.user_id == user_id))
    await session.execute(delete(UserRoleMapping).where(UserRoleMapping.user_id == user_id))
    await session.execute(delete(UserAccount).where(UserAccount.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def cleanup_legal_documents(session, tenant_id):
    await session.rollback()

    await session.execute(
        delete(UserConsent).where(
            UserConsent.tenant_id == tenant_id,
            UserConsent.version.like("test-beta-%"),
        )
    )
    await session.execute(
        delete(LegalDocument).where(
            LegalDocument.tenant_id == tenant_id,
            LegalDocument.version.like("test-beta-%"),
        )
    )
    await session.commit()


async def ensure_legal_documents(session, tenant_id):
    for doc_type in REQUIRED_SPECIALIST_CONSENTS:
        session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type=doc_type,
                version=LEGAL_TEST_VERSION,
                language="ru",
                title=f"{doc_type} beta 0.4 test title",
                content_text=f"{doc_type} beta 0.4 test content",
                status="active",
            )
        )

    await session.commit()


async def accept_specialist_consents(session, tenant_id, user_id):
    await ensure_legal_documents(session, tenant_id)

    service = LegalService(LegalRepository(session))
    await service.accept_required_specialist_consents(
        tenant_id=tenant_id,
        user_id=user_id,
        language="ru",
        platform="telegram",
    )


async def get_reference_data(session):
    category_result = await session.execute(
        select(SpecialistCategory).where(SpecialistCategory.is_active.is_(True)).limit(1)
    )
    category = category_result.scalar_one_or_none()
    assert category is not None, "No active specialist category found. Seed beta taxonomy first."

    profession_result = await session.execute(
        select(Profession).where(
            Profession.category_id == category.id,
            Profession.is_active.is_(True),
        ).limit(1)
    )
    profession = profession_result.scalar_one_or_none()
    assert profession is not None, "No active profession found for selected category."

    city_result = await session.execute(
        select(City).where(City.is_active.is_(True)).limit(1)
    )
    city = city_result.scalar_one_or_none()
    assert city is not None, "No active city found. Seed beta cities first."

    country = await session.get(Country, city.country_id)
    assert country is not None

    return {
        "category_id": category.id,
        "profession_id": profession.id,
        "country_id": country.id,
        "city_id": city.id,
        "latitude": city.latitude,
        "longitude": city.longitude,
    }


async def create_test_user(session):
    platform_user_id = f"test-specialist-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="test_specialist",
        first_name="Test",
        last_name="Specialist",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    assert user is not None
    assert user.tenant_id is not None

    return platform_user_id, user.id, user.tenant_id


def build_registration_data(user_id, tenant_id, refs, **overrides):
    data = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "category_id": refs["category_id"],
        "profession_id": refs["profession_id"],
        "country_id": refs["country_id"],
        "city_id": refs["city_id"],
        "display_name": "Test Specialist",
        "short_description": "Experienced specialist for beta testing.",
        "full_description": "Detailed beta test specialist profile.",
        "price_from": 50,
        "price_to": 100,
        "currency": "EUR",
        "price_unit": "hour",
        "latitude": refs["latitude"],
        "longitude": refs["longitude"],
        "service_radius_km": 25,
        "languages": ["ru", "en"],
        "service_title": "Beta service",
        "service_description": "Service created by beta 0.4 test.",
        "contact_text": BETA_CONTACT_NOTE,
        "language": "ru",
    }
    data.update(overrides)
    return SpecialistRegistrationData(**data)


async def test_create_specialist_profile_pending_moderation(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)
    refs = await get_reference_data(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await accept_specialist_consents(db_session, tenant_id, user_id)

        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        specialist = await service.create_pending_profile(
            build_registration_data(user_id, tenant_id, refs)
        )

        assert specialist.id is not None
        assert specialist.tenant_id == tenant_id
        assert specialist.user_id == user_id
        assert specialist.category_id == refs["category_id"]
        assert specialist.profession_id == refs["profession_id"]
        assert specialist.country_id == refs["country_id"]
        assert specialist.city_id == refs["city_id"]
        assert specialist.status == "pending_moderation"
        assert specialist.is_verified is False
        assert specialist.is_premium is False
        assert specialist.extra_metadata["contact_text"] == BETA_CONTACT_NOTE

        user_after_create = await db_session.get(User, user_id)
        assert user_after_create is not None
        assert user_after_create.active_role == "specialist"

        role_result = await db_session.execute(
            select(UserRoleMapping).where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.role == "specialist",
                UserRoleMapping.status == "active",
            )
        )
        specialist_role = role_result.scalar_one_or_none()
        assert specialist_role is not None

        location_result = await db_session.execute(
            select(SpecialistLocation).where(SpecialistLocation.specialist_id == specialist.id)
        )
        location = location_result.scalar_one_or_none()
        assert location is not None
        assert location.city_id == refs["city_id"]
        assert location.is_current is True
        assert location.visibility_level == "city"

        languages_result = await db_session.execute(
            select(SpecialistLanguage).where(SpecialistLanguage.specialist_id == specialist.id)
        )
        languages = languages_result.scalars().all()
        assert {item.language_code for item in languages} == {"ru", "en"}

        service_result = await db_session.execute(
            select(SpecialistService).where(SpecialistService.specialist_id == specialist.id)
        )
        specialist_service = service_result.scalar_one_or_none()
        assert specialist_service is not None
        assert specialist_service.title == "Beta service"
        assert specialist_service.status == "active"
        event_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == user_id,
                EventLog.entity_type == "specialist",
                EventLog.entity_id == specialist.id,
            )
        )
        events = event_result.scalars().all()
        event_types = {event.event_type for event in events}

        assert "specialist_profile_created" in event_types
        assert "specialist_submitted" in event_types

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_create_specialist_profile_blocked_without_required_consents(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)
    refs = await get_reference_data(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        with pytest.raises(SpecialistRegistrationError, match="Legal consents are required"):
            await service.create_pending_profile(
                build_registration_data(user_id, tenant_id, refs)
            )

        specialist_result = await db_session.execute(
            select(Specialist).where(Specialist.user_id == user_id)
        )
        assert specialist_result.scalar_one_or_none() is None

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_create_specialist_profile_blocked_when_consent_revoked(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)
    refs = await get_reference_data(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await accept_specialist_consents(db_session, tenant_id, user_id)

        consent_result = await db_session.execute(
            select(UserConsent).where(
                UserConsent.tenant_id == tenant_id,
                UserConsent.user_id == user_id,
                UserConsent.consent_type == "specialist_consent",
                UserConsent.revoked_at.is_(None),
            )
        )
        consent = consent_result.scalar_one()
        consent.revoked_at = datetime.utcnow()
        await db_session.commit()

        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        with pytest.raises(SpecialistRegistrationError, match="Legal consents are required"):
            await service.create_pending_profile(
                build_registration_data(user_id, tenant_id, refs)
            )

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_create_specialist_profile_rejects_duplicate(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)
    refs = await get_reference_data(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await accept_specialist_consents(db_session, tenant_id, user_id)

        data = build_registration_data(
            user_id,
            tenant_id,
            refs,
            display_name="Duplicate Specialist",
            short_description="Experienced specialist for duplicate beta test.",
            languages=["ru"],
            service_title="Duplicate service",
        )

        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        first = await service.create_pending_profile(data)
        assert first.status == "pending_moderation"
        assert first.extra_metadata["contact_text"] == BETA_CONTACT_NOTE

        with pytest.raises(SpecialistRegistrationError):
            await service.create_pending_profile(data)

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"display_name": "A"}, "Display name is too short"),
        ({"short_description": "too short"}, "Short description must be at least 20 characters"),
        ({"price_from": -1}, "Price from cannot be negative"),
        ({"price_to": -1}, "Price to cannot be negative"),
        ({"price_from": 100, "price_to": 50}, "Price to cannot be lower"),
        ({"contact_text": ""}, "Contact is required"),
        ({"service_title": "AB"}, "Service title is too short")
    ],
)
async def test_create_specialist_profile_validates_input(db_session, overrides, message):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)
    refs = await get_reference_data(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await accept_specialist_consents(db_session, tenant_id, user_id)

        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        with pytest.raises(SpecialistRegistrationError, match=message):
            await service.create_pending_profile(
                build_registration_data(
                    user_id,
                    tenant_id,
                    refs,
                    **overrides,
                )
            )

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_specialist_fsm_does_not_put_uuid_into_paged_callback_data():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()
    assert "UUID(callback.data.split" not in source
    assert 'callback_data=f"{prefix}:{item.id}"' not in source
    assert "item_index = start + offset" in source
    assert 'callback_data=f"{prefix}:{item_index}"' in source
    assert "geo_candidates" in source
    assert 'callback_data=f"spec_geo_place:{index}"' in source
    assert "GeoService(GeoRepository(session)).confirm_place" in source
    assert "list_active_cities(limit=100)" not in source
    assert "reverse_place" in source
    assert "ReplyKeyboardMarkup" in source
    assert "ReplyKeyboardRemove" in source
    assert "request_location=True" in source


def test_specialist_fsm_callback_literals_are_short():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    callback_literals = [
        "spec_cancel",
        "spec_location_city",
        "spec_location_geo",
        "spec_back_to_categories",
        "spec_back_to_location_mode",
        "spec_lang_done",
        "spec_confirm",
        "register_specialist",
        "SS_START",
        "M",
        "spec_geo_place:",
    ]

    for callback_data in callback_literals:
        assert len(callback_data.encode("utf-8")) <= 64
        assert callback_data in source
def test_specialist_fsm_uses_i18n_for_visible_texts():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    forbidden_fragments = [
        "Сначала нажмите",
        "Юридические документы",
        "Перед регистрацией",
        "Перейти к согласиям",
        "Профиль специалиста",
        "Категории специалистов",
        "Выберите категорию",
        "Категория не найдена",
        "Выберите профессию",
        "Профессия не найдена",
        "Выберите город",
        "Город не найден",
        "Отправьте геолокацию",
        "Пожалуйста, отправьте",
        "Укажите имя",
        "Название слишком короткое",
        "Коротко опишите",
        "Описание слишком короткое",
        "Укажите цену",
        "Не удалось распознать",
        "Выберите языки",
        "Укажите контактную",
        "Контактная заметка",
        "Проверьте профиль",
        "После подтверждения",
        "Не удалось создать",
        "создан и отправлен",
        "Регистрация специалиста отменена",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

def test_specialist_fsm_uses_geo_provider_for_location_selection():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    required_fragments = [
        "entering_city_query",
        "choosing_geo_place",
        "GeoService",
        "GeoRepository",
        "search_places",
        "reverse_place",
        "confirm_place",
        "geo_candidates",
        "candidate.to_state()",
        "callback_data=f\"spec_geo_place:{index}\"",
        "ReplyKeyboardMarkup",
        "ReplyKeyboardRemove",
        "request_location=True",
        "city_id=str(place.city_id)",
        "country_id=str(place.country_id)",
        "latitude=place.latitude",
        "longitude=place.longitude",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "list_active_cities(limit=100)",
        "callback_data=f\"spec_city:{item_index}\"",
        "F.data.startswith(\"spec_city:\")",
        "F.data.startswith(\"spec_cities_page:\")",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

    callback_literals = [
        "spec_location_city",
        "spec_location_geo",
        "spec_back_to_location_mode",
        "spec_geo_place:",
    ]

    for callback_data in callback_literals:
        assert len(callback_data.encode("utf-8")) <= 64

def test_specialist_cabinet_s1_matches_tz10_contract():
    billing_source = open("handlers/billing.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    for fragment in [
        "def cabinet_menu_keyboard",
        'callback_data="SPEC_REQUESTS"',
        'callback_data="SPEC_DIALOGS"',
        'callback_data="CAB_PROFILE"',
        'callback_data="SPEC_SERVICES"',
        'callback_data="CAB_PORTFOLIO"',
        'callback_data="SPEC_REVIEWS"',
        'callback_data="BETA_DISABLED:promotion"',
        'callback_data="SPEC_SETTINGS"',
        'callback_data="ROLE_SWITCH_MENU"',
        "async def show_specialist_cabinet",
        "format_specialist_cabinet_text",
        "specialist_status_notice",
        "list_active_specialist_professions",
        "ContactRequest.specialist_id == specialist.id",
        'ContactRequest.status == "new"',
        'event_type="specialist_menu"',
        "specialist_no_profile_start",
        "async def specialist_requests_entry",
        "async def specialist_dialogs_entry",
        "async def specialist_services_entry",
        "async def specialist_reviews_entry",
        "async def specialist_settings_entry",
    ]:
        assert fragment in billing_source

    for fragment in [
        "specialist_cabinet_title",
        "specialist_new_requests_label",
        "specialist_unread_label",
        "specialist_no_profile_start",
        "specialist_status_active_notice",
        "specialist_status_pending_notice",
        "specialist_status_rejected_notice",
        "specialist_status_paused_notice",
        "specialist_new_requests_btn",
        "specialist_dialogs_btn",
        "specialist_services_btn",
        "specialist_requests_placeholder",
        "specialist_dialogs_placeholder",
        "specialist_reviews_placeholder",
        "specialist_settings_placeholder",
    ]:
        assert fragment in texts_source
def test_specialist_registration_s2_start_matches_tz10_contract():
    legal_source = open("handlers/legal.py", encoding="utf-8").read()
    fsm_source = open("fsm/specialist_form.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    for fragment in [
        "CB_SPECIALIST_START",
        "CB_SPECIALIST_START_CONFIRM",
        "CB_SPECIALIST_START_CANCEL",
        "specialist_registration_start_keyboard",
        "specialist_registration_start_screen",
        "specialist_start_legal_gate",
        "specialist_registration_start_cancel",
        "specialist_registration_start_text",
        "specialist_registration_start_btn",
        "legal_gate_keyboard",
        "get_missing_specialist_consents",
        "accept_required_specialist_consents",
        'event_type="registration_started"',
        'entity_type="specialist_registration"',
        "CB_REGISTER_SPECIALIST",
    ]:
        assert fragment in legal_source

    for fragment in [
        "register_specialist",
        "ensure_specialist_consents",
        "spec_legal_consents_required",
        "SpecialistRegistration",
    ]:
        assert fragment in fsm_source

    for fragment in [
        "specialist_registration_start_text",
        "specialist_registration_start_btn",
        "legal_gate_intro",
        "legal_accept_continue_btn",
        "legal_show_documents_btn",
        "legal_back_to_menu_btn",
        "spec_legal_consents_required",
    ]:
        assert fragment in texts_source
def test_specialist_registration_s3_category_matches_tz10_contract():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    assert "PER_PAGE = 8" in source
    assert "class SpecialistForm" in source
    assert "choosing_category = State()" in source

    assert "def build_category_keyboard" in source
    assert 'prefix="spec_category"' in source
    assert 'page_prefix="spec_categories_page"' in source
    assert "spec_categories_page:" in source
    assert "async def paginate_categories" in source

    assert "async def choose_category" in source
    assert "await state.set_state(SpecialistForm.choosing_profession)" in source

    assert "spec_categories_missing" in source
    assert "spec_category_not_found" in source
    assert "spec_category_not_found_restart" in source

    assert 'text=t("spec_back_btn", language)' in source
    assert 'text=t("search_menu", language)' in source
    assert 'callback_data="spec_cancel"' in source

def test_specialist_registration_s4_profession_matches_tz10_contract():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    assert "choosing_profession = State()" in source

    assert "def build_profession_multi_keyboard" in source
    assert 'callback_data=f"spec_profession:{item_index}"' in source
    assert 'callback_data=f"spec_professions_page:{page - 1}"' in source
    assert 'callback_data=f"spec_professions_page:{page + 1}"' in source

    assert "async def paginate_professions" in source
    assert "async def choose_profession" in source
    assert "async def finish_profession_selection" in source

    assert "selected_profession_ids" in source
    assert "selected_professions" in source
    assert "profession_limit_error_key" in source
    assert "MAX_SPECIALIST_CATEGORIES = 2" in source
    assert "MAX_PROFESSIONS_PER_CATEGORY = 3" in source

    assert "spec_profession_select_one" in source
    assert "spec_professions_missing" in source
    assert "spec_profession_not_found" in source
    assert "spec_profession_not_found_back" in source

    assert 'callback_data="spec_back_to_categories"' in source
    assert 'text=t("search_menu", language)' in source
    assert 'callback_data="spec_cancel"' in source

    assert "await state.set_state(SpecialistForm.choosing_location_mode)" in source
def test_specialist_registration_s5_location_matches_tz10_contract():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "choosing_location_mode = State()" in source
    assert "entering_city_query = State()" in source
    assert "entering_country_query = State()" in source
    assert "waiting_geo = State()" in source

    assert "def location_mode_keyboard" in source
    assert 'callback_data="spec_location_geo"' in source
    assert 'callback_data="spec_location_city"' in source
    assert 'callback_data="spec_location_country"' in source
    assert 'callback_data="spec_location_remote"' in source
    assert 'callback_data="spec_back_to_categories"' in source
    assert 'callback_data="spec_cancel"' in source
    assert 'text=t("search_menu", language)' in source

    assert "async def choose_city_mode" in source
    assert "async def choose_country_mode" in source
    assert "async def geo_location_prompt" in source
    assert "async def receive_geo_location" in source
    assert "async def choose_remote_location" in source

    assert "city_id=None" in source
    assert "country_id=None" in source
    assert 'work_format="remote"' in source
    assert "await state.set_state(SpecialistForm.entering_display_name)" in source

    assert "spec_location_remote_btn" in texts_source
    assert "spec_location_remote_selected" in texts_source

def test_specialist_registration_remote_location_skips_work_format_prompt():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    assert 'work_format="remote"' in source
    assert 'if data.get("work_format") == "remote":' in source
    assert "spec_languages_prompt" in source
    assert "language_keyboard(" in source
    assert "await state.set_state(SpecialistForm.choosing_languages)" in source
def test_specialist_registration_s6_description_matches_tz10_contract():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()

    assert "entering_description = State()" in source
    assert "def description_keyboard" in source

    assert "async def enter_description" in source
    assert "spec_description_prompt" in source
    assert "spec_description_too_short" in source
    assert "len(description) < 20" in source
    assert "await state.update_data(short_description=description)" in source

    assert 'callback_data="spec_back_to_location_mode"' in source
    assert 'text=t("search_menu", language)' in source
    assert 'callback_data="spec_cancel"' in source

    assert "spec_contact_prompt" in source
    assert "await state.set_state(SpecialistForm.entering_contact)" in source

def test_specialist_registration_s7_contact_visibility_matches_tz10_contract():
    form_source = open("fsm/specialist_form.py", encoding="utf-8").read()
    service_source = open("services/specialist.py", encoding="utf-8").read()
    repo_source = open("database/repositories/specialist.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()
    assert "visibility_result = await self.session.execute" in repo_source
    assert "visibility_settings = visibility_result.scalar_one_or_none()" in repo_source
    assert "if visibility_settings:" in repo_source
    assert "visibility_settings.visibility_level = visibility_level" in repo_source
    assert "else:" in repo_source
    assert "ProfileVisibilitySetting(" in repo_source
    assert "choosing_contact_visibility = State()" in form_source
    assert "def contact_visibility_keyboard" in form_source
    assert "ProfileVisibilitySetting" in repo_source
    assert 'profile_type="specialist"' in repo_source
    assert "visibility_level=visibility_level" in repo_source
    assert "visible_to_clients=True" in repo_source
    assert "visible_to_employers=False" in repo_source
    assert "visible_to_agencies=False" in repo_source
    assert "allow_direct_messages=bool(allow_requests)" in repo_source
    assert "allow_profile_export=False" in repo_source
    assert 'callback_data="spec_contact_visibility:platform_only"' in form_source
    assert 'callback_data="spec_contact_visibility:public_limited"' in form_source
    assert 'callback_data="spec_contact_visibility:private"' in form_source
    assert 'callback_data="spec_contact_visibility_done"' in form_source

    assert "async def choose_contact_visibility" in form_source
    assert "async def finish_contact_visibility" in form_source
    assert "async def show_registration_summary" in form_source

    assert "contact_visibility" in form_source
    assert "allow_requests" in form_source
    assert "spec_platform_contact_default" in form_source

    assert 'contact_visibility: str = "platform_only"' in service_source
    assert "allow_requests: bool = True" in service_source
    assert "contact_visibility=data.contact_visibility" in service_source
    assert "allow_requests=data.allow_requests" in service_source

    assert '"contact_visibility": contact_visibility or "platform_only"' in repo_source
    assert '"allow_requests": bool(allow_requests)' in repo_source

    for key in [
        "spec_contact_visibility_prompt",
        "spec_contact_visibility_platform_only",
        "spec_contact_visibility_public_limited",
        "spec_contact_visibility_private",
        "spec_continue_btn",
        "spec_platform_contact_default",
        "spec_contact_visibility_summary",
    ]:
        assert key in texts_source
    enter_contact_block = form_source.split(
        "async def enter_contact",
        1,
    )[1].split(
        "async def show_registration_summary",
        1,
    )[0]

    assert 'contact_visibility=data.get("contact_visibility") or "platform_only"' in enter_contact_block
    assert "allow_requests=True" in enter_contact_block
    assert "await show_registration_summary(message, state, language)" in enter_contact_block
    assert "await state.set_state(SpecialistForm.choosing_contact_visibility)" not in enter_contact_block
    assert "spec_contact_visibility_prompt" not in enter_contact_block
    assert "contact_visibility_keyboard(" not in enter_contact_block
    assert "return" in enter_contact_block

def test_specialist_registration_s8_confirmation_matches_tz10_contract():
    source = open("fsm/specialist_form.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "def confirm_keyboard" in source
    assert 'callback_data="spec_confirm"' in source
    assert 'callback_data="register_specialist"' in source
    assert 'callback_data="spec_cancel"' in source

    assert "async def show_registration_summary" in source
    assert "async def confirm_specialist" in source
    assert "SpecialistRegistrationData(" in source

    assert "required_fields =" in source
    assert "missing_fields =" in source
    assert "spec_draft_missing" in source

    assert "from handlers.billing import show_specialist_cabinet" in source
    assert "await show_specialist_cabinet(callback, state)" in source

    assert "spec_confirm_btn" in texts_source
    assert "Отправить" in texts_source
    assert "spec_restart_btn" in texts_source
    assert "Изменить" in texts_source
    assert "spec_cancel_btn" in texts_source
    assert "spec_draft_missing" in texts_source
    assert "{specialist_id}" not in texts_source
    assert "ID профиля" not in texts_source