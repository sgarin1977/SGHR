import os
import uuid

from sqlalchemy import delete, select

from database.models import (
    LegalDocument,
    User,
    UserAccount,
    UserConsent,
    UserRoleMapping,
)
from database.repositories.legal import LegalRepository
from database.repositories.user import UserRepository
from datetime import datetime
from services.legal import (
    REQUIRED_SPECIALIST_CONSENTS,
    LegalService,
    MissingLegalDocumentError,
)


LEGAL_TEST_VERSION = "test-beta-0.3"


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

    await session.execute(delete(UserConsent).where(UserConsent.user_id == user_id))
    await session.execute(delete(UserRoleMapping).where(UserRoleMapping.user_id == user_id))
    await session.execute(delete(UserAccount).where(UserAccount.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def cleanup_legal_documents(session, tenant_id):
    await session.rollback()

    test_versions = [
        LEGAL_TEST_VERSION,
        f"{LEGAL_TEST_VERSION}-new",
    ]

    await session.execute(
        delete(UserConsent).where(
            UserConsent.tenant_id == tenant_id,
            UserConsent.version.in_(test_versions),
        )
    )
    await session.execute(
        delete(LegalDocument).where(
            LegalDocument.tenant_id == tenant_id,
            LegalDocument.version.in_(test_versions),
        )
    )
    await session.commit()

async def create_test_user(session):
    platform_user_id = f"test-legal-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="test_legal",
        first_name="Test",
        last_name="Legal",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    assert user is not None

    default_tenant_id = os.getenv("DEFAULT_TENANT_ID")
    assert default_tenant_id
    assert str(user.tenant_id) == default_tenant_id

    return platform_user_id, user.id, user.tenant_id


async def ensure_legal_documents(session, tenant_id):
    for doc_type in REQUIRED_SPECIALIST_CONSENTS:
        session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type=doc_type,
                version=LEGAL_TEST_VERSION,
                language="ru",
                title=f"{doc_type} test title",
                content_text=f"{doc_type} test content",
                status="active",
            )
        )

    await session.commit()


async def test_specialist_registration_blocked_without_required_consents(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        service = LegalService(LegalRepository(db_session))

        has_consents = await service.has_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert has_consents is False

        missing = await service.get_missing_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert {doc.doc_type for doc in missing} == set(REQUIRED_SPECIALIST_CONSENTS)

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_accept_required_consents_unlocks_specialist_registration(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        service = LegalService(LegalRepository(db_session))

        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

        has_consents = await service.has_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert has_consents is True

        consent_result = await db_session.execute(
            select(UserConsent).where(
                UserConsent.tenant_id == tenant_id,
                UserConsent.user_id == user_id,
                UserConsent.revoked_at.is_(None),
            )
        )
        consents = consent_result.scalars().all()

        accepted_by_type = {item.consent_type: item for item in consents}

        assert set(REQUIRED_SPECIALIST_CONSENTS).issubset(set(accepted_by_type))
        assert all(
            accepted_by_type[doc_type].platform == "telegram"
            for doc_type in REQUIRED_SPECIALIST_CONSENTS
        )

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_revoked_consent_blocks_specialist_registration(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        service = LegalService(LegalRepository(db_session))

        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

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

        has_consents = await service.has_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert has_consents is False

        missing = await service.get_missing_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert "specialist_consent" in {doc.doc_type for doc in missing}

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)
async def test_repeated_accept_does_not_duplicate_consents(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        service = LegalService(LegalRepository(db_session))

        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

        consent_result = await db_session.execute(
            select(UserConsent).where(
                UserConsent.tenant_id == tenant_id,
                UserConsent.user_id == user_id,
                UserConsent.revoked_at.is_(None),
            )
        )
        consents = consent_result.scalars().all()

        consent_types = [
            item.consent_type
            for item in consents
            if item.consent_type in REQUIRED_SPECIALIST_CONSENTS
        ]

        assert sorted(consent_types) == sorted(REQUIRED_SPECIALIST_CONSENTS)

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_accept_only_missing_consents_after_partial_accept(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        repo = LegalRepository(db_session)
        service = LegalService(repo)

        documents = await repo.get_current_documents(
            tenant_id=tenant_id,
            doc_types=REQUIRED_SPECIALIST_CONSENTS,
            language="ru",
        )

        await repo.accept_consent(
            tenant_id=tenant_id,
            user_id=user_id,
            consent_type="terms",
            version=documents["terms"].version,
            platform="telegram",
        )
        await db_session.commit()

        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

        consent_result = await db_session.execute(
            select(UserConsent).where(
                UserConsent.tenant_id == tenant_id,
                UserConsent.user_id == user_id,
                UserConsent.revoked_at.is_(None),
            )
        )
        consents = consent_result.scalars().all()

        consent_types = [
            item.consent_type
            for item in consents
            if item.consent_type in REQUIRED_SPECIALIST_CONSENTS
        ]

        assert sorted(consent_types) == sorted(REQUIRED_SPECIALIST_CONSENTS)
        assert consent_types.count("terms") == 1

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_new_legal_document_version_requires_new_consent(db_session):
    platform_user_id, user_id, tenant_id = await create_test_user(db_session)

    try:
        await cleanup_legal_documents(db_session, tenant_id)
        await ensure_legal_documents(db_session, tenant_id)

        service = LegalService(LegalRepository(db_session))

        await service.accept_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
            platform="telegram",
        )

        db_session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type="terms",
                version=f"{LEGAL_TEST_VERSION}-new",
                language="ru",
                title="terms new version",
                content_text="terms new content",
                status="active",
            )
        )
        await db_session.commit()

        has_consents = await service.has_required_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert has_consents is False

        missing = await service.get_missing_specialist_consents(
            tenant_id=tenant_id,
            user_id=user_id,
            language="ru",
        )

        assert "terms" in {doc.doc_type for doc in missing}

    finally:
        await cleanup_user_by_platform_id(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)