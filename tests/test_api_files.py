import pytest
from sqlalchemy.dialects.postgresql import JSONB

from database.models import FileStorageObject


def test_file_storage_model_supports_release_lifecycle():
    table = FileStorageObject.__table__

    assert table.name == "file_storage_objects"

    required_columns = {
        "id",
        "tenant_id",
        "owner_user_id",
        "entity_type",
        "entity_id",
        "file_type",
        "mime_type",
        "size_bytes",
        "storage_provider",
        "storage_path",
        "visibility_scope",
        "status",
        "antivirus_status",
        "provider_metadata",
        "completed_at",
        "retention_until",
        "created_at",
        "updated_at",
    }
    assert required_columns <= set(table.c.keys())

    for column_name in (
        "tenant_id",
        "owner_user_id",
        "file_type",
        "mime_type",
        "size_bytes",
        "storage_provider",
        "storage_path",
        "visibility_scope",
        "status",
        "antivirus_status",
        "provider_metadata",
        "created_at",
        "updated_at",
    ):
        assert table.c[column_name].nullable is False

    assert isinstance(
        table.c.provider_metadata.type,
        JSONB,
    )
    assert table.c.completed_at.type.timezone
    assert table.c.retention_until.type.timezone
    assert table.c.created_at.type.timezone
    assert table.c.updated_at.type.timezone

    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }
    assert {
        "ck_file_storage_objects_status",
        "ck_file_storage_objects_antivirus_status",
        "ck_file_storage_objects_size",
        "ck_file_storage_objects_provider_metadata_object",
        "uq_file_storage_objects_provider_path",
    } <= constraint_names


def test_file_antivirus_statuses_match_final_release_contract():
    table = FileStorageObject.__table__

    constraint = next(
        item
        for item in table.constraints
        if item.name
        == "ck_file_storage_objects_antivirus_status"
    )
    sql = str(constraint.sqltext)

    required_statuses = {
        "pending",
        "not_scanned",
        "clean",
        "infected",
        "quarantined",
        "scan_failed",
    }
    for status in required_statuses:
        assert f"'{status}'" in sql

    assert "'error'" not in sql


def test_file_storage_provider_contract_is_provider_neutral():
    from services.file_storage import (
        FILE_SIGNED_URL_TTL_SECONDS,
        FileStorageObjectMetadata,
        FileStorageProvider,
    )

    class FakeStorageProvider:
        async def create_signed_upload_url(
            self,
            *,
            storage_path,
            mime_type,
            expires_in,
        ):
            return "https://storage.example/upload"

        async def create_signed_download_url(
            self,
            *,
            storage_path,
            expires_in,
        ):
            return "https://storage.example/download"

        async def inspect_object(
            self,
            *,
            storage_path,
        ):
            return FileStorageObjectMetadata(
                exists=True,
                size_bytes=1024,
                mime_type="application/pdf",
                provider_metadata={
                    "etag": "safe-provider-etag",
                },
            )

        async def delete(
            self,
            *,
            storage_path,
        ):
            return None

    provider = FakeStorageProvider()

    assert FILE_SIGNED_URL_TTL_SECONDS == 15 * 60
    assert isinstance(provider, FileStorageProvider)

    metadata = FileStorageObjectMetadata(
        exists=True,
        size_bytes=1024,
        mime_type="application/pdf",
        provider_metadata={"etag": "safe"},
    )
    assert metadata.exists is True
    assert metadata.size_bytes == 1024
    assert metadata.mime_type == "application/pdf"
    assert metadata.provider_metadata == {
        "etag": "safe",
    }


@pytest.mark.asyncio
async def test_existing_supabase_storage_supports_release_file_contract():
    from services.file_storage import (
        FileStorageProvider,
    )
    from services.portfolio_storage import (
        SupabaseFileStorage,
        SupabasePortfolioStorage,
    )

    class FakeResponse:
        def __init__(
            self,
            *,
            payload=None,
            headers=None,
            status_code=200,
        ):
            self._payload = payload or {}
            self.headers = headers or {}
            self.status_code = status_code

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def post(
            self,
            url,
            *,
            headers,
            timeout,
            json=None,
        ):
            self.calls.append(("POST", url, json))

            if "/object/upload/sign/" in url:
                return FakeResponse(
                    payload={
                        "signedURL": (
                            "/storage/v1/object/"
                            "upload/sign/files/"
                            "tenant/file.pdf"
                            "?token=upload-token"
                        ),
                    }
                )

            return FakeResponse(
                payload={
                    "signedURL": (
                        "/storage/v1/object/sign/"
                        "files/tenant/file.pdf"
                        "?token=download-token"
                    ),
                }
            )

        async def head(
            self,
            url,
            *,
            headers,
            timeout,
        ):
            self.calls.append(("HEAD", url, None))
            return FakeResponse(
                headers={
                    "content-length": "2048",
                    "content-type": "application/pdf",
                    "etag": '"safe-etag"',
                    "last-modified": (
                        "Thu, 10 Sep 2026 "
                        "12:00:00 GMT"
                    ),
                }
            )

    client = FakeClient()
    storage = SupabaseFileStorage(
        base_url="https://storage.example",
        service_role_key="private-service-key",
        bucket="files",
        client=client,
    )

    assert (
        SupabaseFileStorage
        is SupabasePortfolioStorage
    )
    assert isinstance(
        storage,
        FileStorageProvider,
    )

    upload_url = await (
        storage.create_signed_upload_url(
            storage_path="tenant/file.pdf",
            mime_type="application/pdf",
            expires_in=900,
        )
    )
    metadata = await storage.inspect_object(
        storage_path="tenant/file.pdf",
    )
    download_url = await (
        storage.create_signed_download_url(
            storage_path="tenant/file.pdf",
            expires_in=900,
        )
    )

    assert upload_url.startswith(
        "https://storage.example/"
    )
    assert download_url.startswith(
        "https://storage.example/"
    )
    assert metadata.exists is True
    assert metadata.size_bytes == 2048
    assert metadata.mime_type == "application/pdf"
    assert metadata.provider_metadata == {
        "etag": '"safe-etag"',
        "last_modified": (
            "Thu, 10 Sep 2026 "
            "12:00:00 GMT"
        ),
    }

    assert [
        method
        for method, _, _ in client.calls
    ] == ["POST", "HEAD", "POST"]


@pytest.mark.asyncio
async def test_file_repository_creates_scoped_pending_upload_without_commit():
    from uuid import uuid4

    from database.models import (
        FileStorageObject,
    )
    from database.repositories.files import (
        FileRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    entity_id = uuid4()

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush_count = 0

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flush_count += 1

    session = FakeSession()
    repository = FileRepository(session)

    result = await repository.create_pending_upload(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        entity_type="document",
        entity_id=entity_id,
        file_type="document",
        mime_type="application/pdf",
        size_bytes=2048,
        storage_provider="supabase",
        storage_path=(
            f"{tenant_id}/{user_id}/"
            "private-file.pdf"
        ),
    )

    assert isinstance(result, FileStorageObject)
    assert result.tenant_id == tenant_id
    assert result.owner_user_id == user_id
    assert result.entity_type == "document"
    assert result.entity_id == entity_id
    assert result.file_type == "document"
    assert result.mime_type == "application/pdf"
    assert result.size_bytes == 2048
    assert result.storage_provider == "supabase"
    assert result.status == "pending_upload"
    assert result.antivirus_status == "pending"
    assert result.provider_metadata == {}
    assert result.completed_at is None
    assert result.public_url is None

    assert session.added == [result]
    assert session.flush_count == 1


@pytest.mark.parametrize(
    (
        "object_type",
        "filename",
        "mime_type",
        "size_bytes",
        "expected_file_type",
    ),
    [
        (
            "portfolio",
            "work.jpg",
            "image/jpeg",
            10 * 1024 * 1024,
            "image",
        ),
        (
            "message",
            "photo.png",
            "image/png",
            1024,
            "image",
        ),
        (
            "portfolio",
            "preview.webp",
            "image/webp",
            2048,
            "image",
        ),
        (
            "document",
            "contract.pdf",
            "application/pdf",
            20 * 1024 * 1024,
            "pdf",
        ),
        (
            "cv",
            "resume.docx",
            (
                "application/vnd.openxmlformats-"
                "officedocument.wordprocessingml."
                "document"
            ),
            20 * 1024 * 1024,
            "docx",
        ),
    ],
)
def test_file_upload_validation_matches_release_mime_and_size_contract(
    object_type,
    filename,
    mime_type,
    size_bytes,
    expected_file_type,
):
    from services.files import (
        validate_file_upload,
    )

    result = validate_file_upload(
        object_type=object_type,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )

    assert result.object_type == object_type
    assert result.filename == filename
    assert result.mime_type == mime_type
    assert result.size_bytes == size_bytes
    assert result.file_type == expected_file_type


@pytest.mark.parametrize(
    (
        "object_type",
        "filename",
        "mime_type",
        "size_bytes",
    ),
    [
        (
            "portfolio",
            "vector.svg",
            "image/svg+xml",
            1024,
        ),
        (
            "message",
            "page.html",
            "text/html",
            1024,
        ),
        (
            "document",
            "program.exe",
            "application/octet-stream",
            1024,
        ),
        (
            "portfolio",
            "large.jpg",
            "image/jpeg",
            10 * 1024 * 1024 + 1,
        ),
        (
            "document",
            "large.pdf",
            "application/pdf",
            20 * 1024 * 1024 + 1,
        ),
        (
            "message",
            "attachment.docx",
            (
                "application/vnd.openxmlformats-"
                "officedocument.wordprocessingml."
                "document"
            ),
            1024,
        ),
        (
            "portfolio",
            "wrong.png",
            "image/jpeg",
            1024,
        ),
        (
            "review",
            "review.jpg",
            "image/jpeg",
            1024,
        ),
    ],
)
def test_file_upload_validation_denies_unsupported_release_files(
    object_type,
    filename,
    mime_type,
    size_bytes,
):
    from services.files import (
        FileUploadValidationError,
        validate_file_upload,
    )

    with pytest.raises(
        FileUploadValidationError
    ):
        validate_file_upload(
            object_type=object_type,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )


@pytest.mark.asyncio
async def test_file_service_creates_scoped_signed_upload_request_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.files import (
        FileService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    entity_id = uuid4()
    file_id = uuid4()
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def create_pending_upload(
            self,
            **kwargs,
        ):
            calls.append(("reserve", kwargs))
            return SimpleNamespace(
                id=file_id,
                tenant_id=tenant_id,
                owner_user_id=user_id,
                entity_type="document",
                entity_id=entity_id,
                file_type="pdf",
                mime_type="application/pdf",
                size_bytes=2048,
                storage_provider=(
                    kwargs["storage_provider"]
                ),
                storage_path=kwargs[
                    "storage_path"
                ],
                status="pending_upload",
            )

    class FakeStorage:
        async def create_signed_upload_url(
            self,
            **kwargs,
        ):
            calls.append(("sign", kwargs))
            return (
                "https://storage.example/"
                "signed-upload"
            )

    service = FileService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        storage_provider_name="supabase",
    )

    result = await service.create_upload_request(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        object_type="document",
        entity_id=entity_id,
        filename="private-contract.pdf",
        mime_type="application/pdf",
        size_bytes=2048,
    )

    assert [
        name
        for name, _ in calls
    ] == [
        "reserve",
        "sign",
        "commit",
    ]

    reserve_call = calls[0][1]
    assert reserve_call["tenant_id"] == tenant_id
    assert (
        reserve_call["owner_user_id"]
        == user_id
    )
    assert reserve_call["entity_type"] == (
        "document"
    )
    assert reserve_call["entity_id"] == entity_id
    assert reserve_call["file_type"] == "pdf"
    assert reserve_call["mime_type"] == (
        "application/pdf"
    )
    assert reserve_call["size_bytes"] == 2048
    assert reserve_call[
        "storage_provider"
    ] == "supabase"

    storage_path = reserve_call["storage_path"]
    assert storage_path.startswith(
        f"{tenant_id}/{user_id}/"
    )
    assert storage_path.endswith(".pdf")
    assert "private-contract" not in storage_path

    sign_call = calls[1][1]
    assert sign_call == {
        "storage_path": storage_path,
        "mime_type": "application/pdf",
        "expires_in": 15 * 60,
    }

    assert result.id == file_id
    assert result.upload_url == (
        "https://storage.example/"
        "signed-upload"
    )
    assert result.expires_in == 15 * 60
    assert result.status == "pending_upload"
    assert result.mime_type == "application/pdf"
    assert result.size_bytes == 2048


@pytest.mark.asyncio
async def test_file_repository_locks_pending_upload_in_full_actor_scope():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.files import (
        FileRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    expected = object()

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = FileRepository(session)

    result = await (
        repository.get_pending_upload_for_update(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            file_id=file_id,
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert (
        "file_storage_objects.tenant_id = "
        f"'{tenant_id}'"
    ) in sql
    assert (
        "file_storage_objects.owner_user_id = "
        f"'{user_id}'"
    ) in sql
    assert (
        "file_storage_objects.id = "
        f"'{file_id}'"
    ) in sql
    assert (
        "file_storage_objects.status = "
        "'pending_upload'"
    ) in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_file_service_completes_verified_storage_object_atomically():
    from datetime import UTC
    from types import SimpleNamespace
    from uuid import uuid4

    from services.file_storage import (
        FileStorageObjectMetadata,
    )
    from services.files import FileService

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    calls = []

    storage_object = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        owner_user_id=user_id,
        storage_path=(
            f"{tenant_id}/{user_id}/file.pdf"
        ),
        mime_type="application/pdf",
        size_bytes=2048,
        status="pending_upload",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_pending_upload_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return storage_object

        async def complete_upload(
            self,
            **kwargs,
        ):
            calls.append(("complete", kwargs))
            storage_object.status = "ready"
            storage_object.antivirus_status = (
                "not_scanned"
            )
            storage_object.provider_metadata = (
                kwargs["provider_metadata"]
            )
            storage_object.completed_at = (
                kwargs["completed_at"]
            )
            return storage_object

    class FakeStorage:
        async def inspect_object(self, **kwargs):
            calls.append(("inspect", kwargs))
            return FileStorageObjectMetadata(
                exists=True,
                size_bytes=2048,
                mime_type="application/pdf",
                provider_metadata={
                    "etag": '"verified-etag"',
                },
            )

    service = FileService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        storage_provider_name="supabase",
    )

    result = await service.complete_upload(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        file_id=file_id,
    )

    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "inspect",
        "complete",
        "commit",
    ]
    assert calls[0][1] == {
        "tenant_id": tenant_id,
        "owner_user_id": user_id,
        "file_id": file_id,
    }
    assert calls[1][1] == {
        "storage_path": (
            storage_object.storage_path
        ),
    }

    complete_call = calls[2][1]
    assert (
        complete_call["storage_object"]
        is storage_object
    )
    assert complete_call[
        "provider_metadata"
    ] == {
        "etag": '"verified-etag"',
    }
    assert (
        complete_call["completed_at"].tzinfo
        is UTC
    )

    assert result.id == file_id
    assert result.status == "ready"
    assert result.antivirus_status == (
        "not_scanned"
    )
    assert result.mime_type == (
        "application/pdf"
    )
    assert result.size_bytes == 2048
    assert result.completed_at.tzinfo is UTC
    assert not hasattr(result, "storage_path")
    assert not hasattr(result, "provider_metadata")


@pytest.mark.parametrize(
    "actual_metadata",
    [
        {
            "exists": False,
            "size_bytes": None,
            "mime_type": None,
            "provider_metadata": {},
        },
        {
            "exists": True,
            "size_bytes": 2049,
            "mime_type": "application/pdf",
            "provider_metadata": {
                "etag": '"wrong-size"',
            },
        },
        {
            "exists": True,
            "size_bytes": 2048,
            "mime_type": "text/html",
            "provider_metadata": {
                "etag": '"wrong-mime"',
            },
        },
        {
            "exists": True,
            "size_bytes": 2048,
            "mime_type": "application/pdf",
            "provider_metadata": "invalid",
        },
    ],
)
@pytest.mark.asyncio
async def test_file_completion_rejects_unverified_provider_object(
    actual_metadata,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.file_storage import (
        FileStorageObjectMetadata,
    )
    from services.files import (
        FileService,
        FileUploadVerificationError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    calls = []

    storage_object = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        owner_user_id=user_id,
        storage_path="private/object.pdf",
        mime_type="application/pdf",
        size_bytes=2048,
        status="pending_upload",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_pending_upload_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return storage_object

        async def complete_upload(
            self,
            **kwargs,
        ):
            calls.append(("complete", kwargs))
            return storage_object

    class FakeStorage:
        async def inspect_object(self, **kwargs):
            calls.append(("inspect", kwargs))
            return FileStorageObjectMetadata(
                **actual_metadata
            )

    service = FileService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        storage_provider_name="supabase",
    )

    with pytest.raises(
        FileUploadVerificationError
    ):
        await service.complete_upload(
            tenant_id=tenant_id,
            owner_user_id=user_id,
            file_id=file_id,
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "inspect",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_file_repository_gets_downloadable_object_in_full_owner_scope():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.files import (
        FileRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    expected = object()

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = FileRepository(session)

    result = await repository.get_downloadable_file(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        file_id=file_id,
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert (
        "file_storage_objects.tenant_id = "
        f"'{tenant_id}'"
    ) in sql
    assert (
        "file_storage_objects.owner_user_id = "
        f"'{user_id}'"
    ) in sql
    assert (
        "file_storage_objects.id = "
        f"'{file_id}'"
    ) in sql
    assert (
        "file_storage_objects.status = 'ready'"
    ) in sql
    assert (
        "file_storage_objects.antivirus_status "
        "IN ('not_scanned', 'clean')"
    ) in sql


@pytest.mark.asyncio
async def test_file_service_creates_safe_scoped_download_url():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.files import FileService

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    calls = []

    storage_object = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        owner_user_id=user_id,
        storage_path="private/opaque-file.pdf",
        mime_type="application/pdf",
        size_bytes=4096,
        status="ready",
        provider_metadata={
            "etag": "must-not-leak",
        },
        public_url=(
            "https://storage.example/permanent"
        ),
    )

    class FakeRepository:
        session = object()

        async def get_downloadable_file(
            self,
            **kwargs,
        ):
            calls.append(("lookup", kwargs))
            return storage_object

    class FakeStorage:
        async def create_signed_download_url(
            self,
            **kwargs,
        ):
            calls.append(("sign", kwargs))
            return (
                "https://storage.example/"
                "signed-download"
            )

    service = FileService(
        repository=FakeRepository(),
        storage=FakeStorage(),
        storage_provider_name="supabase",
    )

    result = await service.create_download_url(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        file_id=file_id,
    )

    assert calls == [
        (
            "lookup",
            {
                "tenant_id": tenant_id,
                "owner_user_id": user_id,
                "file_id": file_id,
            },
        ),
        (
            "sign",
            {
                "storage_path": (
                    storage_object.storage_path
                ),
                "expires_in": 15 * 60,
            },
        ),
    ]

    assert result.id == file_id
    assert result.download_url == (
        "https://storage.example/"
        "signed-download"
    )
    assert result.expires_in == 15 * 60
    assert result.mime_type == (
        "application/pdf"
    )
    assert result.size_bytes == 4096
    assert not hasattr(result, "storage_path")
    assert not hasattr(
        result,
        "provider_metadata",
    )
    assert not hasattr(result, "public_url")


@pytest.mark.asyncio
async def test_file_repository_locks_orphan_uploads_before_cleanup():
    from datetime import UTC, datetime

    from sqlalchemy.dialects import postgresql

    from database.repositories.files import (
        FileRepository,
    )

    cutoff = datetime(
        2026,
        9,
        10,
        12,
        0,
        tzinfo=UTC,
    )
    expected = [object(), object()]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = FileRepository(session)

    result = await (
        repository.list_orphan_uploads_for_update(
            cutoff=cutoff,
            limit=50,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert (
        "file_storage_objects.status = "
        "'pending_upload'"
    ) in sql
    assert (
        "file_storage_objects.created_at <= "
        "'2026-09-10 12:00:00+00:00'"
    ) in sql
    assert (
        "ORDER BY "
        "file_storage_objects.created_at ASC"
    ) in sql
    assert "LIMIT 50" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_file_orphan_cleanup_deletes_storage_and_metadata_after_24_hours():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.files import (
        FileOrphanCleanupService,
    )

    now = datetime(
        2026,
        9,
        11,
        12,
        0,
        tzinfo=UTC,
    )
    objects = [
        SimpleNamespace(
            id=uuid4(),
            storage_path="tenant/user/one.pdf",
        ),
        SimpleNamespace(
            id=uuid4(),
            storage_path="tenant/user/two.jpg",
        ),
    ]
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def list_orphan_uploads_for_update(
            self,
            **kwargs,
        ):
            calls.append(("select", kwargs))
            return objects

        async def delete_storage_record(
            self,
            **kwargs,
        ):
            calls.append(
                ("metadata_delete", kwargs)
            )

    class FakeStorage:
        async def delete(self, **kwargs):
            calls.append(
                ("storage_delete", kwargs)
            )

    service = FileOrphanCleanupService(
        repository=FakeRepository(),
        storage=FakeStorage(),
    )

    deleted = await service.cleanup(
        now=now,
        limit=50,
    )

    assert deleted == 2
    assert calls == [
        (
            "select",
            {
                "cutoff": (
                    now - timedelta(hours=24)
                ),
                "limit": 50,
            },
        ),
        (
            "storage_delete",
            {
                "storage_path": (
                    objects[0].storage_path
                ),
            },
        ),
        (
            "metadata_delete",
            {
                "storage_object": objects[0],
            },
        ),
        (
            "storage_delete",
            {
                "storage_path": (
                    objects[1].storage_path
                ),
            },
        ),
        (
            "metadata_delete",
            {
                "storage_object": objects[1],
            },
        ),
        ("commit", {}),
    ]


def test_existing_storage_cleanup_job_runs_file_orphan_retention():
    from pathlib import Path

    script = Path(
        "scripts/cleanup_portfolio_storage.py"
    ).read_text(encoding="utf-8-sig")

    service = Path(
        "deploy/systemd/"
        "sghr-portfolio-cleanup.service"
    ).read_text(encoding="utf-8-sig")

    timer = Path(
        "deploy/systemd/"
        "sghr-portfolio-cleanup.timer"
    ).read_text(encoding="utf-8-sig")

    assert "FileRepository" in script
    assert "FileOrphanCleanupService" in script
    assert "SupabaseFileStorage" in script
    assert "orphan_cleanup.cleanup(" in script
    assert "orphan_deleted_count" in script

    assert (
        "scripts/cleanup_portfolio_storage.py"
        in service
    )
    assert "Type=oneshot" in service
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer


def test_file_service_dependency_uses_server_configured_storage_provider(
    monkeypatch,
):
    from api.dependencies import (
        get_file_service,
    )
    from database.repositories.files import (
        FileRepository,
    )
    from services.files import FileService
    from services.portfolio_storage import (
        SupabaseFileStorage,
    )

    monkeypatch.setenv(
        "FILE_STORAGE_PROVIDER",
        "supabase",
    )
    monkeypatch.setenv(
        "SUPABASE_URL",
        "https://storage.example",
    )
    monkeypatch.setenv(
        "SUPABASE_SERVICE_ROLE_KEY",
        "private-service-key",
    )
    monkeypatch.setenv(
        "SUPABASE_STORAGE_BUCKET",
        "private-files",
    )

    session = object()
    service = get_file_service(
        session=session,
    )

    assert isinstance(service, FileService)
    assert isinstance(
        service.repository,
        FileRepository,
    )
    assert service.repository.session is session
    assert isinstance(
        service.storage,
        SupabaseFileStorage,
    )
    assert service.storage_provider_name == (
        "supabase"
    )
    assert service.storage.bucket == (
        "private-files"
    )


def test_file_service_factory_rejects_unknown_storage_provider(
    monkeypatch,
):
    from services.files import (
        build_file_service,
    )

    monkeypatch.setenv(
        "FILE_STORAGE_PROVIDER",
        "unknown-provider",
    )

    with pytest.raises(ValueError):
        build_file_service(object())



@pytest.mark.asyncio
async def test_file_upload_request_endpoint_uses_current_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_file_service
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    entity_id = uuid4()
    file_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeFileService:
        async def create_upload_request(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=file_id,
                upload_url=(
                    "https://storage.example.com/"
                    "signed-upload"
                ),
                expires_in=900,
                status="pending_upload",
                mime_type="application/pdf",
                size_bytes=2048,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/files/upload-request",
            headers={
                "X-Request-ID": (
                    "file-upload-request"
                ),
            },
            json={
                "object_type": "document",
                "entity_id": str(entity_id),
                "filename": "contract.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 2048,
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "tenant_id": tenant_id,
            "owner_user_id": user_id,
            "object_type": "document",
            "entity_id": entity_id,
            "filename": "contract.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
        }
    ]
    assert response.json() == {
        "data": {
            "id": str(file_id),
            "upload_url": (
                "https://storage.example.com/"
                "signed-upload"
            ),
            "expires_in": 900,
            "status": "pending_upload",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
        },
        "meta": {},
        "request_id": "file-upload-request",
    }

    serialized = response.text
    assert "storage_path" not in serialized
    assert "storage_provider" not in serialized
    assert "provider_metadata" not in serialized



@pytest.mark.asyncio
async def test_file_complete_endpoint_uses_current_actor_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_file_service
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    completed_at = datetime(
        2026,
        9,
        11,
        12,
        30,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeFileService:
        async def complete_upload(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=file_id,
                status="ready",
                antivirus_status="not_scanned",
                mime_type="application/pdf",
                size_bytes=2048,
                completed_at=completed_at,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/files/{file_id}/complete",
            headers={
                "X-Request-ID": "file-complete",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "owner_user_id": user_id,
            "file_id": file_id,
        }
    ]
    assert response.json() == {
        "data": {
            "id": str(file_id),
            "status": "ready",
            "antivirus_status": "not_scanned",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
            "completed_at": (
                "2026-09-11T12:30:00Z"
            ),
        },
        "meta": {},
        "request_id": "file-complete",
    }

    serialized = response.text
    assert "storage_path" not in serialized
    assert "storage_provider" not in serialized
    assert "provider_metadata" not in serialized



@pytest.mark.asyncio
async def test_file_complete_hides_missing_or_foreign_file():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_file_service
    from database.repositories.files import (
        FileStorageObjectNotFoundError,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeFileService:
        async def complete_upload(
            self,
            **kwargs,
        ):
            raise FileStorageObjectNotFoundError(
                "Private tenant and storage details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/files/{file_id}/complete",
            headers={
                "X-Request-ID": (
                    "file-complete-not-found"
                ),
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "file_not_found",
            "message": "File was not found.",
            "request_id": (
                "file-complete-not-found"
            ),
        }
    }
    assert "Private tenant" not in response.text
    assert "storage details" not in response.text



@pytest.mark.asyncio
async def test_file_download_url_endpoint_uses_current_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_file_service
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeFileService:
        async def create_download_url(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=file_id,
                download_url=(
                    "https://storage.example.com/"
                    "signed-download"
                ),
                expires_in=900,
                mime_type="application/pdf",
                size_bytes=2048,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/v1/files/{file_id}/download-url",
            headers={
                "X-Request-ID": (
                    "file-download-url"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "owner_user_id": user_id,
            "file_id": file_id,
        }
    ]
    assert response.json() == {
        "data": {
            "id": str(file_id),
            "download_url": (
                "https://storage.example.com/"
                "signed-download"
            ),
            "expires_in": 900,
            "mime_type": "application/pdf",
            "size_bytes": 2048,
        },
        "meta": {},
        "request_id": "file-download-url",
    }

    serialized = response.text
    assert "storage_path" not in serialized
    assert "storage_provider" not in serialized
    assert "provider_metadata" not in serialized
    assert "public_url" not in serialized



@pytest.mark.asyncio
async def test_file_download_url_hides_missing_foreign_or_unavailable_file():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_file_service
    from database.repositories.files import (
        FileStorageObjectNotFoundError,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeFileService:
        async def create_download_url(
            self,
            **kwargs,
        ):
            raise FileStorageObjectNotFoundError(
                "Private storage state and owner details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/v1/files/{file_id}/download-url",
            headers={
                "X-Request-ID": (
                    "file-download-not-found"
                ),
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "file_not_found",
            "message": "File was not found.",
            "request_id": (
                "file-download-not-found"
            ),
        }
    }
    assert "Private storage" not in response.text
    assert "owner details" not in response.text



@pytest.mark.asyncio
async def test_file_complete_sanitizes_storage_verification_failure():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_file_service
    from services.api_identity import ApiActorContext
    from services.files import (
        FileUploadVerificationError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeFileService:
        async def complete_upload(
            self,
            **kwargs,
        ):
            raise FileUploadVerificationError(
                "Private provider response and storage path."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/files/{file_id}/complete",
            headers={
                "X-Request-ID": (
                    "file-complete-invalid"
                ),
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "file_verification_failed",
            "message": (
                "Uploaded file could not be verified."
            ),
            "request_id": (
                "file-complete-invalid"
            ),
        }
    }
    assert "Private provider" not in response.text
    assert "storage path" not in response.text



@pytest.mark.asyncio
async def test_files_routes_enforce_authenticated_user_rate_limit():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_request_rate_limit_service,
        get_file_service,
    )
    from services.api_identity import ApiActorContext
    from services.api_rate_limits import (
        ApiRequestRateLimitExceededError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    file_id = uuid4()
    file_service_calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeRateLimitService:
        async def ensure_authenticated_user_allowed(
            self,
            **kwargs,
        ):
            raise ApiRequestRateLimitExceededError(
                "Private Redis details."
            )

    class FakeFileService:
        async def create_download_url(
            self,
            **kwargs,
        ):
            file_service_calls.append(kwargs)
            return SimpleNamespace(
                id=file_id,
                download_url="https://storage.example/file",
                expires_in=900,
                mime_type="application/pdf",
                size_bytes=2048,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimitService()
    application.dependency_overrides[
        get_file_service
    ] = lambda: FakeFileService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/v1/files/{file_id}/download-url",
            headers={
                "X-Request-ID": (
                    "files-rate-limit"
                ),
            },
        )

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "RATE_LIMIT_EXCEEDED",
            "message": "Rate limit exceeded.",
            "request_id": "files-rate-limit",
        }
    }
    assert file_service_calls == []
    assert "Private Redis" not in response.text



@pytest.fixture(autouse=True)
def file_api_rate_limit_sandbox(
    monkeypatch,
):
    from api.dependencies import (
        get_api_request_rate_limit_service,
    )

    monkeypatch.setenv(
        "API_ENVIRONMENT",
        "sandbox",
    )
    monkeypatch.setenv(
        "REDIS_URL",
        "",
    )
    get_api_request_rate_limit_service.cache_clear()

    yield

    get_api_request_rate_limit_service.cache_clear()

@pytest.mark.asyncio
async def test_file_access_denies_non_owner_without_parent_relation():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.file_access import (
        FileAccessDeniedError,
        FileAccessService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    owner_user_id = uuid4()
    portfolio_id = uuid4()
    calls = []

    storage_object = SimpleNamespace(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        entity_type="portfolio",
        entity_id=portfolio_id,
    )

    class FakeParentAccess:
        async def can_actor_read_parent(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return False

    service = FileAccessService(
        parent_access=FakeParentAccess(),
    )

    with pytest.raises(FileAccessDeniedError):
        await service.ensure_user_can_read(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            storage_object=storage_object,
        )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "entity_type": "portfolio",
            "entity_id": portfolio_id,
        },
    ]

