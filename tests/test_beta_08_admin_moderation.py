import uuid
from uuid import uuid4
import pytest
from sqlalchemy import delete, or_, select, text
from database.models import (
    AdminAction,
    Blacklist,
    EventLog,
    LegalDocument,
    RiskFlag,
    Specialist,
    User,
    UserAccount,
    UserConsent,
    UserRoleMapping,
    Complaint,
    Tenant,
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
        "ADM_RV_HIDE:",
        "admin_review_ids",
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
        "ADM_CP_SCOPED_BLOCK:",
        "ADM_CP_ADMIN:",
        "ADM_SCOPED_BLACKLIST",
        "ADM_BL_REVOKE:",
        "ADM_BL_ADD",
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
            tenant_id=tenant_id,
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
                    EventLog.event_type == "profile_moderated",
                    EventLog.entity_id == specialist.id,
                )
            )
        ).scalar_one_or_none()

        assert event is not None
        assert event.payload["decision"] == "approved"
        assert event.payload["reason"] == "profile is valid"
        assert event.payload["before_status"] == "pending_moderation"
        assert event.payload["after_status"] == "active"
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
            tenant_id=tenant_id,
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
        event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type == "profile_moderated",
                    EventLog.entity_id == specialist.id,
                )
            )
        ).scalar_one_or_none()

        assert event is not None
        assert event.payload["decision"] == "rejected"
        assert event.payload["reason"] == "missing documents"
        assert event.payload["before_status"] == "pending_moderation"
        assert event.payload["after_status"] == "rejected"
    finally:
        await cleanup_user(db_session, specialist_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_moderator_returns_specialist_profile_for_changes(db_session):
    moderator_platform_user_id, moderator_user_id, tenant_id = (
        await create_admin_user(
            db_session,
            role="moderator",
        )
    )
    (
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        result = await service.request_specialist_changes(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            specialist_id=specialist.id,
            reason="Update profile description",
        )

        await db_session.refresh(specialist)

        assert result.status == "draft"
        assert specialist.status == "draft"
        assert (
            specialist.moderation_comment
            == "Update profile description"
        )

        action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.action_type
                    == "request_specialist_changes",
                    AdminAction.target_id == specialist.id,
                    AdminAction.admin_user_id
                    == moderator_user_id,
                )
            )
        ).scalar_one_or_none()

        assert action is not None
        assert action.reason == "Update profile description"

        event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type == "profile_moderated",
                    EventLog.entity_id == specialist.id,
                )
            )
        ).scalar_one_or_none()

        assert event is not None
        assert event.payload["decision"] == "changes_requested"
        assert event.payload["reason"] == "Update profile description"
        assert event.payload["before_status"] == "pending_moderation"
        assert event.payload["after_status"] == "draft"

    finally:
        await cleanup_user(
            db_session,
            specialist_platform_user_id,
        )
        await cleanup_user(
            db_session,
            moderator_platform_user_id,
        )

async def test_moderator_cannot_moderate_own_specialist_profile(
    db_session,
):
    (
        moderator_platform_user_id,
        moderator_user_id,
        tenant_id,
    ) = await create_admin_user(
        db_session,
        role="moderator",
    )

    refs = await get_reference_data(db_session)
    registration_data = build_registration_data(
        moderator_user_id,
        tenant_id,
        refs,
    )

    specialist_service = SpecialistRegistrationService(
        SpecialistRepository(db_session)
    )
    specialist = await specialist_service.create_pending_profile(
        registration_data
    )
    await db_session.commit()

    # Зберігаємо UUID до rollback усередині moderation service.
    specialist_id = specialist.id

    try:
        moderation_service = ModerationService(
            ModerationRepository(db_session)
        )

        queue = (
            await moderation_service.open_pending_specialists_queue(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                page=0,
                page_size=5,
            )
        )

        assert specialist_id not in {
            item.specialist_id for item in queue
        }

        with pytest.raises(
            ModerationError,
            match="own profile",
        ):
            await moderation_service.get_moderator_specialist_card(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )

        with pytest.raises(
            ModerationError,
            match="own profile",
        ):
            await moderation_service.approve_specialist(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                reason="Approve own profile",
            )

        with pytest.raises(
            ModerationError,
            match="own profile",
        ):
            await moderation_service.reject_specialist(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                reason="Reject own profile",
            )

        with pytest.raises(
            ModerationError,
            match="own profile",
        ):
            await moderation_service.request_specialist_changes(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                reason="Change own profile",
            )

    finally:
        await cleanup_user(
            db_session,
            moderator_platform_user_id,
        )

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
        complaint_id = complaint.id

        review_result = await service.resolve_complaint(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            status="in_review",
            reason="taken for review",
        )

        assert review_result.status == "in_review"

        result = await service.resolve_complaint(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            complaint_id=complaint_id,
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

async def test_duplicate_active_complaint_is_rejected(db_session):
    reporter_platform_user_id, reporter_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    specialist_platform_user_id, _, _, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        await service.create_complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type="specialist",
            target_id=specialist.id,
            reason="fake",
            comment="First complaint",
        )

        with pytest.raises(
            ModerationError,
            match="active complaint",
        ):
            await service.create_complaint(
                tenant_id=tenant_id,
                reporter_user_id=reporter_user_id,
                target_type="specialist",
                target_id=specialist.id,
                reason="fake",
                comment="Duplicate complaint",
            )
    finally:
        await cleanup_user(
            db_session,
            specialist_platform_user_id,
        )
        await cleanup_user(
            db_session,
            reporter_platform_user_id,
        )

async def test_moderator_adds_tenant_scoped_blacklist_without_global_block(
    db_session,
):
    (
        moderator_platform_user_id,
        moderator_user_id,
        tenant_id,
    ) = await create_admin_user(
        db_session,
        role="moderator",
    )

    (
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    specialist_id = specialist.id

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        result = await service.add_specialist_owner_scoped_blacklist(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            reason="Tenant policy violation",
        )

        blacklist_id = result.entity_id

        user = await db_session.get(User, specialist_user_id)
        blacklist = await db_session.get(Blacklist, blacklist_id)

        assert result.status == "active"
        assert user is not None
        assert user.status == "active"

        assert blacklist is not None
        assert blacklist.tenant_id == tenant_id
        assert blacklist.user_id == specialist_user_id
        assert blacklist.status == "active"
        assert blacklist.reason == "Tenant policy violation"
        assert blacklist.created_by == moderator_user_id

        event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type
                    == "scoped_blacklist_changed",
                    EventLog.entity_id == specialist_user_id,
                )
            )
        ).scalar_one_or_none()

        assert event is not None
        assert event.payload["action"] == "added"
        assert event.payload["scope"] == "tenant"
        assert event.payload["reason"] == "Tenant policy violation"
        assert event.payload["blacklist_id"] == str(blacklist_id)

        with pytest.raises(
            ModerationError,
            match="already blacklisted",
        ):
            await service.add_specialist_owner_scoped_blacklist(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                reason="Duplicate tenant block",
            )

    finally:
        await cleanup_user(
            db_session,
            specialist_platform_user_id,
        )
        await cleanup_user(
            db_session,
            moderator_platform_user_id,
        )

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

        await service.open_pending_specialists_queue(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            page=0,
            page_size=5,
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
        "callback_data=f\"ADM_RV_HIDE:{index}\"",
    ]

    for fragment in required_fragments:
        assert fragment in source
    assert "ADM_RV_REJECT:" not in source
    assert "entering_review_reject_reason" not in source

def test_admin_rbac_full_beta_contract_is_covered():
    moderation_repo = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    moderation_service = open(
        "services/moderation.py",
        encoding="utf-8",
    ).read()
    admin_handler = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    billing_repo = open(
        "database/repositories/billing.py",
        encoding="utf-8",
    ).read()

    for role in [
        "super_admin",
        "admin",
        "moderator",
        "support",
        "finance_admin",
        "content_manager",
    ]:
        assert role in moderation_repo

    assert 'ROLE_MANAGEMENT_ROLES = {"super_admin"}' in moderation_repo
    assert "GRANTABLE_ADMIN_ROLES" in moderation_repo
    assert "grant_admin_role" in moderation_repo
    assert "revoke_admin_role" in moderation_repo
    assert 'action_type="grant_admin_role"' in moderation_repo
    assert 'action_type="revoke_admin_role"' in moderation_repo
    assert 'event_type="admin_role_granted"' in moderation_repo
    assert 'event_type="admin_role_revoked"' in moderation_repo

    assert "grant_admin_role" in moderation_service
    assert "revoke_admin_role" in moderation_service
    assert "_require_reason" in moderation_service

    assert 'ADMIN_ROLE_MENU_ROLES = {"super_admin"}' in admin_handler
    assert 'callback_data="ADM_ROLES"' in admin_handler
    assert 'F.data == "ADM_ROLE_GRANT"' in admin_handler
    assert 'F.data == "ADM_ROLE_REVOKE"' in admin_handler
    assert "AdminModerationFSM.entering_role_grant" in admin_handler
    assert "AdminModerationFSM.entering_role_revoke" in admin_handler
    assert '"super_admin" not in roles' in admin_handler

    assert "ApprovalRequest" in billing_repo
def test_admin_panel_uses_active_role_context_for_visible_menu():
    source = open("handlers/admin.py", encoding="utf-8").read()

    assert "def effective_panel_roles" in source
    assert "active_role in roles" in source
    assert "panel_roles = effective_panel_roles(" in source
    assert "admin_panel_keyboard(" in source
    assert "panel_roles," in source

    show_panel_block = source.split(
        "async def show_admin_panel",
        1,
    )[1].split(
        "@admin_router.message(Command(\"admin\"))",
        1,
    )[0]

    assert "panel_roles.intersection(ADMIN_MODERATION_MENU_ROLES)" in show_panel_block
    assert "panel_roles.intersection(ADMIN_SUPPORT_MENU_ROLES)" in show_panel_block
    assert "reply_markup=admin_panel_keyboard(" in show_panel_block

async def test_other_complaint_requires_comment(db_session):
    reporter_platform_user_id, reporter_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    specialist_platform_user_id, _, _, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        with pytest.raises(
            ModerationError,
            match="Comment is required",
        ):
            await service.create_complaint(
                tenant_id=tenant_id,
                reporter_user_id=reporter_user_id,
                target_type="specialist",
                target_id=specialist.id,
                reason="other",
                comment=None,
            )
    finally:
        await cleanup_user(
            db_session,
            specialist_platform_user_id,
        )
        await cleanup_user(
            db_session,
            reporter_platform_user_id,
        )

async def test_complaint_confirmation_creates_audit_event(db_session):
    reporter_platform_user_id, reporter_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    specialist_platform_user_id, _, _, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        complaint = await service.create_complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type="specialist",
            target_id=specialist.id,
            reason="contact",
            comment=None,
        )

        confirmed = await service.confirm_complaint(
            reporter_user_id=reporter_user_id,
            complaint_id=complaint.id,
        )

        assert confirmed.id == complaint.id
        assert confirmed.status == "new"

        event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type == "complaint_confirmed",
                    EventLog.entity_type == "complaint",
                    EventLog.entity_id == complaint.id,
                )
            )
        ).scalar_one_or_none()

        assert event is not None
        assert event.payload["complaint_number"] == (
            str(complaint.id).split("-", 1)[0]
        )
        assert "severity" not in event.payload
    finally:
        await cleanup_user(
            db_session,
            specialist_platform_user_id,
        )
        await cleanup_user(
            db_session,
            reporter_platform_user_id,
        )

async def test_complaint_status_transitions_are_strict(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = (
        await create_admin_user(
            db_session,
            role="admin",
        )
    )
    reporter_platform_user_id, reporter_user_id, tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    specialist_platform_user_id, _, _, specialist = (
        await create_pending_specialist(db_session)
    )

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        complaint = await service.create_complaint(
            tenant_id=tenant_id,
            reporter_user_id=reporter_user_id,
            target_type="specialist",
            target_id=specialist.id,
            reason="abuse",
            comment="Status transition test",
        )

        complaint_id = complaint.id

        with pytest.raises(
            ModerationError,
            match="not allowed",
        ):
            await service.resolve_complaint(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                status="resolved",
                reason="Cannot resolve new complaint",
            )

        review_result = await service.resolve_complaint(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            status="in_review",
            reason="Taken for review",
        )

        assert review_result.status == "in_review"

        resolved_result = await service.resolve_complaint(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            status="resolved",
            reason="Complaint confirmed",
        )

        assert resolved_result.status == "resolved"

        with pytest.raises(
            ModerationError,
            match="not allowed",
        ):
            await service.resolve_complaint(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                status="rejected",
                reason="Final status cannot change",
            )
    finally:
        await cleanup_user(
            db_session,
            specialist_platform_user_id,
        )
        await cleanup_user(
            db_session,
            reporter_platform_user_id,
        )
        await cleanup_user(
            db_session,
            admin_platform_user_id,
        )

async def test_moderator_cannot_resolve_complaint_from_another_tenant(
    db_session,
):
    (
        moderator_platform_id,
        moderator_user_id,
        tenant_id,
    ) = await create_admin_user(
        db_session,
        role="moderator",
    )

    (
        reporter_platform_id,
        reporter_user_id,
        _,
    ) = await create_user_with_accepted_consents(
        db_session
    )

    tenant_token = uuid4().hex[:8]
    other_tenant_id = uuid4()

    await db_session.execute(
        text("""
            insert into tenants (
                id,
                name,
                slug,
                status
            )
            values (
                :id,
                :name,
                :slug,
                'active'
            )
        """),
        {
            "id": other_tenant_id,
            "name": f"MOD7 foreign tenant {tenant_token}",
            "slug": f"mod7-foreign-{tenant_token}",
        },
    )

    complaint = Complaint(
        tenant_id=other_tenant_id,
        reporter_user_id=reporter_user_id,
        target_type="user",
        target_id=reporter_user_id,
        reason="abuse",
        comment="Foreign tenant complaint",
        status="in_review",
    )
    db_session.add(complaint)
    await db_session.commit()

    complaint_id = complaint.id

    service = ModerationService(
        ModerationRepository(db_session)
    )

    try:
        with pytest.raises(
            ModerationError,
            match="Complaint not found",
        ):
            await service.resolve_complaint(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                status="resolved",
                reason="Must not cross tenant boundary",
            )

        stored_complaint = await db_session.get(
            Complaint,
            complaint_id,
        )

        assert stored_complaint is not None
        assert stored_complaint.status == "in_review"

    finally:
        await cleanup_user(
            db_session,
            moderator_platform_id,
        )
        await cleanup_user(
            db_session,
            reporter_platform_id,
        )

        await db_session.execute(
            delete(Tenant).where(
                Tenant.id == other_tenant_id,
            )
        )
        await db_session.commit()


async def test_moderator_specialist_card_includes_open_risk_flags(
    db_session,
):
    (
        moderator_platform_id,
        moderator_user_id,
        tenant_id,
    ) = await create_admin_user(
        db_session,
        role="moderator",
    )

    (
        specialist_platform_id,
        specialist_user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(
        db_session
    )

    risk_flag = RiskFlag(
        tenant_id=tenant_id,
        entity_type="specialist",
        entity_id=specialist.id,
        flag_code="mod3_test_risk",
        severity="medium",
        status="open",
        details={"source": "MOD3 test"},
    )
    db_session.add(risk_flag)
    await db_session.commit()

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        card = await service.get_moderator_specialist_card(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            specialist_id=specialist.id,
        )

        assert card.complaints_count >= 0
        assert card.open_risk_flags_count == 1

    finally:
        await cleanup_user(
            db_session,
            specialist_platform_id,
        )
        await cleanup_user(
            db_session,
            moderator_platform_id,
        )