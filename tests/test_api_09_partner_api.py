import pytest

def test_api_client_model_matches_partner_contract():
    from database.models import ApiClient

    table = ApiClient.__table__

    assert table.name == "api_clients"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "owner_type",
        "owner_id",
        "name",
        "status",
        "metadata",
        "created_at",
        "updated_at",
    }

    assert table.c.tenant_id.nullable is False
    assert table.c.owner_type.nullable is False
    assert table.c.owner_id.nullable is True
    assert table.c.name.nullable is False
    assert table.c.status.nullable is False
    assert table.c.metadata.nullable is False

    tenant_targets = {
        foreign_key.target_fullname
        for foreign_key in table.c.tenant_id.foreign_keys
    }
    assert tenant_targets == {"tenants.id"}
    assert not table.c.owner_id.foreign_keys

    constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )
    for owner_type in (
        "agency",
        "partner",
        "enterprise",
        "service_account",
    ):
        assert owner_type in constraint_sql

    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

def test_api_client_supports_composite_tenant_reference():
    from sqlalchemy import UniqueConstraint

    from database.models import ApiClient

    unique_column_sets = {
        frozenset(
            column.name
            for column in constraint.columns
        )
        for constraint in ApiClient.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert frozenset(
        {"tenant_id", "id"}
    ) in unique_column_sets

def test_api_key_model_is_tenant_scoped_and_hash_only():
    from database.models import ApiKey

    table = ApiKey.__table__

    assert table.name == "api_keys"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "api_client_id",
        "name",
        "key_prefix",
        "key_hash",
        "environment",
        "status",
        "expires_at",
        "last_used_at",
        "ip_allowlist",
        "created_at",
    }

    assert table.c.tenant_id.nullable is False
    assert table.c.api_client_id.nullable is False
    assert table.c.key_prefix.nullable is False
    assert table.c.key_hash.nullable is False
    assert table.c.environment.nullable is False
    assert table.c.status.nullable is False
    assert table.c.expires_at.nullable is True
    assert table.c.last_used_at.nullable is True
    assert table.c.ip_allowlist.nullable is True

    forbidden_columns = {
        "key",
        "secret",
        "plaintext_key",
        "raw_key",
    }
    assert forbidden_columns.isdisjoint(
        table.columns.keys()
    )

    constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )

    for value in ("sandbox", "production"):
        assert value in constraint_sql

    for value in ("active", "revoked", "expired"):
        assert value in constraint_sql

    assert table.c.created_at.type.timezone is True
    assert table.c.expires_at.type.timezone is True
    assert table.c.last_used_at.type.timezone is True

def test_api_key_codec_issues_opaque_hashable_key():
    from api.security import ApiKeyCodec

    codec = ApiKeyCodec()

    material = codec.issue_api_key()
    another = codec.issue_api_key()

    assert material.key
    assert material.key_prefix
    assert material.key_hash
    assert material.key != material.key_hash
    assert material.key.startswith(
        material.key_prefix + "."
    )
    assert len(material.key_hash) == 64

    assert material.key_hash == (
        codec.hash_api_key(material.key)
    )
    assert codec.verify_api_key(
        material.key,
        material.key_hash,
    )
    assert not codec.verify_api_key(
        another.key,
        material.key_hash,
    )

    assert another.key != material.key
    assert another.key_prefix != material.key_prefix
    assert another.key_hash != material.key_hash

@pytest.mark.asyncio
async def test_partner_repository_creates_tenant_scoped_api_client():
    from uuid import uuid4

    from database.models import ApiClient
    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    owner_id = uuid4()

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = PartnerApiRepository(session)

    result = await repository.create_api_client(
        tenant_id=tenant_id,
        owner_type="partner",
        owner_id=owner_id,
        name="Release integration",
        metadata={"source": "manual"},
    )

    assert isinstance(result, ApiClient)
    assert result.tenant_id == tenant_id
    assert result.owner_type == "partner"
    assert result.owner_id == owner_id
    assert result.name == "Release integration"
    assert result.status == "active"
    assert result.extra_metadata == {
        "source": "manual",
    }
    assert session.added == [result]
    assert session.flushes == 1

@pytest.mark.asyncio
async def test_partner_repository_lists_api_clients_by_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

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
    repository = PartnerApiRepository(session)

    result = await repository.list_api_clients(
        tenant_id=tenant_id,
        limit=21,
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
    normalized = " ".join(sql.split())

    assert (
        "api_clients.tenant_id = "
        f"'{tenant_id}'"
    ) in normalized
    assert (
        "ORDER BY api_clients.created_at DESC, "
        "api_clients.id DESC"
    ) in normalized
    assert "LIMIT 21" in normalized

@pytest.mark.asyncio
async def test_partner_repository_gets_api_client_by_tenant_and_id():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    expected = SimpleNamespace(
        id=api_client_id,
    )

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
    repository = PartnerApiRepository(session)

    result = await repository.get_api_client(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
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
    normalized = " ".join(sql.split())

    assert (
        "api_clients.tenant_id = "
        f"'{tenant_id}'"
    ) in normalized
    assert (
        "api_clients.id = "
        f"'{api_client_id}'"
    ) in normalized
    assert "LIMIT 1" in normalized

@pytest.mark.asyncio
async def test_partner_repository_stores_api_key_hash_only():
    from datetime import UTC, datetime
    import inspect
    from uuid import uuid4

    from database.models import ApiKey
    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    parameters = inspect.signature(
        PartnerApiRepository.create_api_key
    ).parameters

    assert "key_prefix" in parameters
    assert "key_hash" in parameters
    assert "key" not in parameters
    assert "secret" not in parameters
    assert "plaintext_key" not in parameters

    tenant_id = uuid4()
    api_client_id = uuid4()
    expires_at = datetime(
        2027,
        1,
        1,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = PartnerApiRepository(session)

    result = await repository.create_api_key(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        name="Production integration",
        key_prefix="sghr_0123456789abcdef",
        key_hash="a" * 64,
        environment="production",
        expires_at=expires_at,
        ip_allowlist=[
            "192.0.2.10",
            "198.51.100.0/24",
        ],
    )

    assert isinstance(result, ApiKey)
    assert result.tenant_id == tenant_id
    assert result.api_client_id == api_client_id
    assert result.name == "Production integration"
    assert result.key_prefix == (
        "sghr_0123456789abcdef"
    )
    assert result.key_hash == "a" * 64
    assert result.environment == "production"
    assert result.status == "active"
    assert result.expires_at == expires_at
    assert result.ip_allowlist == [
        "192.0.2.10",
        "198.51.100.0/24",
    ]
    assert session.added == [result]
    assert session.flushes == 1

@pytest.mark.asyncio
async def test_partner_repository_lists_keys_by_tenant_and_api_client():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

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
    repository = PartnerApiRepository(session)

    result = await repository.list_api_keys(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        limit=21,
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
    normalized = " ".join(sql.split())

    assert (
        "api_keys.tenant_id = "
        f"'{tenant_id}'"
    ) in normalized
    assert (
        "api_keys.api_client_id = "
        f"'{api_client_id}'"
    ) in normalized
    assert (
        "ORDER BY api_keys.created_at DESC, "
        "api_keys.id DESC"
    ) in normalized
    assert "LIMIT 21" in normalized

@pytest.mark.asyncio
async def test_partner_repository_locks_scoped_api_key_before_revoke():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_key_id = uuid4()
    expected = SimpleNamespace(
        id=api_key_id,
        status="active",
    )

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
    repository = PartnerApiRepository(session)

    result = await repository.get_api_key_for_update(
        tenant_id=tenant_id,
        api_key_id=api_key_id,
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
    normalized = " ".join(sql.split())

    assert (
        "api_keys.tenant_id = "
        f"'{tenant_id}'"
    ) in normalized
    assert (
        "api_keys.id = "
        f"'{api_key_id}'"
    ) in normalized
    assert "FOR UPDATE" in normalized

@pytest.mark.asyncio
async def test_partner_repository_revokes_locked_api_key_idempotently():
    from types import SimpleNamespace

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = PartnerApiRepository(session)
    api_key = SimpleNamespace(
        status="active",
    )

    result = await repository.revoke_api_key(
        api_key=api_key,
    )

    assert result is api_key
    assert api_key.status == "revoked"
    assert session.flushes == 1

    repeated = await repository.revoke_api_key(
        api_key=api_key,
    )

    assert repeated is api_key
    assert api_key.status == "revoked"
    assert session.flushes == 1

@pytest.mark.asyncio
async def test_partner_repository_resolves_active_key_for_environment():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    now = datetime(
        2026,
        9,
        4,
        12,
        0,
        tzinfo=UTC,
    )
    expected = SimpleNamespace(
        key_prefix="sghr_0123456789abcdef",
    )

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
    repository = PartnerApiRepository(session)

    result = await repository.get_active_api_key(
        key_prefix="sghr_0123456789abcdef",
        environment="production",
        now=now,
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
    normalized = " ".join(sql.split())

    assert (
        "api_keys.key_prefix = "
        "'sghr_0123456789abcdef'"
    ) in normalized
    assert (
        "api_keys.environment = 'production'"
    ) in normalized
    assert "api_keys.status = 'active'" in normalized
    assert "api_keys.expires_at IS NULL" in normalized
    assert "api_keys.expires_at >" in normalized
    assert "api_clients.status = 'active'" in normalized
    assert "tenants.status = 'active'" in normalized

@pytest.mark.asyncio
async def test_partner_repository_records_api_key_last_used_at():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    now = datetime(
        2026,
        9,
        4,
        12,
        30,
        tzinfo=UTC,
    )
    api_key = SimpleNamespace(
        last_used_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = PartnerApiRepository(session)

    result = await repository.mark_api_key_used(
        api_key=api_key,
        now=now,
    )

    assert result is api_key
    assert api_key.last_used_at == now
    assert api_key.last_used_at.tzinfo is UTC
    assert session.flushes == 1

def test_api_key_codec_extracts_lookup_prefix():
    import pytest

    from api.security import ApiKeyCodec

    codec = ApiKeyCodec()
    material = codec.issue_api_key()

    assert codec.extract_key_prefix(
        material.key
    ) == material.key_prefix

    for invalid_key in (
        "",
        "missing-separator",
        ".missing-prefix",
        "prefix.",
        "prefix.secret.extra",
    ):
        with pytest.raises(ValueError):
            codec.extract_key_prefix(
                invalid_key
            )

@pytest.mark.asyncio
async def test_partner_service_creates_tenant_scoped_api_client():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiClientView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    owner_id = uuid4()
    api_client_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        13,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        9,
        4,
        13,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeRepository:
        async def create_api_client(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=api_client_id,
                tenant_id=tenant_id,
                owner_type="partner",
                owner_id=owner_id,
                name="Release integration",
                status="active",
                extra_metadata={
                    "source": "manual",
                },
                created_at=created_at,
                updated_at=updated_at,
            )

    session = FakeSession()
    service = PartnerApiService(
        session=session,
        repository=FakeRepository(),
    )

    result = await service.create_api_client(
        tenant_id=tenant_id,
        owner_type="partner",
        owner_id=owner_id,
        name="Release integration",
        metadata={"source": "manual"},
    )

    assert isinstance(result, ApiClientView)
    assert result.id == api_client_id
    assert result.owner_type == "partner"
    assert result.owner_id == owner_id
    assert result.name == "Release integration"
    assert result.status == "active"
    assert result.metadata == {
        "source": "manual",
    }
    assert result.created_at == created_at
    assert result.updated_at == updated_at

    assert calls == [
        {
            "tenant_id": tenant_id,
            "owner_type": "partner",
            "owner_id": owner_id,
            "name": "Release integration",
            "metadata": {
                "source": "manual",
            },
        }
    ]
    assert session.commits == 1
    assert session.rollbacks == 0

@pytest.mark.asyncio
async def test_partner_service_rolls_back_and_sanitizes_create_failure():
    from uuid import uuid4

    from services.partner_api import (
        PartnerApiOperationError,
        PartnerApiService,
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeRepository:
        async def create_api_client(
            self,
            **_kwargs,
        ):
            raise RuntimeError(
                "private database details"
            )

    session = FakeSession()
    service = PartnerApiService(
        session=session,
        repository=FakeRepository(),
    )

    with pytest.raises(
        PartnerApiOperationError
    ) as captured:
        await service.create_api_client(
            tenant_id=uuid4(),
            owner_type="partner",
            owner_id=None,
            name="Release integration",
            metadata={},
        )

    assert str(captured.value) == (
        "API client operation failed."
    )
    assert "private database details" not in str(
        captured.value
    )
    assert session.commits == 0
    assert session.rollbacks == 1

@pytest.mark.asyncio
async def test_partner_repository_api_client_list_supports_pagination_offset():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    class FakeScalars:
        def all(self):
            return []

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    session = FakeSession()
    repository = PartnerApiRepository(session)

    await repository.list_api_clients(
        tenant_id=uuid4(),
        limit=21,
        offset=40,
    )

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(sql.split())

    assert "LIMIT 21" in normalized
    assert "OFFSET 40" in normalized

@pytest.mark.asyncio
async def test_partner_service_lists_safe_tenant_api_clients():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiClientPage,
        ApiClientView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    timestamp = datetime(
        2026,
        9,
        4,
        14,
        0,
        tzinfo=UTC,
    )
    rows = [
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_type="partner",
            owner_id=None,
            name=f"Integration {index}",
            status="active",
            extra_metadata={},
            created_at=timestamp,
            updated_at=timestamp,
        )
        for index in range(3)
    ]
    calls = []

    class FakeRepository:
        async def list_api_clients(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return rows

    service = PartnerApiService(
        session=object(),
        repository=FakeRepository(),
    )

    result = await service.list_api_clients(
        tenant_id=tenant_id,
        page=1,
        page_size=2,
    )

    assert isinstance(result, ApiClientPage)
    assert all(
        isinstance(item, ApiClientView)
        for item in result.items
    )
    assert len(result.items) == 2
    assert result.page == 1
    assert result.has_next is True

    assert calls == [
        {
            "tenant_id": tenant_id,
            "limit": 3,
            "offset": 2,
        }
    ]
    assert not hasattr(result.items[0], "tenant_id")
    assert not hasattr(result.items[0], "key_hash")

@pytest.mark.asyncio
async def test_partner_service_gets_safe_tenant_api_client():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiClientView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    timestamp = datetime(
        2026,
        9,
        4,
        15,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeRepository:
        async def get_api_client(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=api_client_id,
                tenant_id=tenant_id,
                owner_type="enterprise",
                owner_id=None,
                name="Enterprise integration",
                status="active",
                extra_metadata={},
                created_at=timestamp,
                updated_at=timestamp,
            )

    service = PartnerApiService(
        session=object(),
        repository=FakeRepository(),
    )

    result = await service.get_api_client(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
    )

    assert isinstance(result, ApiClientView)
    assert result.id == api_client_id
    assert result.owner_type == "enterprise"
    assert result.name == "Enterprise integration"
    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
        }
    ]
    assert not hasattr(result, "tenant_id")
    assert not hasattr(result, "key_hash")

@pytest.mark.asyncio
async def test_partner_repository_api_key_list_supports_pagination_offset():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    class FakeScalars:
        def all(self):
            return []

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    session = FakeSession()
    repository = PartnerApiRepository(session)

    await repository.list_api_keys(
        tenant_id=uuid4(),
        api_client_id=uuid4(),
        limit=21,
        offset=40,
    )

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(sql.split())

    assert "LIMIT 21" in normalized
    assert "OFFSET 40" in normalized

@pytest.mark.asyncio
async def test_partner_service_lists_safe_api_key_views():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiKeyPage,
        ApiKeyView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    timestamp = datetime(
        2026,
        9,
        4,
        16,
        0,
        tzinfo=UTC,
    )
    calls = []

    rows = [
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            api_client_id=api_client_id,
            name=f"Key {index}",
            key_prefix=f"sghr_prefix_{index}",
            key_hash="private-hash",
            environment="production",
            status="active",
            expires_at=None,
            last_used_at=None,
            ip_allowlist=["192.0.2.10"],
            created_at=timestamp,
        )
        for index in range(3)
    ]

    class FakeRepository:
        async def get_api_client(
            self,
            **kwargs,
        ):
            calls.append(
                ("client", kwargs)
            )
            return SimpleNamespace(
                id=api_client_id,
            )

        async def list_api_keys(
            self,
            **kwargs,
        ):
            calls.append(
                ("keys", kwargs)
            )
            return rows

        async def list_api_key_scopes(
            self,
            **kwargs,
        ):
            calls.append(
                ("scopes", kwargs)
            )
            return (
                "services.read",
                "reviews.read",
            )

    service = PartnerApiService(
        session=object(),
        repository=FakeRepository(),
    )

    result = await service.list_api_keys(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        page=1,
        page_size=2,
    )

    assert isinstance(result, ApiKeyPage)
    assert all(
        isinstance(item, ApiKeyView)
        for item in result.items
    )
    assert len(result.items) == 2
    assert result.page == 1
    assert result.has_next is True
    assert result.items[0].ip_allowlist == (
        "192.0.2.10",
    )
    assert result.items[0].scopes == (
        "services.read",
        "reviews.read",
    )

    assert calls == [
        (
            "client",
            {
                "tenant_id": tenant_id,
                "api_client_id": api_client_id,
            },
        ),
        (
            "keys",
            {
                "tenant_id": tenant_id,
                "api_client_id": api_client_id,
                "limit": 3,
                "offset": 2,
            },
        ),
        (
            "scopes",
            {
                "tenant_id": tenant_id,
                "api_key_id": rows[0].id,
            },
        ),
        (
            "scopes",
            {
                "tenant_id": tenant_id,
                "api_key_id": rows[1].id,
            },
        ),
    ]

    assert not hasattr(
        result.items[0],
        "tenant_id",
    )
    assert not hasattr(
        result.items[0],
        "key_hash",
    )
    assert not hasattr(
        result.items[0],
        "key",
    )

@pytest.mark.asyncio
async def test_partner_service_revokes_tenant_api_key_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiKeyView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    timestamp = datetime(
        2026,
        9,
        4,
        17,
        0,
        tzinfo=UTC,
    )
    calls = []

    api_key = SimpleNamespace(
        id=api_key_id,
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        name="Production key",
        key_prefix="sghr_0123456789abcdef",
        key_hash="private-hash",
        environment="production",
        status="active",
        expires_at=None,
        last_used_at=None,
        ip_allowlist=None,
        created_at=timestamp,
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeRepository:
        async def get_api_key_for_update(
            self,
            **kwargs,
        ):
            calls.append(
                ("lock", kwargs)
            )
            return api_key

        async def revoke_api_key(
            self,
            **kwargs,
        ):
            calls.append(
                ("revoke", kwargs)
            )
            kwargs["api_key"].status = "revoked"
            return kwargs["api_key"]

    session = FakeSession()
    service = PartnerApiService(
        session=session,
        repository=FakeRepository(),
    )

    result = await service.revoke_api_key(
        tenant_id=tenant_id,
        api_key_id=api_key_id,
    )

    assert isinstance(result, ApiKeyView)
    assert result.id == api_key_id
    assert result.status == "revoked"
    assert not hasattr(result, "key_hash")

    assert calls == [
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "api_key_id": api_key_id,
            },
        ),
        (
            "revoke",
            {
                "api_key": api_key,
            },
        ),
    ]
    assert session.commits == 1
    assert session.rollbacks == 0



@pytest.mark.asyncio
async def test_partner_service_issues_api_key_plaintext_once():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiKeyIssuedView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        18,
        0,
        tzinfo=UTC,
    )
    plaintext_key = (
        "sghr_0123456789abcdef."
        "private-api-key-secret"
    )
    calls = []

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeCodec:
        def issue_api_key(self):
            calls.append(("issue", {}))
            return SimpleNamespace(
                key=plaintext_key,
                key_prefix=(
                    "sghr_0123456789abcdef"
                ),
                key_hash="a" * 64,
            )

    class FakeRepository:
        async def get_api_client(
            self,
            **kwargs,
        ):
            calls.append(("client", kwargs))
            return SimpleNamespace(
                id=api_client_id,
                status="active",
            )

        async def create_api_key(
            self,
            **kwargs,
        ):
            calls.append(("key", kwargs))
            assert "key" not in kwargs
            return SimpleNamespace(
                id=api_key_id,
                tenant_id=tenant_id,
                api_client_id=api_client_id,
                name=kwargs["name"],
                key_prefix=kwargs["key_prefix"],
                key_hash=kwargs["key_hash"],
                environment=kwargs["environment"],
                status="active",
                expires_at=None,
                last_used_at=None,
                ip_allowlist=None,
                created_at=created_at,
            )

        async def create_api_key_scopes(
            self,
            **kwargs,
        ):
            calls.append(("scopes", kwargs))
            return tuple(
                SimpleNamespace(scope=scope)
                for scope in kwargs["scopes"]
            )

    session = FakeSession()
    service = PartnerApiService(
        session=session,
        repository=FakeRepository(),
        api_key_codec=FakeCodec(),
        environment="production",
    )

    result = await service.create_api_key(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        name="Production integration",
        expires_at=None,
        ip_allowlist=None,
        scopes=(
            "services.read",
            "reviews.read",
            "services.read",
        ),
    )

    assert isinstance(
        result,
        ApiKeyIssuedView,
    )
    assert result.key == plaintext_key
    assert result.scopes == (
        "services.read",
        "reviews.read",
    )
    assert not hasattr(result, "key_hash")
    assert not hasattr(result, "tenant_id")

    assert calls[-1] == (
        "scopes",
        {
            "tenant_id": tenant_id,
            "api_key_id": api_key_id,
            "scopes": (
                "services.read",
                "reviews.read",
            ),
        },
    )
    assert session.commits == 1
    assert session.rollbacks == 0


def test_partner_api_scope_registry_is_release_whitelist():
    import pytest

    from services.partner_api import (
        ALLOWED_API_KEY_SCOPES,
        PartnerApiScopeError,
        validate_api_key_scopes,
    )

    expected = frozenset(
        {
            "specialists.read",
            "specialists.search",
            "professional_cabinets.read",
            "services.read",
            "contact_requests.read",
            "contact_requests.write",
            "service_orders.read",
            "service_orders.write",
            "reviews.read",
            "files.read",
            "webhooks.read",
            "webhooks.write",
        }
    )

    assert ALLOWED_API_KEY_SCOPES == expected

    result = validate_api_key_scopes(
        [
            "services.read",
            "reviews.read",
            "services.read",
        ]
    )
    assert result == (
        "services.read",
        "reviews.read",
    )

    forbidden = (
        "admin.users.read",
        "super_admin.tenants.read",
        "root.access",
        "finance.read",
        "billing.write",
        "hr.candidates.read",
        "construction.core",
        "legal.documents.read",
        "unknown.scope",
    )

    for scope in forbidden:
        with pytest.raises(
            PartnerApiScopeError
        ):
            validate_api_key_scopes(
                [scope]
            )

    with pytest.raises(
        PartnerApiScopeError
    ):
        validate_api_key_scopes([])



def test_api_key_scope_model_is_tenant_scoped_and_normalized():
    from sqlalchemy import (
        ForeignKeyConstraint,
        UniqueConstraint,
    )

    from database.models import ApiKeyScope

    table = ApiKeyScope.__table__

    assert table.name == "api_key_scopes"
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "api_key_id",
        "scope",
        "created_at",
    }

    assert table.c.tenant_id.nullable is False
    assert table.c.api_key_id.nullable is False
    assert table.c.scope.nullable is False
    assert table.c.created_at.nullable is False

    composite_foreign_keys = {
        tuple(
            element.target_fullname
            for element in constraint.elements
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }
    assert (
        "api_keys.tenant_id",
        "api_keys.id",
    ) in composite_foreign_keys

    unique_columns = {
        frozenset(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }
    assert frozenset(
        {
            "tenant_id",
            "api_key_id",
            "scope",
        }
    ) in unique_columns

    forbidden_columns = {
        "permission",
        "permissions",
        "role",
        "roles",
        "metadata",
    }
    assert not (
        forbidden_columns
        & set(table.columns.keys())
    )



@pytest.mark.asyncio
async def test_partner_repository_stores_tenant_scoped_api_key_scopes():
    from uuid import uuid4

    from database.models import ApiKeyScope
    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_key_id = uuid4()
    added = []

    class FakeSession:
        def add(self, value):
            added.append(value)

        async def flush(self):
            pass

    repository = PartnerApiRepository(
        FakeSession()
    )

    result = (
        await repository.create_api_key_scopes(
            tenant_id=tenant_id,
            api_key_id=api_key_id,
            scopes=(
                "services.read",
                "reviews.read",
            ),
        )
    )

    assert isinstance(result, tuple)
    assert result == tuple(added)
    assert len(result) == 2

    assert all(
        isinstance(item, ApiKeyScope)
        for item in result
    )
    assert {
        item.scope
        for item in result
    } == {
        "services.read",
        "reviews.read",
    }
    assert all(
        item.tenant_id == tenant_id
        for item in result
    )
    assert all(
        item.api_key_id == api_key_id
        for item in result
    )
    assert (
        "permission"
        not in ApiKeyScope.__table__.columns
    )
    assert (
        "permissions"
        not in ApiKeyScope.__table__.columns
    )
    assert (
        "metadata"
        not in ApiKeyScope.__table__.columns
    )



def test_api_client_status_uses_release_allowlist():
    from sqlalchemy import CheckConstraint

    from database.models import ApiClient

    constraints = {
        constraint.name: constraint
        for constraint
        in ApiClient.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert "ck_api_clients_status" in constraints

    definition = str(
        constraints[
            "ck_api_clients_status"
        ].sqltext
    )

    assert "'active'" in definition
    assert "'suspended'" in definition
    assert "'disabled'" in definition

    assert "'revoked'" not in definition
    assert "'expired'" not in definition
    assert "'deleted'" not in definition



def test_partner_api_environment_comes_from_deployment_config():
    import pytest

    from api.settings import (
        ApiConfigurationError,
        ApiPartnerSettings,
    )

    sandbox = ApiPartnerSettings.from_env(
        {
            "API_ENVIRONMENT": "sandbox",
        }
    )
    production = ApiPartnerSettings.from_env(
        {
            "API_ENVIRONMENT": "production",
        }
    )

    assert sandbox.environment == "sandbox"
    assert production.environment == "production"

    with pytest.raises(
        ApiConfigurationError
    ):
        ApiPartnerSettings.from_env(
            {
                "API_ENVIRONMENT": "staging",
            }
        )

    with pytest.raises(
        ApiConfigurationError
    ):
        ApiPartnerSettings.from_env(
            {
                "API_ENVIRONMENT": "",
            }
        )

    from pathlib import Path

    env_example = Path(
        ".env.example"
    ).read_text(encoding="utf-8-sig")

    assert (
        "API_ENVIRONMENT=sandbox"
        in env_example
    )



@pytest.mark.asyncio
async def test_partner_repository_lists_scopes_by_tenant_and_api_key():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_key_id = uuid4()

    class FakeScalars:
        def all(self):
            return [
                "services.read",
                "reviews.read",
            ]

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
    repository = PartnerApiRepository(session)

    result = await repository.list_api_key_scopes(
        tenant_id=tenant_id,
        api_key_id=api_key_id,
    )

    assert result == (
        "services.read",
        "reviews.read",
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

    assert "FROM api_key_scopes" in sql
    assert "api_key_scopes.tenant_id" in sql
    assert "api_key_scopes.api_key_id" in sql
    assert str(tenant_id) in sql
    assert str(api_key_id) in sql
    assert "api_key_scopes.scope" in sql



@pytest.mark.asyncio
async def test_partner_api_authentication_resolves_scoped_principal():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api_auth import (
        PartnerApiAuthenticationService,
        PartnerApiPrincipal,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    now = datetime(
        2026,
        9,
        5,
        12,
        0,
        tzinfo=UTC,
    )
    plaintext_key = (
        "sghr_0123456789abcdef."
        "private-api-key-secret"
    )
    key_prefix = "sghr_0123456789abcdef"
    key_hash = "a" * 64
    calls = []

    api_key = SimpleNamespace(
        id=api_key_id,
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        key_hash=key_hash,
        environment="production",
        status="active",
        ip_allowlist=None,
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeCodec:
        def extract_key_prefix(self, value):
            calls.append(
                ("prefix", value)
            )
            return key_prefix

        def verify_api_key(
            self,
            value,
            expected_hash,
        ):
            calls.append(
                (
                    "verify",
                    value,
                    expected_hash,
                )
            )
            return True

    class FakeRepository:
        async def get_active_api_key(
            self,
            **kwargs,
        ):
            calls.append(("key", kwargs))
            return api_key

        async def list_api_key_scopes(
            self,
            **kwargs,
        ):
            calls.append(("scopes", kwargs))
            return (
                "services.read",
                "reviews.read",
            )

        async def mark_api_key_used(
            self,
            **kwargs,
        ):
            calls.append(("used", kwargs))
            return kwargs["api_key"]

    session = FakeSession()
    service = PartnerApiAuthenticationService(
        session=session,
        repository=FakeRepository(),
        codec=FakeCodec(),
        environment="production",
    )

    result = await service.authenticate(
        api_key=plaintext_key,
        remote_ip="192.0.2.10",
        now=now,
    )

    assert isinstance(
        result,
        PartnerApiPrincipal,
    )
    assert result.tenant_id == tenant_id
    assert result.api_client_id == (
        api_client_id
    )
    assert result.api_key_id == api_key_id
    assert result.scopes == frozenset(
        {
            "services.read",
            "reviews.read",
        }
    )

    assert not hasattr(result, "key")
    assert not hasattr(result, "key_hash")
    assert not hasattr(result, "roles")
    assert not hasattr(result, "permissions")

    assert (
        "production"
        == calls[1][1]["environment"]
    )
    assert calls[-1] == (
        "used",
        {
            "api_key": api_key,
            "now": now,
        },
    )
    assert session.commits == 1
    assert session.rollbacks == 0



def test_partner_api_authorization_uses_only_canonical_api_key_scheme():
    import pytest

    from api.auth import (
        parse_api_key_authorization,
    )
    from api.errors import ApiHttpError

    plaintext_key = (
        "sghr_0123456789abcdef."
        "private-api-key-secret"
    )

    assert parse_api_key_authorization(
        f"ApiKey {plaintext_key}"
    ) == plaintext_key

    invalid_headers = (
        None,
        "",
        plaintext_key,
        f"Bearer {plaintext_key}",
        f"apikey {plaintext_key}",
        f"APIKEY {plaintext_key}",
        "ApiKey",
        "ApiKey ",
        f"ApiKey {plaintext_key} extra",
    )

    for header in invalid_headers:
        with pytest.raises(
            ApiHttpError
        ) as captured:
            parse_api_key_authorization(
                header
            )

        assert (
            captured.value.status_code
            == 401
        )
        assert captured.value.code == (
            "invalid_api_key"
        )



@pytest.mark.asyncio
async def test_partner_api_dependency_uses_authorization_and_server_context():
    from datetime import UTC
    import inspect
    from types import SimpleNamespace
    from uuid import uuid4

    from api.auth import (
        get_partner_api_principal,
    )
    from services.partner_api_auth import (
        PartnerApiPrincipal,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    plaintext_key = (
        "sghr_0123456789abcdef."
        "private-api-key-secret"
    )
    calls = []

    expected = PartnerApiPrincipal(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        api_key_id=api_key_id,
        scopes=frozenset(
            {"services.read"}
        ),
    )

    class FakeService:
        async def authenticate(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    request = SimpleNamespace(
        client=SimpleNamespace(
            host="192.0.2.10",
        ),
        state=SimpleNamespace(),
    )

    result = await get_partner_api_principal(
        request=request,
        authorization=(
            f"ApiKey {plaintext_key}"
        ),
        service=FakeService(),
    )

    assert result is expected
    assert (
        request.state.partner_api_principal
        is expected
    )
    assert len(calls) == 1
    assert calls[0]["api_key"] == (
        plaintext_key
    )
    assert calls[0]["remote_ip"] == (
        "192.0.2.10"
    )
    assert calls[0]["now"].tzinfo is UTC

    parameters = inspect.signature(
        get_partner_api_principal
    ).parameters

    assert "authorization" in parameters
    assert "x_api_key" not in parameters
    assert "tenant_id" not in parameters
    assert "environment" not in parameters
    assert "user_id" not in parameters



@pytest.mark.asyncio
async def test_partner_api_scope_guard_is_fail_closed():
    from uuid import uuid4

    import pytest

    from api.auth import (
        require_partner_api_scope,
    )
    from api.errors import ApiHttpError
    from services.partner_api import (
        PartnerApiScopeError,
    )
    from services.partner_api_auth import (
        PartnerApiPrincipal,
    )

    principal = PartnerApiPrincipal(
        tenant_id=uuid4(),
        api_client_id=uuid4(),
        api_key_id=uuid4(),
        scopes=frozenset(
            {
                "services.read",
                "reviews.read",
            }
        ),
    )

    guard = require_partner_api_scope(
        "services.read",
    )
    assert await guard(
        principal=principal
    ) is principal

    missing_guard = (
        require_partner_api_scope(
            "webhooks.write",
        )
    )

    with pytest.raises(
        ApiHttpError
    ) as captured:
        await missing_guard(
            principal=principal
        )

    assert captured.value.status_code == 403
    assert captured.value.code == (
        "api_key_scope_required"
    )

    forbidden = (
        "admin.users.read",
        "super_admin.access",
        "root.access",
        "finance.read",
        "billing.write",
        "hr.candidates.read",
        "construction.core",
        "legal.documents.read",
        "unknown.scope",
    )

    for scope in forbidden:
        with pytest.raises(
            PartnerApiScopeError
        ):
            require_partner_api_scope(scope)



def test_api_log_model_is_metadata_only_and_tenant_scoped():
    from sqlalchemy import ForeignKeyConstraint

    from database.models import ApiLog

    table = ApiLog.__table__

    assert table.name == "api_logs"
    assert set(table.columns.keys()) == {
        "id",
        "request_id",
        "api_key_id",
        "api_client_id",
        "tenant_id",
        "user_id",
        "endpoint",
        "method",
        "status_code",
        "duration_ms",
        "ip",
        "user_agent",
        "created_at",
    }

    assert table.c.request_id.nullable is False
    assert table.c.api_key_id.nullable is False
    assert table.c.api_client_id.nullable is False
    assert table.c.tenant_id.nullable is False
    assert table.c.user_id.nullable is True
    assert table.c.endpoint.nullable is False
    assert table.c.method.nullable is False
    assert table.c.status_code.nullable is False
    assert table.c.duration_ms.nullable is False
    assert table.c.created_at.nullable is False

    foreign_key_targets = {
        element.target_fullname
        for constraint in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
        for element in constraint.elements
    }

    assert "api_keys.id" in foreign_key_targets
    assert "api_clients.id" in foreign_key_targets
    assert "tenants.id" in foreign_key_targets
    assert "users.id" in foreign_key_targets

    forbidden = {
        "request_body",
        "response_body",
        "body",
        "authorization",
        "api_key",
        "key_hash",
        "token",
        "password",
        "email",
        "phone",
        "metadata",
    }

    assert not (
        forbidden
        & set(table.columns.keys())
    )



@pytest.mark.asyncio
async def test_partner_repository_records_metadata_only_api_log():
    from datetime import UTC, datetime
    from uuid import uuid4

    from database.models import ApiLog
    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_key_id = uuid4()
    api_client_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        14,
        0,
        tzinfo=UTC,
    )
    added = []

    class FakeSession:
        def add(self, value):
            added.append(value)

        async def flush(self):
            pass

    repository = PartnerApiRepository(
        FakeSession()
    )

    result = await repository.create_api_log(
        request_id="partner-request-123",
        tenant_id=tenant_id,
        api_key_id=api_key_id,
        api_client_id=api_client_id,
        user_id=None,
        endpoint="/api/v1/partner/services",
        method="GET",
        status_code=200,
        duration_ms=37,
        ip="192.0.2.10",
        user_agent="Partner Client/1.0",
        created_at=created_at,
    )

    assert isinstance(result, ApiLog)
    assert added == [result]

    assert result.request_id == (
        "partner-request-123"
    )
    assert result.tenant_id == tenant_id
    assert result.api_key_id == api_key_id
    assert result.api_client_id == (
        api_client_id
    )
    assert result.user_id is None
    assert result.endpoint == (
        "/api/v1/partner/services"
    )
    assert result.method == "GET"
    assert result.status_code == 200
    assert result.duration_ms == 37
    assert result.ip == "192.0.2.10"
    assert result.user_agent == (
        "Partner Client/1.0"
    )
    assert result.created_at == created_at

    columns = set(
        ApiLog.__table__.columns.keys()
    )
    assert "request_body" not in columns
    assert "response_body" not in columns
    assert "authorization" not in columns
    assert "api_key" not in columns
    assert "key_hash" not in columns



@pytest.mark.asyncio
async def test_partner_api_client_create_endpoint_uses_jwt_actor_tenant():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.partner_api import (
        ApiClientView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    owner_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        15,
        0,
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
        permissions=(
            "api.clients.create",
        ),
    )

    class FakeService:
        async def create_api_client(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiClientView(
                id=api_client_id,
                owner_type="partner",
                owner_id=owner_id,
                name="Release integration",
                status="active",
                metadata={
                    "system": "crm",
                },
                created_at=created_at,
                updated_at=created_at,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/partner/api-clients",
            json={
                "owner_type": "partner",
                "owner_id": str(owner_id),
                "name": "Release integration",
                "metadata": {
                    "system": "crm",
                },
            },
            headers={
                "X-Request-ID": (
                    "partner-client-create"
                ),
            },
        )

    assert response.status_code == 201
    payload = response.json()

    assert payload["data"]["id"] == (
        str(api_client_id)
    )
    assert payload["data"]["status"] == (
        "active"
    )
    assert "tenant_id" not in payload["data"]

    assert calls == [
        {
            "tenant_id": tenant_id,
            "owner_type": "partner",
            "owner_id": owner_id,
            "name": "Release integration",
            "metadata": {
                "system": "crm",
            },
        }
    ]

    operation = application.openapi()[
        "paths"
    ][
        "/api/v1/partner/api-clients"
    ]["post"]

    assert all(
        parameter["name"] != "tenant_id"
        for parameter
        in operation.get("parameters", [])
    )



@pytest.mark.asyncio
async def test_partner_api_client_list_endpoint_uses_jwt_actor_tenant():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.partner_api import (
        ApiClientPage,
        ApiClientView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        16,
        0,
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
        permissions=(
            "api.clients.read",
        ),
    )

    item = ApiClientView(
        id=api_client_id,
        owner_type="partner",
        owner_id=None,
        name="Release integration",
        status="active",
        metadata={},
        created_at=created_at,
        updated_at=created_at,
    )

    class FakeService:
        async def list_api_clients(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiClientPage(
                items=(item,),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/partner/api-clients",
            params={"limit": 20},
            headers={
                "X-Request-ID": (
                    "partner-client-list"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"][0]["id"] == (
        str(api_client_id)
    )
    assert "tenant_id" not in (
        payload["data"][0]
    )
    assert payload["meta"] == {
        "next_cursor": None,
        "has_more": False,
    }

    assert calls == [
        {
            "tenant_id": tenant_id,
            "page": 0,
            "page_size": 20,
        }
    ]



@pytest.mark.asyncio
async def test_partner_api_client_detail_endpoint_uses_jwt_actor_tenant():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.partner_api import (
        ApiClientView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        17,
        0,
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
        permissions=(
            "api.clients.read",
        ),
    )

    item = ApiClientView(
        id=api_client_id,
        owner_type="partner",
        owner_id=None,
        name="Release integration",
        status="active",
        metadata={},
        created_at=created_at,
        updated_at=created_at,
    )

    class FakeService:
        async def get_api_client(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return item

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
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
                "/api/v1/partner/"
                f"api-clients/{api_client_id}"
            ),
            headers={
                "X-Request-ID": (
                    "partner-client-detail"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"]["id"] == (
        str(api_client_id)
    )
    assert "tenant_id" not in payload["data"]

    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
        }
    ]



@pytest.mark.asyncio
async def test_partner_api_key_create_endpoint_uses_jwt_actor_and_hides_environment():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.partner_api import (
        ApiKeyIssuedView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        18,
        0,
        tzinfo=UTC,
    )
    plaintext_key = (
        "sghr_0123456789abcdef."
        "private-api-key-secret"
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
        permissions=(
            "api.keys.create",
        ),
    )

    class FakeService:
        async def create_api_key(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiKeyIssuedView(
                id=api_key_id,
                api_client_id=api_client_id,
                name="Production CRM",
                key_prefix=(
                    "sghr_0123456789abcdef"
                ),
                environment="production",
                status="active",
                expires_at=None,
                last_used_at=None,
                ip_allowlist=(),
                created_at=created_at,
                scopes=(
                    "services.read",
                    "reviews.read",
                ),
                key=plaintext_key,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
    ] = lambda: FakeService()

    valid_body = {
        "name": "Production CRM",
        "expires_at": None,
        "ip_allowlist": [],
        "scopes": [
            "services.read",
            "reviews.read",
        ],
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/partner/api-clients/"
                f"{api_client_id}/keys"
            ),
            json=valid_body,
            headers={
                "X-Request-ID": (
                    "partner-key-create"
                ),
                "Idempotency-Key": (
                    "partner-key-create-001"
                ),
            },
        )

        invalid_response = await client.post(
            (
                "/api/v1/partner/api-clients/"
                f"{api_client_id}/keys"
            ),
            json={
                **valid_body,
                "environment": "sandbox",
            },
            headers={
                "Idempotency-Key": (
                    "partner-key-invalid-001"
                ),
            },
        )

    assert response.status_code == 201
    payload = response.json()

    assert payload["data"]["key"] == (
        plaintext_key
    )
    assert payload["data"]["scopes"] == [
        "services.read",
        "reviews.read",
    ]
    assert "key_hash" not in payload["data"]
    assert "tenant_id" not in payload["data"]

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "idempotency_key": (
                "partner-key-create-001"
            ),
            "api_client_id": api_client_id,
            "name": "Production CRM",
            "expires_at": None,
            "ip_allowlist": [],
            "scopes": (
                "services.read",
                "reviews.read",
            ),
        }
    ]

    assert invalid_response.status_code == 422



@pytest.mark.asyncio
async def test_partner_service_lists_api_keys_with_scopes_and_without_secrets():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiKeyPage,
        PartnerApiService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        19,
        0,
        tzinfo=UTC,
    )
    calls = []

    api_key = SimpleNamespace(
        id=api_key_id,
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        name="CRM integration",
        key_prefix="sghr_0123456789abcdef",
        key_hash="private-hash",
        environment="production",
        status="active",
        expires_at=None,
        last_used_at=None,
        ip_allowlist=None,
        created_at=created_at,
    )

    class FakeRepository:
        async def get_api_client(
            self,
            **kwargs,
        ):
            calls.append(("client", kwargs))
            return SimpleNamespace(
                id=api_client_id,
            )

        async def list_api_keys(
            self,
            **kwargs,
        ):
            calls.append(("keys", kwargs))
            return [api_key]

        async def list_api_key_scopes(
            self,
            **kwargs,
        ):
            calls.append(("scopes", kwargs))
            return (
                "services.read",
                "reviews.read",
            )

    service = PartnerApiService(
        session=object(),
        repository=FakeRepository(),
    )

    result = await service.list_api_keys(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        page=0,
        page_size=20,
    )

    assert isinstance(result, ApiKeyPage)
    assert len(result.items) == 1

    item = result.items[0]
    assert item.scopes == (
        "services.read",
        "reviews.read",
    )
    assert not hasattr(item, "key")
    assert not hasattr(item, "key_hash")
    assert not hasattr(item, "tenant_id")

    assert (
        "scopes",
        {
            "tenant_id": tenant_id,
            "api_key_id": api_key_id,
        },
    ) in calls



@pytest.mark.asyncio
async def test_partner_api_key_list_endpoint_uses_jwt_actor_and_hides_secrets():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.partner_api import (
        ApiKeyPage,
        ApiKeyView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        20,
        0,
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
        permissions=(
            "api.keys.read",
        ),
    )

    item = ApiKeyView(
        id=api_key_id,
        api_client_id=api_client_id,
        name="CRM integration",
        key_prefix="sghr_0123456789abcdef",
        environment="production",
        status="active",
        expires_at=None,
        last_used_at=None,
        ip_allowlist=(),
        created_at=created_at,
        scopes=(
            "services.read",
            "reviews.read",
        ),
    )

    class FakeService:
        async def list_api_keys(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiKeyPage(
                items=(item,),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
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
                "/api/v1/partner/api-clients/"
                f"{api_client_id}/keys"
            ),
            params={"limit": 20},
            headers={
                "X-Request-ID": (
                    "partner-key-list"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"][0]

    assert data["id"] == str(api_key_id)
    assert data["scopes"] == [
        "services.read",
        "reviews.read",
    ]
    assert "key" not in data
    assert "key_hash" not in data
    assert "tenant_id" not in data

    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
            "page": 0,
            "page_size": 20,
        }
    ]



@pytest.mark.asyncio
async def test_partner_api_key_revoke_endpoint_uses_jwt_actor_tenant():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.partner_api import ApiKeyView

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        21,
        0,
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
        permissions=(
            "api.keys.revoke",
        ),
    )

    class FakeService:
        async def revoke_api_key(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiKeyView(
                id=api_key_id,
                api_client_id=api_client_id,
                name="CRM integration",
                key_prefix=(
                    "sghr_0123456789abcdef"
                ),
                environment="production",
                status="revoked",
                expires_at=None,
                last_used_at=None,
                ip_allowlist=(),
                created_at=created_at,
                scopes=(),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
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
                "/api/v1/partner/"
                f"api-keys/{api_key_id}/revoke"
            ),
            headers={
                "X-Request-ID": (
                    "partner-key-revoke"
                ),
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "api_key_id": str(api_key_id),
        "status": "revoked",
    }

    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_key_id": api_key_id,
        }
    ]


@pytest.mark.asyncio
async def test_partner_api_request_log_service_records_metadata_only():
    from datetime import datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api_logging import (
        PartnerApiRequestLogService,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    calls = []

    principal = SimpleNamespace(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        api_key_id=api_key_id,
        scopes=("specialists.read",),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", None))

    class FakeRepository:
        async def create_api_log(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(**kwargs)

    service = PartnerApiRequestLogService(
        session=FakeSession(),
        repository=FakeRepository(),
    )

    result = await service.record_request(
        principal=principal,
        request_id="partner-request-1",
        endpoint="/api/v1/partner/specialists",
        method="GET",
        status_code=200,
        duration_ms=17,
        ip="192.0.2.10",
        user_agent="partner-client/1.0",
    )

    assert calls[0][0] == "create"
    values = calls[0][1]

    assert set(values) == {
        "request_id",
        "tenant_id",
        "api_key_id",
        "api_client_id",
        "user_id",
        "endpoint",
        "method",
        "status_code",
        "duration_ms",
        "ip",
        "user_agent",
        "created_at",
    }
    assert values["request_id"] == (
        "partner-request-1"
    )
    assert values["tenant_id"] == tenant_id
    assert values["api_client_id"] == (
        api_client_id
    )
    assert values["api_key_id"] == api_key_id
    assert values["user_id"] is None
    assert values["endpoint"] == (
        "/api/v1/partner/specialists"
    )
    assert values["method"] == "GET"
    assert values["status_code"] == 200
    assert values["duration_ms"] == 17
    assert values["ip"] == "192.0.2.10"
    assert values["user_agent"] == (
        "partner-client/1.0"
    )
    assert isinstance(
        values["created_at"],
        datetime,
    )
    assert values["created_at"].tzinfo is not None
    assert values["created_at"].utcoffset() is not None

    assert calls[1] == ("commit", None)
    assert result.request_id == (
        "partner-request-1"
    )


@pytest.mark.asyncio
async def test_partner_api_logging_middleware_records_authenticated_request_only():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx
    from fastapi import FastAPI, Request

    from api.middleware import (
        PartnerApiRequestLoggingMiddleware,
    )

    calls = []
    principal = SimpleNamespace(
        tenant_id=uuid4(),
        api_client_id=uuid4(),
        api_key_id=uuid4(),
        scopes=("specialists.read",),
    )

    class FakeLogger:
        async def record_request(
            self,
            **kwargs,
        ):
            calls.append(kwargs)

    application = FastAPI()
    application.add_middleware(
        PartnerApiRequestLoggingMiddleware,
        logger=FakeLogger(),
    )

    @application.get("/partner-resource")
    async def partner_resource(
        request: Request,
    ):
        request.state.request_id = (
            "partner-http-request"
        )
        request.state.partner_api_principal = (
            principal
        )
        return {"status": "ok"}

    @application.get("/public-resource")
    async def public_resource():
        return {"status": "ok"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
        headers={
            "User-Agent": "partner-test/1.0",
        },
    ) as client:
        response = await client.get(
            (
                "/partner-resource"
                "?token=must-not-be-logged"
            ),
            headers={
                "Authorization": (
                    "ApiKey private-api-key"
                ),
            },
        )
        public_response = await client.get(
            "/public-resource"
        )

    assert response.status_code == 200
    assert public_response.status_code == 200
    assert len(calls) == 1

    values = calls[0]
    assert set(values) == {
        "principal",
        "request_id",
        "endpoint",
        "method",
        "status_code",
        "duration_ms",
        "ip",
        "user_agent",
    }
    assert values["principal"] is principal
    assert values["request_id"] == (
        "partner-http-request"
    )
    assert values["endpoint"] == (
        "/partner-resource"
    )
    assert values["method"] == "GET"
    assert values["status_code"] == 200
    assert isinstance(
        values["duration_ms"],
        int,
    )
    assert values["duration_ms"] >= 0
    assert values["ip"] == "127.0.0.1"
    assert values["user_agent"] == (
        "partner-test/1.0"
    )

    serialized = repr(values)
    assert "private-api-key" not in serialized
    assert "must-not-be-logged" not in serialized
    assert "Authorization" not in serialized


@pytest.mark.asyncio
async def test_partner_api_principal_is_attached_to_request_state():
    from types import SimpleNamespace
    from uuid import uuid4

    from starlette.requests import Request

    from api.auth import (
        get_partner_api_principal,
    )

    principal = SimpleNamespace(
        tenant_id=uuid4(),
        api_client_id=uuid4(),
        api_key_id=uuid4(),
        scopes=("specialists.read",),
    )
    calls = []

    class FakeAuthenticationService:
        async def authenticate(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return principal

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": (
                "/api/v1/partner/specialists"
            ),
            "raw_path": (
                b"/api/v1/partner/specialists"
            ),
            "query_string": b"",
            "headers": [],
            "client": ("192.0.2.10", 443),
            "server": ("api.example.com", 443),
        }
    )

    result = await get_partner_api_principal(
        request=request,
        service=FakeAuthenticationService(),
        authorization=(
            "ApiKey sghr_test.private-secret"
        ),
    )

    assert result is principal
    assert (
        request.state.partner_api_principal
        is principal
    )
    assert len(calls) == 1
    assert calls[0]["api_key"] == (
        "sghr_test.private-secret"
    )
    assert calls[0]["remote_ip"] == (
        "192.0.2.10"
    )
    assert calls[0]["now"].tzinfo is not None
    assert calls[0]["now"].utcoffset() is not None


def test_api_app_registers_partner_api_request_logging_middleware():
    from api.app import create_app
    from api.middleware import (
        PartnerApiRequestLoggingMiddleware,
    )

    application = create_app()

    middleware = [
        item
        for item in application.user_middleware
        if (
            item.cls
            is PartnerApiRequestLoggingMiddleware
        )
    ]

    assert len(middleware) == 1
    assert "logger" in middleware[0].kwargs
    assert hasattr(
        middleware[0].kwargs["logger"],
        "record_request",
    )


@pytest.mark.asyncio
async def test_partner_api_authenticated_http_request_is_logged_without_secrets():
    from uuid import uuid4

    import httpx
    from fastapi import Depends

    from api.app import create_app
    from api.auth import (
        require_partner_api_scope,
    )
    from api.dependencies import (
        get_partner_api_authentication_service,
    )
    from api.middleware import (
        PartnerApiRequestLoggingMiddleware,
    )
    from services.partner_api_auth import (
        PartnerApiPrincipal,
    )

    calls = []
    principal = PartnerApiPrincipal(
        tenant_id=uuid4(),
        api_client_id=uuid4(),
        api_key_id=uuid4(),
        scopes=("specialists.read",),
    )

    class FakeAuthenticationService:
        async def authenticate(
            self,
            **kwargs,
        ):
            calls.append(("authenticate", kwargs))
            return principal

    class FakeLogger:
        async def record_request(
            self,
            **kwargs,
        ):
            calls.append(("log", kwargs))

    application = create_app()
    application.dependency_overrides[
        get_partner_api_authentication_service
    ] = lambda: FakeAuthenticationService()

    middleware = [
        item
        for item in application.user_middleware
        if (
            item.cls
            is PartnerApiRequestLoggingMiddleware
        )
    ]
    assert len(middleware) == 1
    middleware[0].kwargs["logger"] = FakeLogger()

    scope_guard = require_partner_api_scope(
        "specialists.read"
    )

    @application.get(
        "/api/v1/partner/contract-test"
    )
    async def partner_contract_test(
        _principal=Depends(scope_guard),
    ):
        return {"status": "ok"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/partner/contract-test"
                "?token=query-secret"
            ),
            headers={
                "Authorization": (
                    "ApiKey opaque-private-key"
                ),
                "User-Agent": (
                    "partner-contract/1.0"
                ),
                "X-Request-ID": (
                    "partner-http-e2e"
                ),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
    }

    assert calls[0][0] == "authenticate"
    assert calls[0][1]["api_key"] == (
        "opaque-private-key"
    )
    assert calls[0][1]["remote_ip"] == (
        "127.0.0.1"
    )

    assert calls[1][0] == "log"
    logged = calls[1][1]

    assert logged["principal"] is principal
    assert logged["request_id"] == (
        "partner-http-e2e"
    )
    assert logged["endpoint"] == (
        "/api/v1/partner/contract-test"
    )
    assert logged["method"] == "GET"
    assert logged["status_code"] == 200
    assert logged["duration_ms"] >= 0
    assert logged["ip"] == "127.0.0.1"
    assert logged["user_agent"] == (
        "partner-contract/1.0"
    )

    serialized = repr(logged)
    assert "opaque-private-key" not in serialized
    assert "query-secret" not in serialized
    assert "Authorization" not in serialized


@pytest.mark.asyncio
async def test_partner_repository_deletes_api_logs_older_than_retention_cutoff():
    from datetime import UTC, datetime

    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    cutoff = datetime(
        2026,
        6,
        9,
        12,
        0,
        tzinfo=UTC,
    )

    class FakeResult:
        rowcount = 7

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(
            self,
            statement,
        ):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = PartnerApiRepository(session)

    deleted = await repository.delete_api_logs_before(
        cutoff=cutoff,
    )

    assert deleted == 7
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert normalized.startswith(
        "DELETE FROM api_logs"
    )
    assert "api_logs.created_at <" in normalized
    assert "2026-06-09 12:00:00+00:00" in normalized


@pytest.mark.asyncio
async def test_partner_api_request_logger_enforces_90_day_retention(
    monkeypatch,
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    import services.partner_api_logging as logging_module
    from services.partner_api_logging import (
        PartnerApiRequestLogger,
    )

    now = datetime(
        2026,
        9,
        7,
        12,
        0,
        tzinfo=UTC,
    )
    expected_cutoff = datetime(
        2026,
        6,
        9,
        12,
        0,
        tzinfo=UTC,
    )
    calls = []

    principal = SimpleNamespace(
        tenant_id=uuid4(),
        api_client_id=uuid4(),
        api_key_id=uuid4(),
        scopes=("specialists.read",),
    )

    class FixedDateTime:
        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            return now

    class FakeSession:
        async def commit(self):
            calls.append(("commit", None))

    session = FakeSession()

    class FakeSessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(
            self,
            exception_type,
            exception,
            traceback,
        ):
            return False

    class FakeRepository:
        async def delete_api_logs_before(
            self,
            **kwargs,
        ):
            calls.append(("retention", kwargs))
            return 3

        async def create_api_log(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(**kwargs)

    repository = FakeRepository()

    monkeypatch.setattr(
        logging_module,
        "datetime",
        FixedDateTime,
    )
    monkeypatch.setattr(
        logging_module,
        "async_session",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        logging_module,
        "PartnerApiRepository",
        lambda _session: repository,
    )

    logger = PartnerApiRequestLogger()
    await logger.record_request(
        principal=principal,
        request_id="retention-request",
        endpoint="/api/v1/partner/test",
        method="GET",
        status_code=200,
        duration_ms=10,
        ip="192.0.2.10",
        user_agent="partner-test/1.0",
    )

    assert calls[0] == (
        "retention",
        {
            "cutoff": expected_cutoff,
        },
    )
    assert calls[1][0] == "create"
    assert calls[1][1]["created_at"] == now
    assert calls[2] == ("commit", None)


@pytest.mark.asyncio
async def test_api_key_cannot_create_another_api_key():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_partner_api_service,
    )

    calls = []

    class FakePartnerApiService:
        async def create_api_key(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "API key management service "
                "must not be called."
            )

    application = create_app()
    application.dependency_overrides[
        get_partner_api_service
    ] = lambda: FakePartnerApiService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/partner/api-clients/"
                f"{uuid4()}/keys"
            ),
            headers={
                "Authorization": (
                    "ApiKey opaque-private-key"
                ),
                "X-Request-ID": (
                    "api-key-management-denied"
                ),
            },
            json={
                "name": "Forbidden child key",
                "scopes": [
                    "specialists.read",
                ],
                "ip_allowlist": [],
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_partner_auth_rejects_wrong_secret():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from api.security import ApiKeyCodec
    from services.partner_api_auth import (
        PartnerApiAuthenticationError,
        PartnerApiAuthenticationService,
    )

    codec = ApiKeyCodec()
    material = codec.issue_api_key()
    wrong_key = material.key_prefix + ".wrong-secret"
    assert not codec.verify_api_key(wrong_key, material.key_hash)
    codec.verify_api_key = Mock(wraps=codec.verify_api_key)

    stored = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), api_client_id=uuid4(),
        key_hash=material.key_hash, ip_allowlist=None,
    )
    repository = SimpleNamespace(
        get_active_api_key=AsyncMock(return_value=stored),
        list_api_key_scopes=AsyncMock(return_value=("services.read",)),
        mark_api_key_used=AsyncMock(),
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = PartnerApiAuthenticationService(
        session=session, repository=repository,
        codec=codec, environment="production",
    )
    now = datetime.now(UTC)

    with pytest.raises(PartnerApiAuthenticationError) as captured:
        await service.authenticate(
            api_key=wrong_key, remote_ip="192.0.2.10", now=now,
        )

    assert str(captured.value) == "API Key authentication failed."
    repository.get_active_api_key.assert_awaited_once_with(
        key_prefix=material.key_prefix, environment="production", now=now,
    )
    codec.verify_api_key.assert_called_once_with(wrong_key, material.key_hash)
    repository.list_api_key_scopes.assert_not_awaited()
    repository.mark_api_key_used.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_ip,allowed", [
    ("192.0.2.10", True),
    ("198.51.100.10", False),
    (None, False),
])
async def test_partner_auth_enforces_ip_allowlist(remote_ip, allowed):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from api.security import ApiKeyCodec
    from services.partner_api_auth import (
        PartnerApiAuthenticationError,
        PartnerApiAuthenticationService,
    )

    codec = ApiKeyCodec()
    material = codec.issue_api_key()
    stored = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), api_client_id=uuid4(),
        key_hash=material.key_hash, ip_allowlist=["192.0.2.10"],
    )
    repository = SimpleNamespace(
        get_active_api_key=AsyncMock(return_value=stored),
        list_api_key_scopes=AsyncMock(return_value=("services.read",)),
        mark_api_key_used=AsyncMock(),
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = PartnerApiAuthenticationService(
        session=session, repository=repository,
        codec=codec, environment="production",
    )
    now = datetime.now(UTC)
    arguments = dict(api_key=material.key, remote_ip=remote_ip, now=now)

    if allowed:
        principal = await service.authenticate(**arguments)
        assert principal.tenant_id == stored.tenant_id
        assert principal.api_key_id == stored.id
        repository.mark_api_key_used.assert_awaited_once_with(
            api_key=stored, now=now,
        )
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    else:
        with pytest.raises(PartnerApiAuthenticationError) as captured:
            await service.authenticate(**arguments)
        assert str(captured.value) == "API Key authentication failed."
        repository.list_api_key_scopes.assert_not_awaited()
        repository.mark_api_key_used.assert_not_awaited()
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden_scope", [
    "admin.users.read",
    "api.keys.create",
    "hr.candidates.read",
    "unknown.read",
])
async def test_partner_auth_rejects_forbidden_stored_scopes(forbidden_scope):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from api.security import ApiKeyCodec
    from services.partner_api_auth import (
        PartnerApiAuthenticationError,
        PartnerApiAuthenticationService,
    )

    codec = ApiKeyCodec()
    material = codec.issue_api_key()
    stored = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), api_client_id=uuid4(),
        key_hash=material.key_hash, ip_allowlist=None,
    )
    repository = SimpleNamespace(
        get_active_api_key=AsyncMock(return_value=stored),
        list_api_key_scopes=AsyncMock(
            return_value=("services.read", forbidden_scope),
        ),
        mark_api_key_used=AsyncMock(),
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = PartnerApiAuthenticationService(
        session=session, repository=repository,
        codec=codec, environment="production",
    )

    with pytest.raises(PartnerApiAuthenticationError) as captured:
        await service.authenticate(
            api_key=material.key,
            remote_ip="192.0.2.10",
            now=datetime.now(UTC),
        )

    assert str(captured.value) == "API Key authentication failed."
    repository.list_api_key_scopes.assert_awaited_once_with(
        tenant_id=stored.tenant_id, api_key_id=stored.id,
    )
    repository.mark_api_key_used.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_partner_http_500_is_logged_without_exception_details(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import httpx
    from fastapi import Depends

    from api.app import create_app
    from api.auth import require_partner_api_scope
    from api.dependencies import get_partner_api_authentication_service
    from services.partner_api_auth import PartnerApiPrincipal
    from services.partner_api_logging import PartnerApiRequestLogger

    principal = PartnerApiPrincipal(
        tenant_id=uuid4(), api_client_id=uuid4(), api_key_id=uuid4(),
        scopes=frozenset({"services.read"}),
    )
    authentication = SimpleNamespace(
        authenticate=AsyncMock(return_value=principal),
    )
    logger = AsyncMock()
    monkeypatch.setattr(PartnerApiRequestLogger, "record_request", logger)
    application = create_app()
    application.dependency_overrides[
        get_partner_api_authentication_service
    ] = lambda: authentication

    @application.get("/api/v1/partner/error-contract")
    async def failing_endpoint(
        actor=Depends(require_partner_api_scope("services.read")),
    ):
        raise RuntimeError("private-database-detail")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application, raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/partner/error-contract",
            headers={
                "Authorization": "ApiKey private-key",
                "X-Request-ID": "partner-error-contract",
            },
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    authentication.authenticate.assert_awaited_once()
    logger.assert_awaited_once()
    logged = logger.await_args.kwargs
    assert logged["principal"] is principal
    assert logged["status_code"] == 500
    assert logged["request_id"] == "partner-error-contract"
    assert logged["endpoint"] == "/api/v1/partner/error-contract"
    assert "private-database-detail" not in repr(logged)
    assert "private-key" not in repr(logged)
    assert "private-database-detail" not in response.text


@pytest.mark.asyncio
async def test_partner_log_retention_runs_without_http_traffic(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import services.partner_api_logging as logging_module
    from services.partner_api_logging import PartnerApiLogRetentionService

    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    cutoff = datetime(2026, 6, 9, 12, tzinfo=UTC)
    calls = []

    class FixedDateTime:
        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            return now

    async def delete_old_logs(**kwargs):
        calls.append(("delete", kwargs))
        return 3

    async def commit():
        calls.append(("commit", None))

    repository = SimpleNamespace(
        delete_api_logs_before=AsyncMock(side_effect=delete_old_logs),
        create_api_log=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(side_effect=commit), rollback=AsyncMock(),
    )
    monkeypatch.setattr(logging_module, "datetime", FixedDateTime)

    service = PartnerApiLogRetentionService(
        session=session, repository=repository,
    )
    deleted = await service.purge_expired_logs()

    assert deleted == 3
    assert calls == [
        ("delete", {"cutoff": cutoff}),
        ("commit", None),
    ]
    repository.create_api_log.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_partner_api_log_retention_has_periodic_job():
    from pathlib import Path

    script_path = Path(
        "scripts/cleanup_partner_api_logs.py"
    )
    service_path = Path(
        "deploy/systemd/"
        "sghr-partner-api-log-retention.service"
    )
    timer_path = Path(
        "deploy/systemd/"
        "sghr-partner-api-log-retention.timer"
    )

    assert script_path.is_file()
    assert service_path.is_file()
    assert timer_path.is_file()

    script = script_path.read_text(
        encoding="utf-8-sig"
    )
    assert "PartnerApiLogRetentionService" in script
    assert "PartnerApiRepository" in script
    assert "async_session" in script
    assert "asyncio.run" in script

    service = service_path.read_text(
        encoding="utf-8-sig"
    )
    assert "Type=oneshot" in service
    assert (
        "scripts/cleanup_partner_api_logs.py"
        in service
    )
    assert "EnvironmentFile=/opt/sghr/.env" in service

    timer = timer_path.read_text(
        encoding="utf-8-sig"
    )
    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer
    assert (
        "Unit=sghr-partner-api-log-retention.service"
        in timer
    )
    assert "WantedBy=timers.target" in timer



@pytest.mark.asyncio
async def test_partner_service_replays_api_key_creation_without_duplicate():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        ApiKeyIssuedView,
        PartnerApiService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()

    expires_at = datetime(
        2026,
        12,
        8,
        12,
        0,
        tzinfo=UTC,
    )
    created_at = datetime(
        2026,
        9,
        9,
        12,
        0,
        tzinfo=UTC,
    )
    plaintext_key = (
        "sghr_0123456789abcdef."
        "replayed-private-secret"
    )
    reserve_calls = []

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            reserve_calls.append(kwargs)
            return SimpleNamespace(
                is_replay=True,
                response_status=201,
                response_payload={
                    "id": str(api_key_id),
                    "api_client_id": str(
                        api_client_id
                    ),
                    "name": "Production CRM",
                    "key_prefix": (
                        "sghr_0123456789abcdef"
                    ),
                    "environment": "production",
                    "status": "active",
                    "expires_at": (
                        expires_at.isoformat()
                    ),
                    "last_used_at": None,
                    "ip_allowlist": [
                        "203.0.113.10",
                    ],
                    "created_at": (
                        created_at.isoformat()
                    ),
                    "scopes": [
                        "services.read",
                        "reviews.read",
                    ],
                    "key": plaintext_key,
                },
            )

        async def complete(self, **kwargs):
            raise AssertionError(
                "Replay must not be completed again."
            )

    class ForbiddenRepository:
        def __getattr__(self, name):
            raise AssertionError(
                f"Replay must not access repository: {name}"
            )

    class ForbiddenCodec:
        def issue_api_key(self):
            raise AssertionError(
                "Replay must not issue another API key."
            )

    class ForbiddenSession:
        async def commit(self):
            raise AssertionError(
                "Replay must not commit."
            )

        async def rollback(self):
            raise AssertionError(
                "Replay must not roll back."
            )

    service = PartnerApiService(
        session=ForbiddenSession(),
        repository=ForbiddenRepository(),
        api_key_codec=ForbiddenCodec(),
        environment="production",
        idempotency=FakeIdempotency(),
    )

    result = await service.create_api_key(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        idempotency_key="partner-key-create-001",
        api_client_id=api_client_id,
        name="Production CRM",
        expires_at=expires_at,
        ip_allowlist=[
            "203.0.113.10",
        ],
        scopes=(
            "services.read",
            "reviews.read",
        ),
    )

    assert reserve_calls == [
        {
            "tenant_id": tenant_id,
            "principal_type": "user",
            "principal_id": actor_user_id,
            "operation": "api_key.create",
            "idempotency_key": (
                "partner-key-create-001"
            ),
            "payload": {
                "api_client_id": str(
                    api_client_id
                ),
                "name": "Production CRM",
                "expires_at": (
                    expires_at.isoformat()
                ),
                "ip_allowlist": [
                    "203.0.113.10",
                ],
                "scopes": [
                    "services.read",
                    "reviews.read",
                ],
            },
        }
    ]

    assert isinstance(result, ApiKeyIssuedView)
    assert result.id == api_key_id
    assert result.api_client_id == api_client_id
    assert result.key == plaintext_key
    assert result.expires_at == expires_at
    assert result.created_at == created_at
    assert result.scopes == (
        "services.read",
        "reviews.read",
    )



def test_partner_api_dependency_injects_shared_idempotency_service(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_idempotency import (
        ApiIdempotencyService,
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
    service = get_partner_api_service(
        session=session,
    )

    assert isinstance(
        service.idempotency,
        ApiIdempotencyService,
    )
    assert (
        service.idempotency.repository.session
        is session
    )



@pytest.mark.asyncio
async def test_partner_service_completes_api_key_and_idempotency_atomically():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.partner_api import (
        PartnerApiService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=30)
    created_at = now
    plaintext_key = (
        "sghr_0123456789abcdef."
        "new-private-secret"
    )
    events = []

    reservation = SimpleNamespace(
        is_replay=False,
        record=object(),
    )

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            events.append(("reserve", kwargs))
            return reservation

        async def complete(self, **kwargs):
            events.append(("complete", kwargs))
            assert kwargs["reservation"] is reservation
            assert kwargs["response_status"] == 201

            payload = kwargs["response_payload"]
            assert payload["id"] == str(api_key_id)
            assert payload["api_client_id"] == str(
                api_client_id
            )
            assert payload["key"] == plaintext_key
            assert payload["key_prefix"] == (
                "sghr_0123456789abcdef"
            )
            assert payload["environment"] == (
                "production"
            )
            assert payload["scopes"] == [
                "services.read",
                "reviews.read",
            ]
            assert payload["expires_at"] == (
                expires_at.isoformat()
            )
            assert payload["created_at"] == (
                created_at.isoformat()
            )

    class FakeCodec:
        def issue_api_key(self):
            events.append(("issue", {}))
            return SimpleNamespace(
                key=plaintext_key,
                key_prefix=(
                    "sghr_0123456789abcdef"
                ),
                key_hash="a" * 64,
            )

    class FakeRepository:
        async def get_api_client(self, **kwargs):
            events.append(("client", kwargs))
            return SimpleNamespace(
                id=api_client_id,
                status="active",
            )

        async def create_api_key(self, **kwargs):
            events.append(("key", kwargs))
            return SimpleNamespace(
                id=api_key_id,
                api_client_id=api_client_id,
                name=kwargs["name"],
                key_prefix=kwargs["key_prefix"],
                key_hash=kwargs["key_hash"],
                environment=kwargs["environment"],
                status="active",
                expires_at=kwargs["expires_at"],
                last_used_at=None,
                ip_allowlist=kwargs["ip_allowlist"],
                created_at=created_at,
            )

        async def create_api_key_scopes(
            self,
            **kwargs,
        ):
            events.append(("scopes", kwargs))
            return ()

    class FakeSession:
        async def commit(self):
            events.append(("commit", {}))

        async def rollback(self):
            events.append(("rollback", {}))

    service = PartnerApiService(
        session=FakeSession(),
        repository=FakeRepository(),
        api_key_codec=FakeCodec(),
        environment="production",
        idempotency=FakeIdempotency(),
    )

    result = await service.create_api_key(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        idempotency_key="new-api-key-001",
        api_client_id=api_client_id,
        name="Production CRM",
        expires_at=expires_at,
        ip_allowlist=[
            "203.0.113.10",
        ],
        scopes=(
            "services.read",
            "reviews.read",
        ),
    )

    assert result.key == plaintext_key
    assert [
        event_name
        for event_name, _ in events
    ] == [
        "reserve",
        "client",
        "issue",
        "key",
        "scopes",
        "complete",
        "commit",
    ]



@pytest.mark.asyncio
async def test_partner_api_key_create_maps_idempotency_reuse_to_exact_conflict():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_idempotency import (
        ApiIdempotencyKeyReusedError,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    api_client_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "api.keys.create",
        ),
    )

    class FakeService:
        async def create_api_key(
            self,
            **kwargs,
        ):
            raise ApiIdempotencyKeyReusedError(
                "Private request hash mismatch."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
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
                "/api/v1/partner/api-clients/"
                f"{api_client_id}/keys"
            ),
            headers={
                "X-Request-ID": (
                    "partner-key-reused"
                ),
                "Idempotency-Key": (
                    "reused-partner-key"
                ),
            },
            json={
                "name": "Different body",
                "expires_at": None,
                "ip_allowlist": [],
                "scopes": [
                    "services.read",
                ],
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
            "request_id": "partner-key-reused",
        }
    }
    assert (
        "Private request hash mismatch."
        not in response.text
    )
