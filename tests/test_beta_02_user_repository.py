import os
import uuid

from sqlalchemy import delete, select
import pytest
from database.models import EventLog, User, UserAccount, UserRoleMapping
from database.repositories.user import UserRepository
from services.user import TelegramUserData, UserService
from handlers.start import get_main_menu_keyboard, role_switch_keyboard

async def cleanup_user_by_platform_id(session, platform_user_id: str):
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

    await session.execute(delete(EventLog).where(EventLog.user_id == user_id))
    await session.execute(delete(UserRoleMapping).where(UserRoleMapping.user_id == user_id))
    await session.execute(delete(UserAccount).where(UserAccount.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def test_create_telegram_client_user(db_session):
    repo = UserRepository(db_session)
    platform_user_id = f"test-client-{uuid.uuid4()}"

    try:
        user_id = await repo.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username="test_client",
            first_name="Test",
            last_name="Client",
            language_code="uk",
            role="client",
        )

        user = await db_session.get(User, user_id)
        assert user is not None
        assert user.status == "active"
        assert user.language_code == "uk"
        assert user.active_role is None

        default_tenant_id = os.getenv("DEFAULT_TENANT_ID")
        assert default_tenant_id
        assert str(user.tenant_id) == default_tenant_id

        account = await repo.get_by_platform_account("telegram", platform_user_id)
        assert account is not None
        assert account.user_id == user_id
        assert account.username == "test_client"

        role_result = await db_session.execute(
            select(UserRoleMapping).where(UserRoleMapping.user_id == user_id)
        )
        role = role_result.scalar_one_or_none()

        assert role is not None
        assert role.role == "client"
        assert role.status == "active"

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)


async def test_create_telegram_super_admin_user(db_session):
    repo = UserRepository(db_session)
    platform_user_id = f"test-admin-{uuid.uuid4()}"

    try:
        user_id = await repo.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username="test_admin",
            first_name="Test",
            last_name="Admin",
            language_code="ru",
            role="super_admin",
        )

        user = await db_session.get(User, user_id)
        assert user is not None
        assert user.status == "active"
        assert user.active_role == "super_admin"

        default_tenant_id = os.getenv("DEFAULT_TENANT_ID")
        assert default_tenant_id
        assert str(user.tenant_id) == default_tenant_id

        role_result = await db_session.execute(
            select(UserRoleMapping).where(UserRoleMapping.user_id == user_id)
        )
        roles = role_result.scalars().all()
        roles_by_name = {item.role: item for item in roles}

        assert roles_by_name["client"].status == "active"
        assert roles_by_name["super_admin"].status == "active"

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)


async def test_create_telegram_user_is_idempotent(db_session):
    repo = UserRepository(db_session)
    platform_user_id = f"test-idempotent-{uuid.uuid4()}"

    try:
        first_user_id = await repo.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username="same_user",
            first_name="Same",
            last_name="User",
            language_code="en",
            role="client",
        )

        second_user_id = await repo.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username="same_user",
            first_name="Same",
            last_name="User",
            language_code="en",
            role="client",
        )

        assert second_user_id == first_user_id

        account_result = await db_session.execute(
            select(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == platform_user_id,
            )
        )
        accounts = account_result.scalars().all()

        assert len(accounts) == 1
        assert accounts[0].user_id == first_user_id

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)


async def test_get_by_platform_account_returns_existing_client(db_session):
    repo = UserRepository(db_session)
    platform_user_id = f"test-existing-client-{uuid.uuid4()}"

    try:
        user_id = await repo.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username="existing_client",
            first_name="Existing",
            last_name="Client",
            language_code="en",
            role="client",
        )

        account = await repo.get_by_platform_account("telegram", platform_user_id)

        assert account is not None
        assert account.user_id == user_id
        assert account.platform == "telegram"
        assert account.platform_user_id == platform_user_id
        assert account.username == "existing_client"

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)

async def test_admin_bootstrap_from_env_creates_super_admin(db_session, monkeypatch):
    platform_user_id = f"test-env-admin-{uuid.uuid4()}"
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", platform_user_id)

    try:
        service = UserService(db_session)

        result = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="env_admin",
                first_name="Env",
                last_name="Admin",
                language_code="ru",
            )
        )

        assert result.role == "super_admin"
        assert result.is_new is True

        user = await db_session.get(User, result.user_id)
        assert user is not None
        assert user.active_role == "super_admin"

        role_result = await db_session.execute(
            select(UserRoleMapping).where(UserRoleMapping.user_id == result.user_id)
        )
        roles = role_result.scalars().all()
        roles_by_name = {item.role: item for item in roles}

        assert roles_by_name["client"].status == "active"
        assert roles_by_name["super_admin"].status == "active"
        event_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == result.user_id,
                EventLog.event_type == "user_started",
            )
        )
        event = event_result.scalar_one_or_none()

        assert event is not None
        assert event.platform == "telegram"
        assert event.entity_type == "user"
        assert event.entity_id == result.user_id
        assert event.payload["role"] == "super_admin"
        assert event.payload["is_new"] is True
    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)

async def test_existing_telegram_user_gets_base_client_role_on_start(db_session):
    platform_user_id = f"test-existing-base-client-{uuid.uuid4()}"

    try:
        repo = UserRepository(db_session)
        user_id = await repo.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username="base_client",
            first_name="Base",
            last_name="Client",
            language_code="ru",
            role="super_admin",
        )

        result = await db_session.execute(
            select(UserRoleMapping).where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.role == "client",
            )
        )
        existing_client_role = result.scalar_one_or_none()
        if existing_client_role:
            await db_session.delete(existing_client_role)
            await db_session.commit()

        service = UserService(db_session)
        await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="base_client",
                first_name="Base",
                last_name="Client",
                language_code="ru",
            )
        )

        roles_result = await db_session.execute(
            select(UserRoleMapping.role, UserRoleMapping.status).where(
                UserRoleMapping.user_id == user_id,
            )
        )
        roles = dict(roles_result.all())

        assert roles["client"] == "active"
        assert roles["super_admin"] == "active"

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)


async def test_register_existing_user_logs_return_start_without_duplicates(db_session):
    platform_user_id = f"test-repeat-start-{uuid.uuid4()}"

    try:
        service = UserService(db_session)

        first = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="repeat_user",
                first_name="Repeat",
                last_name="User",
                language_code="ru",
            )
        )

        second = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="repeat_user",
                first_name="Repeat",
                last_name="User",
                language_code="ru",
            )
        )

        assert first.is_new is True
        assert second.is_new is False
        assert second.user_id == first.user_id

        accounts_result = await db_session.execute(
            select(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == platform_user_id,
            )
        )
        assert len(accounts_result.scalars().all()) == 1

        roles_result = await db_session.execute(
            select(UserRoleMapping).where(UserRoleMapping.user_id == first.user_id)
        )
        roles = roles_result.scalars().all()
        assert len(roles) == 1
        assert roles[0].role == "client"

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == first.user_id,
                EventLog.event_type == "user_started",
            )
        )
        events = events_result.scalars().all()
        assert len(events) == 2
        assert {event.payload["is_new"] for event in events} == {True, False}

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)

async def test_existing_admin_user_keeps_same_account_and_logs_return_start(db_session, monkeypatch):
    platform_user_id = f"test-existing-admin-{uuid.uuid4()}"
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", platform_user_id)

    try:
        service = UserService(db_session)

        first = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="existing_admin",
                first_name="Existing",
                last_name="Admin",
                language_code="ru",
            )
        )

        second = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="existing_admin",
                first_name="Existing",
                last_name="Admin",
                language_code="ru",
            )
        )

        assert first.is_new is True
        assert second.is_new is False
        assert second.user_id == first.user_id
        assert second.role == "super_admin"

        user = await db_session.get(User, first.user_id)
        assert user is not None
        assert user.active_role == "super_admin"

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == first.user_id,
                EventLog.event_type == "user_started",
            )
        )
        events = events_result.scalars().all()

        assert len(events) == 2
        assert {event.payload["is_new"] for event in events} == {True, False}
        assert all(event.payload["role"] == "super_admin" for event in events)

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)


async def test_get_user_by_telegram_id_returns_none_for_unknown_user(db_session):
    service = UserService(db_session)

    user = await service.get_user_by_telegram_id(f"unknown-{uuid.uuid4()}")

    assert user is None
async def test_user_service_switches_active_role(db_session):
    platform_user_id = f"test-role-switch-{uuid.uuid4()}"

    try:
        service = UserService(db_session)

        result = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="role_switch",
                first_name="Role",
                last_name="Switch",
                language_code="ru",
            )
        )

        user = await db_session.get(User, result.user_id)
        assert user is not None

        db_session.add(
            UserRoleMapping(
                user_id=user.id,
                tenant_id=user.tenant_id,
                role="support",
                status="active",
            )
        )
        await db_session.commit()

        context = await service.get_role_switch_context(platform_user_id)

        assert context is not None
        assert context.active_role is None
        assert context.available_roles == ["client", "support"]

        switched = await service.switch_active_role(
            platform_user_id,
            "support",
        )

        assert switched.active_role == "support"
        assert switched.available_roles == ["client", "support"]

        user_after = await db_session.get(User, user.id)
        assert user_after.active_role == "support"

        event_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == user.id,
                EventLog.event_type == "role_switched",
            )
        )
        event = event_result.scalar_one_or_none()

        assert event is not None
        assert event.payload["active_role"] == "support"

        with pytest.raises(ValueError):
            await service.switch_active_role(platform_user_id, "finance_admin")

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)

def _keyboard_callback_data(keyboard):
    return [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]


def _keyboard_texts(keyboard):
    return [
        button.text
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_start_menu_is_short_global_menu_without_role_switch():
    single_role_keyboard = get_main_menu_keyboard(
        "ru",
        show_role_switch=False,
    )
    single_role_callbacks = _keyboard_callback_data(single_role_keyboard)

    assert "ROLE_SWITCH_MENU" not in single_role_callbacks

    multi_role_keyboard = get_main_menu_keyboard(
        "ru",
        show_role_switch=True,
    )
    multi_role_callbacks = _keyboard_callback_data(multi_role_keyboard)

    assert "ROLE_SWITCH_MENU" not in multi_role_callbacks

    assert "M_FIND" in multi_role_callbacks
    assert "M_SPECIALIST" in multi_role_callbacks
    assert "M_RFQ_STUB" in multi_role_callbacks
    assert "CLIENT_DIALOGS" in multi_role_callbacks
    assert "M_CABINET" in multi_role_callbacks
    assert "M_COMMUNITY_STUB" in multi_role_callbacks
    assert "M_HR_STUB" in multi_role_callbacks
    assert "M_SETTINGS" in multi_role_callbacks

    assert "JOBS_MENU" not in multi_role_callbacks
    assert "ROLE_SWITCH_MENU" not in multi_role_callbacks
    assert "SS_START" not in multi_role_callbacks

def test_role_switch_keyboard_marks_active_role_and_uses_role_callbacks():
    keyboard = role_switch_keyboard(
        roles=["client", "specialist", "support", "moderator"],
        active_role="specialist",
        language="ru",
        role_details={"specialist": "Электрик"},
        unread_counts={"specialist": 3, "client": 1},
    )

    callbacks = _keyboard_callback_data(keyboard)
    texts = _keyboard_texts(keyboard)

    assert "ROLE_SWITCH:client" in callbacks
    assert "ROLE_SWITCH:support" in callbacks
    assert "ROLE_SWITCH:moderator" in callbacks
    assert "ROLE_SWITCH:specialist" in callbacks
    assert any("Специалист: Электрик" in text for text in texts)
    assert "GLOBAL_MAIN_MENU" in callbacks
    assert any("Специалист: Электрик (3)" in text for text in texts)
    assert any(text.startswith("Клиент") and "(1)" in text for text in texts)
    assert any(text.startswith("* ") for text in texts)
async def test_start_opened_audit_event_is_written_on_start(db_session):
    platform_user_id = f"test-start-opened-{uuid.uuid4()}"

    try:
        service = UserService(db_session)

        first = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="start_opened",
                first_name="Start",
                last_name="Opened",
                language_code="ru",
            )
        )

        second = await service.register_telegram_user(
            TelegramUserData(
                platform_user_id=platform_user_id,
                username="start_opened",
                first_name="Start",
                last_name="Opened",
                language_code="ru",
            )
        )

        assert second.user_id == first.user_id

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == first.user_id,
                EventLog.event_type == "start_opened",
            )
        )
        events = events_result.scalars().all()

        assert len(events) == 2
        assert events[0].payload["is_new"] is True
        assert events[1].payload["is_new"] is False
        assert all(event.payload["role"] == "client" for event in events)
        assert all("active_role" in event.payload for event in events)

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)

def test_role_switch_opens_matching_cabinet_after_context_save():
    source = open("handlers/start.py", encoding="utf-8").read()

    assert "async def open_active_role_cabinet" in source
    assert "from handlers.admin import show_admin_panel" in source
    assert "from handlers.billing import show_specialist_cabinet" in source
    assert 'role in {"support", "moderator", "admin", "super_admin"}' in source
    assert 'role == "client"' in source
    assert 'role == "specialist"' in source
    assert "from handlers.billing import show_client_cabinet" in source
    billing_source = open("handlers/billing.py", encoding="utf-8").read()

    assert '@billing_router.callback_query(F.data == "M_CABINET")' in billing_source
    assert "await open_current_role_cabinet(callback, state)" in billing_source
    assert "async def show_specialist_cabinet" in billing_source
    switch_block = source.split(
        '@start_router.callback_query(F.data.startswith("ROLE_SWITCH:"))',
        1,
    )[1]

    assert "await service.switch_active_role(" in switch_block
    assert "await open_active_role_cabinet(" in switch_block
    assert switch_block.index("await service.switch_active_role(") < switch_block.index(
        "await open_active_role_cabinet("
    )

def test_start_opens_release_main_menu_instead_of_role_wizard():
    source = open("handlers/start.py", encoding="utf-8").read()

    assert "async def cmd_start(message: Message, state: FSMContext)" in source
    assert "t(\"search_main_menu\", language)" in source
    assert "get_main_menu_keyboard(" in source
    assert "ROLE_SWITCH_MENU" not in source.split(
        "async def cmd_start(message: Message, state: FSMContext)",
        1,
    )[1].split(
        "@start_router.callback_query(F.data == \"ROLE_SWITCH_MENU\")",
        1,
    )[0]
    cmd_start_block = source.split(
        "async def cmd_start(message: Message, state: FSMContext)",
        1,
    )[1].split(
        "@start_router.callback_query(F.data == \"ROLE_SWITCH_MENU\")",
        1,
    )[0]

    main_menu_block = source.split(
        "def get_main_menu_keyboard",
        1,
    )[1].split(
        "async def get_main_menu_keyboard_for_user",
        1,
    )[0]

    assert "JOBS_MENU" not in cmd_start_block
    assert "JOBS_MENU" not in main_menu_block

def test_specialist_and_admin_cabinets_show_role_switch_for_multi_role_users():
    billing_source = open("handlers/billing.py", encoding="utf-8").read()
    admin_source = open("handlers/admin.py", encoding="utf-8").read()

    assert "show_role_switch: bool = False" in billing_source
    assert "callback_data=\"ROLE_SWITCH_MENU\"" in billing_source
    assert "get_role_switch_context(callback.from_user.id)" in billing_source
    assert "show_role_switch=show_role_switch" in billing_source

    assert "show_role_switch: bool = False" in admin_source
    assert "callback_data=\"ROLE_SWITCH_MENU\"" in admin_source
    assert "get_role_switch_context(user.id)" in admin_source
    assert "show_role_switch=show_role_switch" in admin_source