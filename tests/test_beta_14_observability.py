import uuid

import pytest
from sqlalchemy import delete, select

from database.models import (
    DataSubjectRequest,
    DeletionJob,
    EventLog,
    User,
    UserAccount,
)
from database.repositories.user import UserRepository
from scripts.monitor_failed_jobs import (
    MonitorResult,
    build_alert_message,
    collect_monitor_result,
)


pytestmark = pytest.mark.asyncio


async def create_observability_test_user(session):
    platform_user_id = f"observability-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="observability_user",
        first_name="Observability",
        last_name="Test",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    return platform_user_id, user.id, user.tenant_id


async def cleanup_observability_test_user(session, platform_user_id):
    account_result = await session.execute(
        select(UserAccount).where(UserAccount.platform_user_id == platform_user_id)
    )
    account = account_result.scalar_one_or_none()

    if account:
        user_id = account.user_id
        await session.execute(delete(EventLog).where(EventLog.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def cleanup_observability_test_jobs(session, tenant_id, user_id):
    await session.execute(
        delete(DeletionJob).where(
            DeletionJob.tenant_id == tenant_id,
            DeletionJob.user_id == user_id,
        )
    )
    await session.execute(
        delete(DataSubjectRequest).where(
            DataSubjectRequest.tenant_id == tenant_id,
            DataSubjectRequest.user_id == user_id,
        )
    )
    await session.commit()


async def test_monitor_failed_jobs_counts_existing_failed_jobs(db_session):
    platform_user_id, user_id, tenant_id = await create_observability_test_user(
        db_session
    )

    deletion_job = DeletionJob(
        tenant_id=tenant_id,
        user_id=user_id,
        status="failed",
        anonymization_report={"test": True},
        error_message="observability test deletion failure",
    )
    db_session.add(deletion_job)

    data_request = DataSubjectRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        request_type="export_data",
        status="rejected",
        result_comment="observability test export failure",
    )
    db_session.add(data_request)

    await db_session.commit()

    try:
        result = await collect_monitor_result()

        assert result.db_ok is True
        assert result.deletion_failed_count >= 1
        assert result.data_export_failed_count >= 1
        assert result.failed_jobs_count >= 2

        alert = build_alert_message(
            result=result,
            failed_jobs_threshold=1,
            translation_fail_threshold=100,
        )

        assert alert is not None
        assert "SGHR monitoring alert" in alert
        assert "failed_jobs_count" in alert
        assert "deletion_failed" in alert
        assert "data_export_failed" in alert
    finally:
        await cleanup_observability_test_jobs(
            db_session,
            tenant_id,
            user_id,
        )
        await cleanup_observability_test_user(db_session, platform_user_id)


async def test_monitor_alert_message_detects_translation_fail_spike():
    result = MonitorResult(
        db_ok=True,
        translation_failed_count=2,
        translation_retry_count=1,
        deletion_failed_count=0,
        data_export_failed_count=0,
    )

    alert = build_alert_message(
        result=result,
        failed_jobs_threshold=100,
        translation_fail_threshold=3,
    )

    assert alert is not None
    assert "SGHR monitoring alert" in alert
    assert "translation_problem_count=3 >= 3" in alert
    assert "translation_failed: 2" in alert
    assert "translation_retry: 1" in alert


async def test_monitor_alert_message_is_empty_below_threshold():
    result = await collect_monitor_result()

    alert = build_alert_message(
        result=result,
        failed_jobs_threshold=result.failed_jobs_count + 100,
        translation_fail_threshold=(
            result.translation_failed_count + result.translation_retry_count + 100
        ),
    )

    assert alert is None
async def test_event_repository_generates_trace_id_when_missing(db_session):
    platform_user_id, user_id, tenant_id = await create_observability_test_user(
        db_session
    )

    try:
        from database.repositories.event import EventRepository

        event = await EventRepository(db_session).create_event(
            event_type="observability_trace_check",
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="test",
            entity_id=None,
            payload={"test": True},
            platform="telegram",
        )
        await db_session.commit()

        assert event.trace_id
        assert event.trace_id.startswith("evt_")
        assert len(event.trace_id) > 10
    finally:
        await cleanup_observability_test_user(db_session, platform_user_id)