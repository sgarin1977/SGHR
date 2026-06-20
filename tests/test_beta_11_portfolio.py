from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from database.models import (
    FileStorageObject,
    SpecialistPortfolioItem,
)
from database.repositories.portfolio import PortfolioRepository
from services.portfolio import PortfolioService, PortfolioServiceError
from services.portfolio_storage import (
    PDF_MAX_SIZE,
    PHOTO_MAX_SIZE,
    PortfolioFileValidationError,
    validate_portfolio_file,
)
from tests.test_beta_04_specialist_registration import cleanup_test_user
from tests.test_beta_08_admin_moderation import (
    cleanup_user,
    create_admin_user,
    create_pending_specialist,
)
from database.models import (
    AdminAction,
    EventLog,
    FileStorageObject,
    RiskFlag,
    SpecialistPortfolioItem,
    UserAccount,
)

@pytest.mark.parametrize(
    ("filename", "mime_type", "content", "expected_type"),
    [
        ("photo.jpg", "image/jpeg", b"\xff\xd8\xfftest", "photo"),
        ("photo.jpeg", "image/jpeg", b"\xff\xd8\xfftest", "photo"),
        ("photo.png", "image/png", b"\x89PNG\r\n\x1a\ntest", "photo"),
        ("photo.webp", "image/webp", b"RIFF0000WEBPtest", "photo"),
        ("certificate.pdf", "application/pdf", b"%PDF-1.7 test", "pdf"),
    ],
)
def test_allowed_portfolio_files(
    filename,
    mime_type,
    content,
    expected_type,
):
    result = validate_portfolio_file(
        filename=filename,
        mime_type=mime_type,
        content=content,
    )

    assert result.file_type == expected_type
    assert result.mime_type == mime_type
    assert result.size_bytes == len(content)


@pytest.mark.parametrize(
    "filename",
    [
        "document.doc",
        "document.docx",
        "table.xls",
        "table.xlsx",
        "archive.zip",
        "archive.rar",
        "program.exe",
        "video.mp4",
    ],
)
def test_disallowed_portfolio_extensions(filename):
    with pytest.raises(PortfolioFileValidationError):
        validate_portfolio_file(
            filename=filename,
            mime_type="application/octet-stream",
            content=b"test",
        )


def test_portfolio_file_rejects_mime_mismatch():
    with pytest.raises(
        PortfolioFileValidationError,
        match="MIME type",
    ):
        validate_portfolio_file(
            filename="photo.jpg",
            mime_type="application/pdf",
            content=b"\xff\xd8\xfftest",
        )


def test_portfolio_file_rejects_fake_content():
    with pytest.raises(
        PortfolioFileValidationError,
        match="content does not match",
    ):
        validate_portfolio_file(
            filename="photo.jpg",
            mime_type="image/jpeg",
            content=b"MZ fake executable",
        )


def test_portfolio_file_rejects_empty_content():
    with pytest.raises(
        PortfolioFileValidationError,
        match="empty",
    ):
        validate_portfolio_file(
            filename="photo.png",
            mime_type="image/png",
            content=b"",
        )


def test_portfolio_photo_size_limit():
    content = b"\xff\xd8\xff" + (
        b"x" * (PHOTO_MAX_SIZE - 3)
    )

    validate_portfolio_file(
        filename="photo.jpg",
        mime_type="image/jpeg",
        content=content,
    )

    with pytest.raises(
        PortfolioFileValidationError,
        match="10 MB",
    ):
        validate_portfolio_file(
            filename="photo.jpg",
            mime_type="image/jpeg",
            content=content + b"x",
        )


def test_portfolio_pdf_size_limit():
    content = b"%PDF-" + (
        b"x" * (PDF_MAX_SIZE - 5)
    )

    validate_portfolio_file(
        filename="certificate.pdf",
        mime_type="application/pdf",
        content=content,
    )

    with pytest.raises(
        PortfolioFileValidationError,
        match="20 MB",
    ):
        validate_portfolio_file(
            filename="certificate.pdf",
            mime_type="application/pdf",
            content=content + b"x",
        )


class FakePortfolioStorage:
    def __init__(self):
        self.uploaded = {}
        self.deleted = []
        self.signed_paths = []

    async def upload(
        self,
        *,
        storage_path: str,
        content: bytes,
        mime_type: str,
    ) -> None:
        self.uploaded[storage_path] = {
            "content": content,
            "mime_type": mime_type,
        }

    async def create_signed_url(
        self,
        *,
        storage_path: str,
        expires_in: int = 900,
    ) -> str:
        self.signed_paths.append(storage_path)
        return (
            f"https://signed.example/{storage_path}"
            f"?expires={expires_in}"
        )

    async def delete(self, *, storage_path: str) -> None:
        self.deleted.append(storage_path)
        self.uploaded.pop(storage_path, None)

async def cleanup_all_test_portfolio(session):
    await session.rollback()

    test_user_ids = select(UserAccount.user_id).where(
        UserAccount.platform == "telegram",
        UserAccount.platform_user_id.like("test-%"),
    )

    file_ids = select(FileStorageObject.id).where(
        FileStorageObject.owner_user_id.in_(test_user_ids),
        FileStorageObject.entity_type == "specialist_portfolio_item",
    )

    await session.execute(
        delete(SpecialistPortfolioItem).where(
            SpecialistPortfolioItem.file_id.in_(file_ids)
        )
    )

    await session.execute(
        delete(FileStorageObject).where(
            FileStorageObject.owner_user_id.in_(test_user_ids),
            FileStorageObject.entity_type == "specialist_portfolio_item",
        )
    )

    await session.commit()


@pytest.fixture(autouse=True)
async def cleanup_test_portfolio_records(db_session):
    await cleanup_all_test_portfolio(db_session)

    yield

    await cleanup_all_test_portfolio(db_session)

async def cleanup_portfolio_specialist(
    session,
    *,
    platform_user_id: str,
    specialist_id,
):
    await session.rollback()

    file_ids = list(
        (
            await session.execute(
                select(SpecialistPortfolioItem.file_id).where(
                    SpecialistPortfolioItem.specialist_id
                    == specialist_id
                )
            )
        )
        .scalars()
        .all()
    )

    await session.execute(
        delete(SpecialistPortfolioItem).where(
            SpecialistPortfolioItem.specialist_id == specialist_id
        )
    )

    if file_ids:
        await session.execute(
            delete(FileStorageObject).where(
                FileStorageObject.id.in_(file_ids)
            )
        )

    await session.commit()
    await cleanup_test_user(session, platform_user_id)


async def test_portfolio_upload_creates_private_pending_item(
    db_session,
):
    (
        platform_user_id,
        user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    specialist_id = specialist.id

    storage = FakePortfolioStorage()
    service = PortfolioService(
        PortfolioRepository(db_session),
        storage=storage,
    )

    try:
        item = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            filename="work.png",
            mime_type="image/png",
            content=b"\x89PNG\r\n\x1a\ntest image",
            title="Completed work",
            description="Portfolio test",
        )

        item_id = item.id
        file_id = item.file_id

        storage_object = await db_session.get(
            FileStorageObject,
            file_id,
        )

        assert item.status == "pending_moderation"
        assert item.file_url is None
        assert storage_object is not None
        assert storage_object.owner_user_id == user_id
        assert storage_object.entity_type == (
            "specialist_portfolio_item"
        )
        assert storage_object.entity_id == item_id
        assert storage_object.file_type == "photo"
        assert storage_object.mime_type == "image/png"
        assert storage_object.storage_provider == "supabase"
        assert storage_object.visibility_scope == "private"
        assert storage_object.public_url is None
        assert storage_object.retention_until is None
        assert storage_object.storage_path in storage.uploaded

        owner_items = await service.list_owner_items(
            tenant_id=tenant_id,
            owner_user_id=user_id,
        )
        assert len(owner_items) == 1
        assert owner_items[0].item.id == item_id
        assert owner_items[0].signed_url.startswith(
            "https://signed.example/"
        )

        public_items = await service.list_active_items(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
        )
        assert public_items == []

    finally:
        await cleanup_portfolio_specialist(
            db_session,
            platform_user_id=platform_user_id,
            specialist_id=specialist_id,
        )


async def test_portfolio_moderation_and_delete_lifecycle(
    db_session,
):
    (
        platform_user_id,
        user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    specialist_id = specialist.id

    (
        moderator_platform_user_id,
        moderator_user_id,
        moderator_tenant_id,
    ) = await create_admin_user(
        db_session,
        role="moderator",
    )

    assert moderator_tenant_id == tenant_id

    storage = FakePortfolioStorage()
    service = PortfolioService(
        PortfolioRepository(db_session),
        storage=storage,
    )

    try:
        item = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            filename="certificate.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.7 test certificate",
            title="Certificate",
        )

        item_id = item.id
        file_id = item.file_id

        pending_items = await service.list_pending_items(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
        )

        pending_item = next(
            (
                view
                for view in pending_items
                if view.item.id == item_id
            ),
            None,
        )

        assert pending_item is not None
        assert pending_item.signed_url is not None

        approved = await service.approve_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=item_id,
            reason="Portfolio content is valid",
        )
        assert approved.status == "active"

        approved_event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type == "portfolio_moderated",
                    EventLog.entity_id == item_id,
                )
            )
        ).scalar_one_or_none()

        assert approved_event is not None
        assert approved_event.payload["decision"] == "approved"
        assert (
            approved_event.payload["reason"]
            == "Portfolio content is valid"
        )
        assert (
            approved_event.payload["before_status"]
            == "pending_moderation"
        )
        assert approved_event.payload["after_status"] == "active"

        active_items = await service.list_active_items(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
        )

        active_item = next(
            (
                view
                for view in active_items
                if view.item.id == item_id
            ),
            None,
        )

        assert active_item is not None
        assert active_item.storage_object.file_type == "pdf"
        assert active_item.signed_url is not None

        item_to_reject = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            filename="rejected-certificate.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.7 rejected test certificate",
            title="Rejected certificate",
        )

        rejected_item_id = item_to_reject.id
        rejected_file_id = item_to_reject.file_id

        rejected = await service.reject_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=rejected_item_id,
            reason="Portfolio content requires rejection",
        )

        assert rejected.status == "rejected"

        rejected_event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type == "portfolio_moderated",
                    EventLog.entity_id == rejected_item_id,
                )
            )
        ).scalar_one_or_none()

        assert rejected_event is not None
        assert rejected_event.payload["decision"] == "rejected"
        assert (
            rejected_event.payload["reason"]
            == "Portfolio content requires rejection"
        )
        assert (
            rejected_event.payload["before_status"]
            == "pending_moderation"
        )
        assert rejected_event.payload["after_status"] == "rejected"

        rejected_items = await service.list_rejected_items(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
        )

        rejected_item = next(
            (
                view
                for view in rejected_items
                if view.item.id == rejected_item_id
            ),
            None,
        )

        assert rejected_item is not None
        assert rejected_item.item.status == "rejected"
        assert rejected_item.signed_url is not None

        rejected_storage_object = await db_session.get(
            FileStorageObject,
            rejected_file_id,
        )

        assert rejected_storage_object is not None
        assert rejected_storage_object.retention_until is not None
        assert rejected_storage_object.retention_until > (
            datetime.now(timezone.utc) + timedelta(days=89)
        )

        active_items = await service.list_active_items(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
        )

        assert all(
            view.item.id != rejected_item_id
            for view in active_items
        )

        storage_object = await db_session.get(
            FileStorageObject,
            file_id,
        )
        assert storage_object is not None


        deleted = await service.delete_owner_item(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            item_id=item_id,
        )
        assert deleted.status == "deleted"

        await db_session.refresh(storage_object)

        assert storage_object.retention_until is not None
        assert storage_object.retention_until < (
            datetime.now(timezone.utc) + timedelta(days=31)
        )

        owner_items = await service.list_owner_items(
            tenant_id=tenant_id,
            owner_user_id=user_id,
        )

        assert all(
            view.item.id != item_id
            for view in owner_items
        )

        assert storage_object.storage_path in storage.uploaded
        assert storage.deleted == []

    finally:
        await cleanup_portfolio_specialist(
            db_session,
            platform_user_id=platform_user_id,
            specialist_id=specialist_id,
        )
        await cleanup_user(
            db_session,
            moderator_platform_user_id,
        )

async def test_moderator_cannot_moderate_own_portfolio_item(
    db_session,
):
    (
        moderator_platform_user_id,
        moderator_user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    specialist_id = specialist.id

    from database.models import UserRoleMapping

    db_session.add(
        UserRoleMapping(
            user_id=moderator_user_id,
            tenant_id=tenant_id,
            role="moderator",
            status="active",
        )
    )
    await db_session.commit()

    storage = FakePortfolioStorage()
    service = PortfolioService(
        PortfolioRepository(db_session),
        storage=storage,
    )

    try:
        item = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=moderator_user_id,
            filename="own-item.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.7 own moderator item",
            title="Own moderator item",
        )

        item_id = item.id

        queue = await service.list_pending_items(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            page=0,
            page_size=5,
        )

        assert item_id not in {
            view.item.id for view in queue
        }

        with pytest.raises(
            PortfolioServiceError,
            match="own portfolio item",
        ):
            await service.approve_item(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                item_id=item_id,
                reason="Approve own item",
            )

        with pytest.raises(
            PortfolioServiceError,
            match="own portfolio item",
        ):
            await service.reject_item(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                item_id=item_id,
                reason="Reject own item",
            )

    finally:
        await cleanup_portfolio_specialist(
            db_session,
            platform_user_id=moderator_platform_user_id,
            specialist_id=specialist_id,
        )

async def test_portfolio_beta_limits(
    db_session,
):
    (
        platform_user_id,
        user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    specialist_id = specialist.id

    storage = FakePortfolioStorage()
    service = PortfolioService(
        PortfolioRepository(db_session),
        storage=storage,
    )

    try:
        for index in range(10):
            await service.upload_item(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                filename=f"photo-{index}.jpg",
                mime_type="image/jpeg",
                content=b"\xff\xd8\xfftest",
            )

        assert len(storage.uploaded) == 10

        with pytest.raises(
            PortfolioServiceError,
            match="photo limit",
        ):
            await service.upload_item(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                filename="photo-over-limit.jpg",
                mime_type="image/jpeg",
                content=b"\xff\xd8\xfftest",
            )

        assert len(storage.uploaded) == 10

        for index in range(10):
            await service.upload_item(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                filename=f"certificate-{index}.pdf",
                mime_type="application/pdf",
                content=b"%PDF-1.7 test",
            )

        assert len(storage.uploaded) == 20

        with pytest.raises(
            PortfolioServiceError,
            match="Portfolio item limit",
        ):
            await service.upload_item(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                filename="certificate-over-limit.pdf",
                mime_type="application/pdf",
                content=b"%PDF-1.7 test",
            )

        assert len(storage.uploaded) == 20

        counts = await PortfolioRepository(
            db_session
        ).get_portfolio_counts(
            specialist_id=specialist_id,
        )

        assert counts == {
            "total": 20,
            "photo": 10,
            "pdf": 10,
        }

    finally:
        await cleanup_portfolio_specialist(
            db_session,
            platform_user_id=platform_user_id,
            specialist_id=specialist_id,
        )


async def test_portfolio_cleanup_physically_deletes_due_file(
    db_session,
):
    (
        platform_user_id,
        user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    specialist_id = specialist.id

    storage = FakePortfolioStorage()
    repository = PortfolioRepository(db_session)
    service = PortfolioService(
        repository,
        storage=storage,
    )

    try:
        item = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            filename="old-photo.png",
            mime_type="image/png",
            content=b"\x89PNG\r\n\x1a\ntest",
        )

        item_id = item.id
        file_id = item.file_id

        storage_object = await db_session.get(
            FileStorageObject,
            file_id,
        )

        assert storage_object is not None
        storage_path = storage_object.storage_path

        await service.delete_owner_item(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            item_id=item_id,
        )

        assert storage_path in storage.uploaded
        assert storage.deleted == []

        storage_object.retention_until = datetime(
            2000,
            1,
            1,
            tzinfo=timezone.utc,
        )
        await db_session.commit()

        assert await service.cleanup_due_items(limit=1) == 1
        assert storage_path not in storage.uploaded
        assert storage_path in storage.deleted

        await db_session.refresh(storage_object)
        assert storage_object.retention_until is None

        due_rows = await repository.list_cleanup_due(
            now=datetime.now(timezone.utc),
            limit=500,
        )

        assert all(
            due_storage.id != file_id
            for _due_item, due_storage in due_rows
        )

    finally:
        await cleanup_portfolio_specialist(
            db_session,
            platform_user_id=platform_user_id,
            specialist_id=specialist_id,
        )
def test_public_portfolio_screen_matches_tz10_c19_contract():
    search_source = open("handlers/search.py", encoding="utf-8").read()
    moderation_source = open("services/moderation.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()
    repository_source = open("database/repositories/portfolio.py", encoding="utf-8").read()
    service_source = open("services/portfolio.py", encoding="utf-8").read()

    for fragment in [
        "render_public_portfolio",
        "public_portfolio_caption",
        "public_portfolio_keyboard",
        "portfolio_viewed",
        "search_portfolio_page:",
        "search_portfolio_report",
        "public_portfolio_item_ids",
        "pending_report_target_type=\"portfolio_item\"",
        "pending_report_target_id",
        "portfolio_open_button",
        "view.signed_url",
        "public_portfolio_empty",
        "search_result_back_to_card:",
    ]:
        assert fragment in search_source

    for fragment in [
        "public_portfolio_title",
        "public_portfolio_empty",
        "public_portfolio_report_btn",
        "portfolio_open_button",
        "prev_btn",
        "next_btn",
    ]:
        assert fragment in texts_source

    for fragment in [
        'SpecialistPortfolioItem.status == "active"',
        'FileStorageObject.visibility_scope == "private"',
        "list_active_items",
    ]:
        assert fragment in repository_source

    for fragment in [
        "list_active_items",
        "create_signed_url",
        "signed_url",
    ]:
        assert fragment in service_source

    assert '"portfolio_item"' in moderation_source

async def test_forbidden_portfolio_rejection_creates_risk_flag(
    db_session,
):
    (
        owner_platform_id,
        owner_user_id,
        tenant_id,
        specialist,
    ) = await create_pending_specialist(db_session)

    (
        moderator_platform_id,
        moderator_user_id,
        moderator_tenant_id,
    ) = await create_admin_user(
        db_session,
        role="moderator",
    )

    assert moderator_tenant_id == tenant_id

    specialist_id = specialist.id
    forbidden_item_id = None
    regular_item_id = None

    service = PortfolioService(
        PortfolioRepository(db_session),
        storage=FakePortfolioStorage(),
    )

    try:
        forbidden_item = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            filename="forbidden.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.7 forbidden content test",
            title="Forbidden content",
        )
        forbidden_item_id = forbidden_item.id

        rejected = await service.reject_forbidden_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=forbidden_item_id,
            reason="Contains prohibited material",
        )

        assert rejected.status == "rejected"

        risk_flag = (
            await db_session.execute(
                select(RiskFlag).where(
                    RiskFlag.tenant_id == tenant_id,
                    RiskFlag.entity_type == "portfolio_item",
                    RiskFlag.entity_id == forbidden_item_id,
                    RiskFlag.flag_code
                    == "forbidden_portfolio_content",
                    RiskFlag.status == "open",
                )
            )
        ).scalar_one_or_none()

        assert risk_flag is not None
        assert risk_flag.severity == "high"
        assert (
            risk_flag.details["reason"]
            == "Contains prohibited material"
        )

        risk_event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type
                    == "portfolio_risk_flagged",
                    EventLog.entity_id == forbidden_item_id,
                )
            )
        ).scalar_one_or_none()

        assert risk_event is not None
        assert risk_event.payload["severity"] == "high"

        moderation_event = (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event_type
                    == "portfolio_moderated",
                    EventLog.entity_id == forbidden_item_id,
                )
            )
        ).scalar_one_or_none()

        assert moderation_event is not None
        assert moderation_event.payload["decision"] == "rejected"
        assert moderation_event.payload["risk_flagged"] is True

        regular_item = await service.upload_item(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            filename="regular-reject.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.7 regular rejection test",
            title="Regular rejection",
        )
        regular_item_id = regular_item.id

        await service.reject_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=regular_item_id,
            reason="Caption needs correction",
        )

        regular_risk = (
            await db_session.execute(
                select(RiskFlag).where(
                    RiskFlag.tenant_id == tenant_id,
                    RiskFlag.entity_type == "portfolio_item",
                    RiskFlag.entity_id == regular_item_id,
                )
            )
        ).scalar_one_or_none()

        assert regular_risk is None

    finally:
        item_ids = [
            item_id
            for item_id in {
                forbidden_item_id,
                regular_item_id,
            }
            if item_id is not None
        ]

        if item_ids:
            await db_session.execute(
                delete(RiskFlag).where(
                    RiskFlag.entity_id.in_(item_ids)
                )
            )
            await db_session.execute(
                delete(EventLog).where(
                    EventLog.entity_id.in_(item_ids)
                )
            )
            await db_session.execute(
                delete(AdminAction).where(
                    AdminAction.target_id.in_(item_ids)
                )
            )
            await db_session.commit()

        await cleanup_portfolio_specialist(
            db_session,
            platform_user_id=owner_platform_id,
            specialist_id=specialist_id,
        )
        await cleanup_user(
            db_session,
            moderator_platform_id,
        )