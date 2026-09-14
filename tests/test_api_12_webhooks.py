import pytest

from sqlalchemy import LargeBinary


def test_webhook_endpoint_model_matches_release_contract():
    from database.models import WebhookEndpoint

    table = WebhookEndpoint.__table__

    assert table.name == "webhook_endpoints"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "api_client_id",
        "callback_url",
        "secret_ciphertext",
        "status",
        "consecutive_failures",
        "created_at",
        "updated_at",
    }

    assert table.c.tenant_id.nullable is False
    assert table.c.api_client_id.nullable is False
    assert table.c.callback_url.nullable is False
    assert table.c.secret_ciphertext.nullable is False
    assert isinstance(
        table.c.secret_ciphertext.type,
        LargeBinary,
    )

    assert table.c.status.nullable is False
    assert (
        table.c.consecutive_failures.nullable
        is False
    )

    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }
    assert (
        "fk_webhook_endpoints_tenant_client"
        in constraint_names
    )
    assert (
        "ck_webhook_endpoints_status"
        in constraint_names
    )
    assert (
        "uq_webhook_endpoints_tenant_id_id"
        in constraint_names
    )

    assert "secret" not in table.columns
    assert "plaintext_secret" not in table.columns



def test_webhook_subscription_model_matches_release_contract():
    from database.models import (
        WebhookSubscription,
    )

    table = WebhookSubscription.__table__

    assert table.name == "webhook_subscriptions"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "webhook_endpoint_id",
        "event_type",
        "created_at",
    }

    assert table.c.tenant_id.nullable is False
    assert (
        table.c.webhook_endpoint_id.nullable
        is False
    )
    assert table.c.event_type.nullable is False

    constraints = {
        constraint.name: constraint
        for constraint in table.constraints
        if constraint.name
    }

    assert (
        "fk_webhook_subscriptions_tenant_endpoint"
        in constraints
    )
    assert (
        "uq_webhook_subscriptions_endpoint_event"
        in constraints
    )
    assert (
        "ck_webhook_subscriptions_event_type"
        in constraints
    )

    event_sql = str(
        constraints[
            "ck_webhook_subscriptions_event_type"
        ].sqltext
    )

    release_events = {
        "contact_request.created",
        "contact_request.updated",
        "service_order.created",
        "service_order.confirmed",
        "service_order.completed",
        "service_order.cancelled",
        "review.created",
        "review.published",
        "professional_cabinet.updated",
        (
            "professional_cabinet."
            "availability_changed"
        ),
    }

    for event_type in release_events:
        assert event_type in event_sql

    assert "billing." not in event_sql
    assert "hr." not in event_sql
    assert "construction." not in event_sql



def test_webhook_event_model_matches_release_contract():
    from sqlalchemy.dialects.postgresql import JSONB

    from database.models import WebhookEvent

    table = WebhookEvent.__table__

    assert table.name == "webhook_events"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "event_type",
        "payload",
        "created_at",
        "expires_at",
    }

    assert table.c.tenant_id.nullable is False
    assert table.c.event_type.nullable is False
    assert table.c.payload.nullable is False
    assert isinstance(table.c.payload.type, JSONB)
    assert table.c.created_at.nullable is False
    assert table.c.created_at.type.timezone is True
    assert table.c.expires_at.nullable is False
    assert table.c.expires_at.type.timezone is True

    constraints = {
        constraint.name: constraint
        for constraint in table.constraints
        if constraint.name
    }

    assert (
        "fk_webhook_events_tenant"
        in constraints
    )
    assert (
        "uq_webhook_events_tenant_id_id"
        in constraints
    )
    assert (
        "ck_webhook_events_event_type"
        in constraints
    )
    assert (
        "ck_webhook_events_payload_object"
        in constraints
    )
    assert (
        "ck_webhook_events_expiration"
        in constraints
    )

    event_sql = str(
        constraints[
            "ck_webhook_events_event_type"
        ].sqltext
    )

    release_events = {
        "contact_request.created",
        "contact_request.updated",
        "service_order.created",
        "service_order.confirmed",
        "service_order.completed",
        "service_order.cancelled",
        "review.created",
        "review.published",
        "professional_cabinet.updated",
        (
            "professional_cabinet."
            "availability_changed"
        ),
    }

    for event_type in release_events:
        assert event_type in event_sql

    assert "billing." not in event_sql
    assert "hr." not in event_sql
    assert "construction." not in event_sql



def test_webhook_delivery_model_matches_release_contract():
    from database.models import WebhookDelivery

    table = WebhookDelivery.__table__

    assert table.name == "webhook_deliveries"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "webhook_event_id",
        "webhook_endpoint_id",
        "status",
        "attempt_count",
        "next_attempt_at",
        "response_status",
        "error_category",
        "delivered_at",
        "created_at",
        "updated_at",
    }

    required_columns = {
        "tenant_id",
        "webhook_event_id",
        "webhook_endpoint_id",
        "status",
        "attempt_count",
        "created_at",
        "updated_at",
    }
    for column_name in required_columns:
        assert (
            table.c[column_name].nullable
            is False
        )

    optional_columns = {
        "next_attempt_at",
        "response_status",
        "error_category",
        "delivered_at",
    }
    for column_name in optional_columns:
        assert (
            table.c[column_name].nullable
            is True
        )

    assert table.c.next_attempt_at.type.timezone
    assert table.c.delivered_at.type.timezone
    assert table.c.created_at.type.timezone
    assert table.c.updated_at.type.timezone

    constraints = {
        constraint.name: constraint
        for constraint in table.constraints
        if constraint.name
    }

    assert (
        "fk_webhook_deliveries_tenant_event"
        in constraints
    )
    assert (
        "fk_webhook_deliveries_tenant_endpoint"
        in constraints
    )
    assert (
        "uq_webhook_deliveries_event_endpoint"
        in constraints
    )
    assert (
        "ck_webhook_deliveries_status"
        in constraints
    )
    assert (
        "ck_webhook_deliveries_attempt_count"
        in constraints
    )

    status_sql = str(
        constraints[
            "ck_webhook_deliveries_status"
        ].sqltext
    )
    for status in {
        "pending",
        "processing",
        "delivered",
        "failed",
    }:
        assert status in status_sql

    attempt_sql = str(
        constraints[
            "ck_webhook_deliveries_attempt_count"
        ].sqltext
    )
    assert "5" in attempt_sql

    forbidden_columns = {
        "response_body",
        "request_body",
        "authorization",
        "secret",
    }
    assert (
        forbidden_columns
        & set(table.columns.keys())
    ) == set()



def test_webhook_secret_codec_issues_random_encrypted_secret():
    from cryptography.fernet import Fernet

    from services.webhooks import (
        WebhookSecretCodec,
    )

    encryption_key = (
        Fernet.generate_key().decode("ascii")
    )
    codec = WebhookSecretCodec(
        encryption_key=encryption_key,
    )

    first = codec.issue_secret()
    second = codec.issue_secret()

    assert isinstance(first.secret, str)
    assert len(first.secret) >= 32
    assert first.secret != second.secret

    assert isinstance(
        first.secret_ciphertext,
        bytes,
    )
    assert (
        first.secret.encode("utf-8")
        not in first.secret_ciphertext
    )
    assert (
        first.secret_ciphertext
        != second.secret_ciphertext
    )

    assert codec.decrypt(
        first.secret_ciphertext
    ) == first.secret
    assert codec.decrypt(
        second.secret_ciphertext
    ) == second.secret



def test_webhook_signature_matches_release_wire_contract():
    import hashlib
    import hmac

    from services.webhooks import (
        WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS,
        WebhookSigner,
    )

    secret = "endpoint-private-secret"
    timestamp = 1788955200
    webhook_id = (
        "7a5631d1-c568-4a25-9b15-"
        "e937d6972cd7"
    )
    raw_body = (
        b'{"id":"evt_test","type":'
        b'"service_order.completed","data":{}}'
    )

    expected_digest = hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode("ascii")
        + b"."
        + raw_body,
        hashlib.sha256,
    ).hexdigest()

    signer = WebhookSigner()

    assert signer.sign(
        secret=secret,
        timestamp=timestamp,
        raw_body=raw_body,
    ) == f"v1={expected_digest}"

    assert signer.build_headers(
        webhook_id=webhook_id,
        secret=secret,
        timestamp=timestamp,
        raw_body=raw_body,
    ) == {
        "X-SGHR-Webhook-Id": webhook_id,
        "X-SGHR-Webhook-Timestamp": str(
            timestamp
        ),
        "X-SGHR-Webhook-Signature": (
            f"v1={expected_digest}"
        ),
    }

    assert (
        WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS
        == 300
    )



@pytest.mark.asyncio
async def test_webhook_callback_url_validator_blocks_release_ssrf_targets():
    from services.webhooks import (
        WebhookCallbackUrlError,
        WebhookCallbackUrlValidator,
    )

    resolved_hosts = []

    async def resolver(hostname):
        resolved_hosts.append(hostname)

        if hostname == "private.example.com":
            return ("10.20.30.40",)

        return ("93.184.216.34",)

    validator = WebhookCallbackUrlValidator(
        resolver=resolver,
    )

    valid_url = (
        "https://hooks.example.com/"
        "sghr/events"
    )
    assert await validator.validate(
        valid_url,
        environment="production",
    ) == valid_url
    assert resolved_hosts == [
        "hooks.example.com",
    ]

    invalid_urls = (
        "http://hooks.example.com/events",
        "https://user:pass@hooks.example.com/events",
        "https://localhost/events",
        "https://127.0.0.1/events",
        "https://10.0.0.10/events",
        "https://169.254.169.254/events",
        "https://[::1]/events",
        "https://private.example.com/events",
    )

    for callback_url in invalid_urls:
        with pytest.raises(
            WebhookCallbackUrlError
        ):
            await validator.validate(
                callback_url,
                environment="production",
            )



@pytest.mark.asyncio
async def test_webhook_repository_creates_tenant_scoped_encrypted_endpoint():
    from uuid import uuid4

    from database.models import WebhookEndpoint
    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    ciphertext = b"encrypted-endpoint-secret"
    added = []
    flushes = []

    class FakeSession:
        def add(self, value):
            added.append(value)

        async def flush(self):
            flushes.append("flush")

    repository = WebhookRepository(
        FakeSession()
    )

    result = await repository.create_endpoint(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        callback_url=(
            "https://hooks.example.com/events"
        ),
        secret_ciphertext=ciphertext,
    )

    assert len(added) == 1
    assert result is added[0]
    assert isinstance(result, WebhookEndpoint)

    assert result.tenant_id == tenant_id
    assert result.api_client_id == api_client_id
    assert result.callback_url == (
        "https://hooks.example.com/events"
    )
    assert result.secret_ciphertext == ciphertext
    assert result.status == "active"
    assert result.consecutive_failures == 0

    assert not hasattr(result, "secret")
    assert not hasattr(
        result,
        "plaintext_secret",
    )
    assert flushes == ["flush"]



@pytest.mark.asyncio
async def test_webhook_repository_creates_tenant_scoped_subscriptions():
    from uuid import uuid4

    from database.models import (
        WebhookSubscription,
    )
    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    endpoint_id = uuid4()
    added_batches = []
    flushes = []

    class FakeSession:
        def add_all(self, values):
            added_batches.append(tuple(values))

        async def flush(self):
            flushes.append("flush")

    repository = WebhookRepository(
        FakeSession()
    )

    result = await (
        repository.create_subscriptions(
            tenant_id=tenant_id,
            webhook_endpoint_id=endpoint_id,
            event_types=(
                "contact_request.created",
                "service_order.completed",
            ),
        )
    )

    assert len(added_batches) == 1
    assert result == added_batches[0]
    assert len(result) == 2
    assert all(
        isinstance(
            item,
            WebhookSubscription,
        )
        for item in result
    )
    assert {
        item.event_type
        for item in result
    } == {
        "contact_request.created",
        "service_order.completed",
    }
    assert all(
        item.tenant_id == tenant_id
        for item in result
    )
    assert all(
        item.webhook_endpoint_id
        == endpoint_id
        for item in result
    )
    assert flushes == ["flush"]



@pytest.mark.asyncio
async def test_webhook_repository_resolves_active_api_client_in_tenant_scope():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await (
        repository.get_active_api_client(
            tenant_id=tenant_id,
            api_client_id=api_client_id,
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

    assert "FROM api_clients" in sql
    assert (
        f"api_clients.tenant_id = '{tenant_id}'"
        in sql
    )
    assert (
        f"api_clients.id = '{api_client_id}'"
        in sql
    )
    assert (
        "api_clients.status = 'active'"
        in sql
    )



def test_webhook_event_type_validation_uses_release_allowlist():
    from services.webhooks import (
        ALLOWED_WEBHOOK_EVENT_TYPES,
        WebhookEventTypeError,
        validate_webhook_event_types,
    )

    assert ALLOWED_WEBHOOK_EVENT_TYPES == frozenset(
        {
            "contact_request.created",
            "contact_request.updated",
            "service_order.created",
            "service_order.confirmed",
            "service_order.completed",
            "service_order.cancelled",
            "review.created",
            "review.published",
            "professional_cabinet.updated",
            (
                "professional_cabinet."
                "availability_changed"
            ),
        }
    )

    assert validate_webhook_event_types(
        (
            "service_order.completed",
            "contact_request.created",
            "service_order.completed",
        )
    ) == (
        "service_order.completed",
        "contact_request.created",
    )

    for invalid_events in (
        (),
        ("billing.payment.paid",),
        ("hr.candidate.created",),
        ("construction.project.updated",),
        ("unknown.event",),
    ):
        with pytest.raises(
            WebhookEventTypeError
        ):
            validate_webhook_event_types(
                invalid_events
            )



@pytest.mark.asyncio
async def test_webhook_service_creates_endpoint_and_idempotency_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookEndpointIssuedView,
        WebhookService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        9,
        15,
        0,
        tzinfo=UTC,
    )
    callback_url = (
        "https://hooks.example.com/events"
    )
    plaintext_secret = (
        "new-webhook-endpoint-secret"
    )
    secret_ciphertext = (
        b"encrypted-webhook-endpoint-secret"
    )
    events = []

    reservation = SimpleNamespace(
        is_replay=False,
        record=object(),
    )

    class FakeValidator:
        async def validate(
            self,
            value,
            *,
            environment,
        ):
            events.append(
                (
                    "validate",
                    {
                        "callback_url": value,
                        "environment": environment,
                    },
                )
            )
            return value

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            events.append(("reserve", kwargs))
            assert kwargs == {
                "tenant_id": tenant_id,
                "principal_type": "user",
                "principal_id": user_id,
                "operation": (
                    "webhook_endpoint.create"
                ),
                "idempotency_key": (
                    "webhook-endpoint-001"
                ),
                "payload": {
                    "api_client_id": str(
                        api_client_id
                    ),
                    "callback_url": callback_url,
                    "event_types": [
                        "contact_request.created",
                        "service_order.completed",
                    ],
                },
            }
            return reservation

        async def complete(self, **kwargs):
            events.append(("complete", kwargs))
            assert kwargs["reservation"] is reservation
            assert kwargs["response_status"] == 201

            payload = kwargs["response_payload"]
            assert payload == {
                "id": str(endpoint_id),
                "api_client_id": str(
                    api_client_id
                ),
                "callback_url": callback_url,
                "status": "active",
                "event_types": [
                    "contact_request.created",
                    "service_order.completed",
                ],
                "created_at": (
                    created_at.isoformat()
                ),
                "secret": plaintext_secret,
            }

    class FakeSecretCodec:
        def issue_secret(self):
            events.append(("secret", {}))
            return SimpleNamespace(
                secret=plaintext_secret,
                secret_ciphertext=(
                    secret_ciphertext
                ),
            )

    class FakeRepository:
        async def get_active_api_client(
            self,
            **kwargs,
        ):
            events.append(("client", kwargs))
            return SimpleNamespace(
                id=api_client_id,
                status="active",
            )

        async def create_endpoint(
            self,
            **kwargs,
        ):
            events.append(("endpoint", kwargs))
            assert "secret" not in kwargs
            assert kwargs[
                "secret_ciphertext"
            ] == secret_ciphertext

            return SimpleNamespace(
                id=endpoint_id,
                tenant_id=tenant_id,
                api_client_id=api_client_id,
                callback_url=callback_url,
                status="active",
                consecutive_failures=0,
                created_at=created_at,
            )

        async def create_subscriptions(
            self,
            **kwargs,
        ):
            events.append(
                ("subscriptions", kwargs)
            )
            return ()

    class FakeSession:
        async def commit(self):
            events.append(("commit", {}))

        async def rollback(self):
            events.append(("rollback", {}))

    service = WebhookService(
        session=FakeSession(),
        repository=FakeRepository(),
        secret_codec=FakeSecretCodec(),
        callback_validator=FakeValidator(),
        environment="production",
        idempotency=FakeIdempotency(),
    )

    result = await service.create_endpoint(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=user_id,
        api_client_id=api_client_id,
        callback_url=callback_url,
        event_types=(
            "contact_request.created",
            "service_order.completed",
        ),
        idempotency_key=(
            "webhook-endpoint-001"
        ),
    )

    assert isinstance(
        result,
        WebhookEndpointIssuedView,
    )
    assert result.id == endpoint_id
    assert result.api_client_id == api_client_id
    assert result.callback_url == callback_url
    assert result.status == "active"
    assert result.event_types == (
        "contact_request.created",
        "service_order.completed",
    )
    assert result.created_at == created_at
    assert result.secret == plaintext_secret

    assert [
        event_name
        for event_name, _ in events
    ] == [
        "validate",
        "reserve",
        "client",
        "secret",
        "endpoint",
        "subscriptions",
        "complete",
        "commit",
    ]



@pytest.mark.asyncio
async def test_webhook_service_replays_endpoint_creation_without_duplicate():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookEndpointIssuedView,
        WebhookService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        9,
        16,
        0,
        tzinfo=UTC,
    )
    callback_url = (
        "https://hooks.example.com/events"
    )
    plaintext_secret = (
        "replayed-webhook-secret"
    )

    class FakeValidator:
        async def validate(
            self,
            value,
            *,
            environment,
        ):
            assert environment == "production"
            return value

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            return SimpleNamespace(
                is_replay=True,
                response_status=201,
                response_payload={
                    "id": str(endpoint_id),
                    "api_client_id": str(
                        api_client_id
                    ),
                    "callback_url": (
                        callback_url
                    ),
                    "status": "active",
                    "event_types": [
                        "contact_request.created",
                    ],
                    "created_at": (
                        created_at.isoformat()
                    ),
                    "secret": (
                        plaintext_secret
                    ),
                },
            )

        async def complete(self, **kwargs):
            raise AssertionError(
                "Replay must not be completed."
            )

    class ForbiddenRepository:
        def __getattr__(self, name):
            raise AssertionError(
                "Replay must not access "
                f"repository: {name}"
            )

    class ForbiddenSecretCodec:
        def issue_secret(self):
            raise AssertionError(
                "Replay must not issue a secret."
            )

    class FakeSession:
        async def commit(self):
            raise AssertionError(
                "Replay must not commit."
            )

        async def rollback(self):
            raise AssertionError(
                "Replay must not roll back."
            )

    service = WebhookService(
        session=FakeSession(),
        repository=ForbiddenRepository(),
        secret_codec=ForbiddenSecretCodec(),
        callback_validator=FakeValidator(),
        environment="production",
        idempotency=FakeIdempotency(),
    )

    result = await service.create_endpoint(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=user_id,
        api_client_id=api_client_id,
        callback_url=callback_url,
        event_types=(
            "contact_request.created",
        ),
        idempotency_key=(
            "webhook-endpoint-replay"
        ),
    )

    assert isinstance(
        result,
        WebhookEndpointIssuedView,
    )
    assert result.id == endpoint_id
    assert result.api_client_id == api_client_id
    assert result.callback_url == callback_url
    assert result.status == "active"
    assert result.event_types == (
        "contact_request.created",
    )
    assert result.created_at == created_at
    assert result.secret == plaintext_secret



def test_webhook_settings_load_encryption_key_and_deployment_environment(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.settings import (
        ApiWebhookSettings,
    )

    encryption_key = (
        Fernet.generate_key().decode("ascii")
    )
    monkeypatch.setenv(
        "WEBHOOK_SECRET_ENCRYPTION_KEY",
        encryption_key,
    )
    monkeypatch.setenv(
        "API_ENVIRONMENT",
        "production",
    )

    settings = ApiWebhookSettings.from_env()

    assert settings.secret_encryption_key == (
        encryption_key
    )
    assert settings.environment == "production"

    monkeypatch.delenv(
        "WEBHOOK_SECRET_ENCRYPTION_KEY"
    )
    with pytest.raises(ValueError):
        ApiWebhookSettings.from_env()

    monkeypatch.setenv(
        "WEBHOOK_SECRET_ENCRYPTION_KEY",
        encryption_key,
    )
    monkeypatch.setenv(
        "API_ENVIRONMENT",
        "invalid-environment",
    )
    with pytest.raises(ValueError):
        ApiWebhookSettings.from_env()



def test_webhook_dependency_builds_secure_scoped_service(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.dependencies import (
        get_webhook_service,
    )
    from database.repositories.webhooks import (
        WebhookRepository,
    )
    from services.api_idempotency import (
        ApiIdempotencyService,
    )
    from services.webhooks import (
        WebhookCallbackUrlValidator,
        WebhookSecretCodec,
        WebhookService,
    )

    monkeypatch.setenv(
        "WEBHOOK_SECRET_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv(
        "API_IDEMPOTENCY_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv(
        "API_ENVIRONMENT",
        "production",
    )

    session = object()
    service = get_webhook_service(
        session=session,
    )

    assert isinstance(service, WebhookService)
    assert service.session is session
    assert isinstance(
        service.repository,
        WebhookRepository,
    )
    assert service.repository.session is session

    assert isinstance(
        service.secret_codec,
        WebhookSecretCodec,
    )
    assert isinstance(
        service.callback_validator,
        WebhookCallbackUrlValidator,
    )
    assert service.environment == "production"

    assert isinstance(
        service.idempotency,
        ApiIdempotencyService,
    )
    assert (
        service.idempotency.repository.session
        is session
    )



@pytest.mark.asyncio
async def test_webhook_endpoint_create_uses_jwt_actor_and_idempotency_key():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.routes.webhooks import (
        require_webhooks_write,
    )
    from api.dependencies import (
        get_webhook_service,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointIssuedView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        9,
        17,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=user_id,
        api_client_id=None,
        grants=frozenset(
            {"webhooks.write"}
        ),
    )

    class FakeService:
        async def create_endpoint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointIssuedView(
                id=endpoint_id,
                api_client_id=api_client_id,
                callback_url=(
                    "https://hooks.example.com/events"
                ),
                status="active",
                event_types=(
                    "contact_request.created",
                    "service_order.completed",
                ),
                created_at=created_at,
                secret="one-time-secret",
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_write
    ] = lambda: actor
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/webhooks/endpoints",
            headers={
                "X-Request-ID": (
                    "webhook-endpoint-create"
                ),
                "Idempotency-Key": (
                    "webhook-endpoint-001"
                ),
            },
            json={
                "api_client_id": str(
                    api_client_id
                ),
                "callback_url": (
                    "https://hooks.example.com/events"
                ),
                "events": [
                    "contact_request.created",
                    "service_order.completed",
                ],
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "tenant_id": tenant_id,
            "principal_type": "user",
            "principal_id": user_id,
            "api_client_id": api_client_id,
            "callback_url": (
                "https://hooks.example.com/events"
            ),
            "event_types": (
                "contact_request.created",
                "service_order.completed",
            ),
            "idempotency_key": (
                "webhook-endpoint-001"
            ),
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(endpoint_id),
            "api_client_id": str(
                api_client_id
            ),
            "callback_url": (
                "https://hooks.example.com/events"
            ),
            "status": "active",
            "events": [
                "contact_request.created",
                "service_order.completed",
            ],
            "created_at": (
                created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            "secret": "one-time-secret",
        },
        "meta": {},
        "request_id": (
            "webhook-endpoint-create"
        ),
    }



@pytest.mark.asyncio
async def test_webhook_repository_lists_endpoints_by_tenant_and_api_client():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await repository.list_endpoints(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        limit=21,
        offset=0,
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

    assert "FROM webhook_endpoints" in sql
    assert (
        f"webhook_endpoints.tenant_id = "
        f"'{tenant_id}'"
    ) in sql
    assert (
        f"webhook_endpoints.api_client_id = "
        f"'{api_client_id}'"
    ) in sql
    assert "LIMIT 21" in sql



@pytest.mark.asyncio
async def test_webhook_repository_lists_tenant_endpoints_without_api_client_filter():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await repository.list_endpoints(
        tenant_id=tenant_id,
        api_client_id=None,
        limit=21,
        offset=0,
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

    where_clause = sql.split("WHERE", 1)[1]

    assert (
        f"webhook_endpoints.tenant_id = "
        f"'{tenant_id}'"
    ) in where_clause
    assert (
        "webhook_endpoints.api_client_id"
        not in where_clause
    )



@pytest.mark.asyncio
async def test_webhook_repository_lists_subscriptions_for_scoped_endpoints():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    endpoint_ids = (
        uuid4(),
        uuid4(),
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
    repository = WebhookRepository(session)

    result = await (
        repository
        .list_subscriptions_for_endpoints(
            tenant_id=tenant_id,
            endpoint_ids=endpoint_ids,
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

    assert "FROM webhook_subscriptions" in sql
    assert (
        f"webhook_subscriptions.tenant_id = "
        f"'{tenant_id}'"
    ) in sql
    assert (
        "webhook_subscriptions."
        "webhook_endpoint_id IN"
    ) in sql

    for endpoint_id in endpoint_ids:
        assert str(endpoint_id) in sql



@pytest.mark.asyncio
async def test_webhook_service_lists_safe_scoped_endpoint_views():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookEndpointPage,
        WebhookEndpointView,
        WebhookService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    endpoint_ids = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    created_at = datetime(
        2026,
        9,
        9,
        18,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        9,
        18,
        30,
        tzinfo=UTC,
    )
    calls = []

    endpoints = [
        SimpleNamespace(
            id=endpoint_id,
            api_client_id=api_client_id,
            callback_url=(
                f"https://hooks.example.com/{index}"
            ),
            status="active",
            secret_ciphertext=b"private-secret",
            created_at=created_at,
            updated_at=updated_at,
        )
        for index, endpoint_id
        in enumerate(endpoint_ids)
    ]

    subscriptions = [
        SimpleNamespace(
            webhook_endpoint_id=endpoint_ids[0],
            event_type="contact_request.created",
        ),
        SimpleNamespace(
            webhook_endpoint_id=endpoint_ids[0],
            event_type="service_order.completed",
        ),
        SimpleNamespace(
            webhook_endpoint_id=endpoint_ids[1],
            event_type="review.published",
        ),
    ]

    class FakeRepository:
        async def list_endpoints(
            self,
            **kwargs,
        ):
            calls.append(("endpoints", kwargs))
            return endpoints

        async def list_subscriptions_for_endpoints(
            self,
            **kwargs,
        ):
            calls.append(("subscriptions", kwargs))
            return subscriptions

    service = WebhookService(
        session=object(),
        repository=FakeRepository(),
        secret_codec=object(),
        callback_validator=object(),
        environment="production",
        idempotency=object(),
    )

    page = await service.list_endpoints(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        page=0,
        page_size=2,
    )

    assert isinstance(page, WebhookEndpointPage)
    assert page.page == 0
    assert page.has_next is True
    assert len(page.items) == 2
    assert all(
        isinstance(item, WebhookEndpointView)
        for item in page.items
    )

    assert calls == [
        (
            "endpoints",
            {
                "tenant_id": tenant_id,
                "api_client_id": api_client_id,
                "limit": 3,
                "offset": 0,
            },
        ),
        (
            "subscriptions",
            {
                "tenant_id": tenant_id,
                "endpoint_ids": endpoint_ids[:2],
            },
        ),
    ]

    first = page.items[0]
    assert first.id == endpoint_ids[0]
    assert first.api_client_id == api_client_id
    assert first.event_types == (
        "contact_request.created",
        "service_order.completed",
    )
    assert first.created_at == created_at
    assert first.updated_at == updated_at

    assert not hasattr(first, "secret")
    assert not hasattr(
        first,
        "secret_ciphertext",
    )



@pytest.mark.asyncio
async def test_webhook_endpoint_list_uses_jwt_actor_tenant_and_cursor():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.routes.webhooks import (
        require_webhooks_read,
    )
    from api.dependencies import (
        get_webhook_service,
    )
    from api.pagination import (
        encode_page_cursor,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointPage,
        WebhookEndpointView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        9,
        19,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        9,
        19,
        30,
        tzinfo=UTC,
    )
    calls = []

    actor = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=user_id,
        api_client_id=None,
        grants=frozenset(
            {"webhooks.read"}
        ),
    )

    class FakeService:
        async def list_endpoints(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointPage(
                items=(
                    WebhookEndpointView(
                        id=endpoint_id,
                        api_client_id=api_client_id,
                        callback_url=(
                            "https://hooks.example.com/events"
                        ),
                        status="active",
                        event_types=(
                            "contact_request.created",
                            "review.published",
                        ),
                        created_at=created_at,
                        updated_at=updated_at,
                    ),
                ),
                page=0,
                has_next=True,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_read
    ] = lambda: actor
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/webhooks/endpoints",
            params={"limit": 2},
            headers={
                "X-Request-ID": (
                    "webhook-endpoint-list"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": None,
            "page": 0,
            "page_size": 2,
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(endpoint_id),
                "api_client_id": str(
                    api_client_id
                ),
                "callback_url": (
                    "https://hooks.example.com/events"
                ),
                "status": "active",
                "events": [
                    "contact_request.created",
                    "review.published",
                ],
                "created_at": (
                    created_at.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                ),
                "updated_at": (
                    updated_at.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                ),
            }
        ],
        "meta": {
            "next_cursor": (
                encode_page_cursor(1)
            ),
            "has_more": True,
        },
        "request_id": (
            "webhook-endpoint-list"
        ),
    }



@pytest.mark.asyncio
async def test_webhook_access_context_resolves_partner_api_key_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from api.auth import (
        get_webhook_access_context,
    )
    from services.partner_api_auth import (
        PartnerApiPrincipal,
    )
    from services.webhooks import (
        WebhookAccessContext,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    plaintext_key = (
        "sghr_0123456789abcdef."
        "private-webhook-key"
    )
    calls = []

    partner_principal = PartnerApiPrincipal(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        api_key_id=api_key_id,
        scopes=frozenset(
            {
                "webhooks.read",
                "webhooks.write",
            }
        ),
    )

    class FakePartnerAuthentication:
        async def authenticate(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return partner_principal

    request = SimpleNamespace(
        client=SimpleNamespace(
            host="192.0.2.10",
        ),
        state=SimpleNamespace(),
    )

    context = await get_webhook_access_context(
        request=request,
        authorization=(
            f"ApiKey {plaintext_key}"
        ),
        codec=object(),
        identity_service=object(),
        partner_service=(
            FakePartnerAuthentication()
        ),
    )

    assert isinstance(
        context,
        WebhookAccessContext,
    )
    assert context.tenant_id == tenant_id
    assert context.principal_type == "api_key"
    assert context.principal_id == api_key_id
    assert context.api_client_id == api_client_id
    assert context.grants == frozenset(
        {
            "webhooks.read",
            "webhooks.write",
        }
    )

    assert len(calls) == 1
    assert calls[0]["api_key"] == plaintext_key
    assert calls[0]["remote_ip"] == (
        "192.0.2.10"
    )
    assert calls[0]["now"].tzinfo is not None

    assert (
        request.state.webhook_access_context
        is context
    )



@pytest.mark.asyncio
async def test_webhook_access_guard_checks_jwt_permissions_and_api_key_scopes():
    from uuid import uuid4

    from api.auth import (
        require_webhook_access,
    )
    from api.errors import ApiHttpError
    from services.webhooks import (
        WebhookAccessContext,
    )

    tenant_id = uuid4()

    user_context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=uuid4(),
        api_client_id=None,
        grants=frozenset(
            {"webhooks.read"}
        ),
    )
    api_key_context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=uuid4(),
        api_client_id=uuid4(),
        grants=frozenset(
            {"webhooks.read"}
        ),
    )

    read_guard = require_webhook_access(
        "webhooks.read"
    )

    assert await read_guard(
        context=user_context
    ) is user_context
    assert await read_guard(
        context=api_key_context
    ) is api_key_context

    write_guard = require_webhook_access(
        "webhooks.write"
    )

    with pytest.raises(
        ApiHttpError
    ) as user_error:
        await write_guard(
            context=user_context
        )

    assert user_error.value.status_code == 403
    assert user_error.value.code == (
        "permission_required"
    )

    with pytest.raises(
        ApiHttpError
    ) as api_key_error:
        await write_guard(
            context=api_key_context
        )

    assert api_key_error.value.status_code == 403
    assert api_key_error.value.code == (
        "api_key_scope_required"
    )



@pytest.mark.asyncio
async def test_webhook_endpoint_list_scopes_partner_api_key_to_own_client():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_read,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointPage,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.read"}
        ),
    )

    class FakeService:
        async def list_endpoints(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointPage(
                items=(),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_read
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/webhooks/endpoints",
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "X-Request-ID": (
                    "webhook-api-key-list"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
            "page": 0,
            "page_size": 20,
        }
    ]
    assert response.json() == {
        "data": [],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": (
            "webhook-api-key-list"
        ),
    }



@pytest.mark.asyncio
async def test_webhook_endpoint_create_scopes_partner_api_key_to_own_client():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_write,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointIssuedView,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    endpoint_id = uuid4()
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.write"}
        ),
    )

    class FakeService:
        async def create_endpoint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointIssuedView(
                id=endpoint_id,
                api_client_id=api_client_id,
                callback_url=(
                    "https://hooks.example.com/events"
                ),
                status="active",
                event_types=(
                    "service_order.completed",
                ),
                created_at=datetime(
                    2026,
                    9,
                    9,
                    20,
                    0,
                    tzinfo=UTC,
                ),
                secret="one-time-secret",
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_write
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/webhooks/endpoints",
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "Idempotency-Key": (
                    "partner-webhook-create-001"
                ),
                "X-Request-ID": (
                    "partner-webhook-create"
                ),
            },
            json={
                "callback_url": (
                    "https://hooks.example.com/events"
                ),
                "events": [
                    "service_order.completed",
                ],
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "tenant_id": tenant_id,
            "principal_type": "api_key",
            "principal_id": api_key_id,
            "api_client_id": api_client_id,
            "callback_url": (
                "https://hooks.example.com/events"
            ),
            "event_types": (
                "service_order.completed",
            ),
            "idempotency_key": (
                "partner-webhook-create-001"
            ),
        }
    ]

    body = response.json()
    assert body["data"]["id"] == str(
        endpoint_id
    )
    assert body["data"]["api_client_id"] == str(
        api_client_id
    )
    assert body["data"]["secret"] == (
        "one-time-secret"
    )



@pytest.mark.asyncio
async def test_webhook_repository_gets_endpoint_with_jwt_and_api_key_scopes():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    endpoint_id = uuid4()
    api_client_id = uuid4()
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
    repository = WebhookRepository(session)

    for api_client_scope in (
        None,
        api_client_id,
    ):
        result = await repository.get_endpoint(
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
            api_client_id=api_client_scope,
        )

        assert result is expected

    assert len(session.statements) == 2

    jwt_sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    api_key_sql = str(
        session.statements[1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    jwt_where = jwt_sql.split("WHERE", 1)[1]
    api_key_where = api_key_sql.split(
        "WHERE",
        1,
    )[1]

    for where_clause in (
        jwt_where,
        api_key_where,
    ):
        assert (
            f"webhook_endpoints.tenant_id = "
            f"'{tenant_id}'"
        ) in where_clause
        assert (
            f"webhook_endpoints.id = "
            f"'{endpoint_id}'"
        ) in where_clause

    assert (
        "webhook_endpoints.api_client_id"
        not in jwt_where
    )
    assert (
        f"webhook_endpoints.api_client_id = "
        f"'{api_client_id}'"
    ) in api_key_where



@pytest.mark.asyncio
async def test_webhook_service_gets_safe_scoped_endpoint_detail():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookEndpointView,
        WebhookService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        9,
        21,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        9,
        21,
        30,
        tzinfo=UTC,
    )
    calls = []

    endpoint = SimpleNamespace(
        id=endpoint_id,
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        callback_url=(
            "https://hooks.example.com/events"
        ),
        status="active",
        secret_ciphertext=b"encrypted-private-secret",
        created_at=created_at,
        updated_at=updated_at,
    )
    subscriptions = [
        SimpleNamespace(
            webhook_endpoint_id=endpoint_id,
            event_type="contact_request.updated",
        ),
        SimpleNamespace(
            webhook_endpoint_id=endpoint_id,
            event_type="service_order.confirmed",
        ),
    ]

    class FakeRepository:
        async def get_endpoint(
            self,
            **kwargs,
        ):
            calls.append(("endpoint", kwargs))
            return endpoint

        async def list_subscriptions_for_endpoints(
            self,
            **kwargs,
        ):
            calls.append(("subscriptions", kwargs))
            return subscriptions

    service = WebhookService(
        session=object(),
        repository=FakeRepository(),
        secret_codec=object(),
        callback_validator=object(),
        environment="production",
        idempotency=object(),
    )

    result = await service.get_endpoint(
        tenant_id=tenant_id,
        endpoint_id=endpoint_id,
        api_client_id=api_client_id,
    )

    assert isinstance(
        result,
        WebhookEndpointView,
    )
    assert result.id == endpoint_id
    assert result.api_client_id == api_client_id
    assert result.callback_url == (
        "https://hooks.example.com/events"
    )
    assert result.status == "active"
    assert result.event_types == (
        "contact_request.updated",
        "service_order.confirmed",
    )
    assert result.created_at == created_at
    assert result.updated_at == updated_at

    assert not hasattr(result, "secret")
    assert not hasattr(
        result,
        "secret_ciphertext",
    )

    assert calls == [
        (
            "endpoint",
            {
                "tenant_id": tenant_id,
                "endpoint_id": endpoint_id,
                "api_client_id": api_client_id,
            },
        ),
        (
            "subscriptions",
            {
                "tenant_id": tenant_id,
                "endpoint_ids": (
                    endpoint_id,
                ),
            },
        ),
    ]



@pytest.mark.asyncio
async def test_webhook_endpoint_detail_uses_verified_partner_api_key_scope():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_read,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointView,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        9,
        22,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        9,
        22,
        30,
        tzinfo=UTC,
    )
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.read"}
        ),
    )

    class FakeService:
        async def get_endpoint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointView(
                id=endpoint_id,
                api_client_id=api_client_id,
                callback_url=(
                    "https://hooks.example.com/events"
                ),
                status="active",
                event_types=(
                    "contact_request.created",
                    "service_order.completed",
                ),
                created_at=created_at,
                updated_at=updated_at,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_read
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/webhooks/endpoints/"
                f"{endpoint_id}"
            ),
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "X-Request-ID": (
                    "webhook-endpoint-detail"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "endpoint_id": endpoint_id,
            "api_client_id": api_client_id,
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(endpoint_id),
            "api_client_id": str(
                api_client_id
            ),
            "callback_url": (
                "https://hooks.example.com/events"
            ),
            "status": "active",
            "events": [
                "contact_request.created",
                "service_order.completed",
            ],
            "created_at": (
                created_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            ),
            "updated_at": (
                updated_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            ),
        },
        "meta": {},
        "request_id": (
            "webhook-endpoint-detail"
        ),
    }

    assert "secret" not in response.text
    assert "ciphertext" not in response.text



@pytest.mark.asyncio
async def test_webhook_repository_locks_scoped_endpoint_for_update():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await (
        repository.get_endpoint_for_update(
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
            api_client_id=api_client_id,
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
    where_clause = sql.split(
        "WHERE",
        1,
    )[1]

    assert (
        f"webhook_endpoints.tenant_id = "
        f"'{tenant_id}'"
    ) in where_clause
    assert (
        f"webhook_endpoints.id = "
        f"'{endpoint_id}'"
    ) in where_clause
    assert (
        f"webhook_endpoints.api_client_id = "
        f"'{api_client_id}'"
    ) in where_clause
    assert "FOR UPDATE" in sql



@pytest.mark.asyncio
async def test_webhook_repository_updates_only_safe_endpoint_fields():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    original_ciphertext = (
        b"encrypted-secret-must-not-change"
    )
    endpoint = SimpleNamespace(
        callback_url=(
            "https://hooks.example.com/old"
        ),
        status="active",
        secret_ciphertext=original_ciphertext,
    )

    class FakeSession:
        def __init__(self):
            self.flush = AsyncMock()

    session = FakeSession()
    repository = WebhookRepository(session)

    result = await repository.update_endpoint(
        endpoint=endpoint,
        callback_url=(
            "https://hooks.example.com/new"
        ),
        status="suspended",
    )

    assert result is endpoint
    assert endpoint.callback_url == (
        "https://hooks.example.com/new"
    )
    assert endpoint.status == "suspended"
    assert endpoint.secret_ciphertext == (
        original_ciphertext
    )

    session.flush.assert_awaited_once_with()



@pytest.mark.asyncio
async def test_webhook_repository_replaces_tenant_scoped_subscriptions():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    endpoint_id = uuid4()
    event_types = (
        "contact_request.updated",
        "service_order.completed",
    )

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.added = []
            self.flush_count = 0

        async def execute(self, statement):
            self.statements.append(statement)

        def add_all(self, values):
            self.added.extend(values)

        async def flush(self):
            self.flush_count += 1

    session = FakeSession()
    repository = WebhookRepository(session)

    result = await (
        repository.replace_subscriptions(
            tenant_id=tenant_id,
            webhook_endpoint_id=endpoint_id,
            event_types=event_types,
        )
    )

    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "DELETE FROM webhook_subscriptions" in sql
    assert (
        f"webhook_subscriptions.tenant_id = "
        f"'{tenant_id}'"
    ) in sql
    assert (
        f"webhook_subscriptions."
        f"webhook_endpoint_id = "
        f"'{endpoint_id}'"
    ) in sql

    assert tuple(result) == tuple(
        session.added
    )
    assert tuple(
        item.event_type
        for item in result
    ) == event_types
    assert all(
        item.tenant_id == tenant_id
        for item in result
    )
    assert all(
        item.webhook_endpoint_id
        == endpoint_id
        for item in result
    )
    assert session.flush_count == 1



@pytest.mark.asyncio
async def test_webhook_service_updates_endpoint_and_subscriptions_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookEndpointView,
        WebhookService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        10,
        10,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        10,
        10,
        30,
        tzinfo=UTC,
    )
    calls = []

    endpoint = SimpleNamespace(
        id=endpoint_id,
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        callback_url=(
            "https://hooks.example.com/old"
        ),
        status="active",
        secret_ciphertext=(
            b"encrypted-secret"
        ),
        created_at=created_at,
        updated_at=updated_at,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeValidator:
        async def validate(
            self,
            callback_url,
            *,
            environment,
        ):
            calls.append(
                (
                    "validate",
                    {
                        "callback_url": (
                            callback_url
                        ),
                        "environment": environment,
                    },
                )
            )
            return callback_url

    class FakeRepository:
        async def get_endpoint_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return endpoint

        async def update_endpoint(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            endpoint.callback_url = (
                kwargs["callback_url"]
            )
            endpoint.status = kwargs["status"]
            return endpoint

        async def replace_subscriptions(
            self,
            **kwargs,
        ):
            calls.append(
                ("subscriptions", kwargs)
            )
            return ()

    service = WebhookService(
        session=FakeSession(),
        repository=FakeRepository(),
        secret_codec=object(),
        callback_validator=FakeValidator(),
        environment="production",
        idempotency=object(),
    )

    result = await service.update_endpoint(
        tenant_id=tenant_id,
        endpoint_id=endpoint_id,
        api_client_id=api_client_id,
        callback_url=(
            "https://hooks.example.com/new"
        ),
        event_types=(
            "service_order.completed",
            "review.published",
        ),
        status="active",
    )

    assert isinstance(
        result,
        WebhookEndpointView,
    )
    assert result.id == endpoint_id
    assert result.api_client_id == api_client_id
    assert result.callback_url == (
        "https://hooks.example.com/new"
    )
    assert result.status == "active"
    assert result.event_types == (
        "service_order.completed",
        "review.published",
    )

    assert calls == [
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "endpoint_id": endpoint_id,
                "api_client_id": api_client_id,
            },
        ),
        (
            "validate",
            {
                "callback_url": (
                    "https://hooks.example.com/new"
                ),
                "environment": "production",
            },
        ),
        (
            "update",
            {
                "endpoint": endpoint,
                "callback_url": (
                    "https://hooks.example.com/new"
                ),
                "status": "active",
            },
        ),
        (
            "subscriptions",
            {
                "tenant_id": tenant_id,
                "webhook_endpoint_id": (
                    endpoint_id
                ),
                "event_types": (
                    "service_order.completed",
                    "review.published",
                ),
            },
        ),
        ("commit", {}),
    ]

    assert not any(
        name == "rollback"
        for name, _ in calls
    )
    assert not hasattr(
        result,
        "secret_ciphertext",
    )



@pytest.mark.asyncio
async def test_webhook_endpoint_update_uses_verified_access_scope():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_write,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointView,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        10,
        11,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        10,
        11,
        30,
        tzinfo=UTC,
    )
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.write"}
        ),
    )

    class FakeService:
        async def update_endpoint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointView(
                id=endpoint_id,
                api_client_id=api_client_id,
                callback_url=(
                    "https://hooks.example.com/new"
                ),
                status="active",
                event_types=(
                    "service_order.completed",
                    "review.published",
                ),
                created_at=created_at,
                updated_at=updated_at,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_write
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/webhooks/endpoints/"
                f"{endpoint_id}"
            ),
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "X-Request-ID": (
                    "webhook-endpoint-update"
                ),
            },
            json={
                "callback_url": (
                    "https://hooks.example.com/new"
                ),
                "events": [
                    "service_order.completed",
                    "review.published",
                ],
                "status": "active",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "endpoint_id": endpoint_id,
            "api_client_id": api_client_id,
            "callback_url": (
                "https://hooks.example.com/new"
            ),
            "event_types": (
                "service_order.completed",
                "review.published",
            ),
            "status": "active",
        }
    ]

    body = response.json()
    assert body["data"] == {
        "id": str(endpoint_id),
        "api_client_id": str(
            api_client_id
        ),
        "callback_url": (
            "https://hooks.example.com/new"
        ),
        "status": "active",
        "events": [
            "service_order.completed",
            "review.published",
        ],
        "created_at": (
            created_at.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        ),
        "updated_at": (
            updated_at.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        ),
    }
    assert body["meta"] == {}
    assert body["request_id"] == (
        "webhook-endpoint-update"
    )
    assert "secret" not in response.text



@pytest.mark.asyncio
async def test_webhook_endpoint_delete_soft_disables_in_verified_scope():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_write,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookEndpointView,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        10,
        12,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        10,
        12,
        30,
        tzinfo=UTC,
    )
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.write"}
        ),
    )

    class FakeService:
        async def update_endpoint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookEndpointView(
                id=endpoint_id,
                api_client_id=api_client_id,
                callback_url=(
                    "https://hooks.example.com/events"
                ),
                status="disabled",
                event_types=(
                    "service_order.completed",
                ),
                created_at=created_at,
                updated_at=updated_at,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_write
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.delete(
            (
                "/api/v1/webhooks/endpoints/"
                f"{endpoint_id}"
            ),
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "X-Request-ID": (
                    "webhook-endpoint-disable"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "endpoint_id": endpoint_id,
            "api_client_id": api_client_id,
            "callback_url": None,
            "event_types": None,
            "status": "disabled",
        }
    ]

    body = response.json()
    assert body["data"]["id"] == str(
        endpoint_id
    )
    assert body["data"]["status"] == (
        "disabled"
    )
    assert body["meta"] == {}
    assert body["request_id"] == (
        "webhook-endpoint-disable"
    )
    assert "secret" not in response.text



@pytest.mark.asyncio
async def test_webhook_repository_lists_deliveries_in_verified_api_client_scope():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await repository.list_deliveries(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        limit=21,
        offset=0,
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
    where_clause = sql.split(
        "WHERE",
        1,
    )[1]

    assert "FROM webhook_deliveries" in sql
    assert "JOIN webhook_endpoints" in sql
    assert (
        f"webhook_deliveries.tenant_id = "
        f"'{tenant_id}'"
    ) in where_clause
    assert (
        f"webhook_endpoints.api_client_id = "
        f"'{api_client_id}'"
    ) in where_clause
    assert (
        "webhook_endpoints.tenant_id = "
        "webhook_deliveries.tenant_id"
    ) in sql
    assert (
        "webhook_endpoints.id = "
        "webhook_deliveries.webhook_endpoint_id"
    ) in sql
    assert "LIMIT 21" in sql



@pytest.mark.asyncio
async def test_webhook_service_lists_safe_scoped_delivery_views():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookDeliveryPage,
        WebhookDeliveryView,
        WebhookService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    now = datetime(
        2026,
        9,
        10,
        15,
        0,
        tzinfo=UTC,
    )
    calls = []

    rows = [
        SimpleNamespace(
            id=uuid4(),
            webhook_event_id=uuid4(),
            webhook_endpoint_id=uuid4(),
            status="delivered",
            attempt_count=1,
            next_attempt_at=None,
            response_status=204,
            error_category=None,
            delivered_at=now,
            created_at=now,
            updated_at=now,
            payload={"private": "must-not-leak"},
            secret_ciphertext=b"must-not-leak",
            response_body="must-not-leak",
        ),
        SimpleNamespace(
            id=uuid4(),
            webhook_event_id=uuid4(),
            webhook_endpoint_id=uuid4(),
            status="failed",
            attempt_count=2,
            next_attempt_at=now,
            response_status=503,
            error_category="server_error",
            delivered_at=None,
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=uuid4(),
            webhook_event_id=uuid4(),
            webhook_endpoint_id=uuid4(),
            status="pending",
            attempt_count=0,
            next_attempt_at=now,
            response_status=None,
            error_category=None,
            delivered_at=None,
            created_at=now,
            updated_at=now,
        ),
    ]

    class FakeRepository:
        async def list_deliveries(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return rows

    service = WebhookService(
        session=object(),
        repository=FakeRepository(),
        secret_codec=object(),
        callback_validator=object(),
        environment="production",
        idempotency=object(),
    )

    page = await service.list_deliveries(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        page=1,
        page_size=2,
    )

    assert isinstance(page, WebhookDeliveryPage)
    assert page.page == 1
    assert page.has_next is True
    assert len(page.items) == 2
    assert all(
        isinstance(item, WebhookDeliveryView)
        for item in page.items
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
            "limit": 3,
            "offset": 2,
        }
    ]

    first = page.items[0]
    assert first.id == rows[0].id
    assert first.webhook_event_id == (
        rows[0].webhook_event_id
    )
    assert first.webhook_endpoint_id == (
        rows[0].webhook_endpoint_id
    )
    assert first.status == "delivered"
    assert first.attempt_count == 1
    assert first.response_status == 204
    assert first.delivered_at == now

    exposed_fields = set(vars(first))
    assert "tenant_id" not in exposed_fields
    assert "payload" not in exposed_fields
    assert "secret" not in exposed_fields
    assert "secret_ciphertext" not in exposed_fields
    assert "response_body" not in exposed_fields



@pytest.mark.asyncio
async def test_webhook_delivery_list_uses_verified_access_scope_and_cursor():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.pagination import (
        encode_page_cursor,
    )
    from api.routes.webhooks import (
        require_webhooks_read,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookDeliveryPage,
        WebhookDeliveryView,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    delivery_id = uuid4()
    event_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        10,
        16,
        0,
        tzinfo=UTC,
    )
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.read"}
        ),
    )

    class FakeService:
        async def list_deliveries(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookDeliveryPage(
                items=(
                    WebhookDeliveryView(
                        id=delivery_id,
                        webhook_event_id=event_id,
                        webhook_endpoint_id=(
                            endpoint_id
                        ),
                        status="failed",
                        attempt_count=2,
                        next_attempt_at=created_at,
                        response_status=503,
                        error_category=(
                            "server_error"
                        ),
                        delivered_at=None,
                        created_at=created_at,
                        updated_at=created_at,
                    ),
                ),
                page=1,
                has_next=True,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_read
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/webhooks/deliveries",
            params={
                "limit": 2,
                "cursor": encode_page_cursor(1),
            },
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "X-Request-ID": (
                    "webhook-delivery-list"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
            "page": 1,
            "page_size": 2,
        }
    ]

    body = response.json()
    assert body["data"] == [
        {
            "id": str(delivery_id),
            "webhook_event_id": str(event_id),
            "webhook_endpoint_id": str(
                endpoint_id
            ),
            "status": "failed",
            "attempt_count": 2,
            "next_attempt_at": (
                "2026-09-10T16:00:00Z"
            ),
            "response_status": 503,
            "error_category": "server_error",
            "delivered_at": None,
            "created_at": (
                "2026-09-10T16:00:00Z"
            ),
            "updated_at": (
                "2026-09-10T16:00:00Z"
            ),
        }
    ]
    assert body["meta"] == {
        "next_cursor": encode_page_cursor(2),
        "has_more": True,
    }
    assert body["request_id"] == (
        "webhook-delivery-list"
    )

    serialized = response.text
    assert "secret" not in serialized
    assert "response_body" not in serialized



@pytest.mark.asyncio
async def test_webhook_repository_locks_scoped_delivery_for_redelivery():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    delivery_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await (
        repository.get_delivery_for_update(
            tenant_id=tenant_id,
            delivery_id=delivery_id,
            api_client_id=api_client_id,
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
    where_clause = sql.split(
        "WHERE",
        1,
    )[1]

    assert "FROM webhook_deliveries" in sql
    assert "JOIN webhook_endpoints" in sql
    assert (
        "webhook_endpoints.tenant_id = "
        "webhook_deliveries.tenant_id"
    ) in sql
    assert (
        "webhook_endpoints.id = "
        "webhook_deliveries.webhook_endpoint_id"
    ) in sql
    assert (
        f"webhook_deliveries.tenant_id = "
        f"'{tenant_id}'"
    ) in where_clause
    assert (
        f"webhook_deliveries.id = "
        f"'{delivery_id}'"
    ) in where_clause
    assert (
        f"webhook_endpoints.api_client_id = "
        f"'{api_client_id}'"
    ) in where_clause
    assert "FOR UPDATE" in sql



@pytest.mark.asyncio
async def test_webhook_repository_requeues_locked_delivery_without_commit():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    queued_at = datetime(
        2026,
        9,
        10,
        17,
        0,
        tzinfo=UTC,
    )
    delivery = SimpleNamespace(
        status="failed",
        attempt_count=5,
        next_attempt_at=None,
        response_status=503,
        error_category="server_error",
        delivered_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flush = AsyncMock()
            self.commit = AsyncMock()

    session = FakeSession()
    repository = WebhookRepository(session)

    result = await repository.requeue_delivery(
        delivery=delivery,
        queued_at=queued_at,
    )

    assert result is delivery
    assert delivery.status == "pending"
    assert delivery.attempt_count == 0
    assert delivery.next_attempt_at == queued_at
    assert delivery.response_status is None
    assert delivery.error_category is None
    assert delivery.delivered_at is None

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()



@pytest.mark.asyncio
async def test_webhook_service_redelivers_with_idempotency_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookDeliveryView,
        WebhookService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    delivery_id = uuid4()
    event_id = uuid4()
    endpoint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        10,
        18,
        0,
        tzinfo=UTC,
    )
    calls = []

    delivery = SimpleNamespace(
        id=delivery_id,
        tenant_id=tenant_id,
        webhook_event_id=event_id,
        webhook_endpoint_id=endpoint_id,
        status="failed",
        attempt_count=5,
        next_attempt_at=None,
        response_status=503,
        error_category="server_error",
        delivered_at=None,
        created_at=created_at,
        updated_at=created_at,
    )
    reservation = SimpleNamespace(
        is_replay=False,
        response_payload=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_delivery_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return delivery

        async def requeue_delivery(
            self,
            **kwargs,
        ):
            calls.append(("requeue", kwargs))
            queued_at = kwargs["queued_at"]
            delivery.status = "pending"
            delivery.attempt_count = 0
            delivery.next_attempt_at = queued_at
            delivery.response_status = None
            delivery.error_category = None
            delivery.delivered_at = None
            return delivery

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            calls.append(("reserve", kwargs))
            return reservation

        async def complete(self, **kwargs):
            calls.append(("complete", kwargs))

    service = WebhookService(
        session=FakeSession(),
        repository=FakeRepository(),
        secret_codec=object(),
        callback_validator=object(),
        environment="production",
        idempotency=FakeIdempotency(),
    )

    result = await service.redeliver_delivery(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        delivery_id=delivery_id,
        idempotency_key="redeliver-001",
    )

    assert isinstance(result, WebhookDeliveryView)
    assert result.id == delivery_id
    assert result.status == "pending"
    assert result.attempt_count == 0
    assert result.next_attempt_at is not None
    assert result.next_attempt_at.tzinfo is UTC
    assert result.response_status is None
    assert result.error_category is None

    assert [
        name
        for name, _ in calls
    ] == [
        "reserve",
        "lock",
        "requeue",
        "complete",
        "commit",
    ]

    reserve_call = calls[0][1]
    assert reserve_call == {
        "tenant_id": tenant_id,
        "principal_type": "api_key",
        "principal_id": api_key_id,
        "operation": "webhook_delivery.redeliver",
        "idempotency_key": "redeliver-001",
        "payload": {
            "delivery_id": str(delivery_id),
        },
    }

    lock_call = calls[1][1]
    assert lock_call == {
        "tenant_id": tenant_id,
        "delivery_id": delivery_id,
        "api_client_id": api_client_id,
    }

    requeue_call = calls[2][1]
    assert requeue_call["delivery"] is delivery
    assert requeue_call["queued_at"].tzinfo is UTC

    complete_call = calls[3][1]
    assert complete_call["reservation"] is reservation
    assert complete_call["response_status"] == 200
    assert complete_call["response_payload"][
        "id"
    ] == str(delivery_id)
    assert complete_call["response_payload"][
        "status"
    ] == "pending"



@pytest.mark.asyncio
async def test_webhook_service_replays_redelivery_without_requeue():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WebhookDeliveryView,
        WebhookService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    delivery_id = uuid4()
    event_id = uuid4()
    endpoint_id = uuid4()
    queued_at = datetime(
        2026,
        9,
        10,
        19,
        0,
        tzinfo=UTC,
    )
    calls = []

    replay_payload = {
        "id": str(delivery_id),
        "webhook_event_id": str(event_id),
        "webhook_endpoint_id": str(
            endpoint_id
        ),
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": (
            queued_at.isoformat()
        ),
        "response_status": None,
        "error_category": None,
        "delivered_at": None,
        "created_at": queued_at.isoformat(),
        "updated_at": queued_at.isoformat(),
    }

    class FakeSession:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    class FakeRepository:
        async def get_delivery_for_update(
            self,
            **kwargs,
        ):
            calls.append("lock")
            raise AssertionError(
                "Replay must not lock delivery."
            )

        async def requeue_delivery(
            self,
            **kwargs,
        ):
            calls.append("requeue")
            raise AssertionError(
                "Replay must not requeue delivery."
            )

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            calls.append(("reserve", kwargs))
            return SimpleNamespace(
                is_replay=True,
                response_payload=replay_payload,
            )

        async def complete(self, **kwargs):
            calls.append("complete")
            raise AssertionError(
                "Replay must not be completed again."
            )

    service = WebhookService(
        session=FakeSession(),
        repository=FakeRepository(),
        secret_codec=object(),
        callback_validator=object(),
        environment="production",
        idempotency=FakeIdempotency(),
    )

    result = await service.redeliver_delivery(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        delivery_id=delivery_id,
        idempotency_key="redeliver-replay",
    )

    assert isinstance(result, WebhookDeliveryView)
    assert result.id == delivery_id
    assert result.webhook_event_id == event_id
    assert result.webhook_endpoint_id == (
        endpoint_id
    )
    assert result.status == "pending"
    assert result.attempt_count == 0
    assert result.next_attempt_at == queued_at

    assert len(calls) == 1
    assert calls[0][0] == "reserve"
    assert calls[0][1]["payload"] == {
        "delivery_id": str(delivery_id),
    }



@pytest.mark.asyncio
async def test_webhook_redelivery_endpoint_uses_verified_scope_and_idempotency():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_write,
    )
    from services.webhooks import (
        WebhookAccessContext,
        WebhookDeliveryView,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    delivery_id = uuid4()
    event_id = uuid4()
    endpoint_id = uuid4()
    queued_at = datetime(
        2026,
        9,
        10,
        20,
        0,
        tzinfo=UTC,
    )
    calls = []

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.write"}
        ),
    )

    class FakeService:
        async def redeliver_delivery(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return WebhookDeliveryView(
                id=delivery_id,
                webhook_event_id=event_id,
                webhook_endpoint_id=(
                    endpoint_id
                ),
                status="pending",
                attempt_count=0,
                next_attempt_at=queued_at,
                response_status=None,
                error_category=None,
                delivered_at=None,
                created_at=queued_at,
                updated_at=queued_at,
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_write
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/webhooks/deliveries/"
                f"{delivery_id}/redeliver"
            ),
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "Idempotency-Key": (
                    "manual-redelivery-001"
                ),
                "X-Request-ID": (
                    "webhook-redelivery"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "principal_type": "api_key",
            "principal_id": api_key_id,
            "api_client_id": api_client_id,
            "delivery_id": delivery_id,
            "idempotency_key": (
                "manual-redelivery-001"
            ),
        }
    ]

    body = response.json()
    assert body["data"]["id"] == str(
        delivery_id
    )
    assert body["data"][
        "webhook_event_id"
    ] == str(event_id)
    assert body["data"][
        "webhook_endpoint_id"
    ] == str(endpoint_id)
    assert body["data"]["status"] == (
        "pending"
    )
    assert body["data"]["attempt_count"] == 0
    assert body["data"][
        "next_attempt_at"
    ] == "2026-09-10T20:00:00Z"
    assert body["meta"] == {}
    assert body["request_id"] == (
        "webhook-redelivery"
    )
    assert "secret" not in response.text
    assert "response_body" not in response.text



@pytest.mark.asyncio
async def test_webhook_redelivery_maps_idempotency_reuse_to_exact_conflict():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_webhook_service,
    )
    from api.routes.webhooks import (
        require_webhooks_write,
    )
    from services.api_idempotency import (
        ApiIdempotencyKeyReusedError,
    )
    from services.webhooks import (
        WebhookAccessContext,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    delivery_id = uuid4()

    context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset(
            {"webhooks.write"}
        ),
    )

    class FakeService:
        async def redeliver_delivery(
            self,
            **kwargs,
        ):
            raise ApiIdempotencyKeyReusedError(
                "private request hash mismatch"
            )

    application = create_app()
    application.dependency_overrides[
        require_webhooks_write
    ] = lambda: context
    application.dependency_overrides[
        get_webhook_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/webhooks/deliveries/"
                f"{delivery_id}/redeliver"
            ),
            headers={
                "Authorization": (
                    "ApiKey test-webhook-key"
                ),
                "Idempotency-Key": (
                    "reused-redelivery-key"
                ),
                "X-Request-ID": (
                    "webhook-redelivery-conflict"
                ),
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": (
                "Idempotency key was reused with "
                "a different request."
            ),
            "request_id": (
                "webhook-redelivery-conflict"
            ),
        }
    }
    assert (
        "private request hash mismatch"
        not in response.text
    )



@pytest.mark.asyncio
async def test_webhook_repository_creates_tenant_event_without_commit():
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from database.models import WebhookEvent
    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    payload = {
        "service_order_id": str(uuid4()),
        "status": "completed",
    }

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush = AsyncMock()
            self.commit = AsyncMock()

        def add(self, value):
            self.added.append(value)

    session = FakeSession()
    repository = WebhookRepository(session)

    event = await repository.create_event(
        tenant_id=tenant_id,
        event_type="service_order.completed",
        payload=payload,
    )

    assert isinstance(event, WebhookEvent)
    assert event.tenant_id == tenant_id
    assert event.event_type == (
        "service_order.completed"
    )
    assert event.payload == payload
    assert event.payload is not payload

    assert session.added == [event]
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()



@pytest.mark.asyncio
async def test_webhook_repository_lists_active_subscribed_endpoints():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
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
    repository = WebhookRepository(session)

    result = await (
        repository.list_active_subscribed_endpoints(
            tenant_id=tenant_id,
            event_type="service_order.completed",
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
    where_clause = sql.split(
        "WHERE",
        1,
    )[1]

    assert "FROM webhook_endpoints" in sql
    assert "JOIN webhook_subscriptions" in sql
    assert (
        "webhook_subscriptions.tenant_id = "
        "webhook_endpoints.tenant_id"
    ) in sql
    assert (
        "webhook_subscriptions."
        "webhook_endpoint_id = "
        "webhook_endpoints.id"
    ) in sql
    assert (
        f"webhook_endpoints.tenant_id = "
        f"'{tenant_id}'"
    ) in where_clause
    assert (
        "webhook_endpoints.status = 'active'"
    ) in where_clause
    assert (
        "webhook_subscriptions.event_type = "
        "'service_order.completed'"
    ) in where_clause



@pytest.mark.asyncio
async def test_webhook_repository_creates_pending_deliveries_without_commit():
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from database.models import WebhookDelivery
    from database.repositories.webhooks import (
        WebhookRepository,
    )

    tenant_id = uuid4()
    event_id = uuid4()
    endpoint_ids = (
        uuid4(),
        uuid4(),
    )
    queued_at = datetime(
        2026,
        9,
        10,
        21,
        0,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush = AsyncMock()
            self.commit = AsyncMock()

        def add_all(self, values):
            self.added.extend(values)

    session = FakeSession()
    repository = WebhookRepository(session)

    deliveries = await (
        repository.create_deliveries(
            tenant_id=tenant_id,
            webhook_event_id=event_id,
            webhook_endpoint_ids=(
                endpoint_ids
            ),
            queued_at=queued_at,
        )
    )

    assert isinstance(deliveries, tuple)
    assert len(deliveries) == 2
    assert session.added == list(
        deliveries
    )

    for delivery, endpoint_id in zip(
        deliveries,
        endpoint_ids,
        strict=True,
    ):
        assert isinstance(
            delivery,
            WebhookDelivery,
        )
        assert delivery.tenant_id == tenant_id
        assert delivery.webhook_event_id == (
            event_id
        )
        assert delivery.webhook_endpoint_id == (
            endpoint_id
        )
        assert delivery.status == "pending"
        assert delivery.attempt_count == 0
        assert delivery.next_attempt_at == (
            queued_at
        )
        assert delivery.response_status is None
        assert delivery.error_category is None
        assert delivery.delivered_at is None

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()



@pytest.mark.asyncio
async def test_webhook_event_publisher_creates_event_and_fanout_without_commit():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from services.webhooks import (
        WebhookEventPublisher,
    )

    tenant_id = uuid4()
    event_id = uuid4()
    endpoint_ids = (
        uuid4(),
        uuid4(),
    )
    created_at = datetime(
        2026,
        9,
        10,
        22,
        0,
        tzinfo=UTC,
    )
    payload = {
        "service_order_id": str(uuid4()),
        "status": "completed",
    }
    event = SimpleNamespace(
        id=event_id,
        tenant_id=tenant_id,
        event_type="service_order.completed",
        payload=dict(payload),
        created_at=created_at,
    )
    endpoints = [
        SimpleNamespace(id=value)
        for value in endpoint_ids
    ]
    deliveries = (
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    )
    calls = []

    class FakeRepository:
        def __init__(self):
            self.session = SimpleNamespace(
                commit=AsyncMock(),
            )

        async def create_event(self, **kwargs):
            calls.append(("event", kwargs))
            return event

        async def list_active_subscribed_endpoints(
            self,
            **kwargs,
        ):
            calls.append(("endpoints", kwargs))
            return endpoints

        async def create_deliveries(
            self,
            **kwargs,
        ):
            calls.append(("deliveries", kwargs))
            return deliveries

    repository = FakeRepository()
    publisher = WebhookEventPublisher(
        repository=repository,
    )

    result = await publisher.publish(
        tenant_id=tenant_id,
        event_type="service_order.completed",
        payload=payload,
    )

    assert result is event
    assert [
        name
        for name, _ in calls
    ] == [
        "event",
        "endpoints",
        "deliveries",
    ]

    assert calls[0][1] == {
        "tenant_id": tenant_id,
        "event_type": (
            "service_order.completed"
        ),
        "payload": payload,
    }
    assert calls[1][1] == {
        "tenant_id": tenant_id,
        "event_type": (
            "service_order.completed"
        ),
    }

    delivery_call = calls[2][1]
    assert delivery_call[
        "tenant_id"
    ] == tenant_id
    assert delivery_call[
        "webhook_event_id"
    ] == event_id
    assert delivery_call[
        "webhook_endpoint_ids"
    ] == endpoint_ids
    assert delivery_call[
        "queued_at"
    ].tzinfo is UTC

    repository.session.commit.assert_not_awaited()



@pytest.mark.asyncio
async def test_webhook_event_publisher_rejects_payload_over_256_kb():
    from uuid import uuid4

    import pytest

    from services.webhooks import (
        WEBHOOK_MAX_PAYLOAD_BYTES,
        WebhookEventPublisher,
        WebhookPayloadTooLargeError,
    )

    calls = []

    class FakeRepository:
        async def create_event(self, **kwargs):
            calls.append(("event", kwargs))
            raise AssertionError(
                "Oversized payload must not "
                "reach repository."
            )

        async def list_active_subscribed_endpoints(
            self,
            **kwargs,
        ):
            calls.append(("endpoints", kwargs))
            raise AssertionError(
                "Oversized payload must not "
                "create fan-out."
            )

        async def create_deliveries(
            self,
            **kwargs,
        ):
            calls.append(("deliveries", kwargs))
            raise AssertionError(
                "Oversized payload must not "
                "create deliveries."
            )

    publisher = WebhookEventPublisher(
        repository=FakeRepository(),
    )

    with pytest.raises(
        WebhookPayloadTooLargeError
    ):
        await publisher.publish(
            tenant_id=uuid4(),
            event_type=(
                "service_order.completed"
            ),
            payload={
                "data": "x" * (
                    WEBHOOK_MAX_PAYLOAD_BYTES
                ),
            },
        )

    assert calls == []



def test_webhook_delivery_runtime_uses_release_limits():
    from services.webhooks import (
        WEBHOOK_HTTP_TIMEOUT_SECONDS,
        WEBHOOK_MAX_RETRIES,
        WEBHOOK_MAX_TOTAL_ATTEMPTS,
        WEBHOOK_RETRY_DELAYS_SECONDS,
    )

    assert WEBHOOK_MAX_RETRIES == 5
    assert WEBHOOK_MAX_TOTAL_ATTEMPTS == 6
    assert WEBHOOK_RETRY_DELAYS_SECONDS == (
        60,
        5 * 60,
        30 * 60,
        2 * 60 * 60,
        12 * 60 * 60,
    )
    assert (
        len(WEBHOOK_RETRY_DELAYS_SECONDS)
        == WEBHOOK_MAX_RETRIES
    )
    assert WEBHOOK_HTTP_TIMEOUT_SECONDS == 10



def test_webhook_event_body_matches_release_wire_contract():
    import json
    from datetime import UTC, datetime
    from uuid import uuid4

    from services.webhooks import (
        build_webhook_event_body,
    )

    event_id = uuid4()
    created_at = datetime(
        2026,
        9,
        10,
        23,
        0,
        tzinfo=UTC,
    )
    data = {
        "service_order_id": str(uuid4()),
        "status": "completed",
    }

    raw_body = build_webhook_event_body(
        event_id=event_id,
        event_type="service_order.completed",
        created_at=created_at,
        data=data,
    )
    repeated_raw_body = (
        build_webhook_event_body(
            event_id=event_id,
            event_type=(
                "service_order.completed"
            ),
            created_at=created_at,
            data=data,
        )
    )

    assert isinstance(raw_body, bytes)
    assert raw_body == repeated_raw_body
    assert b"\n" not in raw_body
    assert b": " not in raw_body

    decoded = json.loads(
        raw_body.decode("utf-8")
    )
    assert decoded == {
        "id": str(event_id),
        "type": "service_order.completed",
        "created_at": (
            "2026-09-10T23:00:00Z"
        ),
        "data": data,
    }



@pytest.mark.asyncio
async def test_webhook_delivery_sender_uses_secure_signed_http_contract():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WEBHOOK_HTTP_TIMEOUT_SECONDS,
        WebhookDeliverySender,
        WebhookSigner,
        build_webhook_event_body,
    )

    now = datetime(
        2026,
        9,
        11,
        0,
        0,
        tzinfo=UTC,
    )
    event = SimpleNamespace(
        id=uuid4(),
        event_type="service_order.completed",
        created_at=now,
        payload={
            "service_order_id": str(
                uuid4()
            ),
            "status": "completed",
        },
    )
    endpoint = SimpleNamespace(
        callback_url=(
            "https://hooks.example.com/events"
        ),
        secret_ciphertext=b"encrypted-secret",
    )
    calls = []

    class FakeValidator:
        async def validate(
            self,
            callback_url,
            *,
            environment,
        ):
            calls.append(
                (
                    "validate",
                    callback_url,
                    environment,
                )
            )
            return callback_url

    class FakeSecretCodec:
        def decrypt(self, value):
            calls.append(("decrypt", value))
            return "webhook-secret"

    class FakeResponse:
        status_code = 204

        @property
        def text(self):
            raise AssertionError(
                "Webhook response body must "
                "not be read."
            )

        @property
        def content(self):
            raise AssertionError(
                "Webhook response body must "
                "not be stored."
            )

    class FakeHttpClient:
        async def post(self, url, **kwargs):
            calls.append(
                ("post", url, kwargs)
            )
            return FakeResponse()

    sender = WebhookDeliverySender(
        http_client=FakeHttpClient(),
        callback_validator=FakeValidator(),
        secret_codec=FakeSecretCodec(),
        signer=WebhookSigner(),
        environment="production",
        clock=lambda: now,
    )

    result = await sender.send(
        event=event,
        endpoint=endpoint,
    )

    raw_body = build_webhook_event_body(
        event_id=event.id,
        event_type=event.event_type,
        created_at=event.created_at,
        data=event.payload,
    )
    timestamp = int(now.timestamp())
    expected_signature = (
        WebhookSigner.sign(
            secret="webhook-secret",
            timestamp=timestamp,
            raw_body=raw_body,
        )
    )

    assert result.succeeded is True
    assert result.status_code == 204
    assert result.error_category is None

    assert calls[0] == (
        "validate",
        endpoint.callback_url,
        "production",
    )
    assert calls[1] == (
        "decrypt",
        endpoint.secret_ciphertext,
    )
    assert calls[2][0:2] == (
        "post",
        endpoint.callback_url,
    )

    request_kwargs = calls[2][2]
    assert request_kwargs["content"] == (
        raw_body
    )
    assert request_kwargs["timeout"] == (
        WEBHOOK_HTTP_TIMEOUT_SECONDS
    )
    assert request_kwargs[
        "follow_redirects"
    ] is False
    assert request_kwargs["headers"] == {
        "Content-Type": "application/json",
        "X-SGHR-Webhook-Id": str(
            event.id
        ),
        "X-SGHR-Webhook-Timestamp": str(
            timestamp
        ),
        "X-SGHR-Webhook-Signature": (
            expected_signature
        ),
    }



@pytest.mark.asyncio
async def test_webhook_repository_marks_success_and_resets_endpoint_failures():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    delivered_at = datetime(
        2026,
        9,
        11,
        1,
        0,
        tzinfo=UTC,
    )
    delivery = SimpleNamespace(
        status="processing",
        attempt_count=1,
        next_attempt_at=delivered_at,
        response_status=None,
        error_category=None,
        delivered_at=None,
    )
    endpoint = SimpleNamespace(
        status="active",
        consecutive_failures=7,
    )

    class FakeSession:
        def __init__(self):
            self.flush = AsyncMock()
            self.commit = AsyncMock()

    session = FakeSession()
    repository = WebhookRepository(session)

    result = await (
        repository.mark_delivery_succeeded(
            delivery=delivery,
            endpoint=endpoint,
            response_status=204,
            delivered_at=delivered_at,
        )
    )

    assert result is delivery
    assert delivery.status == "delivered"
    assert delivery.attempt_count == 2
    assert delivery.next_attempt_at is None
    assert delivery.response_status == 204
    assert delivery.error_category is None
    assert delivery.delivered_at == delivered_at

    assert endpoint.status == "active"
    assert endpoint.consecutive_failures == 0

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    assert not hasattr(
        delivery,
        "response_body",
    )



@pytest.mark.asyncio
async def test_webhook_repository_suspends_endpoint_after_twentieth_failure():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    retry_at = datetime(
        2026,
        9,
        11,
        2,
        0,
        tzinfo=UTC,
    )
    delivery = SimpleNamespace(
        status="processing",
        attempt_count=2,
        next_attempt_at=None,
        response_status=None,
        error_category=None,
        delivered_at=None,
    )
    endpoint = SimpleNamespace(
        status="active",
        consecutive_failures=19,
    )

    class FakeSession:
        def __init__(self):
            self.flush = AsyncMock()
            self.commit = AsyncMock()

    session = FakeSession()
    repository = WebhookRepository(session)

    result = await repository.mark_delivery_failed(
        delivery=delivery,
        endpoint=endpoint,
        response_status=503,
        error_category="http_error",
        retry_at=retry_at,
    )

    assert result is delivery
    assert delivery.status == "failed"
    assert delivery.attempt_count == 3
    assert delivery.next_attempt_at == retry_at
    assert delivery.response_status == 503
    assert delivery.error_category == (
        "http_error"
    )
    assert delivery.delivered_at is None

    assert endpoint.consecutive_failures == 20
    assert endpoint.status == "suspended"

    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    assert not hasattr(
        delivery,
        "response_body",
    )



@pytest.mark.asyncio
async def test_webhook_repository_locks_due_delivery_context_skip_locked():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    now = datetime(
        2026,
        9,
        11,
        3,
        0,
        tzinfo=UTC,
    )
    expected = (
        object(),
        object(),
        object(),
    )

    class FakeResult:
        def one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WebhookRepository(session)

    result = await (
        repository
        .get_due_delivery_context_for_update(
            now=now,
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
    where_clause = sql.split(
        "WHERE",
        1,
    )[1]

    assert (
        "FROM webhook_deliveries"
    ) in sql
    assert "JOIN webhook_events" in sql
    assert "JOIN webhook_endpoints" in sql

    assert (
        "webhook_events.tenant_id = "
        "webhook_deliveries.tenant_id"
    ) in sql
    assert (
        "webhook_events.id = "
        "webhook_deliveries.webhook_event_id"
    ) in sql
    assert (
        "webhook_endpoints.tenant_id = "
        "webhook_deliveries.tenant_id"
    ) in sql
    assert (
        "webhook_endpoints.id = "
        "webhook_deliveries.webhook_endpoint_id"
    ) in sql

    assert (
        "webhook_endpoints.status = 'active'"
    ) in where_clause
    assert (
        "webhook_deliveries.status IN "
        "('pending', 'failed')"
    ) in where_clause
    assert (
        "webhook_deliveries.next_attempt_at "
        "IS NOT NULL"
    ) in where_clause
    assert (
        "webhook_deliveries.next_attempt_at <= "
        "'2026-09-11 03:00:00+00:00'"
    ) in where_clause

    assert "LIMIT 1" in sql
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql



@pytest.mark.asyncio
async def test_webhook_delivery_worker_commits_successful_attempt_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.webhooks import (
        WebhookDeliveryAttemptResult,
        WebhookDeliveryWorker,
    )

    now = datetime(
        2026,
        9,
        11,
        4,
        0,
        tzinfo=UTC,
    )
    delivery = SimpleNamespace(
        status="pending",
        attempt_count=0,
    )
    event = SimpleNamespace()
    endpoint = SimpleNamespace()
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_due_delivery_context_for_update(
            self,
            **kwargs,
        ):
            calls.append(("claim", kwargs))
            return (
                delivery,
                event,
                endpoint,
            )

        async def mark_delivery_succeeded(
            self,
            **kwargs,
        ):
            calls.append(("success", kwargs))
            return delivery

        async def mark_delivery_failed(
            self,
            **kwargs,
        ):
            calls.append(("failure", kwargs))
            raise AssertionError(
                "Successful attempt must not "
                "be marked failed."
            )

    class FakeSender:
        async def send(self, **kwargs):
            calls.append(("send", kwargs))
            return WebhookDeliveryAttemptResult(
                succeeded=True,
                status_code=204,
                error_category=None,
            )

    worker = WebhookDeliveryWorker(
        session=FakeSession(),
        repository=FakeRepository(),
        sender=FakeSender(),
        clock=lambda: now,
    )

    processed = await worker.run_once()

    assert processed is True
    assert [
        name
        for name, _ in calls
    ] == [
        "claim",
        "send",
        "success",
        "commit",
    ]
    assert calls[0][1] == {
        "now": now,
    }
    assert calls[1][1] == {
        "event": event,
        "endpoint": endpoint,
    }
    assert calls[2][1] == {
        "delivery": delivery,
        "endpoint": endpoint,
        "response_status": 204,
        "delivered_at": now,
    }



@pytest.mark.asyncio
async def test_webhook_repository_deletes_expired_events_for_retention():
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from sqlalchemy.dialects import postgresql

    from database.repositories.webhooks import (
        WebhookRepository,
    )

    cutoff = datetime(
        2026,
        9,
        11,
        5,
        0,
        tzinfo=UTC,
    )

    class FakeResult:
        rowcount = 7

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.commit = AsyncMock()

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WebhookRepository(session)

    deleted = await (
        repository.delete_expired_events(
            cutoff=cutoff,
        )
    )

    assert deleted == 7
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert sql.startswith(
        "DELETE FROM webhook_events"
    )
    assert (
        "webhook_events.expires_at <= "
        "'2026-09-11 05:00:00+00:00'"
    ) in sql

    session.commit.assert_not_awaited()



@pytest.mark.asyncio
async def test_webhook_retention_service_purges_expired_events_atomically():
    from datetime import UTC, datetime

    from services.webhooks import (
        WebhookRetentionService,
    )

    now = datetime(
        2026,
        9,
        11,
        6,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def delete_expired_events(
            self,
            **kwargs,
        ):
            calls.append(("delete", kwargs))
            return 7

    service = WebhookRetentionService(
        session=FakeSession(),
        repository=FakeRepository(),
        clock=lambda: now,
    )

    deleted = await (
        service.purge_expired_events()
    )

    assert deleted == 7
    assert calls == [
        (
            "delete",
            {
                "cutoff": now,
            },
        ),
        ("commit", {}),
    ]



def test_webhook_retention_has_independent_periodic_job():
    from pathlib import Path

    script_path = Path(
        "scripts/cleanup_webhook_events.py"
    )
    service_path = Path(
        "deploy/systemd/"
        "sghr-webhook-retention.service"
    )
    timer_path = Path(
        "deploy/systemd/"
        "sghr-webhook-retention.timer"
    )

    assert script_path.is_file()
    assert service_path.is_file()
    assert timer_path.is_file()

    script = script_path.read_text(
        encoding="utf-8-sig"
    )
    assert "WebhookRetentionService" in script
    assert "WebhookRepository" in script
    assert "async_session" in script
    assert "asyncio.run" in script

    service = service_path.read_text(
        encoding="utf-8-sig"
    )
    assert "Type=oneshot" in service
    assert (
        "scripts/cleanup_webhook_events.py"
        in service
    )
    assert (
        "EnvironmentFile=/opt/sghr/.env"
        in service
    )

    timer = timer_path.read_text(
        encoding="utf-8-sig"
    )
    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer
    assert (
        "Unit=sghr-webhook-retention.service"
        in timer
    )
    assert "WantedBy=timers.target" in timer



@pytest.mark.asyncio
async def test_webhook_management_rate_limit_uses_release_actor_and_api_key_limits():
    from uuid import uuid4

    import pytest

    from services.api_rate_limits import (
        API_RATE_LIMIT_WINDOW_SECONDS,
        WEBHOOK_MANAGEMENT_RATE_LIMIT,
        ApiRequestRateLimitExceededError,
        ApiRequestRateLimitService,
    )

    assert WEBHOOK_MANAGEMENT_RATE_LIMIT == 60
    assert API_RATE_LIMIT_WINDOW_SECONDS == 60

    class FakeStore:
        def __init__(self):
            self.counts = {}
            self.calls = []

        async def increment(
            self,
            *,
            key,
            window_seconds,
        ):
            self.calls.append(
                {
                    "key": key,
                    "window_seconds": window_seconds,
                }
            )
            self.counts[key] = (
                self.counts.get(key, 0) + 1
            )
            return self.counts[key]

    tenant_id = uuid4()
    actor_id = uuid4()
    api_key_id = uuid4()
    store = FakeStore()
    service = ApiRequestRateLimitService(
        store=store
    )

    for _ in range(60):
        await service.ensure_webhook_management_allowed(
            tenant_id=tenant_id,
            principal_type="user",
            principal_id=actor_id,
        )

    with pytest.raises(
        ApiRequestRateLimitExceededError
    ):
        await service.ensure_webhook_management_allowed(
            tenant_id=tenant_id,
            principal_type="user",
            principal_id=actor_id,
        )

    await service.ensure_webhook_management_allowed(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
    )

    actor_key = store.calls[0]["key"]
    api_key_scope = store.calls[-1]

    assert str(tenant_id) in actor_key
    assert str(actor_id) in actor_key
    assert "user" in actor_key

    assert str(tenant_id) in api_key_scope["key"]
    assert str(api_key_id) in api_key_scope["key"]
    assert "api_key" in api_key_scope["key"]
    assert api_key_scope["key"] != actor_key

    assert {
        call["window_seconds"]
        for call in store.calls
    } == {60}



@pytest.mark.asyncio
async def test_redis_api_rate_limit_store_increments_with_atomic_window():
    from services.api_rate_limits import (
        RedisApiRequestRateLimitStore,
    )

    calls = []

    class FakeRedis:
        async def eval(
            self,
            script,
            numkeys,
            *values,
        ):
            calls.append(
                {
                    "script": script,
                    "numkeys": numkeys,
                    "values": values,
                }
            )
            return 7

    store = RedisApiRequestRateLimitStore(
        client=FakeRedis()
    )

    result = await store.increment(
        key=(
            "api-rate-limit:tenant:test:"
            "webhook-management:user:actor"
        ),
        window_seconds=60,
    )

    assert result == 7
    assert len(calls) == 1

    call = calls[0]
    normalized_script = " ".join(
        call["script"].upper().split()
    )

    assert call["numkeys"] == 1
    assert call["values"] == (
        (
            "api-rate-limit:tenant:test:"
            "webhook-management:user:actor"
        ),
        60,
    )
    assert "INCR" in normalized_script
    assert "EXPIRE" in normalized_script
    assert "CURRENT == 1" in normalized_script



def test_api_rate_limit_settings_require_redis_in_production():
    import pytest

    from api.settings import (
        ApiConfigurationError,
        ApiRateLimitSettings,
    )

    settings = ApiRateLimitSettings.from_env(
        {
            "API_ENVIRONMENT": "production",
            "REDIS_URL": (
                "rediss://redis.example.com:6379/0"
            ),
        }
    )

    assert settings.environment == "production"
    assert settings.redis_url == (
        "rediss://redis.example.com:6379/0"
    )

    with pytest.raises(
        ApiConfigurationError
    ):
        ApiRateLimitSettings.from_env(
            {
                "API_ENVIRONMENT": "production",
                "REDIS_URL": "",
            }
        )

    sandbox = ApiRateLimitSettings.from_env(
        {
            "API_ENVIRONMENT": "sandbox",
            "REDIS_URL": "",
        }
    )

    assert sandbox.environment == "sandbox"
    assert sandbox.redis_url is None



@pytest.mark.asyncio
async def test_webhook_management_rate_limit_guard_uses_verified_access_context():
    from uuid import uuid4

    from api.auth import (
        require_webhook_management_rate_limit,
    )
    from services.webhooks import (
        WebhookAccessContext,
    )

    calls = []

    class FakeRateLimitService:
        async def ensure_webhook_management_allowed(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return 1

    service = FakeRateLimitService()
    guard = require_webhook_management_rate_limit(
        context_dependency=lambda: None,
        service_dependency=lambda: None,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_key_id = uuid4()
    api_client_id = uuid4()

    user_context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="user",
        principal_id=user_id,
        api_client_id=None,
        grants=frozenset({"webhooks.read"}),
    )
    api_key_context = WebhookAccessContext(
        tenant_id=tenant_id,
        principal_type="api_key",
        principal_id=api_key_id,
        api_client_id=api_client_id,
        grants=frozenset({"webhooks.read"}),
    )

    assert await guard(
        context=user_context,
        service=service,
    ) is user_context

    assert await guard(
        context=api_key_context,
        service=service,
    ) is api_key_context

    assert calls == [
        {
            "tenant_id": tenant_id,
            "principal_type": "user",
            "principal_id": user_id,
        },
        {
            "tenant_id": tenant_id,
            "principal_type": "api_key",
            "principal_id": api_key_id,
        },
    ]



@pytest.mark.asyncio
async def test_webhook_management_rate_limit_guard_returns_exact_429_contract():
    from uuid import uuid4

    import pytest

    from api.auth import (
        require_webhook_management_rate_limit,
    )
    from api.errors import ApiHttpError
    from services.api_rate_limits import (
        ApiRequestRateLimitExceededError,
    )
    from services.webhooks import (
        WebhookAccessContext,
    )

    class FakeRateLimitService:
        async def ensure_webhook_management_allowed(
            self,
            **kwargs,
        ):
            raise ApiRequestRateLimitExceededError(
                "Private Redis counter details."
            )

    context = WebhookAccessContext(
        tenant_id=uuid4(),
        principal_type="user",
        principal_id=uuid4(),
        api_client_id=None,
        grants=frozenset({"webhooks.read"}),
    )

    guard = require_webhook_management_rate_limit(
        context_dependency=lambda: None,
        service_dependency=lambda: None,
    )

    with pytest.raises(ApiHttpError) as captured:
        await guard(
            context=context,
            service=FakeRateLimitService(),
        )

    error = captured.value

    assert error.status_code == 429
    assert error.code == "RATE_LIMIT_EXCEEDED"
    assert error.message == "Rate limit exceeded."
    assert "Redis" not in error.message



def test_api_rate_limit_factory_uses_redis_in_production_and_memory_only_in_sandbox():
    import pytest

    from services.api_rate_limits import (
        InMemoryApiRequestRateLimitStore,
        RedisApiRequestRateLimitStore,
        build_api_request_rate_limit_service,
    )

    redis_client = object()

    production_service = (
        build_api_request_rate_limit_service(
            environment="production",
            redis_client=redis_client,
        )
    )

    assert isinstance(
        production_service.store,
        RedisApiRequestRateLimitStore,
    )
    assert (
        production_service.store.client
        is redis_client
    )

    with pytest.raises(ValueError):
        build_api_request_rate_limit_service(
            environment="production",
            redis_client=None,
        )

    sandbox_service = (
        build_api_request_rate_limit_service(
            environment="sandbox",
            redis_client=None,
        )
    )

    assert isinstance(
        sandbox_service.store,
        InMemoryApiRequestRateLimitStore,
    )



def test_api_rate_limit_dependency_reuses_shared_sandbox_service(
    monkeypatch,
):
    from api.dependencies import (
        get_api_request_rate_limit_service,
    )
    from services.api_rate_limits import (
        ApiRequestRateLimitService,
        InMemoryApiRequestRateLimitStore,
    )

    monkeypatch.setenv(
        "API_ENVIRONMENT",
        "sandbox",
    )
    monkeypatch.delenv(
        "REDIS_URL",
        raising=False,
    )

    get_api_request_rate_limit_service.cache_clear()

    first = get_api_request_rate_limit_service()
    second = get_api_request_rate_limit_service()

    assert first is second
    assert isinstance(
        first,
        ApiRequestRateLimitService,
    )
    assert isinstance(
        first.store,
        InMemoryApiRequestRateLimitStore,
    )

    get_api_request_rate_limit_service.cache_clear()



def test_api_runtime_declares_redis_for_production_rate_limits():
    from pathlib import Path

    requirements = (
        Path("requirements.txt")
        .read_text(encoding="utf-8-sig")
        .splitlines()
    )

    normalized = [
        line.strip().lower()
        for line in requirements
        if line.strip()
        and not line.lstrip().startswith("#")
    ]

    assert any(
        line == "redis"
        or line.startswith("redis>")
        or line.startswith("redis=")
        or line.startswith("redis<")
        or line.startswith("redis[")
        for line in normalized
    )



def test_all_webhook_management_routes_use_release_rate_limit_guard():
    from api.routes.webhooks import (
        router as webhooks_router,
    )

    expected_routes = {
        ("GET", "/webhooks/endpoints"),
        ("POST", "/webhooks/endpoints"),
        (
            "GET",
            "/webhooks/endpoints/{endpoint_id}",
        ),
        (
            "PATCH",
            "/webhooks/endpoints/{endpoint_id}",
        ),
        (
            "DELETE",
            "/webhooks/endpoints/{endpoint_id}",
        ),
        ("GET", "/webhooks/deliveries"),
        (
            "POST",
            (
                "/webhooks/deliveries/"
                "{delivery_id}/redeliver"
            ),
        ),
    }

    discovered = set()
    unprotected = []

    for route in webhooks_router.routes:
        path_value = getattr(route, "path", "")

        for method in getattr(
            route,
            "methods",
            set(),
        ):
            if method in {
                "HEAD",
                "OPTIONS",
            }:
                continue

            route_key = (method, path_value)
            discovered.add(route_key)

            dependency_names = set()
            stack = list(
                getattr(
                    route.dependant,
                    "dependencies",
                    (),
                )
            )

            while stack:
                dependency = stack.pop()
                call = getattr(
                    dependency,
                    "call",
                    None,
                )
                dependency_names.add(
                    getattr(call, "__name__", "")
                )
                stack.extend(
                    getattr(
                        dependency,
                        "dependencies",
                        (),
                    )
                )

            if (
                "webhook_rate_limit_dependency"
                not in dependency_names
            ):
                unprotected.append(route_key)

    assert discovered == expected_routes
    assert unprotected == []



@pytest.mark.asyncio
async def test_webhook_callback_validator_returns_pinned_public_target():
    from services.webhooks import (
        WebhookCallbackUrlValidator,
        WebhookResolvedTarget,
    )

    resolver_calls = []

    async def resolver(hostname):
        resolver_calls.append(hostname)
        return (
            "93.184.216.34",
            "93.184.216.35",
        )

    validator = WebhookCallbackUrlValidator(
        resolver=resolver
    )

    target = await validator.resolve_target(
        (
            "https://hooks.example.com:8443/"
            "sghr/events?source=api"
        ),
        environment="production",
    )

    assert isinstance(
        target,
        WebhookResolvedTarget,
    )
    assert target.callback_url == (
        "https://hooks.example.com:8443/"
        "sghr/events?source=api"
    )
    assert target.hostname == (
        "hooks.example.com"
    )
    assert target.port == 8443
    assert target.addresses == (
        "93.184.216.34",
        "93.184.216.35",
    )
    assert resolver_calls == [
        "hooks.example.com",
    ]


@pytest.mark.asyncio
async def test_webhook_delivery_sender_uses_pinned_resolved_target():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.webhooks import (
        WEBHOOK_HTTP_TIMEOUT_SECONDS,
        WebhookDeliverySender,
        WebhookResolvedTarget,
    )

    now = datetime(
        2026,
        9,
        10,
        12,
        0,
        tzinfo=UTC,
    )
    target = WebhookResolvedTarget(
        callback_url=(
            "https://hooks.example.com:8443/events"
        ),
        hostname="hooks.example.com",
        port=8443,
        addresses=("93.184.216.34",),
    )
    calls = []

    class FakeValidator:
        async def resolve_target(
            self,
            callback_url,
            *,
            environment,
        ):
            calls.append(
                (
                    "resolve",
                    callback_url,
                    environment,
                )
            )
            return target

        async def validate(self, *args, **kwargs):
            raise AssertionError(
                "Sender must use the pinned target."
            )

    class FakeSecretCodec:
        def decrypt(self, ciphertext):
            return "private-secret"

    class FakeSigner:
        def build_headers(self, **kwargs):
            return {
                "X-SGHR-Webhook-Id": (
                    kwargs["webhook_id"]
                ),
            }

    class FakePinnedHttpClient:
        async def post_pinned(self, **kwargs):
            calls.append(("post_pinned", kwargs))
            return SimpleNamespace(status_code=202)

        async def post(self, *args, **kwargs):
            raise AssertionError(
                "Unpinned HTTP request is forbidden."
            )

    event = SimpleNamespace(
        id=uuid4(),
        event_type="service_order.completed",
        created_at=now,
        payload={"status": "completed"},
    )
    endpoint = SimpleNamespace(
        callback_url=target.callback_url,
        secret_ciphertext=b"encrypted",
    )

    sender = WebhookDeliverySender(
        http_client=FakePinnedHttpClient(),
        callback_validator=FakeValidator(),
        secret_codec=FakeSecretCodec(),
        signer=FakeSigner(),
        environment="production",
        clock=lambda: now,
    )

    result = await sender.send(
        event=event,
        endpoint=endpoint,
    )

    assert result.succeeded is True
    assert result.status_code == 202
    assert calls[0] == (
        "resolve",
        endpoint.callback_url,
        "production",
    )

    name, request = calls[1]
    assert name == "post_pinned"
    assert request["target"] is target
    assert request["timeout"] == (
        WEBHOOK_HTTP_TIMEOUT_SECONDS
    )
    assert request["follow_redirects"] is False


@pytest.mark.asyncio
async def test_webhook_pinned_http_client_uses_only_verified_addresses(
    monkeypatch,
):
    import services.webhooks as webhook_module
    from services.webhooks import (
        WebhookPinnedHttpClient,
        WebhookResolvedTarget,
    )

    calls = []
    target = WebhookResolvedTarget(
        callback_url=(
            "https://hooks.example.com:8443/events"
        ),
        hostname="hooks.example.com",
        port=8443,
        addresses=(
            "93.184.216.34",
            "93.184.216.35",
        ),
    )

    class FakeConnector:
        def __init__(self, **kwargs):
            calls.append(("connector", kwargs))
            self.resolver = kwargs["resolver"]

    class FakeTimeout:
        def __init__(self, *, total):
            calls.append(("timeout", total))
            self.total = total

    class FakeResponse:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return False

        @property
        def content(self):
            raise AssertionError(
                "Webhook response body must not be read."
            )

    class FakeSession:
        def __init__(self, **kwargs):
            calls.append(("session", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return False

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(
        webhook_module.aiohttp,
        "TCPConnector",
        FakeConnector,
    )
    monkeypatch.setattr(
        webhook_module.aiohttp,
        "ClientTimeout",
        FakeTimeout,
    )
    monkeypatch.setattr(
        webhook_module.aiohttp,
        "ClientSession",
        FakeSession,
    )

    client = WebhookPinnedHttpClient()
    response = await client.post_pinned(
        target=target,
        content=b'{"event":"test"}',
        headers={"Content-Type": "application/json"},
        timeout=10,
        follow_redirects=False,
    )

    assert response.status_code == 204

    connector_options = calls[0][1]
    assert connector_options["use_dns_cache"] is False

    resolver = connector_options["resolver"]
    resolved = await resolver.resolve(
        target.hostname,
        target.port,
    )
    assert tuple(
        item["host"]
        for item in resolved
    ) == target.addresses

    with pytest.raises(OSError):
        await resolver.resolve(
            "changed.example.com",
            target.port,
        )

    post_call = next(
        call
        for call in calls
        if call[0] == "post"
    )
    assert post_call[1] == target.callback_url
    assert post_call[2]["data"] == b'{"event":"test"}'
    assert post_call[2]["allow_redirects"] is False
    assert post_call[2]["timeout"].total == 10


def test_webhook_delivery_sender_fails_closed_with_unpinned_production_transport():
    from services.webhooks import (
        WebhookCallbackUrlValidator,
        WebhookDeliverySender,
    )

    class UnpinnedHttpClient:
        async def post(self, *args, **kwargs):
            raise AssertionError(
                "Unpinned request must never run."
            )

    with pytest.raises(
        ValueError,
        match="DNS-pinned",
    ):
        WebhookDeliverySender(
            http_client=UnpinnedHttpClient(),
            callback_validator=(
                WebhookCallbackUrlValidator()
            ),
            secret_codec=object(),
            signer=object(),
            environment="production",
        )


def test_webhook_delivery_sender_factory_uses_dns_pinned_transport():
    from cryptography.fernet import Fernet

    from services.webhooks import (
        WebhookCallbackUrlValidator,
        WebhookDeliverySender,
        WebhookPinnedHttpClient,
        build_webhook_delivery_sender,
    )

    sender = build_webhook_delivery_sender(
        secret_encryption_key=(
            Fernet.generate_key().decode("ascii")
        ),
        environment="production",
    )

    assert isinstance(
        sender,
        WebhookDeliverySender,
    )
    assert isinstance(
        sender.http_client,
        WebhookPinnedHttpClient,
    )
    assert isinstance(
        sender.callback_validator,
        WebhookCallbackUrlValidator,
    )
    assert sender.environment == "production"


def test_webhook_delivery_worker_has_independent_production_service():
    from pathlib import Path

    script_path = Path(
        "scripts/process_webhook_deliveries.py"
    )
    service_path = Path(
        "deploy/systemd/"
        "sghr-webhook-delivery.service"
    )

    assert script_path.is_file()
    assert service_path.is_file()

    script = script_path.read_text(
        encoding="utf-8-sig"
    )
    assert "WebhookDeliveryWorker" in script
    assert "WebhookRepository" in script
    assert "build_webhook_delivery_sender" in script
    assert "ApiWebhookSettings.from_env" in script
    assert "async_session" in script
    assert "worker.run_once()" in script
    assert "asyncio.sleep" in script
    assert "asyncio.run" in script

    service = service_path.read_text(
        encoding="utf-8-sig"
    )
    assert "Type=simple" in service
    assert (
        "scripts/process_webhook_deliveries.py"
        in service
    )
    assert (
        "EnvironmentFile=/opt/sghr/.env"
        in service
    )
    assert "Restart=always" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateTmp=true" in service


@pytest.mark.asyncio
async def test_service_order_creation_publishes_webhook_event_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    order_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(
                id=order_id,
                thread_id=thread_id,
                contact_request_id=contact_request_id,
                status="draft",
            )

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await (
        service.create_service_order_draft_from_thread(
            tenant_id=tenant_id,
            actor_user_id=user_id,
            thread_id=thread_id,
            description="Consultation",
            currency="EUR",
            platform="api",
        )
    )

    assert result.order_id == order_id
    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "publish",
        "commit",
    ]

    create_call = calls[0][1]
    assert create_call["tenant_id"] == tenant_id
    assert create_call["commit"] is False

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "service_order.created"
    )
    assert event_call["payload"][
        "service_order_id"
    ] == str(order_id)
    assert event_call["payload"]["status"] == "draft"


def test_user_orders_dependency_injects_shared_webhook_publisher(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.dependencies import (
        get_user_orders_service,
    )
    from services.webhooks import (
        WebhookEventPublisher,
    )

    monkeypatch.setenv(
        "API_IDEMPOTENCY_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )

    session = object()
    service = get_user_orders_service(
        session=session,
    )

    publisher = (
        service.chats.webhook_publisher
    )
    assert isinstance(
        publisher,
        WebhookEventPublisher,
    )
    assert publisher.repository.session is session


@pytest.mark.asyncio
async def test_contact_detection_supports_deferred_commit_for_webhook_outbox():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_detection import (
        ContactDetectionService,
    )

    message_id = uuid4()
    tenant_id = uuid4()
    thread_id = uuid4()
    calls = []

    message = SimpleNamespace(
        id=message_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        original_text=(
            "Call me at +380 67 123 45 67"
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_message(self, value):
            calls.append(
                ("get_message", {"id": value})
            )
            return message

        async def mark_message_masked(
            self,
            **kwargs,
        ):
            calls.append(("mask", kwargs))

        async def log_detection(self, **kwargs):
            calls.append(("log", kwargs))

        async def create_risk_flag(
            self,
            **kwargs,
        ):
            calls.append(("risk", kwargs))

        async def restrict_thread(
            self,
            **kwargs,
        ):
            calls.append(("restrict", kwargs))

    result = await ContactDetectionService(
        FakeRepository()
    ).process_message(
        message_id,
        commit=False,
    )

    assert result.is_masked is True
    assert any(
        name == "mask"
        for name, _ in calls
    )
    assert all(
        name != "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_contact_request_creation_publishes_webhook_event_atomically(
    monkeypatch,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import services.contact_chat as contact_module
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    specialist_user_id = uuid4()
    profession_id = uuid4()
    cabinet_id = uuid4()
    request_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()
    notification_id = uuid4()
    calls = []

    context = SimpleNamespace(
        specialist_id=specialist_id,
        specialist_user_id=specialist_user_id,
        profession_id=profession_id,
        professional_cabinet_id=cabinet_id,
    )
    contact_request = SimpleNamespace(
        id=request_id,
        status="new",
        extra_metadata={},
    )
    thread = SimpleNamespace(id=thread_id)
    first_message = SimpleNamespace(id=message_id)
    notification = SimpleNamespace(
        id=notification_id
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_active_contact_request_for_pair(
            self,
            **kwargs,
        ):
            return None

        async def create_contact_request_with_thread(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return (
                contact_request,
                thread,
                first_message,
                notification,
            )

    class FakeDetectionService:
        def __init__(self, repository):
            pass

        async def process_message(
            self,
            value,
            **kwargs,
        ):
            calls.append(
                (
                    "detect",
                    {
                        "message_id": value,
                        **kwargs,
                    },
                )
            )
            return SimpleNamespace(
                is_masked=False,
                detected_types=[],
                thread_restricted=False,
            )

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    class TestContactChatService(
        ContactChatService
    ):
        async def _get_contact_cabinet_context(
            self,
            **kwargs,
        ):
            return context

    monkeypatch.setattr(
        contact_module,
        "ContactDetectionService",
        FakeDetectionService,
    )

    class FakeRateLimitService:
        async def ensure_contact_request_allowed(
            self,
            **kwargs,
        ):
            return None

    service = TestContactChatService(
        FakeRepository(),
        rate_limit_service=FakeRateLimitService(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.create_contact_request(
        tenant_id=tenant_id,
        from_user_id=user_id,
        specialist_id=specialist_id,
        profession_id=profession_id,
        message="Need a consultation.",
        original_language="uk",
        platform="api",
    )

    assert result.contact_request_id == request_id
    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "detect",
        "publish",
        "commit",
    ]

    assert calls[0][1]["commit"] is False
    assert calls[1][1]["commit"] is False

    event_call = calls[2][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "contact_request.created"
    )
    assert event_call["payload"][
        "contact_request_id"
    ] == str(request_id)
    assert event_call["payload"][
        "thread_id"
    ] == str(thread_id)
    assert event_call["payload"]["status"] == "new"


def test_user_dialogs_dependency_injects_shared_webhook_publisher(
    monkeypatch,
):
    import api.dependencies as dependencies
    from database.repositories.webhooks import (
        WebhookRepository,
    )
    from services.webhooks import (
        WebhookEventPublisher,
    )

    session = object()
    idempotency = object()

    monkeypatch.setattr(
        dependencies,
        "get_api_idempotency_service",
        lambda *, session: idempotency,
    )

    service = dependencies.get_user_dialogs_service(
        session=session,
    )

    publisher = service.chats.webhook_publisher

    assert isinstance(
        publisher,
        WebhookEventPublisher,
    )
    assert isinstance(
        publisher.repository,
        WebhookRepository,
    )
    assert publisher.repository.session is session


@pytest.mark.asyncio
async def test_contact_request_creation_rolls_back_when_webhook_publish_fails(
    monkeypatch,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import services.contact_chat as contact_module
    from services.contact_chat import ContactChatService

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    request_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()
    calls = []

    context = SimpleNamespace(
        specialist_id=specialist_id,
        specialist_user_id=uuid4(),
        profession_id=uuid4(),
        professional_cabinet_id=cabinet_id,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_active_contact_request_for_pair(
            self,
            **kwargs,
        ):
            return None

        async def create_contact_request_with_thread(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return (
                SimpleNamespace(
                    id=request_id,
                    status="new",
                    extra_metadata={},
                ),
                SimpleNamespace(id=thread_id),
                SimpleNamespace(id=message_id),
                SimpleNamespace(id=uuid4()),
            )

    class FakeDetectionService:
        def __init__(self, repository):
            pass

        async def process_message(
            self,
            message_id,
            **kwargs,
        ):
            calls.append(("detect", kwargs))
            return SimpleNamespace(
                is_masked=False,
                detected_types=[],
                thread_restricted=False,
            )

    class FailingWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            raise RuntimeError(
                "Private webhook storage details."
            )

    class TestContactChatService(
        ContactChatService
    ):
        async def _get_contact_cabinet_context(
            self,
            **kwargs,
        ):
            return context

    class FakeRateLimitService:
        async def ensure_contact_request_allowed(
            self,
            **kwargs,
        ):
            return None

    monkeypatch.setattr(
        contact_module,
        "ContactDetectionService",
        FakeDetectionService,
    )

    service = TestContactChatService(
        FakeRepository(),
        rate_limit_service=FakeRateLimitService(),
        webhook_publisher=FailingWebhookPublisher(),
    )

    with pytest.raises(RuntimeError):
        await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=user_id,
            specialist_id=specialist_id,
            profession_id=context.profession_id,
            message="Need a consultation.",
            original_language="uk",
            platform="api",
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "detect",
        "publish",
        "rollback",
    ]
    assert all(
        name != "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_service_order_confirmation_publishes_webhook_event_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import ContactChatService

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    order = SimpleNamespace(
        id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="confirmed",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def confirm_service_order(
            self,
            **kwargs,
        ):
            calls.append(("confirm", kwargs))
            return order

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.confirm_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert result.order_id == order_id
    assert [
        name
        for name, _ in calls
    ] == [
        "confirm",
        "publish",
        "commit",
    ]
    assert calls[0][1]["commit"] is False

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "service_order.confirmed"
    )
    assert event_call["payload"][
        "service_order_id"
    ] == str(order_id)
    assert event_call["payload"][
        "thread_id"
    ] == str(thread_id)
    assert event_call["payload"][
        "contact_request_id"
    ] == str(contact_request_id)
    assert event_call["payload"]["status"] == (
        "confirmed"
    )


@pytest.mark.asyncio
async def test_service_order_cancellation_publishes_webhook_event_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import ContactChatService

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    order = SimpleNamespace(
        id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="cancelled",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def cancel_service_order(
            self,
            **kwargs,
        ):
            calls.append(("cancel", kwargs))
            return order

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.cancel_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert result.order_id == order_id
    assert [
        name
        for name, _ in calls
    ] == [
        "cancel",
        "publish",
        "commit",
    ]
    assert calls[0][1]["commit"] is False

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "service_order.cancelled"
    )
    assert event_call["payload"][
        "service_order_id"
    ] == str(order_id)
    assert event_call["payload"][
        "thread_id"
    ] == str(thread_id)
    assert event_call["payload"][
        "contact_request_id"
    ] == str(contact_request_id)
    assert event_call["payload"]["status"] == (
        "cancelled"
    )


@pytest.mark.asyncio
async def test_service_order_completion_publishes_webhook_event_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import ContactChatService

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    order = SimpleNamespace(
        id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="completed",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def complete_service_order(
            self,
            **kwargs,
        ):
            calls.append(("complete", kwargs))
            return order

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.complete_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert result.order_id == order_id
    assert [
        name
        for name, _ in calls
    ] == [
        "complete",
        "publish",
        "commit",
    ]
    assert calls[0][1]["commit"] is False

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "service_order.completed"
    )
    assert event_call["payload"][
        "service_order_id"
    ] == str(order_id)
    assert event_call["payload"][
        "thread_id"
    ] == str(thread_id)
    assert event_call["payload"][
        "contact_request_id"
    ] == str(contact_request_id)
    assert event_call["payload"]["status"] == (
        "completed"
    )


@pytest.mark.asyncio
async def test_contact_request_cancellation_publishes_updated_webhook_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import ContactChatService

    tenant_id = uuid4()
    actor_user_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
    calls = []

    contact_request = SimpleNamespace(
        id=contact_request_id,
        status="cancelled",
    )
    thread = SimpleNamespace(
        id=thread_id,
        status="closed",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def cancel_contact_request_by_client(
            self,
            **kwargs,
        ):
            calls.append(("cancel", kwargs))
            return contact_request, thread

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.cancel_contact_request(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        contact_request_id=contact_request_id,
        platform="api",
    )

    assert result.contact_request_id == (
        contact_request_id
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "cancel",
        "publish",
        "commit",
    ]

    cancel_call = calls[0][1]
    assert cancel_call["tenant_id"] == tenant_id
    assert cancel_call["actor_user_id"] == (
        actor_user_id
    )
    assert cancel_call["platform"] == "api"
    assert cancel_call["commit"] is False

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "contact_request.updated"
    )
    assert event_call["payload"][
        "contact_request_id"
    ] == str(contact_request_id)
    assert event_call["payload"][
        "thread_id"
    ] == str(thread_id)
    assert event_call["payload"]["status"] == (
        "cancelled"
    )
    assert event_call["payload"][
        "thread_status"
    ] == "closed"


@pytest.mark.asyncio
async def test_review_creation_publishes_webhook_event_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.reviews import ReviewService

    tenant_id = uuid4()
    reviewer_user_id = uuid4()
    contact_request_id = uuid4()
    review_id = uuid4()
    cabinet_id = uuid4()
    specialist_id = uuid4()
    calls = []

    review = SimpleNamespace(
        id=review_id,
        tenant_id=tenant_id,
        reviewer_user_id=reviewer_user_id,
        professional_cabinet_id=cabinet_id,
        target_type="specialist",
        target_id=specialist_id,
        context_type="contact_request",
        context_id=contact_request_id,
        rating=5,
        status="pending_moderation",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def create_contact_review(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return review

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ReviewService(
        FakeRepository(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.create_contact_review(
        tenant_id=tenant_id,
        reviewer_user_id=reviewer_user_id,
        contact_request_id=contact_request_id,
        rating=5,
        text="Excellent service.",
    )

    assert result is review
    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "publish",
        "commit",
    ]

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "review.created"
    )
    assert event_call["payload"][
        "review_id"
    ] == str(review_id)
    assert event_call["payload"][
        "professional_cabinet_id"
    ] == str(cabinet_id)
    assert event_call["payload"][
        "context_type"
    ] == "contact_request"
    assert event_call["payload"][
        "context_id"
    ] == str(contact_request_id)
    assert event_call["payload"]["status"] == (
        "pending_moderation"
    )


@pytest.mark.asyncio
async def test_service_order_review_creation_publishes_webhook_event_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.reviews import ReviewService

    tenant_id = uuid4()
    reviewer_user_id = uuid4()
    service_order_id = uuid4()
    review_id = uuid4()
    cabinet_id = uuid4()
    specialist_id = uuid4()
    calls = []

    review = SimpleNamespace(
        id=review_id,
        tenant_id=tenant_id,
        reviewer_user_id=reviewer_user_id,
        professional_cabinet_id=cabinet_id,
        target_type="specialist",
        target_id=specialist_id,
        context_type="service_order",
        context_id=service_order_id,
        rating=4,
        status="pending_moderation",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def create_service_order_review(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return review

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    service = ReviewService(
        FakeRepository(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.create_service_order_review(
        tenant_id=tenant_id,
        reviewer_user_id=reviewer_user_id,
        service_order_id=service_order_id,
        rating=4,
        text="Good work.",
    )

    assert result is review
    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "publish",
        "commit",
    ]

    event_call = calls[1][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "review.created"
    )
    assert event_call["payload"][
        "review_id"
    ] == str(review_id)
    assert event_call["payload"][
        "context_type"
    ] == "service_order"
    assert event_call["payload"][
        "context_id"
    ] == str(service_order_id)
    assert event_call["payload"]["status"] == (
        "pending_moderation"
    )


def test_user_reviews_dependency_injects_shared_webhook_publisher():
    from api.dependencies import (
        get_user_reviews_service,
    )
    from database.repositories.webhooks import (
        WebhookRepository,
    )
    from services.webhooks import (
        WebhookEventPublisher,
    )

    session = object()

    service = get_user_reviews_service(
        session=session,
    )
    publisher = (
        service.reviews.webhook_publisher
    )

    assert isinstance(
        publisher,
        WebhookEventPublisher,
    )
    assert isinstance(
        publisher.repository,
        WebhookRepository,
    )
    assert publisher.repository.session is session
    assert service.session is session


@pytest.mark.asyncio
async def test_review_publication_publishes_webhook_event_atomically(
    monkeypatch,
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    import services.reviews as reviews_module
    from services.reviews import ReviewService

    tenant_id = uuid4()
    moderator_user_id = uuid4()
    review_id = uuid4()
    cabinet_id = uuid4()
    specialist_id = uuid4()
    published_at = datetime(
        2026,
        9,
        11,
        12,
        0,
        tzinfo=UTC,
    )
    calls = []

    review = SimpleNamespace(
        id=review_id,
        tenant_id=tenant_id,
        professional_cabinet_id=cabinet_id,
        target_type="specialist",
        target_id=specialist_id,
        context_type="service_order",
        context_id=uuid4(),
        rating=5,
        status="published",
        published_at=published_at,
    )
    reputation = SimpleNamespace(score=5.0)

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def set_review_status(
            self,
            **kwargs,
        ):
            calls.append(("set_status", kwargs))
            return review, "pending_moderation"

        async def recalculate_professional_cabinet_reputation(
            self,
            **kwargs,
        ):
            calls.append(("cabinet_reputation", kwargs))

        async def recalculate_reputation(
            self,
            **kwargs,
        ):
            calls.append(("reputation", kwargs))
            return reputation

    class FakeModerationRepository:
        def __init__(self, session):
            pass

        async def log_admin_action(
            self,
            **kwargs,
        ):
            calls.append(("admin_audit", kwargs))

        async def log_event(
            self,
            **kwargs,
        ):
            calls.append(("event_audit", kwargs))

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        reviews_module,
        "ModerationRepository",
        FakeModerationRepository,
    )

    service = ReviewService(
        FakeRepository(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.moderate_review(
        tenant_id=tenant_id,
        moderator_user_id=moderator_user_id,
        review_id=review_id,
        status="published",
        reason="Approved review.",
    )

    assert result.review is review
    assert [
        name
        for name, _ in calls
    ] == [
        "set_status",
        "cabinet_reputation",
        "reputation",
        "admin_audit",
        "event_audit",
        "publish",
        "commit",
    ]

    event_call = calls[5][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "review.published"
    )
    assert event_call["payload"][
        "review_id"
    ] == str(review_id)
    assert event_call["payload"][
        "professional_cabinet_id"
    ] == str(cabinet_id)
    assert event_call["payload"]["status"] == (
        "published"
    )
    assert event_call["payload"][
        "published_at"
    ] == published_at.isoformat()


def test_admin_reviews_service_injects_shared_webhook_publisher():
    from database.repositories.webhooks import (
        WebhookRepository,
    )
    from services.api_admin_reviews import (
        build_api_admin_reviews_service,
    )
    from services.webhooks import (
        WebhookEventPublisher,
    )

    session = object()

    service = build_api_admin_reviews_service(
        session
    )
    publisher = (
        service.reviews.webhook_publisher
    )

    assert isinstance(
        publisher,
        WebhookEventPublisher,
    )
    assert isinstance(
        publisher.repository,
        WebhookRepository,
    )
    assert publisher.repository.session is session
    assert service.repository.session is session
    assert (
        service.reviews.repository.session
        is session
    )


@pytest.mark.asyncio
async def test_cabinet_availability_change_publishes_webhook_event_atomically(
    monkeypatch,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import services.specialist as specialist_module
    from services.specialist import SpecialistService

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    specialist = SimpleNamespace(
        id=specialist_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        availability_status="available",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_by_user_id(self, value):
            return specialist

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            return cabinet, object()

        async def update_cabinet_availability(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            cabinet.availability_status = (
                kwargs["availability_status"]
            )
            return cabinet

    class FakeEventRepository:
        def __init__(self, session):
            pass

        async def create_event(self, **kwargs):
            calls.append(("event_audit", kwargs))

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        specialist_module,
        "EventRepository",
        FakeEventRepository,
    )

    service = SpecialistService(
        FakeRepository(),
        rate_limit_service=object(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.update_cabinet_availability(
        tenant_id=tenant_id,
        user_id=user_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
        availability_status="busy",
        platform="api",
    )

    assert result == (
        "available",
        "busy",
        True,
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "update",
        "event_audit",
        "publish",
        "commit",
    ]

    event_call = calls[2][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == (
            "professional_cabinet."
            "availability_changed"
        )
    )
    assert event_call["payload"][
        "professional_cabinet_id"
    ] == str(cabinet_id)
    assert event_call["payload"][
        "specialist_id"
    ] == str(specialist_id)
    assert event_call["payload"]["before"] == (
        "available"
    )
    assert event_call["payload"]["after"] == "busy"


def test_specialist_cabinets_dependency_injects_shared_webhook_publisher():
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from database.repositories.webhooks import (
        WebhookRepository,
    )
    from services.webhooks import (
        WebhookEventPublisher,
    )

    session = object()

    service = get_specialist_cabinets_service(
        session=session,
    )
    publisher = (
        service.specialists.webhook_publisher
    )

    assert isinstance(
        publisher,
        WebhookEventPublisher,
    )
    assert isinstance(
        publisher.repository,
        WebhookRepository,
    )
    assert publisher.repository.session is session
    assert service.session is session
    assert service.repository.session is session
    assert (
        service.specialists.repository.session
        is session
    )


@pytest.mark.asyncio
async def test_professional_cabinet_update_publishes_webhook_event_atomically(
    monkeypatch,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import services.specialist as specialist_module
    from services.specialist import (
        SpecialistProfileUpdateData,
        SpecialistService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    specialist = SimpleNamespace(
        id=specialist_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        description=(
            "Previous professional description."
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_by_user_id(self, value):
            return specialist

        async def get_active_professional_cabinet(
            self,
            **kwargs,
        ):
            return cabinet

        async def update_active_cabinet_description(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            cabinet.description = kwargs[
                "description"
            ]
            return cabinet

    class FakeRateLimitService:
        async def ensure_profile_edit_allowed(
            self,
            **kwargs,
        ):
            return None

    class FakeEventRepository:
        def __init__(self, session):
            pass

        async def create_event(self, **kwargs):
            calls.append(("event_audit", kwargs))

    class FakeWebhookPublisher:
        async def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        specialist_module,
        "EventRepository",
        FakeEventRepository,
    )

    service = SpecialistService(
        FakeRepository(),
        rate_limit_service=FakeRateLimitService(),
        webhook_publisher=FakeWebhookPublisher(),
    )

    result = await service.update_profile_with_audit(
        SpecialistProfileUpdateData(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
            short_description=(
                "Updated professional description."
            ),
        )
    )

    assert result.specialist_id == specialist_id
    assert result.changed is True
    assert [
        name
        for name, _ in calls
    ] == [
        "update",
        "event_audit",
        "publish",
        "commit",
    ]

    event_call = calls[2][1]
    assert event_call["tenant_id"] == tenant_id
    assert (
        event_call["event_type"]
        == "professional_cabinet.updated"
    )
    assert event_call["payload"][
        "professional_cabinet_id"
    ] == str(cabinet_id)
    assert event_call["payload"][
        "specialist_id"
    ] == str(specialist_id)
    assert event_call["payload"][
        "changed_fields"
    ] == ["description"]


def test_specialist_profile_service_injects_shared_webhook_publisher():
    from database.repositories.webhooks import (
        WebhookRepository,
    )
    from services.specialist_profile import (
        SpecialistProfileService,
    )
    from services.webhooks import (
        WebhookEventPublisher,
    )

    session = object()
    service = SpecialistProfileService(session)

    publisher = (
        service.specialists.webhook_publisher
    )

    assert isinstance(
        publisher,
        WebhookEventPublisher,
    )
    assert isinstance(
        publisher.repository,
        WebhookRepository,
    )
    assert publisher.repository.session is session
    assert service.repository.session is session
    assert service.specialists.repository is (
        service.repository
    )



@pytest.mark.asyncio
async def test_webhook_delivery_worker_schedules_first_retry_after_initial_failure():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from services.webhooks import (
        WebhookDeliveryAttemptResult,
        WebhookDeliveryWorker,
    )

    now = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=UTC,
    )
    delivery = SimpleNamespace(
        attempt_count=0,
        status="pending",
    )
    event = SimpleNamespace()
    endpoint = SimpleNamespace()
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_due_delivery_context_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return (
                delivery,
                event,
                endpoint,
            )

        async def mark_delivery_failed(
            self,
            **kwargs,
        ):
            calls.append(("failed", kwargs))
            return delivery

    class FakeSender:
        async def send(self, **kwargs):
            calls.append(("send", kwargs))
            return WebhookDeliveryAttemptResult(
                succeeded=False,
                status_code=503,
                error_category="http_error",
            )

    worker = WebhookDeliveryWorker(
        session=FakeSession(),
        repository=FakeRepository(),
        sender=FakeSender(),
        clock=lambda: now,
    )

    processed = await worker.run_once()

    assert processed is True
    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "send",
        "failed",
        "commit",
    ]
    assert calls[0][1] == {
        "now": now,
    }
    assert calls[1][1] == {
        "event": event,
        "endpoint": endpoint,
    }
    assert calls[2][1] == {
        "delivery": delivery,
        "endpoint": endpoint,
        "response_status": 503,
        "error_category": "http_error",
        "retry_at": (
            now + timedelta(minutes=1)
        ),
    }



@pytest.mark.asyncio
async def test_webhook_delivery_worker_uses_all_five_retry_delays_and_then_stops():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from services.webhooks import (
        WebhookDeliveryAttemptResult,
        WebhookDeliveryWorker,
    )

    now = datetime(
        2026,
        9,
        12,
        11,
        0,
        tzinfo=UTC,
    )
    expected_delays = (
        60,
        5 * 60,
        30 * 60,
        2 * 60 * 60,
        12 * 60 * 60,
        None,
    )

    for previous_attempts, delay in enumerate(
        expected_delays
    ):
        delivery = SimpleNamespace(
            attempt_count=previous_attempts,
            status="pending",
        )
        event = SimpleNamespace()
        endpoint = SimpleNamespace()
        failure_calls = []

        class FakeSession:
            async def commit(self):
                pass

            async def rollback(self):
                raise AssertionError(
                    "Handled failure must not roll back."
                )

        class FakeRepository:
            async def get_due_delivery_context_for_update(
                self,
                **kwargs,
            ):
                return (
                    delivery,
                    event,
                    endpoint,
                )

            async def mark_delivery_failed(
                self,
                **kwargs,
            ):
                failure_calls.append(kwargs)
                return delivery

        class FakeSender:
            async def send(self, **kwargs):
                return WebhookDeliveryAttemptResult(
                    succeeded=False,
                    status_code=503,
                    error_category="http_error",
                )

        worker = WebhookDeliveryWorker(
            session=FakeSession(),
            repository=FakeRepository(),
            sender=FakeSender(),
            clock=lambda: now,
        )

        assert await worker.run_once() is True
        assert len(failure_calls) == 1

        expected_retry_at = (
            None
            if delay is None
            else now + timedelta(seconds=delay)
        )

        assert failure_calls[0][
            "retry_at"
        ] == expected_retry_at
