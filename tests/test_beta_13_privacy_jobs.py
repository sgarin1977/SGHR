import json
import uuid

import pytest
from sqlalchemy import delete, select

from database.models import (
    DataSubjectRequest,
    DeletionJob,
    EventLog,
    SupportMessage,
    SupportTicket,
    User,
    UserAccount,
)
from database.repositories.privacy import PrivacyRepository
from database.repositories.user import UserRepository
from services.privacy import PrivacyService


pytestmark = pytest.mark.asyncio


async def create_privacy_test_user(session):
    platform_user_id = f"privacy-job-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="privacy_user",
        first_name="Privacy",
        last_name="Tester",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    account_result = await session.execute(
        select(UserAccount).where(
            UserAccount.user_id == user_id,
            UserAccount.platform == "telegram",
        )
    )
    account = account_result.scalar_one()

    account.email = "privacy@example.com"
    account.phone = "+351000000000"
    account.raw_profile = {"telegram": "raw"}
    user.extra_metadata = {"source": "privacy_test"}

    await session.commit()
    return platform_user_id, user.id, user.tenant_id


async def cleanup_privacy_test_user(session, platform_user_id):
    account_result = await session.execute(
        select(UserAccount).where(UserAccount.platform_user_id == platform_user_id)
    )
    account = account_result.scalar_one_or_none()

    if account:
        user_id = account.user_id

        await session.execute(delete(EventLog).where(EventLog.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_privacy_data_export_job_creates_json_file(db_session, tmp_path):
    platform_user_id, user_id, tenant_id = await create_privacy_test_user(db_session)
    await db_session.execute(
        delete(DataSubjectRequest).where(
            DataSubjectRequest.status == "requested",
        )
    )
    await db_session.commit()
    service = PrivacyService(PrivacyRepository(db_session))

    try:
        request = await service.request_data_export(
            tenant_id=tenant_id,
            user_id=user_id,
        )

        result = await service.process_requested_data_exports(
            export_dir=tmp_path,
            limit=5,
        )

        assert result.processed_count == 1
        assert result.failed_count == 0

        await db_session.refresh(request)

        assert request.status == "completed"
        assert request.processed_at is not None
        assert "Export prepared:" in request.result_comment

        export_file = tmp_path / f"dsr_export_{request.id}.json"
        assert export_file.exists()

        export_data = json.loads(export_file.read_text(encoding="utf-8"))

        assert export_data["request_id"] == str(request.id)
        assert export_data["user_id"] == str(user_id)
        assert export_data["user"]["id"] == str(user_id)
        assert export_data["user_accounts"][0]["email"] == "privacy@example.com"
        assert "support_tickets" in export_data
        assert "messages" in export_data
    finally:
        await cleanup_privacy_test_user(db_session, platform_user_id)


async def test_privacy_deletion_job_anonymizes_user_pii(db_session):
    platform_user_id, user_id, tenant_id = await create_privacy_test_user(db_session)

    support_ticket = SupportTicket(
        tenant_id=tenant_id,
        user_id=user_id,
        subject="Privacy deletion",
        status="open",
        priority="P3",
        category="technical",
    )
    db_session.add(support_ticket)
    await db_session.flush()

    support_message = SupportMessage(
        tenant_id=tenant_id,
        ticket_id=support_ticket.id,
        sender_user_id=user_id,
        sender_role="user",
        message_text="Please delete my private support message.",
    )
    db_session.add(support_message)


    await db_session.commit()

    service = PrivacyService(PrivacyRepository(db_session))

    job = await service.schedule_profile_deletion(
        tenant_id=tenant_id,
        user_id=user_id,
    )

    result = await service.process_scheduled_deletions(limit=5)

    assert result.processed_count >= 1
    assert result.failed_count == 0

    await db_session.refresh(job)

    assert job.status == "completed"
    assert job.completed_at is not None
    assert job.anonymization_report["user_id"] == str(user_id)
    assert job.anonymization_report["user_found"] is True

    user = await db_session.get(User, user_id)
    assert user.status == "deleted"
    assert user.active_role is None
    assert user.country_id is None
    assert user.city_id is None

    account_result = await db_session.execute(
        select(UserAccount).where(UserAccount.user_id == user_id)
    )
    account = account_result.scalar_one()

    assert account.platform_user_id.startswith("deleted:")
    assert account.username is None
    assert account.first_name is None
    assert account.last_name is None
    assert account.email is None
    assert account.phone is None
    assert account.raw_profile == {}

    await db_session.refresh(support_message)

    assert support_message.message_text == "[deleted by user request]"
