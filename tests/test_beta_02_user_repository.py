import os
import uuid

from sqlalchemy import delete, select

from database.models import EventLog, User, UserAccount, UserRoleMapping
from database.repositories.user import UserRepository
from services.user import TelegramUserData, UserService


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
        role = role_result.scalar_one_or_none()

        assert role is not None
        assert role.role == "super_admin"
        assert role.status == "active"

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
        role = role_result.scalar_one_or_none()

        assert role is not None
        assert role.role == "super_admin"
        assert role.status == "active"
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