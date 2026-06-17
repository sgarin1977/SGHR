import uuid

import pytest
from sqlalchemy import delete, select

from database.models import (
    SupportMessage,
    SupportTicket,
    User,
    UserAccount,
    UserRoleMapping,
)
from database.repositories.support import SupportRepository
from database.repositories.user import UserRepository
from services.support import SupportService, SupportServiceError
from tests.test_beta_08_admin_moderation import create_admin_user


pytestmark = pytest.mark.asyncio


async def cleanup_test_user(session, platform_user_id: str):
    await session.rollback()

    account = (
        await session.execute(
            select(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == platform_user_id,
            )
        )
    ).scalar_one_or_none()

    if not account:
        await session.commit()
        return

    user_id = account.user_id

    await session.execute(
        delete(SupportMessage).where(SupportMessage.sender_user_id == user_id)
    )
    await session.execute(
        delete(SupportTicket).where(SupportTicket.user_id == user_id)
    )
    await session.execute(
        delete(UserRoleMapping).where(UserRoleMapping.user_id == user_id)
    )
    await session.execute(
        delete(UserAccount).where(UserAccount.user_id == user_id)
    )
    await session.execute(
        delete(User).where(User.id == user_id)
    )
    await session.commit()


async def create_support_test_user(session):
    platform_user_id = f"support-test-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="support_test",
        first_name="Support",
        last_name="User",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    assert user is not None
    assert user.tenant_id is not None

    await session.commit()
    return platform_user_id, user_id, user.tenant_id


async def test_support_ticket_create_reply_and_resolve(db_session):
    platform_user_id, user_id, tenant_id = await create_support_test_user(db_session)
    support_platform_user_id, support_user_id, support_tenant_id = await create_admin_user(
        db_session,
        role="support",
    )

    assert support_tenant_id == tenant_id

    service = SupportService(SupportRepository(db_session))

    try:
        ticket = await service.create_ticket(
            tenant_id=tenant_id,
            user_id=user_id,
            subject="Translation issue",
            priority="P2",
            category="translation",
            message_text="Translation does not work.",
        )

        assert ticket.status == "open"
        assert ticket.priority == "P2"
        assert ticket.category == "translation"
        assert ticket.last_message_at is not None

        user_view = await service.get_user_ticket_view(
            tenant_id=tenant_id,
            user_id=user_id,
            ticket_id=ticket.id,
        )

        assert user_view.ticket.id == ticket.id
        assert len(user_view.messages) == 1
        assert user_view.messages[0].sender_role == "user"

        await service.add_staff_message(
            tenant_id=tenant_id,
            staff_user_id=support_user_id,
            ticket_id=ticket.id,
            message_text="We are checking it.",
        )

        staff_view = await service.get_staff_ticket_view(
            tenant_id=tenant_id,
            staff_user_id=support_user_id,
            ticket_id=ticket.id,
        )

        assert len(staff_view.messages) == 2
        assert staff_view.ticket.status == "in_progress"

        resolved = await service.update_ticket_status(
            tenant_id=tenant_id,
            staff_user_id=support_user_id,
            ticket_id=ticket.id,
            status="resolved",
        )

        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None

        with pytest.raises(SupportServiceError):
            await service.add_user_message(
                tenant_id=tenant_id,
                user_id=user_id,
                ticket_id=ticket.id,
                message_text="One more question.",
            )

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_test_user(db_session, support_platform_user_id)


async def test_support_access_required_for_staff_actions(db_session):
    platform_user_id, user_id, tenant_id = await create_support_test_user(db_session)
    other_platform_user_id, other_user_id, _ = await create_support_test_user(db_session)

    service = SupportService(SupportRepository(db_session))

    try:
        ticket = await service.create_ticket(
            tenant_id=tenant_id,
            user_id=user_id,
            subject="Payment issue",
            priority="P3",
            category="payment",
            message_text="Payment check please.",
        )

        with pytest.raises(SupportServiceError):
            await service.get_staff_ticket_view(
                tenant_id=tenant_id,
                staff_user_id=other_user_id,
                ticket_id=ticket.id,
            )

        with pytest.raises(SupportServiceError):
            await service.add_staff_message(
                tenant_id=tenant_id,
                staff_user_id=other_user_id,
                ticket_id=ticket.id,
                message_text="I should not answer.",
            )

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_test_user(db_session, other_platform_user_id)


def test_support_static_contract():
    models = open("database/models.py", encoding="utf-8").read()
    repository = open("database/repositories/support.py", encoding="utf-8").read()
    service = open("services/support.py", encoding="utf-8").read()

    required_model_fragments = [
        'class SupportTicket',
        '__tablename__ = "support_tickets"',
        'class SupportMessage',
        '__tablename__ = "support_messages"',
        'assigned_user_id',
        'priority',
        'sender_role',
    ]

    for fragment in required_model_fragments:
        assert fragment in models

    required_repository_fragments = [
        "class SupportRepository",
        "create_ticket",
        "list_user_tickets",
        "list_staff_tickets",
        "list_ticket_messages",
        "add_message",
        "update_ticket_status",
        "SUPPORT_STAFF_ROLES",
    ]

    for fragment in required_repository_fragments:
        assert fragment in repository

    required_service_fragments = [
        "class SupportService",
        "create_ticket",
        "add_user_message",
        "add_staff_message",
        "update_ticket_status",
        "P1",
        "P2",
        "P3",
        "P4",
    ]

    for fragment in required_service_fragments:
        assert fragment in service

    admin_handler = open("handlers/admin.py", encoding="utf-8").read()
    support_handler = open("handlers/support.py", encoding="utf-8").read()
    support_service = open("services/support.py", encoding="utf-8").read()
    support_repository = open("database/repositories/support.py", encoding="utf-8").read()

    assert 'ADMIN_SUPPORT_MENU_ROLES = {"support"}' in admin_handler
    assert 'ADMIN_SUPPORT_STATS_ROLES = {"support", "admin", "super_admin"}' in admin_handler

    assert 'callback_data="ADM_SUPPORT_VIEW:open:0"' in admin_handler
    assert 'callback_data="ADM_SUPPORT_VIEW:in_progress:0"' in admin_handler
    assert 'callback_data="ADM_SUPPORT_VIEW:resolved:0"' in admin_handler
    assert 'callback_data="ADM_SUPPORT_SEARCH"' in admin_handler
    assert 'callback_data="ADM_SUPPORT_STATS"' in admin_handler
    assert 'callback_data="ROLE_SWITCH_MENU"' in admin_handler

    assert "async def list_staff_tickets" in support_service
    assert "async def search_staff_tickets" in support_service
    assert "async def get_staff_ticket_stats" in support_service
    assert "async def assign_ticket" in support_service
    assert "async def escalate_ticket_to_admin" in support_service

    assert "async def list_staff_tickets" in support_repository
    assert "async def search_staff_tickets" in support_repository
    assert "async def get_staff_ticket_counts" in support_repository
    assert "async def get_staff_ticket_stats" in support_repository

    assert 'event_type="support_menu"' in admin_handler
    assert 'event_type="ticket_search"' in admin_handler
    assert 'event_type="ticket_assigned"' in admin_handler
    assert 'event_type="reply"' in admin_handler
    assert 'event_type="resolved"' in admin_handler
    assert 'event_type="stats_viewed"' in admin_handler

    assert 'event_type="support_opened"' in support_handler
    assert 'event_type="ticket_category"' in support_handler
    assert 'event_type="ticket_created"' in support_handler
    assert 'event_type="ticket_list"' in support_handler
    assert 'event_type="ticket_message"' in support_handler
    assert 'event_type="closed"' in support_handler