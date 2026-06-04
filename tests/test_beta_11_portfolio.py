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
    create_admin_user,
    create_pending_specialist,
)
from database.models import (
    FileStorageObject,
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
        )
        assert approved.status == "active"

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

        rejected = await service.reject_item(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
            item_id=item_id,
        )
        assert rejected.status == "rejected"

        rejected_items = await service.list_rejected_items(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
        )

        rejected_item = next(
            (
                view
                for view in rejected_items
                if view.item.id == item_id
            ),
            None,
        )

        assert rejected_item is not None
        assert rejected_item.item.status == "rejected"
        assert rejected_item.signed_url is not None

        storage_object = await db_session.get(
            FileStorageObject,
            file_id,
        )

        assert storage_object is not None
        assert storage_object.retention_until is not None
        assert storage_object.retention_until > (
            datetime.now(timezone.utc) + timedelta(days=89)
        )

        active_items = await service.list_active_items(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
        )

        assert all(
            view.item.id != item_id
            for view in active_items
        )

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
        await cleanup_test_user(
            db_session,
            moderator_platform_user_id,
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