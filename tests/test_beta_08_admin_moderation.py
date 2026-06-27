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

async def test_support_cannot_view_audit_logs_or_admin_actions(db_session):
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

        with pytest.raises(ModerationError):
            await service.list_recent_event_logs(
                admin_user_id=support_user_id,
                tenant_id=tenant_id,
                limit=5,
            )

        with pytest.raises(ModerationError):
            await service.list_recent_admin_actions(
                admin_user_id=support_user_id,
                tenant_id=tenant_id,
                limit=5,
            )

    finally:
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_permission_matrix_blocks_forbidden_staff_actions(db_session):
    support_platform_user_id, support_user_id, tenant_id = await create_admin_user(
        db_session,
        role="support",
    )
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        with pytest.raises(ModerationError):
            await service.open_moderator_menu(
                moderator_user_id=support_user_id,
                tenant_id=tenant_id,
            )

        with pytest.raises(ModerationError):
            await service.open_complaints_queue(
                moderator_user_id=support_user_id,
                tenant_id=tenant_id,
                statuses={"new", "in_review"},
                page=0,
            )

        with pytest.raises(ModerationError):
            await service.open_scoped_blacklist_queue(
                moderator_user_id=support_user_id,
                tenant_id=tenant_id,
                view="active",
                page=0,
            )

        with pytest.raises(ModerationError):
            await service.open_global_blacklist_queue(
                admin_user_id=support_user_id,
                tenant_id=tenant_id,
                view="active",
                page=0,
            )

        with pytest.raises(ModerationError):
            await service.open_global_blacklist_queue(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                view="active",
                page=0,
            )

        with pytest.raises(ModerationError):
            await service.open_admin_audit(
                admin_user_id=support_user_id,
                tenant_id=tenant_id,
                target_type="all",
                page=0,
            )

    finally:
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)

async def test_permission_matrix_complaints_access(db_session):
    support_platform_user_id, support_user_id, tenant_id = await create_admin_user(
        db_session,
        role="support",
    )
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        moderator_items = await service.open_complaints_queue(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            statuses={"new", "in_review"},
            page=0,
        )
        assert isinstance(moderator_items, list)

        admin_items = await service.open_complaints_queue(
            moderator_user_id=admin_user_id,
            tenant_id=tenant_id,
            statuses={"new", "in_review"},
            page=0,
        )
        assert isinstance(admin_items, list)

        with pytest.raises(ModerationError):
            await service.open_complaints_queue(
                moderator_user_id=support_user_id,
                tenant_id=tenant_id,
                statuses={"new", "in_review"},
                page=0,
            )

    finally:
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_permission_matrix_specialist_moderation_access(db_session):
    support_platform_user_id, support_user_id, tenant_id = await create_admin_user(
        db_session,
        role="support",
    )
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        moderator_items = await service.open_pending_specialists_queue(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            page=0,
        )
        assert isinstance(moderator_items, list)

        admin_items = await service.open_pending_specialists_queue(
            moderator_user_id=admin_user_id,
            tenant_id=tenant_id,
            page=0,
        )
        assert isinstance(admin_items, list)

        with pytest.raises(ModerationError):
            await service.open_pending_specialists_queue(
                moderator_user_id=support_user_id,
                tenant_id=tenant_id,
                page=0,
            )

    finally:
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_permission_matrix_scoped_blacklist_access(db_session):
    support_platform_user_id, support_user_id, tenant_id = await create_admin_user(
        db_session,
        role="support",
    )
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )

    try:
        service = ModerationService(ModerationRepository(db_session))

        moderator_items = await service.open_scoped_blacklist_queue(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            view="active",
            page=0,
        )
        assert isinstance(moderator_items, list)

        admin_items = await service.open_scoped_blacklist_queue(
            moderator_user_id=admin_user_id,
            tenant_id=tenant_id,
            view="active",
            page=0,
        )
        assert isinstance(admin_items, list)

        with pytest.raises(ModerationError):
            await service.open_scoped_blacklist_queue(
                moderator_user_id=support_user_id,
                tenant_id=tenant_id,
                view="active",
                page=0,
            )

    finally:
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_permission_matrix_global_blacklist_access(db_session):
    support_platform_user_id, support_user_id, tenant_id = await create_admin_user(
        db_session,
        role="support",
    )
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    target_platform_user_id, target_user_id, target_tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )

    assert target_tenant_id == tenant_id

    try:
        service = ModerationService(ModerationRepository(db_session))

        with pytest.raises(ModerationError):
            await service.block_user(
                admin_user_id=support_user_id,
                user_id=target_user_id,
                reason="support cannot global block",
            )

        with pytest.raises(ModerationError):
            await service.block_user(
                admin_user_id=moderator_user_id,
                user_id=target_user_id,
                reason="moderator cannot global block",
            )

        blocked = await service.block_user(
            admin_user_id=admin_user_id,
            user_id=target_user_id,
            reason="admin global block",
        )
        assert blocked.status == "blocked"

        with pytest.raises(ModerationError):
            await service.unblock_user(
                admin_user_id=support_user_id,
                user_id=target_user_id,
                reason="support cannot remove global block",
            )

        with pytest.raises(ModerationError):
            await service.unblock_user(
                admin_user_id=moderator_user_id,
                user_id=target_user_id,
                reason="moderator cannot remove global block",
            )

        unblocked = await service.unblock_user(
            admin_user_id=admin_user_id,
            user_id=target_user_id,
            reason="admin remove global block",
        )
        assert unblocked.status == "active"

    finally:
        await cleanup_user(db_session, target_platform_user_id)
        await cleanup_user(db_session, support_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)
        await cleanup_user(db_session, admin_platform_user_id)

async def test_permission_matrix_moderator_audit_is_limited_to_complaints(db_session):
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    reporter_platform_user_id, reporter_user_id, reporter_tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )
    target_platform_user_id, target_user_id, target_tenant_id = (
        await create_user_with_accepted_consents(db_session)
    )

    assert reporter_tenant_id == tenant_id
    assert target_tenant_id == tenant_id

    try:
        service = ModerationService(ModerationRepository(db_session))

        complaint = await service.create_complaint(
            reporter_user_id=reporter_user_id,
            tenant_id=tenant_id,
            target_type="user",
            target_id=target_user_id,
            reason="abuse",
            comment="Permission matrix complaint audit.",
        )

        await service.confirm_complaint(
            reporter_user_id=reporter_user_id,
            complaint_id=complaint.id,
        )

        await service.resolve_complaint(
            admin_user_id=moderator_user_id,
            tenant_id=tenant_id,
            complaint_id=complaint.id,
            status="in_review",
            reason="limited moderator review",
        )

        card = await service.get_moderator_complaint_card(
            moderator_user_id=moderator_user_id,
            tenant_id=tenant_id,
            complaint_id=complaint.id,
        )
        assert card.complaint_id == complaint.id
        assert len(card.history) > 0

        with pytest.raises(ModerationError):
            await service.open_admin_audit(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                target_type="all",
                page=0,
            )

        admin_audit = await service.open_admin_audit(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            target_type="all",
            page=0,
        )
        assert admin_audit.items is not None

    finally:
        await cleanup_user(db_session, reporter_platform_user_id)
        await cleanup_user(db_session, target_platform_user_id)
        await cleanup_user(db_session, moderator_platform_user_id)
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

@pytest.mark.asyncio
async def test_admin_menu_returns_counts_and_writes_audit(
    db_session,
):
    (
        admin_platform_user_id,
        admin_user_id,
        tenant_id,
    ) = await create_admin_user(
        db_session,
        role="admin",
    )

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        summary = await service.open_admin_menu(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
        )

        assert summary.users >= 1
        assert summary.specialists >= 0
        assert summary.tickets >= 0
        assert summary.complaints >= 0
        assert summary.blacklist >= 0
        assert summary.audit_alerts >= 0

        event = (
            await db_session.execute(
                select(EventLog)
                .where(
                    EventLog.tenant_id == tenant_id,
                    EventLog.user_id == admin_user_id,
                    EventLog.event_type == "admin_menu",
                    EventLog.entity_type == "admin_dashboard",
                )
                .order_by(EventLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        assert event is not None
        assert event.entity_id == admin_user_id
        assert event.payload["counts"]["users"] >= 1

    finally:
        await cleanup_user(
            db_session,
            admin_platform_user_id,
        )


def test_minimal_admin_menu_matches_tz10_a1_contract():
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    service_source = open(
        "services/moderation.py",
        encoding="utf-8",
    ).read()
    handler_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()

    assert "async def get_admin_menu_counts" in repository_source
    assert "class AdminMenuSummary" in service_source
    assert "async def open_admin_menu" in service_source
    assert 'event_type="admin_menu"' in service_source

    assert "def format_admin_menu" in handler_source
    assert "def minimal_admin_menu_keyboard" in handler_source

    for callback_data in (
        "ADM_USERS",
        "ADM_ADMIN_SPECIALISTS",
        "ADM_ADMIN_SUPPORT",
        "ADM_MODERATION_MENU",
        "ADM_GLOBAL_BLACKLIST",
        "ADM_LOGS",
        "ROLE_SWITCH_MENU",
    ):
        assert f'callback_data="{callback_data}"' in handler_source

@pytest.mark.asyncio
async def test_admin_searches_user_by_supported_identifiers(
    db_session,
):
    (
        admin_platform_id,
        admin_user_id,
        tenant_id,
    ) = await create_admin_user(
        db_session,
        role="admin",
    )

    (
        target_platform_id,
        target_user_id,
        target_tenant_id,
    ) = await create_user_with_accepted_consents(
        db_session
    )

    account = (
        await db_session.execute(
            select(UserAccount).where(
                UserAccount.user_id == target_user_id,
                UserAccount.platform == "telegram",
            )
        )
    ).scalar_one()

    account.username = f"a2user{uuid4().hex[:8]}"
    account.first_name = "A2"
    account.last_name = "Search User"
    await db_session.commit()

    service = ModerationService(
        ModerationRepository(db_session)
    )

    try:
        assert target_tenant_id == tenant_id

        telegram_results = await service.search_admin_users(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            query=target_platform_id,
        )

        username_results = await service.search_admin_users(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            query=f"@{account.username}",
        )

        number_results = await service.search_admin_users(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            query=f"user-{target_user_id.hex[:8]}",
        )

        for results in (
            telegram_results,
            username_results,
            number_results,
        ):
            assert len(results) == 1

            card = results[0]

            assert card.user_id == target_user_id
            assert card.user_number == (
                f"user-{target_user_id.hex[:8]}"
            )
            assert card.display_name == "A2 Search User"
            assert card.status == "active"

            assert target_platform_id not in card.telegram_id
            assert card.telegram_id.endswith(
                target_platform_id[-4:]
            )

            assert account.username not in card.username
            assert card.username.startswith(
                f"@{account.username[:3]}"
            )

        events = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.tenant_id == tenant_id,
                    EventLog.user_id == admin_user_id,
                    EventLog.event_type == "user_search",
                )
            )
        ).scalars().all()

        assert len(events) >= 3

        for event in events[-3:]:
            assert event.payload["results_count"] == 1
            assert "query" not in event.payload

    finally:
        await cleanup_user(
            db_session,
            target_platform_id,
        )
        await cleanup_user(
            db_session,
            admin_platform_id,
        )


@pytest.mark.asyncio
async def test_moderator_cannot_use_admin_user_search(
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

    try:
        service = ModerationService(
            ModerationRepository(db_session)
        )

        with pytest.raises(
            ModerationError,
            match="access",
        ):
            await service.search_admin_users(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                query=moderator_platform_id,
            )

    finally:
        await cleanup_user(
            db_session,
            moderator_platform_id,
        )


def test_admin_user_search_matches_tz10_a2_contract():
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    service_source = open(
        "services/moderation.py",
        encoding="utf-8",
    ).read()
    handler_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()

    assert "class AdminUserSearchRow" in repository_source
    assert "async def search_admin_users" in repository_source
    assert "UserAccount.platform_user_id" in repository_source
    assert "UserAccount.username.ilike" in repository_source
    assert "cast(User.id, String).ilike" in repository_source

    assert "class AdminUserSearchCard" in service_source
    assert 'event_type="user_search"' in service_source
    assert "masked_telegram_id" in service_source
    assert "masked_username" in service_source

    assert (
        "AdminModerationFSM.entering_admin_user_search"
        in handler_source
    )
    assert 'F.data == "ADM_USERS"' in handler_source
    assert 'callback_data=f"ADM_USER_VIEW:{index}"' in handler_source
    assert "admin_user_search_ids" in handler_source

def test_super_admin_cabinet_matches_part2_sa1_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminMenuSummary" in service_source
    assert "async def open_super_admin_menu" in service_source
    assert "get_super_admin_menu_counts" in service_source
    assert 'event_type="super_admin_menu_opened"' in service_source

    assert "async def get_super_admin_menu_counts" in repository_source
    assert '{"super_admin"}' in repository_source

    assert "def format_super_admin_menu" in admin_source
    assert "def super_admin_menu_keyboard" in admin_source
    assert 'active_role == "super_admin"' in admin_source

    assert "super_admin_menu_text" in texts_source
    assert "super_admin_users_btn" in texts_source
    assert "super_admin_roles_btn" in texts_source
    assert "super_admin_permissions_btn" in texts_source
    assert "super_admin_scopes_btn" in texts_source
    assert "super_admin_system_btn" in texts_source
    assert "super_admin_audit_btn" in texts_source
    assert "super_admin_finance_btn" in texts_source
    assert "super_admin_regions_btn" in texts_source
    assert "super_admin_smoke_tests_btn" in texts_source

def test_super_admin_user_search_matches_part2_sa2_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminUserSearchCard" in service_source
    assert "async def search_super_admin_users" in service_source
    assert 'event_type="super_admin_user_search"' in service_source
    assert '"query_length": len(normalized_query)' in service_source
    assert '"result_count": len(rows)' in service_source
    assert "masked_telegram_id" in service_source
    assert "masked_username" in service_source
    assert "user_number=f\"user-{row.user_id.hex[:8]}\"" in service_source

    assert "class SuperAdminUserSearchRow" in repository_source
    assert "async def search_super_admin_users" in repository_source
    assert '{"super_admin"}' in repository_source
    assert "UserAccount.platform_user_id" in repository_source
    assert "UserAccount.username" in repository_source
    assert "func.string_agg" in repository_source

    assert "waiting_super_admin_user_search" in admin_source
    assert '@admin_router.callback_query(F.data == "SA_USERS")' in admin_source
    assert "format_super_admin_user_search_results" in admin_source
    assert "super_admin_user_search_keyboard" in admin_source
    assert "SA_USER_OPEN:" in admin_source

    assert "super_admin_user_search_prompt" in texts_source
    assert "super_admin_user_not_found" in texts_source
    assert "super_admin_user_search_card" in texts_source

def test_super_admin_user_card_matches_part2_sa3_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminUserDetailsRow" in repository_source
    assert "async def get_super_admin_user_details" in repository_source
    assert '{"super_admin"}' in repository_source
    assert "complaints_count" in repository_source
    assert "blacklist_count" in repository_source

    assert "class SuperAdminUserDetailsCard" in service_source
    assert "async def get_super_admin_user_details" in service_source
    assert 'event_type="super_admin_user_viewed"' in service_source
    assert "masked_telegram_id" in service_source
    assert "masked_username" in service_source
    assert "risk_flags" in service_source

    assert "def format_super_admin_user_card" in admin_source
    assert "def super_admin_user_card_keyboard" in admin_source
    assert 'F.data.startswith("SA_USER_OPEN:")' in admin_source
    assert "super_admin_selected_user_id" in admin_source
    assert "await state.set_state(None)" in admin_source

    assert "super_admin_user_card" in texts_source
    assert "super_admin_user_profile_btn" in texts_source
    assert "super_admin_user_roles_btn" in texts_source
    assert "super_admin_user_scopes_btn" in texts_source
    assert "super_admin_user_audit_btn" in texts_source
    assert "super_admin_impersonate_btn" in texts_source

def test_super_admin_user_roles_matches_part2_sa4_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminUserRoleRow" in repository_source
    assert "async def list_super_admin_user_roles" in repository_source
    assert '{"super_admin"}' in repository_source
    assert "UserRoleMapping.role" in repository_source
    assert "UserRoleMapping.status" in repository_source
    assert "UserRoleMapping.granted_by" in repository_source
    assert "UserRoleMapping.granted_at" in repository_source

    assert "class SuperAdminUserRoleCard" in service_source
    assert "async def list_super_admin_user_roles" in service_source
    assert 'event_type="super_admin_user_roles_viewed"' in service_source
    assert "role_number=f\"role-{row.role_id.hex[:8]}\"" in service_source

    assert "def format_super_admin_user_roles" in admin_source
    assert "def super_admin_user_roles_keyboard" in admin_source
    assert '@admin_router.callback_query(F.data == "SA_USER_ROLES")' in admin_source
    assert "super_admin_selected_user_id" in admin_source

    assert "super_admin_user_roles_title" in texts_source
    assert "super_admin_user_role_card" in texts_source
    assert "super_admin_user_roles_empty" in texts_source
    assert "super_admin_role_grant_btn" in texts_source
    assert "super_admin_role_revoke_btn" in texts_source
    assert "super_admin_role_scope_btn" in texts_source
    assert "super_admin_role_history_btn" in texts_source

def test_super_admin_role_change_matches_part2_sa41_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "SUPER_ADMIN_GRANTABLE_ROLES" in repository_source
    assert "async def grant_super_admin_user_role" in repository_source
    assert "async def revoke_super_admin_user_role" in repository_source
    assert '"root"' in repository_source
    assert "Root role is disabled outside recovery flow." in repository_source
    assert "Cannot revoke the last Super Admin" in repository_source
    assert 'event_type="user_role_changed"' in repository_source
    assert 'event_type="role_change_confirmed"' in repository_source
    assert 'action_type="user_role_changed"' in repository_source

    assert "async def grant_super_admin_user_role" in service_source
    assert "async def revoke_super_admin_user_role" in service_source
    assert "self._require_reason(reason)" in service_source

    assert "entering_super_admin_role_grant" in admin_source
    assert "confirming_super_admin_role_grant" in admin_source
    assert "confirming_super_admin_role_grant_final" in admin_source
    assert "entering_super_admin_role_revoke" in admin_source
    assert "confirming_super_admin_role_revoke" in admin_source
    assert "confirming_super_admin_role_revoke_final" in admin_source
    assert "parse_super_admin_role_action" in admin_source
    assert "super_admin_role_confirm_keyboard" in admin_source
    assert "SA_ROLE_GRANT_CONFIRM" in admin_source
    assert "SA_ROLE_GRANT_FINAL" in admin_source
    assert "SA_ROLE_REVOKE_CONFIRM" in admin_source
    assert "SA_ROLE_REVOKE_FINAL" in admin_source

    assert "super_admin_role_action_format" in texts_source
    assert "super_admin_role_grant_confirm" in texts_source
    assert "super_admin_role_revoke_confirm" in texts_source
    assert "super_admin_role_danger_confirm" in texts_source
    assert "super_admin_role_changed" in texts_source

def test_super_admin_impersonation_matches_part2_sa5_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "async def log_super_admin_impersonation_view" in repository_source
    assert 'event_type=f"impersonation_view_{action}"' in repository_source
    assert 'action_type=f"impersonation_view_{action}"' in repository_source
    assert '"read_only": True' in repository_source

    assert "class SuperAdminImpersonationPreview" in service_source
    assert "async def start_super_admin_impersonation_view" in service_source
    assert "async def stop_super_admin_impersonation_view" in service_source
    assert '"client"' in service_source
    assert '"specialist"' in service_source
    assert '"support"' in service_source
    assert '"moderator"' in service_source
    assert '"admin"' in service_source

    assert "entering_super_admin_impersonation_reason" in admin_source
    assert '@admin_router.callback_query(F.data == "SA_USER_IMPERSONATE")' in admin_source
    assert 'F.data.startswith("SA_IMPERSONATE_ROLE:")' in admin_source
    assert 'F.data == "SA_IMPERSONATE_STOP"' in admin_source
    assert "super_admin_impersonation_keyboard" in admin_source

    assert "super_admin_impersonation_reason_prompt" in texts_source
    assert "super_admin_impersonation_menu" in texts_source
    assert "super_admin_impersonation_preview" in texts_source
    assert "Write actions disabled." in texts_source
    assert "super_admin_impersonation_stopped" in texts_source

def test_super_admin_permission_matrix_matches_part2_sa6_readonly_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminPermissionMatrixRow" in repository_source
    assert "async def list_super_admin_permission_matrix" in repository_source
    assert "role_permissions" in repository_source
    assert "permission_code" in repository_source
    assert "'global' AS scope" in repository_source
    assert "'active' AS status" in repository_source
    assert '{"super_admin"}' in repository_source

    assert "class SuperAdminPermissionMatrixCard" in service_source
    assert "async def list_super_admin_permission_matrix" in service_source
    assert 'event_type="permission_matrix_viewed"' in service_source
    assert 'entity_type="permission_matrix"' in service_source
    assert "permission_number=f\"permission-{row.permission_id.hex[:8]}\"" in service_source

    assert "entering_super_admin_permission_search" in admin_source
    assert '@admin_router.callback_query(F.data == "SA_PERMISSIONS")' in admin_source
    assert '@admin_router.callback_query(F.data == "SA_PERMISSION_SEARCH")' in admin_source
    assert "format_super_admin_permissions" in admin_source
    assert "super_admin_permissions_keyboard" in admin_source

    assert "super_admin_permissions_title" in texts_source
    assert "super_admin_permission_card" in texts_source
    assert "super_admin_permissions_empty" in texts_source
    assert "super_admin_permission_search_btn" in texts_source
    assert "super_admin_permission_grant_btn" in texts_source
    assert "super_admin_permission_revoke_btn" in texts_source
    assert "super_admin_permission_history_btn" in texts_source

def test_super_admin_permission_change_matches_part2_sa6_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "SUPER_ADMIN_PERMISSION_ROLES" in repository_source
    assert "async def grant_super_admin_permission" in repository_source
    assert "async def revoke_super_admin_permission" in repository_source
    assert "INSERT INTO role_permissions" in repository_source
    assert "DELETE FROM role_permissions" in repository_source
    assert 'action_type="permission_changed"' in repository_source
    assert 'event_type="permission_changed"' in repository_source
    assert '{"super_admin"}' in repository_source

    assert "async def grant_super_admin_permission" in service_source
    assert "async def revoke_super_admin_permission" in service_source
    assert "self._require_reason(reason)" in service_source
    assert "await self.repository.session.commit()" in service_source
    assert "await self.repository.session.rollback()" in service_source

    assert "entering_super_admin_permission_grant" in admin_source
    assert "confirming_super_admin_permission_grant" in admin_source
    assert "entering_super_admin_permission_revoke" in admin_source
    assert "confirming_super_admin_permission_revoke" in admin_source
    assert "parse_super_admin_permission_action" in admin_source
    assert "super_admin_permission_confirm_keyboard" in admin_source
    assert 'F.data == "SA_PERMISSION_GRANT"' in admin_source
    assert 'F.data == "SA_PERMISSION_REVOKE"' in admin_source
    assert 'F.data == "SA_PERMISSION_GRANT_CONFIRM"' in admin_source
    assert 'F.data == "SA_PERMISSION_REVOKE_CONFIRM"' in admin_source

    assert "super_admin_permission_action_format" in texts_source
    assert "super_admin_permission_grant_confirm" in texts_source
    assert "super_admin_permission_revoke_confirm" in texts_source
    assert "super_admin_permission_changed" in texts_source

def test_super_admin_global_audit_matches_part2_sa8_list_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()

    assert "async def list_super_admin_audit_actions" in repository_source
    assert "super_admin_audit_records" in repository_source
    super_admin_audit_block = repository_source.split(
        "async def list_super_admin_audit_actions",
        1,
    )[1].split(
        "async def get_admin_audit_action",
        1,
    )[0]

    assert "AdminAction.tenant_id ==" not in super_admin_audit_block
    assert "EventLog.tenant_id ==" not in super_admin_audit_block
    assert "EventLog.event_type != \"audit_viewed\"" in repository_source
    assert '{"super_admin"}' in repository_source

    assert "async def open_super_admin_audit" in service_source
    assert "list_super_admin_audit_actions" in service_source
    assert 'event_type="audit_viewed"' in service_source
    assert '"source": "super_admin_global_audit"' in service_source

    assert '@admin_router.callback_query(F.data == "SA_AUDIT")' in admin_source
    assert 'F.data.startswith("SA_AUDIT_QUEUE:")' in admin_source
    assert 'F.data == "SA_AUDIT_FILTER"' in admin_source
    assert "open_super_admin_audit_queue" in admin_source
    assert "super_admin_audit_filter_keyboard" in admin_source
    assert 'prefix="SA_AUDIT"' in admin_source

def test_super_admin_audit_event_detail_matches_part2_sa81_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminAuditEventDetailRow" in repository_source
    assert "async def get_super_admin_audit_event_detail" in repository_source
    assert "AdminAction.before_state" not in repository_source
    assert "admin_action.before_state or {}" in repository_source
    assert "event.trace_id" in repository_source
    assert '{"super_admin"}' in repository_source

    assert "class SuperAdminAuditEventDetailCard" in service_source
    assert "async def get_super_admin_audit_event_detail" in service_source
    assert 'event_type="audit_event_viewed"' in service_source
    assert "_mask_audit_value" in service_source
    assert "_summarize_audit_dict" in service_source
    assert "correlation_id" in service_source

    assert "def super_admin_audit_card_keyboard" in admin_source
    assert 'callback_data=f"SA_AUDIT_OPEN:{index}"' in admin_source
    assert 'F.data.startswith("SA_AUDIT_OPEN:")' in admin_source
    assert "super_admin_audit_action_ids" in admin_source
    assert "super_admin_audit_event_detail" in texts_source

def test_super_admin_system_matches_part2_sa9_readonly_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    repository_source = open(
        "database/repositories/moderation.py",
        encoding="utf-8",
    ).read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminSystemStatusRow" in repository_source
    assert "async def get_super_admin_system_status" in repository_source
    assert "SELECT version()" in repository_source
    assert "alembic_version" in repository_source
    assert '{"super_admin"}' in repository_source

    assert "class SuperAdminSystemStatusCard" in service_source
    assert "async def open_super_admin_system_status" in service_source
    assert 'event_type="system_settings_viewed"' in service_source
    assert '"source": "super_admin_system"' in service_source
    assert "secrets hidden" in service_source

    assert '@admin_router.callback_query(F.data == "SA_SYSTEM")' in admin_source
    assert 'F.data.startswith("SA_SYSTEM_")' in admin_source
    assert "format_super_admin_system_status" in admin_source
    assert "super_admin_system_keyboard" in admin_source
    assert "SA_SYSTEM_FEATURE_FLAGS" in admin_source
    assert "SA_SYSTEM_HEALTH" in admin_source
    assert "SA_SYSTEM_MAINTENANCE" in admin_source
    assert "SA_SYSTEM_MIGRATIONS" in admin_source
    assert "SA_SYSTEM_ENV" in admin_source

    assert "super_admin_system_status" in texts_source
    assert "Secrets and env values are never shown." in texts_source
    assert "super_admin_feature_flags_btn" in texts_source
    assert "super_admin_health_check_btn" in texts_source
    assert "super_admin_maintenance_btn" in texts_source
    assert "super_admin_migrations_btn" in texts_source
    assert "super_admin_env_status_btn" in texts_source

def test_super_admin_system_detail_buttons_match_part2_sa9_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "async def super_admin_system_detail" in admin_source
    assert 'detail_type == "HEALTH"' in admin_source
    assert 'detail_type == "MIGRATIONS"' in admin_source
    assert 'detail_type == "ENV"' in admin_source
    assert 'detail_type == "FEATURE_FLAGS"' in admin_source
    assert 'detail_type == "MAINTENANCE"' in admin_source
    assert "open_super_admin_system_status" in admin_source
    assert "Env values, tokens and secrets are hidden." in texts_source
    assert "Changing maintenance mode requires a separate confirmation flow." in texts_source
    assert "super_admin_system_health_detail" in texts_source
    assert "super_admin_system_migrations_detail" in texts_source
    assert "super_admin_system_env_detail" in texts_source
    assert "super_admin_system_feature_flags_detail" in texts_source
    assert "super_admin_system_maintenance_detail" in texts_source

def test_super_admin_smoke_tests_match_part2_sa10_safe_contract():
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    service_source = open("services/moderation.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "class SuperAdminSmokeTestResultCard" in service_source
    assert "class SuperAdminSmokeTestRunCard" in service_source
    assert "def list_super_admin_smoke_definitions" in service_source
    assert "async def run_super_admin_smoke_tests" in service_source
    assert 'event_type="smoke_test_run"' in service_source
    assert '"destructive": False' in service_source
    assert '"start"' in service_source
    assert '"registration"' in service_source
    assert '"search"' in service_source
    assert '"request"' in service_source
    assert '"dialogs"' in service_source
    assert '"support"' in service_source
    assert '"moderation"' in service_source
    assert '"admin_access"' in service_source

    assert '@admin_router.callback_query(F.data == "SA_SMOKE")' in admin_source
    assert 'F.data == "SA_SMOKE_RUN_ALL"' in admin_source
    assert "format_super_admin_smoke_tests" in admin_source
    assert "format_super_admin_smoke_run" in admin_source
    assert "super_admin_smoke_keyboard" in admin_source

    assert "super_admin_smoke_title" in texts_source
    assert "super_admin_smoke_card" in texts_source
    assert "super_admin_smoke_run_all_btn" in texts_source
    assert "super_admin_smoke_result_title" in texts_source

    assert "_run_super_admin_smoke_check" in service_source
    assert "_require_tables" in service_source
    assert "_smoke_check_start" in service_source
    assert "_smoke_check_registration" in service_source
    assert "_smoke_check_search" in service_source
    assert "_smoke_check_request" in service_source
    assert "_smoke_check_dialogs" in service_source
    assert "_smoke_check_support" in service_source
    assert "_smoke_check_moderation" in service_source
    assert "_smoke_check_admin_access" in service_source
    assert "Safe read-only check completed." not in service_source

    assert "class SuperAdminSmokeHistoryCard" in service_source
    assert "async def list_super_admin_smoke_history" in service_source
    assert "WHERE event_type = 'smoke_test_run'" in service_source

    assert 'F.data == "SA_SMOKE_RUN_SELECTED"' in admin_source
    assert 'F.data.startswith("SA_SMOKE_RUN:")' in admin_source
    assert 'F.data == "SA_SMOKE_HISTORY"' in admin_source
    assert "super_admin_smoke_selected_keyboard" in admin_source
    assert "format_super_admin_smoke_history" in admin_source
    assert "super_admin_smoke_progress" in admin_source

    assert "super_admin_smoke_select_title" in texts_source
    assert "super_admin_smoke_history_title" in texts_source
    assert "super_admin_smoke_history_card" in texts_source
    assert "super_admin_smoke_history_empty" in texts_source
