import pytest

from services.api_idempotency import (
    hash_request_payload,
)


def test_idempotency_request_hash_is_canonical():
    first = hash_request_payload(
        {
            "dialog_id": "dialog-1",
            "description": "Consultation",
            "details": {
                "currency": "EUR",
                "amount": 125.5,
            },
        }
    )
    reordered = hash_request_payload(
        {
            "details": {
                "amount": 125.5,
                "currency": "EUR",
            },
            "description": "Consultation",
            "dialog_id": "dialog-1",
        }
    )
    changed = hash_request_payload(
        {
            "dialog_id": "dialog-1",
            "description": "Different consultation",
            "details": {
                "currency": "EUR",
                "amount": 125.5,
            },
        }
    )

    assert first == reordered
    assert first != changed
    assert len(first) == 64



@pytest.mark.asyncio
async def test_idempotency_service_rejects_key_reuse_with_different_body():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_idempotency import (
        ApiIdempotencyKeyReusedError,
        ApiIdempotencyService,
        hash_request_payload,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    raw_key = "order-create-private-key"
    calls = []

    class FakeRepository:
        async def get_active_record_for_update(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                request_hash=hash_request_payload(
                    {
                        "description": "First request",
                    }
                ),
                status="completed",
            )

    service = ApiIdempotencyService(
        repository=FakeRepository(),
    )

    with pytest.raises(
        ApiIdempotencyKeyReusedError
    ):
        await service.reserve(
            tenant_id=tenant_id,
            principal_type="user",
            principal_id=user_id,
            operation="service_order.create",
            idempotency_key=raw_key,
            payload={
                "description": "Changed request",
            },
        )

    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["principal_type"] == "user"
    assert calls[0]["principal_id"] == user_id
    assert calls[0]["operation"] == (
        "service_order.create"
    )
    assert calls[0]["key_hash"] != raw_key
    assert len(calls[0]["key_hash"]) == 64



def test_idempotency_model_is_scoped_hash_only_and_expires():
    from sqlalchemy import UniqueConstraint

    from database.models import (
        ApiIdempotencyRecord,
    )

    table = ApiIdempotencyRecord.__table__
    columns = table.columns

    assert set(columns.keys()) == {
        "id",
        "tenant_id",
        "principal_type",
        "principal_id",
        "operation",
        "key_hash",
        "request_hash",
        "status",
        "response_status",
        "response_ciphertext",
        "created_at",
        "completed_at",
        "expires_at",
    }

    assert "idempotency_key" not in columns
    assert "request_body" not in columns
    assert "response_body" not in columns

    assert columns["tenant_id"].nullable is False
    assert columns["principal_id"].nullable is False
    assert columns["key_hash"].nullable is False
    assert columns["request_hash"].nullable is False
    assert columns["response_ciphertext"].nullable is True
    assert columns["expires_at"].nullable is False
    assert columns["expires_at"].type.timezone is True

    unique_columns = {
        frozenset(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert frozenset(
        {
            "tenant_id",
            "principal_type",
            "principal_id",
            "operation",
            "key_hash",
        }
    ) in unique_columns



@pytest.mark.asyncio
async def test_idempotency_repository_locks_active_record_in_full_scope():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.api_idempotency import (
        ApiIdempotencyRepository,
    )

    tenant_id = uuid4()
    principal_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

    repository = ApiIdempotencyRepository(
        FakeSession()
    )
    result = await (
        repository.get_active_record_for_update(
            tenant_id=tenant_id,
            principal_type="user",
            principal_id=principal_id,
            operation="service_order.create",
            key_hash="a" * 64,
            now=datetime(
                2026,
                9,
                8,
                12,
                0,
                tzinfo=UTC,
            ),
        )
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FOR UPDATE" in sql
    assert str(tenant_id) in sql
    assert str(principal_id) in sql
    assert "principal_type" in sql
    assert "service_order.create" in sql
    assert "key_hash" in sql
    assert "expires_at" in sql



@pytest.mark.asyncio
async def test_idempotency_repository_creates_processing_record_without_commit():
    from datetime import UTC, datetime
    from uuid import uuid4

    from database.repositories.api_idempotency import (
        ApiIdempotencyRepository,
    )

    tenant_id = uuid4()
    principal_id = uuid4()
    expires_at = datetime(
        2026,
        9,
        9,
        12,
        0,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush_count = 0
            self.commit_count = 0

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flush_count += 1

        async def commit(self):
            self.commit_count += 1

    session = FakeSession()
    repository = ApiIdempotencyRepository(session)

    record = await repository.create_record(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=principal_id,
        operation="service_order.create",
        key_hash="a" * 64,
        request_hash="b" * 64,
        expires_at=expires_at,
    )

    assert session.added == [record]
    assert session.flush_count == 1
    assert session.commit_count == 0

    assert record.tenant_id == tenant_id
    assert record.principal_type == "user"
    assert record.principal_id == principal_id
    assert record.operation == "service_order.create"
    assert record.key_hash == "a" * 64
    assert record.request_hash == "b" * 64
    assert record.status == "processing"
    assert record.expires_at == expires_at

    assert not hasattr(record, "idempotency_key")
    assert not hasattr(record, "request_body")
    assert not hasattr(record, "response_body")



def test_idempotency_response_codec_encrypts_stored_response():
    from cryptography.fernet import Fernet

    from services.api_idempotency import (
        ApiIdempotencyResponseCodec,
    )

    codec = ApiIdempotencyResponseCodec(
        encryption_key=(
            Fernet.generate_key().decode("ascii")
        )
    )
    payload = {
        "data": {
            "api_key": "sghr_private_plaintext_key",
        },
        "meta": {},
    }

    ciphertext = codec.encrypt(payload)

    assert isinstance(ciphertext, bytes)
    assert (
        b"sghr_private_plaintext_key"
        not in ciphertext
    )
    assert codec.decrypt(ciphertext) == payload



@pytest.mark.asyncio
async def test_idempotency_repository_completes_record_without_commit():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from database.repositories.api_idempotency import (
        ApiIdempotencyRepository,
    )

    completed_at = datetime(
        2026,
        9,
        8,
        14,
        0,
        tzinfo=UTC,
    )
    record = SimpleNamespace(
        status="processing",
        response_status=None,
        response_ciphertext=None,
        completed_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flush_count = 0
            self.commit_count = 0

        async def flush(self):
            self.flush_count += 1

        async def commit(self):
            self.commit_count += 1

    session = FakeSession()
    repository = ApiIdempotencyRepository(session)

    result = await repository.complete_record(
        record=record,
        response_status=201,
        response_ciphertext=b"encrypted-response",
        completed_at=completed_at,
    )

    assert result is record
    assert record.status == "completed"
    assert record.response_status == 201
    assert record.response_ciphertext == (
        b"encrypted-response"
    )
    assert record.completed_at == completed_at
    assert record.completed_at.tzinfo is UTC

    assert session.flush_count == 1
    assert session.commit_count == 0



@pytest.mark.asyncio
async def test_idempotency_service_replays_completed_response_for_same_request():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_idempotency import (
        ApiIdempotencyService,
        hash_request_payload,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    payload = {
        "dialog_id": "dialog-1",
        "description": "Consultation",
    }
    stored_response = {
        "data": {
            "id": "order-1",
            "status": "draft",
        },
        "meta": {},
    }
    create_calls = []
    decrypt_calls = []

    class FakeRepository:
        async def get_active_record_for_update(
            self,
            **kwargs,
        ):
            return SimpleNamespace(
                request_hash=(
                    hash_request_payload(payload)
                ),
                status="completed",
                response_status=201,
                response_ciphertext=b"encrypted",
            )

        async def create_record(self, **kwargs):
            create_calls.append(kwargs)
            raise AssertionError(
                "Replay must not create a record."
            )

    class FakeResponseCodec:
        def decrypt(self, ciphertext):
            decrypt_calls.append(ciphertext)
            return stored_response

    service = ApiIdempotencyService(
        repository=FakeRepository(),
        response_codec=FakeResponseCodec(),
    )

    reservation = await service.reserve(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=user_id,
        operation="service_order.create",
        idempotency_key="same-order-key",
        payload=payload,
    )

    assert reservation.is_replay is True
    assert reservation.response_status == 201
    assert reservation.response_payload == (
        stored_response
    )
    assert decrypt_calls == [b"encrypted"]
    assert create_calls == []



@pytest.mark.asyncio
async def test_idempotency_service_encrypts_and_completes_reserved_response():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_idempotency import (
        ApiIdempotencyReservation,
        ApiIdempotencyService,
    )

    completed_at = datetime(
        2026,
        9,
        8,
        15,
        0,
        tzinfo=UTC,
    )
    record = SimpleNamespace(status="processing")
    calls = []

    class FakeRepository:
        async def complete_record(self, **kwargs):
            calls.append(kwargs)
            return kwargs["record"]

    class FakeResponseCodec:
        def encrypt(self, payload):
            assert payload == {
                "data": {
                    "id": "order-1",
                    "status": "draft",
                },
                "meta": {},
            }
            return b"encrypted-response"

    service = ApiIdempotencyService(
        repository=FakeRepository(),
        response_codec=FakeResponseCodec(),
    )
    reservation = ApiIdempotencyReservation(
        record=record,
        is_replay=False,
    )

    result = await service.complete(
        reservation=reservation,
        response_status=201,
        response_payload={
            "data": {
                "id": "order-1",
                "status": "draft",
            },
            "meta": {},
        },
        now=completed_at,
    )

    assert result is record
    assert calls == [
        {
            "record": record,
            "response_status": 201,
            "response_ciphertext": (
                b"encrypted-response"
            ),
            "completed_at": completed_at,
        }
    ]



def test_idempotency_settings_load_encryption_key_and_release_ttl(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.settings import (
        ApiIdempotencySettings,
    )

    encryption_key = (
        Fernet.generate_key().decode("ascii")
    )
    monkeypatch.setenv(
        "API_IDEMPOTENCY_ENCRYPTION_KEY",
        encryption_key,
    )

    settings = ApiIdempotencySettings.from_env()

    assert settings.encryption_key == encryption_key
    assert settings.retention_seconds == 86400



def test_idempotency_dependency_builds_shared_service_from_environment(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.dependencies import (
        get_api_idempotency_service,
    )
    from services.api_idempotency import (
        ApiIdempotencyResponseCodec,
        ApiIdempotencyService,
    )

    encryption_key = (
        Fernet.generate_key().decode("ascii")
    )
    monkeypatch.setenv(
        "API_IDEMPOTENCY_ENCRYPTION_KEY",
        encryption_key,
    )

    session = object()
    service = get_api_idempotency_service(
        session=session,
    )

    assert isinstance(
        service,
        ApiIdempotencyService,
    )
    assert service.repository.session is session
    assert isinstance(
        service.response_codec,
        ApiIdempotencyResponseCodec,
    )
