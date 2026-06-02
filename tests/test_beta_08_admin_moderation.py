import uuid

import pytest
from sqlalchemy import delete, or_, select
from database.models import (
    AdminAction,
    Blacklist,
    Complaint,
    EventLog,
    LegalDocument,
    RiskFlag,
    Specialist,
    User,
    UserAccount,
    UserConsent,
    UserRoleMapping,
)
from database.repositories.legal import LegalRepository
from database.repositories.moderation import (
    ModerationAccessError,
    ModerationRepository,
)
from database.repositories.specialist import SpecialistRepository
from services.legal import LegalService
from services.moderation import ModerationError, ModerationService
from services.specialist import SpecialistService as SpecialistRegistrationService
from tests.test_beta_04_specialist_registration import (
    accept_specialist_consents,
    build_registration_data,
    cleanup_legal_documents,
    cleanup_test_user,
    create_test_user,
    get_reference_data,
)


pytestmark = pytest.mark.asyncio
@pytest.fixture(autouse=True)
async def cleanup_test_legal_documents_after_admin_tests(db_session):
    yield

    await db_session.rollback()
    await db_session.execute(
        delete(UserConsent).where(
            UserConsent.version.like("test-beta-%"),
        )
    )
    await db_session.execute(
        delete(LegalDocument).where(
            LegalDocument.version.like("test-beta-%"),
        )
    )
    await db_session.commit()

async def cleanup_user(session, platform_user_id: str):
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
        await cleanup_test_user(session, platform_user_id)
        return

    user_id = account.user_id
    user = await session.get(User, user_id)
    tenant_id = user.tenant_id if user else None
    specialist_ids = list(
        (
            await session.execute(
                select(Specialist.id).where(Specialist.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    target_ids = [user_id, *specialist_ids]

    await session.execute(
        delete(Blacklist).where(
            or_(
                Blacklist.user_id == user_id,
                Blacklist.created_by == user_id,
            )
        )
    )
    await session.execute(
        delete(AdminAction).where(
            or_(
                AdminAction.admin_user_id == user_id,
                AdminAction.target_id.in_(target_ids),
            )
        )
    )
    await session.execute(
        delete(RiskFlag).where(RiskFlag.entity_id.in_(target_ids))
    )
    await session.execute(
        delete(Complaint).where(
            or_(
                Complaint.reporter_user_id == user_id,
                Complaint.reviewed_by == user_id,
                Complaint.target_id.in_(target_ids),
            )
        )
    )
    await session.commit()

    await cleanup_test_user(session, platform_user_id)
    if tenant_id:
        await cleanup_legal_documents(session, tenant_id)

async def create_user_with_accepted_consents(session):
    platform_user_id, user_id, tenant_id = await create_test_user(session)

    existing_documents = (
        await session.execute(
            select(LegalDocument.id).where(
                LegalDocument.tenant_id == tenant_id,
                LegalDocument.version == "test-beta-0.4",
            )
        )
    ).scalars().all()

    if not existing_documents:
        await accept_specialist_consents(session, tenant_id, user_id)
    else:
        service = LegalService(LegalRepository(session))
        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

    return platform_user_id, user_id, tenant_id


async def create_admin_user(session, *, role: str = "admin"):
    platform_user_id, user_id, tenant_id = await create_user_with_accepted_consents(session)

    session.add(
        UserRoleMapping(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            status="active",
        )
    )
    await session.commit()
    return platform_user_id, user_id, tenant_id


async def create_pending_specialist(session):
    platform_user_id, user_id, tenant_id = await create_user_with_accepted_consents(session)

    refs = await get_reference_data(session)
    data = build_registration_data(user_id, tenant_id, refs)

    service = SpecialistRegistrationService(SpecialistRepository(session))
    specialist = await service.create_pending_profile(data)

    await session.commit()
    return platform_user_id, user_id, tenant_id, specialist


def test_beta_08_admin_moderation_static_contract():
    models_source = open("database/models.py", encoding="utf-8").read()
    repository_source = open("database/repositories/moderation.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    admin_handler_source = open("handlers/admin.py", encoding="utf-8").read()
    search_source = open("handlers/search.py", encoding="utf-8").read()
    bot_source = open("bot.py", encoding="utf-8").read()

    for fragment in [
        "class Complaint",
        '__tablename__ = "complaints"',
        "class Blacklist",
        '__tablename__ = "blacklist"',
        "class RiskFlag",
        '__tablename__ = "risk_flags"',
        "class AdminAction",
        '__tablename__ = "admin_actions"',
        "class ApprovalRequest",
        '__tablename__ = "approval_requests"',
    ]:
        assert fragment in models_source

    for fragment in [
        "class ModerationRepository",
        "get_admin_roles",
        "require_admin_role",
        "grant_admin_role",
        "revoke_admin_role",
        "ROLE_MANAGEMENT_ROLES",
        "GRANTABLE_ADMIN_ROLES",
        "list_recent_event_logs",
        "list_recent_admin_actions",
        "LOG_VIEW_ROLES",
        "FULL_LOG_VIEW_ROLES",
        "list_pending_specialists",
        "approve_specialist",
        "reject_specialist",
        "create_complaint",
        "list_open_complaints",
        "resolve_complaint",
        "block_user",
        "log_admin_action",
    ]:
        assert fragment in repository_source
        assert fragment in repository_source

    for fragment in [
        "class ModerationService",
        "grant_admin_role",
        "revoke_admin_role",
        "list_recent_event_logs",
        "list_recent_admin_actions",
        "approve_specialist",
        "reject_specialist",
        "create_complaint",
        "resolve_complaint",
        "block_user",
    ]:
        assert fragment in service_source
        assert fragment in service_source

    for fragment in [
        "admin_router = Router()",
        'Command("admin")',
        "ADMIN_MODERATION_MENU_ROLES",
        "ADMIN_PAYMENT_MENU_ROLES",
        "ADMIN_ROLE_MENU_ROLES",
        "ADMIN_LOG_MENU_ROLES",
        "admin_no_available_actions",
        "ADM_PENDING",
        "ADM_COMPLAINTS",
        "ADM_REVIEWS",
        "ADM_RV_VIEW:",
        "ADM_RV_APPROVE:",
        "ADM_RV_REJECT:",
        "ADM_RV_HIDE:",
        "admin_review_ids",
        "entering_review_reject_reason",
        "entering_review_hide_reason",
        "ADM_ROLES",
        "ADM_ROLE_GRANT",
        "ADM_ROLE_REVOKE",
        "ADM_LOGS",
        "format_logs_message",
        "entering_role_grant",
        "entering_role_revoke",
        "ADM_SP_APPROVE:",
        "ADM_SP_REJECT:",
        "ADM_CP_RESOLVE:",
        "ADM_CP_REJECT:",
        "ADM_CP_BLOCK:",
    ]:
        assert fragment in admin_handler_source
        assert fragment in admin_handler_source
        assert fragment in admin_handler_source

    assert "from handlers.admin import admin_router" in bot_source
    assert "dp.include_router(admin_router)" in bot_source
    assert "search_report_pending" in search_source
    assert "create_search_complaint" in search_source
    assert "ModerationService(" in search_source
    assert "ModerationRepository(session)" in search_source
    assert ".create_complaint(" in search_source


async def test_admin_roles_are_read_from_active_user_roles(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )

    try:
        roles = await ModerationRepository(db_session).get_admin_roles(admin_user_id)
        assert "admin" in roles
    finally:
        await cleanup_user(db_session, admin_platform_user_id)

async def test_super_admin_can_grant_and_revoke_admin_role(db_session):
    super_admin_platform_user_id, super_admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="super_admin",
    )
    target_platform_user_id, target_user_id, target_tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        granted = await service.grant_admin_role(
            admin_user_id=super_admin_user_id,
            tenant_id=tenant_id,
            target_platform_user_id=target_platform_user_id,
            role="support",
            reason="beta support access",
        )

        assert granted.entity_id == target_user_id
        assert granted.status == "active"

        roles = await ModerationRepository(db_session).get_admin_roles(target_user_id)
        assert "support" in roles

        revoked = await service.revoke_admin_role(
            admin_user_id=super_admin_user_id,
            tenant_id=tenant_id,
            target_platform_user_id=target_platform_user_id,
            role="support",
            reason="beta support access removed",
        )

        assert revoked.entity_id == target_user_id
        assert revoked.status == "revoked"

        roles = await ModerationRepository(db_session).get_admin_roles(target_user_id)
        assert "support" not in roles

        grant_action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.admin_user_id == super_admin_user_id,
                    AdminAction.target_id == target_user_id,
                    AdminAction.action_type == "grant_admin_role",
                )
            )
        ).scalar_one_or_none()
        assert grant_action is not None

        revoke_action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.admin_user_id == super_admin_user_id,
                    AdminAction.target_id == target_user_id,
                    AdminAction.action_type == "revoke_admin_role",
                )
            )
        ).scalar_one_or_none()
        assert revoke_action is not None

    finally:
        await cleanup_user(db_session, target_platform_user_id)
        await cleanup_user(db_session, super_admin_platform_user_id)


async def test_admin_cannot_grant_admin_role(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    target_platform_user_id, target_user_id, target_tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        with pytest.raises(ModerationError):
            await service.grant_admin_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_platform_user_id=target_platform_user_id,
                role="support",
                reason="not allowed",
            )

    finally:
        await cleanup_user(db_session, target_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_support_can_view_event_logs_but_not_admin_actions(db_session):
    support_platform_user_id, support_user_id, tenant_id = await create_admin_user(
        db_session,
        role="support",
    )
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )

    try:
        repository = ModerationRepository(db_session)

        await repository.log_event(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="test_support_visible_event",
            entity_type="user",
            entity_id=support_user_id,
            payload={"safe": True},
        )
        await repository.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="test_admin_only_action",
            target_type="user",
            target_id=support_user_id,
            before_state={},
            after_state={},
            reason="test action",
        )
        await db_session.commit()

        service = ModerationService(ModerationRepository(db_session))

        events = await service.list_recent_event_logs(
            admin_user_id=support_user_id,
            tenant_id=tenant_id,
            limit=5,
        )
        assert any(item.event_type == "test_support_visible_event" for item in events)

        with pytest.raises(ModerationError):
            await service.list_recent_admin_actions(
                admin_user_id=support_user_id,
                tenant_id=tenant_id,
                limit=5,
            )

    finally:
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_client_cannot_use_admin_repository(db_session):
    platform_user_id, user_id, tenant_id = await create_user_with_accepted_consents(
        db_session
    )

    try:
        repository = ModerationRepository(db_session)

        with pytest.raises(ModerationAccessError):
            await repository.require_admin_role(user_id)
    finally:
        await cleanup_user(db_session, platform_user_id)


async def test_admin_approves_pending_specialist_and_logs_audit(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        result = await service.approve_specialist(
            admin_user_id=admin_user_id,
            specialist_id=specialist.id,
            reason="profile is valid",
        )

        await db_session.refresh(specialist)

        assert result.status == "active"
        assert specialist.status == "active"

        action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.action_type == "approve_specialist",
                    AdminAction.target_id == specialist.id,
                    AdminAction.admin_user_id == admin_user_id,
                )
            )
        ).scalar_one_or_none()
        assert action is not None

        event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type == "specialist_approved",
                    EventLog.entity_id == specialist.id,
                )
            )
        ).scalar_one_or_none()
        assert event is not None
    finally:
        await cleanup_user(db_session, specialist_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)


async def test_admin_rejects_pending_specialist_and_saves_reason(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        result = await service.reject_specialist(
            admin_user_id=admin_user_id,
            specialist_id=specialist.id,
            reason="missing documents",
        )

        await db_session.refresh(specialist)

        assert result.status == "rejected"
        assert specialist.status == "rejected"
        assert specialist.moderation_comment == "missing documents"

        action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.action_type == "reject_specialist",
                    AdminAction.target_id == specialist.id,
                )
            )
        ).scalar_one_or_none()
        assert action is not None
    finally:
        await cleanup_user(db_session, specialist_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)


async def test_complaint_creates_risk_flag(db_session):
    reporter_platform_user_id, reporter_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        complaint = await service.create_complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type="specialist",
            target_id=specialist.id,
            reason="fake",
            comment="Looks suspicious",
        )

        assert complaint.status == "new"

        risk_flag = (
            await db_session.execute(
                select(RiskFlag).where(
                    RiskFlag.entity_type == "specialist",
                    RiskFlag.entity_id == specialist.id,
                    RiskFlag.flag_code == "complaint_fake",
                )
            )
        ).scalar_one_or_none()
        assert risk_flag is not None
        assert risk_flag.status == "open"
    finally:
        await cleanup_user(db_session, specialist_platform_user_id)
        await cleanup_user(db_session, reporter_platform_user_id)


async def test_admin_resolves_complaint_and_logs_action(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    reporter_platform_user_id, reporter_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))
        complaint = await service.create_complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type="specialist",
            target_id=specialist.id,
            reason="abuse",
            comment="Bad behavior",
        )

        result = await service.resolve_complaint(
            admin_user_id=admin_user_id,
            complaint_id=complaint.id,
            status="resolved",
            reason="confirmed",
        )

        await db_session.refresh(complaint)

        assert result.status == "resolved"
        assert complaint.status == "resolved"
        assert complaint.reviewed_by == admin_user_id
        assert complaint.reviewed_at is not None

        action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.action_type == "resolved_complaint",
                    AdminAction.target_id == complaint.id,
                )
            )
        ).scalar_one_or_none()
        assert action is not None
    finally:
        await cleanup_user(db_session, specialist_platform_user_id)
        await cleanup_user(db_session, reporter_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)


async def test_admin_blocks_user_and_creates_blacklist(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    target_platform_user_id, target_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        result = await service.block_user(
            admin_user_id=admin_user_id,
            user_id=target_user_id,
            reason="confirmed abuse",
            comment="manual admin block",
        )

        user = await db_session.get(User, target_user_id)

        assert result.status == "blocked"
        assert user.status == "blocked"

        blacklist = (
            await db_session.execute(
                select(Blacklist).where(
                    Blacklist.user_id == target_user_id,
                    Blacklist.status == "active",
                )
            )
        ).scalar_one_or_none()
        assert blacklist is not None
        assert blacklist.created_by == admin_user_id
    finally:
        await cleanup_user(db_session, target_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)


async def test_moderator_can_review_but_cannot_block_user(db_session):
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    target_platform_user_id, target_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        roles = await service.get_admin_roles(moderator_user_id)
        assert "moderator" in roles

        await service.list_pending_specialists(
            admin_user_id=moderator_user_id,
            limit=5,
        )

        with pytest.raises(ModerationError):
            await service.block_user(
                admin_user_id=moderator_user_id,
                user_id=target_user_id,
                reason="not allowed",
            )
    finally:
        await cleanup_user(db_session, target_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)


def test_admin_callbacks_are_compact_and_do_not_use_uuid_payloads():
    source = open("handlers/admin.py", encoding="utf-8").read()

    forbidden_fragments = [
        "ADM_SP_APPROVE:{specialist_id}",
        "ADM_SP_REJECT:{specialist_id}",
        "ADM_CP_RESOLVE:{complaint_id}",
        "ADM_CP_REJECT:{complaint_id}",
        "ADM_CP_BLOCK:{complaint_id}",
        "UUID(callback.data.split",
        "UUID(callback.data.rsplit",
        "ADM_RV_APPROVE:{review_id}",
        "ADM_RV_REJECT:{review_id}",
        "ADM_RV_HIDE:{review_id}",
        "ADM_RV_VIEW:{review_id}",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

    required_fragments = [
        "admin_pending_specialist_ids",
        "admin_complaint_ids",
        "callback_data=f\"ADM_SP_APPROVE:{index}\"",
        "callback_data=f\"ADM_CP_RESOLVE:{index}\"",
        "admin_review_ids",
        "callback_data=f\"ADM_RV_APPROVE:{index}\"",
        "callback_data=f\"ADM_RV_REJECT:{index}\"",
        "callback_data=f\"ADM_RV_HIDE:{index}\"",
    ]

    for fragment in required_fragments:
        assert fragment in source