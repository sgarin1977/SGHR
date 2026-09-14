import pytest


def test_email_auth_challenge_model_contract():
    from database.models import (
        ApiEmailAuthChallenge,
    )

    table = ApiEmailAuthChallenge.__table__
    columns = table.columns

    assert table.name == (
        "api_email_auth_challenges"
    )
    assert set(columns.keys()) == {
        "id",
        "tenant_id",
        "requested_user_id",
        "email",
        "challenge_hash",
        "challenge_type",
        "purpose",
        "device_id",
        "device_name",
        "status",
        "attempts_count",
        "max_attempts",
        "expires_at",
        "used_at",
        "created_at",
        "updated_at",
    }

    assert "code" not in columns
    assert "token" not in columns
    assert "otp" not in columns

    assert columns.email.type.length == 320
    assert columns.email.nullable is False

    assert columns.challenge_hash.type.length == 64
    assert columns.challenge_hash.nullable is False
    assert columns.challenge_hash.unique is True

    assert columns.challenge_type.type.length == 20
    assert columns.challenge_type.nullable is False
    assert (
        columns.challenge_type.default.arg
        == "otp"
    )

    assert columns.tenant_id.nullable is True
    assert columns.requested_user_id.nullable is True

    tenant_targets = {
        item.target_fullname
        for item in columns.tenant_id.foreign_keys
    }
    user_targets = {
        item.target_fullname
        for item
        in columns.requested_user_id.foreign_keys
    }

    assert tenant_targets == {"tenants.id"}
    assert user_targets == {"users.id"}

    assert columns.status.default.arg == "pending"
    assert columns.purpose.default.arg == "login"
    assert columns.attempts_count.default.arg == 0
    assert columns.max_attempts.default.arg == 5

    assert columns.expires_at.type.timezone is True
    assert columns.used_at.type.timezone is True
    assert columns.created_at.type.timezone is True
    assert columns.updated_at.type.timezone is True


def test_email_auth_code_uses_keyed_hash():
    from api.security import (
        EmailAuthChallengeCodec,
    )

    codec = EmailAuthChallengeCodec(
        secret="s" * 64,
    )

    material = codec.issue_code(
        email=" User@Example.COM ",
    )

    assert len(material.code) == 6
    assert material.code.isdigit()
    assert len(material.challenge_hash) == 64
    assert material.code not in (
        material.challenge_hash
    )

    assert codec.verify_code(
        email="user@example.com",
        code=material.code,
        expected_hash=(
            material.challenge_hash
        ),
    )

    wrong_code = (
        "000001"
        if material.code == "000000"
        else "000000"
    )

    assert not codec.verify_code(
        email="user@example.com",
        code=wrong_code,
        expected_hash=(
            material.challenge_hash
        ),
    )

    assert not codec.verify_code(
        email="other@example.com",
        code=material.code,
        expected_hash=(
            material.challenge_hash
        ),
    )

    other_codec = EmailAuthChallengeCodec(
        secret="x" * 64,
    )

    assert not other_codec.verify_code(
        email="user@example.com",
        code=material.code,
        expected_hash=(
            material.challenge_hash
        ),
    )


def test_email_auth_settings_have_safe_defaults():
    from api.settings import (
        ApiEmailAuthSettings,
    )

    settings = ApiEmailAuthSettings.from_env(
        {
            "API_EMAIL_AUTH_SECRET": (
                "e" * 64
            ),
        }
    )

    assert settings.challenge_secret == "e" * 64
    assert settings.code_ttl_seconds == 600
    assert settings.max_attempts == 5
    assert settings.request_limit == 5
    assert (
        settings.request_window_seconds
        == 900
    )


def test_email_auth_settings_require_strong_secret():
    import pytest

    from api.settings import (
        ApiConfigurationError,
        ApiEmailAuthSettings,
    )

    with pytest.raises(
        ApiConfigurationError
    ):
        ApiEmailAuthSettings.from_env(
            {
                "API_EMAIL_AUTH_SECRET": (
                    "short"
                ),
            }
        )


def test_email_auth_settings_are_documented():
    from pathlib import Path

    source = Path(".env.example").read_text(
        encoding="utf-8-sig"
    )

    required = (
        "API_EMAIL_AUTH_SECRET=",
        "API_EMAIL_AUTH_CODE_TTL_SECONDS=600",
        "API_EMAIL_AUTH_MAX_ATTEMPTS=5",
        "API_EMAIL_AUTH_REQUEST_LIMIT=5",
        (
            "API_EMAIL_AUTH_REQUEST_"
            "WINDOW_SECONDS=900"
        ),
    )

    for value in required:
        assert value in source


@pytest.mark.asyncio
async def test_email_challenge_repository_stores_hash_only():
    from datetime import UTC, datetime
    import inspect

    from database.repositories.api_email_auth import (
        ApiEmailAuthChallengeRepository,
    )

    expires_at = datetime(
        2026,
        8,
        28,
        12,
        10,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, instance):
            self.added.append(instance)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = (
        ApiEmailAuthChallengeRepository(
            session
        )
    )

    parameters = set(
        inspect.signature(
            repository.create_challenge
        ).parameters
    )

    assert "code" not in parameters
    assert "token" not in parameters
    assert "otp" not in parameters

    result = await repository.create_challenge(
        tenant_id=None,
        requested_user_id=None,
        email="user@example.com",
        challenge_hash="a" * 64,
        challenge_type="otp",
        purpose="login",
        device_id="web-device",
        device_name="Test browser",
        max_attempts=5,
        expires_at=expires_at,
    )

    assert session.added == [result]
    assert session.flushes == 1

    assert result.tenant_id is None
    assert result.requested_user_id is None
    assert result.email == "user@example.com"
    assert result.challenge_hash == "a" * 64
    assert result.challenge_type == "otp"
    assert result.purpose == "login"
    assert result.device_id == "web-device"
    assert result.device_name == "Test browser"
    assert result.max_attempts == 5
    assert result.expires_at == expires_at
    assert result.status == "pending"
    assert result.attempts_count == 0

    assert not hasattr(result, "code")
    assert not hasattr(result, "token")
    assert not hasattr(result, "otp")


@pytest.mark.asyncio
async def test_email_challenge_requests_are_counted_for_rate_limit():
    from datetime import UTC, datetime

    from sqlalchemy.dialects import postgresql

    from database.repositories.api_email_auth import (
        ApiEmailAuthChallengeRepository,
    )

    since = datetime(
        2026,
        8,
        28,
        12,
        0,
        tzinfo=UTC,
    )

    class FakeResult:
        def scalar_one(self):
            return 3

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = (
        ApiEmailAuthChallengeRepository(
            session
        )
    )

    result = await (
        repository.count_recent_challenges(
            email=" User@Example.COM ",
            since=since,
        )
    )

    assert result == 3
    assert len(session.statements) == 1

    statement = session.statements[0]
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={
            "literal_binds": True,
        },
    )
    sql = str(compiled)

    assert (
        "api_email_auth_challenges.email "
        "= 'user@example.com'"
        in sql
    )
    assert (
        "api_email_auth_challenges.created_at "
        ">="
        in sql
    )


@pytest.mark.asyncio
async def test_pending_email_challenge_is_locked_before_verification():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.api_email_auth import (
        ApiEmailAuthChallengeRepository,
    )

    now = datetime(
        2026,
        8,
        28,
        12,
        5,
        tzinfo=UTC,
    )
    challenge_id = uuid4()
    expected = SimpleNamespace(
        id=challenge_id,
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
    repository = (
        ApiEmailAuthChallengeRepository(
            session
        )
    )

    result = await (
        repository
        .get_pending_challenge_for_update(
            challenge_id=challenge_id,
            email=" User@Example.COM ",
            now=now,
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    compiled = session.statements[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={
            "literal_binds": True,
        },
    )
    sql = str(compiled)

    assert "FOR UPDATE" in sql
    assert (
        "api_email_auth_challenges.email "
        "= 'user@example.com'"
        in sql
    )
    assert (
        "api_email_auth_challenges.id ="
        in sql
    )
    assert str(challenge_id) in sql
    assert (
        "api_email_auth_challenges.status "
        "= 'pending'"
        in sql
    )
    assert (
        "api_email_auth_challenges.expires_at "
        ">"
        in sql
    )
    assert (
        "api_email_auth_challenges.used_at "
        "IS NULL"
        in sql
    )
    assert (
        "api_email_auth_challenges.attempts_count "
        "< api_email_auth_challenges.max_attempts"
        in sql
    )


@pytest.mark.asyncio
async def test_email_challenge_is_marked_used():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from database.repositories.api_email_auth import (
        ApiEmailAuthChallengeRepository,
    )

    now = datetime(
        2026,
        8,
        28,
        12,
        6,
        tzinfo=UTC,
    )
    challenge = SimpleNamespace(
        status="pending",
        used_at=None,
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = (
        ApiEmailAuthChallengeRepository(
            session
        )
    )

    result = await repository.mark_used(
        challenge=challenge,
        now=now,
    )

    assert result is challenge
    assert challenge.status == "used"
    assert challenge.used_at == now
    assert challenge.updated_at == now
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_email_challenge_failed_attempt_is_recorded():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from database.repositories.api_email_auth import (
        ApiEmailAuthChallengeRepository,
    )

    now = datetime(
        2026,
        8,
        28,
        12,
        7,
        tzinfo=UTC,
    )

    pending = SimpleNamespace(
        status="pending",
        attempts_count=1,
        max_attempts=5,
        updated_at=None,
    )
    final_attempt = SimpleNamespace(
        status="pending",
        attempts_count=4,
        max_attempts=5,
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = (
        ApiEmailAuthChallengeRepository(
            session
        )
    )

    await repository.record_failed_attempt(
        challenge=pending,
        now=now,
    )

    assert pending.attempts_count == 2
    assert pending.status == "pending"
    assert pending.updated_at == now

    await repository.record_failed_attempt(
        challenge=final_attempt,
        now=now,
    )

    assert final_attempt.attempts_count == 5
    assert final_attempt.status == "locked"
    assert final_attempt.updated_at == now
    assert session.flushes == 2


def test_email_auth_magic_link_uses_keyed_hash():
    from api.security import (
        EmailAuthChallengeCodec,
    )

    codec = EmailAuthChallengeCodec(
        secret="s" * 64,
    )

    material = codec.issue_magic_link(
        email=" User@Example.COM ",
    )

    assert len(material.token) >= 43
    assert len(material.challenge_hash) == 64
    assert material.token not in (
        material.challenge_hash
    )

    assert codec.verify_magic_link(
        email="user@example.com",
        token=material.token,
        expected_hash=(
            material.challenge_hash
        ),
    )

    assert not codec.verify_magic_link(
        email="user@example.com",
        token=material.token + "x",
        expected_hash=(
            material.challenge_hash
        ),
    )

    assert not codec.verify_magic_link(
        email="other@example.com",
        token=material.token,
        expected_hash=(
            material.challenge_hash
        ),
    )

    other_codec = EmailAuthChallengeCodec(
        secret="x" * 64,
    )

    assert not other_codec.verify_magic_link(
        email="user@example.com",
        token=material.token,
        expected_hash=(
            material.challenge_hash
        ),
    )


@pytest.mark.asyncio
async def test_email_auth_service_enforces_request_rate_limit():
    from datetime import UTC, datetime

    from services.api_email_auth import (
        ApiEmailAuthRateLimitError,
        ApiEmailAuthService,
    )

    now = datetime(
        2026,
        8,
        28,
        13,
        0,
        tzinfo=UTC,
    )

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeChallengeRepository:
        async def count_recent_challenges(
            self,
            *,
            email,
            since,
        ):
            assert email == "user@example.com"
            assert since == datetime(
                2026,
                8,
                28,
                12,
                45,
                tzinfo=UTC,
            )
            return 5

        async def create_challenge(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Rate-limited request must "
                "not create a challenge."
            )

    class FakeChallengeCodec:
        def issue_code(self, **kwargs):
            raise AssertionError(
                "Rate-limited request must "
                "not generate an OTP."
            )

        def issue_magic_link(self, **kwargs):
            raise AssertionError(
                "Rate-limited request must "
                "not generate a token."
            )

    class FakeEmailDelivery:
        async def send_challenge(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Rate-limited request must "
                "not send email."
            )

    database_session = FakeDatabaseSession()
    service = ApiEmailAuthService(
        session=database_session,
        challenge_repository=(
            FakeChallengeRepository()
        ),
        challenge_codec=(
            FakeChallengeCodec()
        ),
        email_delivery=FakeEmailDelivery(),
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    with pytest.raises(
        ApiEmailAuthRateLimitError
    ):
        await service.request_challenge(
            email=" User@Example.COM ",
            challenge_type="otp",
            device_id="web-device",
            device_name="Test browser",
            now=now,
        )

    assert database_session.commits == 0
    assert database_session.rollbacks == 0


@pytest.mark.parametrize(
    (
        "challenge_type",
        "expected_hash",
        "expected_secret",
    ),
    (
        (
            "otp",
            "a" * 64,
            "123456",
        ),
        (
            "magic_link",
            "b" * 64,
            "magic-link-token",
        ),
    ),
)
@pytest.mark.asyncio
async def test_email_auth_service_creates_and_delivers_challenge(
    challenge_type,
    expected_hash,
    expected_secret,
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_auth import (
        ApiEmailAuthService,
    )

    now = datetime(
        2026,
        8,
        28,
        13,
        0,
        tzinfo=UTC,
    )
    challenge_id = uuid4()
    created = []
    delivered = []

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeChallengeRepository:
        async def count_recent_challenges(
            self,
            **kwargs,
        ):
            return 0

        async def create_challenge(
            self,
            **kwargs,
        ):
            created.append(kwargs)
            return SimpleNamespace(
                id=challenge_id,
            )

    class FakeChallengeCodec:
        def issue_code(self, **kwargs):
            return SimpleNamespace(
                code="123456",
                challenge_hash="a" * 64,
            )

        def issue_magic_link(self, **kwargs):
            return SimpleNamespace(
                token="magic-link-token",
                challenge_hash="b" * 64,
            )

    class FakeEmailDelivery:
        async def send_challenge(
            self,
            **kwargs,
        ):
            delivered.append(kwargs)

    database_session = FakeDatabaseSession()
    service = ApiEmailAuthService(
        session=database_session,
        challenge_repository=(
            FakeChallengeRepository()
        ),
        challenge_codec=(
            FakeChallengeCodec()
        ),
        email_delivery=FakeEmailDelivery(),
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    result = await service.request_challenge(
        email=" User@Example.COM ",
        challenge_type=challenge_type,
        device_id="web-device",
        device_name="Test browser",
        now=now,
    )

    assert result.challenge_id == challenge_id
    assert (
        result.challenge_type
        == challenge_type
    )
    assert result.expires_at == datetime(
        2026,
        8,
        28,
        13,
        10,
        tzinfo=UTC,
    )

    assert len(created) == 1
    assert created[0]["email"] == (
        "user@example.com"
    )
    assert (
        created[0]["challenge_type"]
        == challenge_type
    )
    assert (
        created[0]["challenge_hash"]
        == expected_hash
    )
    assert "secret" not in created[0]
    assert "code" not in created[0]
    assert "token" not in created[0]

    assert len(delivered) == 1
    assert delivered[0]["secret"] == (
        expected_secret
    )
    assert (
        delivered[0]["challenge_id"]
        == challenge_id
    )

    assert not hasattr(result, "secret")
    assert not hasattr(result, "code")
    assert not hasattr(result, "token")

    assert database_session.commits == 1
    assert database_session.rollbacks == 0


@pytest.mark.asyncio
async def test_email_identity_is_locked_before_login_or_registration():
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from database.repositories.user import (
        UserRepository,
    )

    expected = SimpleNamespace(
        user_id="existing-user",
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
    repository = UserRepository(session)

    result = await (
        repository
        .get_email_account_for_update(
            " User@Example.COM "
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    compiled = session.statements[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={
            "literal_binds": True,
        },
    )
    sql = str(compiled)

    assert "FOR UPDATE" in sql
    assert (
        "user_accounts.platform = 'email'"
        in sql
    )
    assert (
        "user_accounts.platform_user_id "
        "= 'user@example.com'"
        in sql
    )


@pytest.mark.asyncio
async def test_email_identity_is_attached_to_existing_user():
    from types import SimpleNamespace
    from uuid import uuid4

    from database.models import (
        User,
        UserAccount,
    )
    from database.repositories.user import (
        UserRepository,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    existing_user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        async def get(self, model, identity):
            assert model is User
            assert identity == user_id
            return existing_user

        def add(self, instance):
            self.added.append(instance)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = UserRepository(session)

    result = await repository.create_email_account(
        user_id=user_id,
        email=" User@Example.COM ",
        source="api_email_link",
    )

    assert isinstance(result, UserAccount)
    assert session.added == [result]
    assert session.flushes == 1

    assert result.user_id == user_id
    assert result.platform == "email"
    assert (
        result.platform_user_id
        == "user@example.com"
    )
    assert result.email == "user@example.com"
    assert result.source == "api_email_link"

    assert not any(
        isinstance(item, User)
        for item in session.added
    )


@pytest.mark.asyncio
async def test_confirmed_email_creates_new_user_identity():
    from uuid import uuid4

    from database.models import (
        User,
        UserAccount,
        UserRoleMapping,
    )
    from database.repositories.user import (
        UserRepository,
    )

    tenant_id = uuid4()
    generated_user_id = uuid4()

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, instance):
            self.added.append(instance)

        async def flush(self):
            self.flushes += 1

            for item in self.added:
                if (
                    isinstance(item, User)
                    and item.id is None
                ):
                    item.id = generated_user_id

    session = FakeSession()
    repository = UserRepository(session)

    result = await repository.create_email_user_core(
        tenant_id=tenant_id,
        email=" User@Example.COM ",
        language_code="uk",
        source="api_email_registration",
    )

    users = [
        item
        for item in session.added
        if isinstance(item, User)
    ]
    accounts = [
        item
        for item in session.added
        if isinstance(item, UserAccount)
    ]
    roles = [
        item
        for item in session.added
        if isinstance(
            item,
            UserRoleMapping,
        )
    ]

    assert users == [result]
    assert len(accounts) == 1
    assert len(roles) == 1

    assert result.id == generated_user_id
    assert result.tenant_id == tenant_id
    assert result.language_code == "uk"
    assert result.status == "active"

    account = accounts[0]
    assert account.user_id == generated_user_id
    assert account.platform == "email"
    assert (
        account.platform_user_id
        == "user@example.com"
    )
    assert account.email == "user@example.com"
    assert (
        account.source
        == "api_email_registration"
    )

    role = roles[0]
    assert role.user_id == generated_user_id
    assert role.tenant_id == tenant_id
    assert role.role == "client"
    assert role.status == "active"

    assert session.flushes == 2


@pytest.mark.asyncio
async def test_verified_existing_email_uses_same_user():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_identity import (
        ApiEmailIdentityService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    existing_account = SimpleNamespace(
        user_id=user_id,
    )
    existing_user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    calls = []

    class FakeUserRepository:
        async def get_email_account_for_update(
            self,
            email,
        ):
            calls.append(
                (
                    "lock_email",
                    email,
                )
            )
            return existing_account

        async def get_by_id(self, value):
            calls.append(
                (
                    "get_user",
                    value,
                )
            )
            return existing_user

        async def create_email_account(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Existing identity must not "
                "be created again."
            )

        async def create_email_user_core(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Existing identity must not "
                "create a new user."
            )

    service = ApiEmailIdentityService(
        user_repository=FakeUserRepository(),
        default_tenant_id=uuid4(),
    )

    result = await (
        service.resolve_verified_email(
            email=" User@Example.COM ",
            requested_user_id=None,
            language_code="uk",
        )
    )

    assert result is existing_user
    assert calls == [
        (
            "lock_email",
            "user@example.com",
        ),
        (
            "get_user",
            user_id,
        ),
    ]


@pytest.mark.asyncio
async def test_verified_email_links_to_authenticated_user():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_identity import (
        ApiEmailIdentityService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    existing_user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    created = []

    class FakeUserRepository:
        async def get_email_account_for_update(
            self,
            email,
        ):
            assert email == "user@example.com"
            return None

        async def get_by_id(self, value):
            assert value == user_id
            return existing_user

        async def create_email_account(
            self,
            **kwargs,
        ):
            created.append(kwargs)
            return SimpleNamespace(**kwargs)

        async def create_email_user_core(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Linking must not create "
                "a new User."
            )

    service = ApiEmailIdentityService(
        user_repository=FakeUserRepository(),
        default_tenant_id=uuid4(),
    )

    result = await (
        service.resolve_verified_email(
            email=" User@Example.COM ",
            requested_user_id=user_id,
            requested_tenant_id=tenant_id,
            language_code="uk",
        )
    )

    assert result is existing_user
    assert created == [
        {
            "user_id": user_id,
            "email": "user@example.com",
            "source": "api_email_link",
        }
    ]


@pytest.mark.asyncio
async def test_verified_email_rejects_foreign_link():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_identity import (
        ApiEmailIdentityConflictError,
        ApiEmailIdentityService,
    )

    requested_user_id = uuid4()
    owner_user_id = uuid4()
    tenant_id = uuid4()

    class FakeUserRepository:
        async def get_email_account_for_update(
            self,
            email,
        ):
            return SimpleNamespace(
                user_id=owner_user_id,
            )

        async def get_by_id(self, value):
            raise AssertionError(
                "Foreign identity must be "
                "rejected before user lookup."
            )

        async def create_email_account(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Foreign identity must not "
                "be relinked."
            )

        async def create_email_user_core(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Foreign identity must not "
                "create a User."
            )

    service = ApiEmailIdentityService(
        user_repository=FakeUserRepository(),
        default_tenant_id=uuid4(),
    )

    with pytest.raises(
        ApiEmailIdentityConflictError
    ):
        await service.resolve_verified_email(
            email="user@example.com",
            requested_user_id=(
                requested_user_id
            ),
            requested_tenant_id=tenant_id,
            language_code="uk",
        )


@pytest.mark.asyncio
async def test_verified_new_email_uses_server_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_identity import (
        ApiEmailIdentityService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    created = []

    class FakeUserRepository:
        async def get_email_account_for_update(
            self,
            email,
        ):
            assert email == "user@example.com"
            return None

        async def get_by_id(self, value):
            raise AssertionError(
                "New Email registration has "
                "no existing user."
            )

        async def create_email_account(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "New user core creates its "
                "Email account atomically."
            )

        async def create_email_user_core(
            self,
            **kwargs,
        ):
            created.append(kwargs)
            return SimpleNamespace(
                id=user_id,
                tenant_id=tenant_id,
                status="active",
            )

    service = ApiEmailIdentityService(
        user_repository=FakeUserRepository(),
        default_tenant_id=tenant_id,
    )

    result = await (
        service.resolve_verified_email(
            email=" User@Example.COM ",
            requested_user_id=None,
            language_code="uk",
        )
    )

    assert result.id == user_id
    assert result.tenant_id == tenant_id
    assert created == [
        {
            "tenant_id": tenant_id,
            "email": "user@example.com",
            "language_code": "uk",
            "source": (
                "api_email_registration"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_invalid_email_otp_records_failed_attempt():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_auth import (
        ApiEmailAuthService,
        ApiEmailAuthVerificationError,
    )

    now = datetime(
        2026,
        8,
        28,
        14,
        0,
        tzinfo=UTC,
    )
    challenge_id = uuid4()
    challenge = SimpleNamespace(
        id=challenge_id,
        email="user@example.com",
        challenge_hash="a" * 64,
        challenge_type="otp",
        status="pending",
    )
    calls = []

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeChallengeRepository:
        async def get_pending_challenge_for_update(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "lock",
                    kwargs,
                )
            )
            return challenge

        async def record_failed_attempt(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "failed",
                    kwargs,
                )
            )
            return challenge

        async def mark_used(self, **kwargs):
            raise AssertionError(
                "Invalid OTP must not be used."
            )

    class FakeChallengeCodec:
        def verify_code(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "verify",
                    kwargs,
                )
            )
            return False

        def verify_magic_link(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "OTP must use OTP verification."
            )

    database_session = FakeDatabaseSession()
    service = ApiEmailAuthService(
        session=database_session,
        challenge_repository=(
            FakeChallengeRepository()
        ),
        challenge_codec=(
            FakeChallengeCodec()
        ),
        email_delivery=object(),
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    with pytest.raises(
        ApiEmailAuthVerificationError
    ):
        await service.verify_challenge(
            challenge_id=challenge_id,
            email=" User@Example.COM ",
            secret="000000",
            language_code="uk",
            now=now,
        )

    assert calls[0] == (
        "lock",
        {
            "challenge_id": challenge_id,
            "email": "user@example.com",
            "now": now,
        },
    )
    assert calls[1][0] == "verify"
    assert calls[2] == (
        "failed",
        {
            "challenge": challenge,
            "now": now,
        },
    )

    assert database_session.commits == 1
    assert database_session.rollbacks == 0


@pytest.mark.asyncio
async def test_shared_auth_service_prepares_email_token_session():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_auth import (
        ApiAuthService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime(
        2026,
        8,
        28,
        14,
        30,
        tzinfo=UTC,
    )
    calls = []

    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeAccessTokenCodec:
        access_ttl_seconds = 900

        def issue_access_token(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "access",
                    kwargs,
                )
            )
            return "email-access-token"

    class FakeRefreshTokenCodec:
        def issue_refresh_token(self):
            calls.append(
                (
                    "refresh",
                    {},
                )
            )
            return SimpleNamespace(
                token="email-refresh-token",
                token_hash="c" * 64,
            )

    class FakeSessionRepository:
        async def create_session(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "session",
                    kwargs,
                )
            )
            return SimpleNamespace(
                id=uuid4(),
            )

    database_session = FakeDatabaseSession()
    service = ApiAuthService(
        session=database_session,
        telegram_verifier=object(),
        user_service=object(),
        access_token_codec=(
            FakeAccessTokenCodec()
        ),
        refresh_token_codec=(
            FakeRefreshTokenCodec()
        ),
        session_repository=(
            FakeSessionRepository()
        ),
        refresh_token_ttl_seconds=2592000,
    )

    result = await service.prepare_token_pair(
        user=user,
        auth_method="email",
        device_id="web-device",
        device_name="Test browser",
        now=now,
    )

    assert result.access_token == (
        "email-access-token"
    )
    assert result.refresh_token == (
        "email-refresh-token"
    )
    assert result.token_type == "bearer"
    assert result.expires_in == 900

    assert calls[0][0] == "refresh"
    assert calls[1] == (
        "access",
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "now": now,
        },
    )

    session_call = calls[2]
    assert session_call[0] == "session"
    assert (
        session_call[1]["tenant_id"]
        == tenant_id
    )
    assert (
        session_call[1]["user_id"]
        == user_id
    )
    assert (
        session_call[1]["auth_method"]
        == "email"
    )
    assert (
        session_call[1][
            "refresh_token_hash"
        ]
        == "c" * 64
    )

    assert database_session.commits == 0
    assert database_session.rollbacks == 0


def test_telegram_auth_reuses_shared_token_preparation():
    import ast
    from pathlib import Path

    source = Path(
        "services/api_auth.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ApiAuthService"
    )
    method = next(
        node
        for node in service.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "authenticate_telegram"
    )

    block = (
        ast.get_source_segment(
            source,
            method,
        )
        or ""
    )

    assert "prepare_token_pair" in block
    assert "issue_refresh_token" not in block
    assert "issue_access_token" not in block
    assert ".create_session" not in block


@pytest.mark.parametrize(
    (
        "challenge_type",
        "secret",
    ),
    (
        (
            "otp",
            "123456",
        ),
        (
            "magic_link",
            "magic-link-token",
        ),
    ),
)
@pytest.mark.asyncio
async def test_verified_email_challenge_returns_shared_token_pair(
    challenge_type,
    secret,
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_email_auth import (
        ApiEmailAuthService,
    )
    from services.api_auth import (
        ApiTokenPair,
    )

    now = datetime(
        2026,
        8,
        28,
        15,
        0,
        tzinfo=UTC,
    )
    challenge_id = uuid4()
    user_id = uuid4()
    tenant_id = uuid4()
    calls = []

    challenge = SimpleNamespace(
        id=challenge_id,
        email="user@example.com",
        challenge_hash="d" * 64,
        challenge_type=challenge_type,
        tenant_id=None,
        requested_user_id=None,
        device_id="web-device",
        device_name="Test browser",
        status="pending",
    )
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    token_pair = ApiTokenPair(
        access_token="email-access-token",
        refresh_token="email-refresh-token",
        token_type="bearer",
        expires_in=900,
    )

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            calls.append(("commit", {}))
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeChallengeRepository:
        async def get_pending_challenge_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return challenge

        async def record_failed_attempt(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Valid secret must not record "
                "a failed attempt."
            )

        async def mark_used(
            self,
            **kwargs,
        ):
            calls.append(("used", kwargs))
            return challenge

    class FakeChallengeCodec:
        def verify_code(self, **kwargs):
            calls.append(("verify_otp", kwargs))
            return challenge_type == "otp"

        def verify_magic_link(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "verify_magic_link",
                    kwargs,
                )
            )
            return (
                challenge_type
                == "magic_link"
            )

    class FakeIdentityService:
        async def resolve_verified_email(
            self,
            **kwargs,
        ):
            calls.append(("identity", kwargs))
            return user

    class FakeTokenService:
        async def prepare_token_pair(
            self,
            **kwargs,
        ):
            calls.append(("tokens", kwargs))
            return token_pair

    database_session = FakeDatabaseSession()
    service = ApiEmailAuthService(
        session=database_session,
        challenge_repository=(
            FakeChallengeRepository()
        ),
        challenge_codec=(
            FakeChallengeCodec()
        ),
        email_delivery=object(),
        identity_service=(
            FakeIdentityService()
        ),
        token_service=FakeTokenService(),
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    result = await service.verify_challenge(
        challenge_id=challenge_id,
        email=" User@Example.COM ",
        secret=secret,
        language_code="uk",
        now=now,
    )

    assert result is token_pair

    identity_call = next(
        item
        for item in calls
        if item[0] == "identity"
    )
    assert identity_call[1] == {
        "email": "user@example.com",
        "requested_user_id": None,
        "requested_tenant_id": None,
        "language_code": "uk",
    }

    token_call = next(
        item
        for item in calls
        if item[0] == "tokens"
    )
    assert token_call[1] == {
        "user": user,
        "auth_method": "email",
        "device_id": "web-device",
        "device_name": "Test browser",
        "now": now,
    }

    used_call = next(
        item
        for item in calls
        if item[0] == "used"
    )
    assert used_call[1] == {
        "challenge": challenge,
        "now": now,
    }

    assert calls[-1][0] == "commit"
    assert database_session.commits == 1
    assert database_session.rollbacks == 0


@pytest.mark.asyncio
async def test_email_auth_request_endpoint_creates_challenge():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_email_auth_service,
    )
    from services.api_email_auth import (
        ApiEmailChallengeRequest,
    )

    challenge_id = uuid4()
    expires_at = datetime(
        2026,
        8,
        28,
        16,
        10,
        tzinfo=UTC,
    )
    calls = []

    class FakeEmailAuthService:
        async def request_challenge(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiEmailChallengeRequest(
                challenge_id=challenge_id,
                challenge_type="otp",
                expires_at=expires_at,
            )

    application = create_app()
    application.dependency_overrides[
        get_api_email_auth_service
    ] = lambda: FakeEmailAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/email/request",
            headers={
                "X-Request-ID": (
                    "email-auth-request"
                ),
            },
            json={
                "email": "User@Example.com",
                "challenge_type": "otp",
                "device_id": "web-device",
                "device_name": "Test browser",
            },
        )

    assert response.status_code == 202

    body = response.json()
    assert body["data"]["challenge_id"] == (
        str(challenge_id)
    )
    assert body["data"]["challenge_type"] == "otp"
    assert body["request_id"] == (
        "email-auth-request"
    )
    assert "secret" not in body["data"]
    assert "code" not in body["data"]
    assert "token" not in body["data"]

    assert calls == [
        {
            "email": "User@Example.com",
            "challenge_type": "otp",
            "device_id": "web-device",
            "device_name": "Test browser",
        }
    ]


@pytest.mark.asyncio
async def test_email_auth_verify_endpoint_returns_token_pair():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_email_auth_service,
    )
    from services.api_auth import ApiTokenPair

    challenge_id = uuid4()
    calls = []

    class FakeEmailAuthService:
        async def verify_challenge(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiTokenPair(
                access_token="email-access-token",
                refresh_token="email-refresh-token",
                token_type="bearer",
                expires_in=900,
            )

    application = create_app()
    application.dependency_overrides[
        get_api_email_auth_service
    ] = lambda: FakeEmailAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/email/verify",
            headers={
                "X-Request-ID": (
                    "email-auth-verify"
                ),
            },
            json={
                "challenge_id": str(challenge_id),
                "email": "User@Example.com",
                "secret": "123456",
                "language_code": "uk",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "access_token": (
                "email-access-token"
            ),
            "refresh_token": (
                "email-refresh-token"
            ),
            "token_type": "bearer",
            "expires_in": 900,
        },
        "meta": {},
        "request_id": "email-auth-verify",
    }

    assert calls == [
        {
            "challenge_id": challenge_id,
            "email": "User@Example.com",
            "secret": "123456",
            "language_code": "uk",
        }
    ]


@pytest.mark.asyncio
async def test_pending_magic_link_is_locked_without_client_email():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.api_email_auth import (
        ApiEmailAuthChallengeRepository,
    )

    challenge_id = uuid4()
    now = datetime(
        2026,
        8,
        28,
        19,
        0,
        tzinfo=UTC,
    )
    expected = SimpleNamespace(
        id=challenge_id,
        email="user@example.com",
        challenge_type="magic_link",
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
    repository = (
        ApiEmailAuthChallengeRepository(
            session
        )
    )

    result = await (
        repository
        .get_pending_magic_link_for_update(
            challenge_id=challenge_id,
            now=now,
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
        )
    )

    assert (
        "api_email_auth_challenges.id"
        in sql
    )
    assert (
        "api_email_auth_challenges."
        "challenge_type"
        in sql
    )
    assert (
        "api_email_auth_challenges.status"
        in sql
    )
    assert (
        "api_email_auth_challenges.expires_at"
        in sql
    )
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_magic_link_token_uses_database_email():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import (
        AsyncMock,
        Mock,
    )
    from uuid import uuid4

    from services.api_auth import ApiTokenPair
    from services.api_email_auth import (
        ApiEmailAuthService,
    )

    challenge_id = uuid4()
    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime(
        2026,
        8,
        28,
        20,
        0,
        tzinfo=UTC,
    )

    challenge = SimpleNamespace(
        id=challenge_id,
        email="verified@example.com",
        challenge_hash="a" * 64,
        challenge_type="magic_link",
        tenant_id=None,
        requested_user_id=None,
        device_id="mobile-device",
        device_name="Test mobile",
    )
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    expected_tokens = ApiTokenPair(
        access_token="access-token",
        refresh_token="refresh-token",
        token_type="bearer",
        expires_in=900,
    )

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        get_pending_magic_link_for_update=(
            AsyncMock(
                return_value=challenge
            )
        ),
        record_failed_attempt=AsyncMock(),
        mark_used=AsyncMock(
            return_value=challenge
        ),
    )
    codec = SimpleNamespace(
        verify_magic_link=Mock(
            return_value=True
        )
    )
    identity_service = SimpleNamespace(
        resolve_verified_email=AsyncMock(
            return_value=user
        )
    )
    token_service = SimpleNamespace(
        prepare_token_pair=AsyncMock(
            return_value=expected_tokens
        )
    )

    service = ApiEmailAuthService(
        session=session,
        challenge_repository=repository,
        challenge_codec=codec,
        email_delivery=object(),
        identity_service=identity_service,
        token_service=token_service,
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    result = await (
        service.verify_magic_link_token(
            token=(
                f"{challenge_id}."
                "private-magic-token"
            ),
            language_code="uk",
            now=now,
        )
    )

    assert result is expected_tokens

    repository.get_pending_magic_link_for_update.assert_awaited_once_with(
        challenge_id=challenge_id,
        now=now,
    )
    codec.verify_magic_link.assert_called_once_with(
        email="verified@example.com",
        token="private-magic-token",
        expected_hash="a" * 64,
    )
    identity_service.resolve_verified_email.assert_awaited_once_with(
        email="verified@example.com",
        requested_user_id=None,
        requested_tenant_id=None,
        language_code="uk",
    )
    token_service.prepare_token_pair.assert_awaited_once_with(
        user=user,
        auth_method="email",
        device_id="mobile-device",
        device_name="Test mobile",
        now=now,
    )
    repository.mark_used.assert_awaited_once_with(
        challenge=challenge,
        now=now,
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_magic_link_endpoint_accepts_token_only():
    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_email_auth_service,
    )
    from services.api_auth import ApiTokenPair

    calls = []

    class FakeEmailAuthService:
        async def verify_magic_link_token(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiTokenPair(
                access_token="magic-access-token",
                refresh_token=(
                    "magic-refresh-token"
                ),
                token_type="bearer",
                expires_in=900,
            )

    application = create_app()
    application.dependency_overrides[
        get_api_email_auth_service
    ] = lambda: FakeEmailAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/auth/email/"
                "magic-link/verify"
            ),
            headers={
                "X-Request-ID": (
                    "magic-link-verify"
                ),
            },
            json={
                "token": (
                    "challenge-id."
                    "private-magic-token"
                ),
                "language_code": "uk",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "access_token": (
                "magic-access-token"
            ),
            "refresh_token": (
                "magic-refresh-token"
            ),
            "token_type": "bearer",
            "expires_in": 900,
        },
        "meta": {},
        "request_id": "magic-link-verify",
    }

    assert calls == [
        {
            "token": (
                "challenge-id."
                "private-magic-token"
            ),
            "language_code": "uk",
        }
    ]


@pytest.mark.asyncio
async def test_delivery_failure_rolls_back_challenge():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest

    from services.api_email_auth import (
        ApiEmailAuthDeliveryError,
        ApiEmailAuthService,
    )

    now = datetime(
        2026,
        8,
        28,
        21,
        0,
        tzinfo=UTC,
    )
    challenge = SimpleNamespace(
        id=uuid4(),
    )

    class FakeRepository:
        async def count_recent_challenges(
            self,
            **kwargs,
        ):
            return 0

        async def create_challenge(
            self,
            **kwargs,
        ):
            return challenge

    class FakeCodec:
        def issue_code(self, **kwargs):
            return SimpleNamespace(
                code="123456",
                challenge_hash="a" * 64,
            )

    class FailingDelivery:
        async def send_challenge(
            self,
            **kwargs,
        ):
            raise RuntimeError(
                "private provider failure"
            )

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = ApiEmailAuthService(
        session=session,
        challenge_repository=(
            FakeRepository()
        ),
        challenge_codec=FakeCodec(),
        email_delivery=FailingDelivery(),
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    with pytest.raises(
        ApiEmailAuthDeliveryError
    ):
        await service.request_challenge(
            email="user@example.com",
            challenge_type="otp",
            device_id=None,
            device_name=None,
            now=now,
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_email_auth_is_security_audited():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    import pytest

    from services.api_email_auth import (
        ApiEmailAuthService,
        ApiEmailAuthVerificationError,
    )

    challenge_id = uuid4()
    now = datetime(
        2026,
        8,
        28,
        22,
        0,
        tzinfo=UTC,
    )
    challenge = SimpleNamespace(
        id=challenge_id,
        email="user@example.com",
        challenge_hash="a" * 64,
        challenge_type="otp",
        tenant_id=None,
        requested_user_id=None,
        status="pending",
    )

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        get_pending_challenge_for_update=(
            AsyncMock(
                return_value=challenge
            )
        ),
        record_failed_attempt=AsyncMock(),
    )
    codec = SimpleNamespace(
        verify_code=Mock(
            return_value=False
        )
    )
    events = SimpleNamespace(
        create_event=AsyncMock(),
    )

    service = ApiEmailAuthService(
        session=session,
        challenge_repository=repository,
        challenge_codec=codec,
        email_delivery=object(),
        event_repository=events,
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    with pytest.raises(
        ApiEmailAuthVerificationError
    ):
        await service.verify_challenge(
            challenge_id=challenge_id,
            email="user@example.com",
            secret="000000",
            language_code="uk",
            now=now,
        )

    events.create_event.assert_awaited_once_with(
        event_type="api_email_auth_failed",
        tenant_id=None,
        user_id=None,
        entity_type=(
            "api_email_auth_challenge"
        ),
        entity_id=challenge_id,
        payload={
            "auth_method": "email",
            "challenge_type": "otp",
            "reason": "invalid_secret",
        },
        platform="api",
    )

    audited_payload = (
        events.create_event
        .await_args.kwargs["payload"]
    )
    assert "email" not in audited_payload
    assert "secret" not in audited_payload
    assert "challenge_hash" not in audited_payload


@pytest.mark.asyncio
async def test_successful_email_auth_is_security_audited():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import (
        AsyncMock,
        Mock,
    )
    from uuid import uuid4

    from services.api_auth import ApiTokenPair
    from services.api_email_auth import (
        ApiEmailAuthService,
    )

    challenge_id = uuid4()
    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime(
        2026,
        8,
        28,
        22,
        30,
        tzinfo=UTC,
    )

    challenge = SimpleNamespace(
        id=challenge_id,
        email="user@example.com",
        challenge_hash="a" * 64,
        challenge_type="otp",
        tenant_id=None,
        requested_user_id=None,
        device_id="web-device",
        device_name="Test browser",
        status="pending",
    )
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    tokens = ApiTokenPair(
        access_token="access-token",
        refresh_token="refresh-token",
        token_type="bearer",
        expires_in=900,
    )

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        get_pending_challenge_for_update=(
            AsyncMock(
                return_value=challenge
            )
        ),
        record_failed_attempt=AsyncMock(),
        mark_used=AsyncMock(),
    )
    identity_service = SimpleNamespace(
        resolve_verified_email=AsyncMock(
            return_value=user
        )
    )
    token_service = SimpleNamespace(
        prepare_token_pair=AsyncMock(
            return_value=tokens
        )
    )
    events = SimpleNamespace(
        create_event=AsyncMock(),
    )

    service = ApiEmailAuthService(
        session=session,
        challenge_repository=repository,
        challenge_codec=SimpleNamespace(
            verify_code=Mock(
                return_value=True
            )
        ),
        email_delivery=object(),
        identity_service=identity_service,
        token_service=token_service,
        event_repository=events,
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    result = await service.verify_challenge(
        challenge_id=challenge_id,
        email="user@example.com",
        secret="123456",
        language_code="uk",
        now=now,
    )

    assert result is tokens
    events.create_event.assert_awaited_once_with(
        event_type=(
            "api_email_auth_succeeded"
        ),
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type=(
            "api_email_auth_challenge"
        ),
        entity_id=challenge_id,
        payload={
            "auth_method": "email",
            "challenge_type": "otp",
        },
        platform="api",
    )


@pytest.mark.asyncio
async def test_email_link_request_uses_current_actor():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_email_auth_service,
        get_current_actor,
    )
    from services.api_email_auth import (
        ApiEmailChallengeRequest,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    challenge_id = uuid4()
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

    class FakeEmailAuthService:
        async def request_challenge(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiEmailChallengeRequest(
                challenge_id=challenge_id,
                challenge_type="otp",
                expires_at=datetime(
                    2026,
                    8,
                    29,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_email_auth_service
    ] = lambda: FakeEmailAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/email/link/request",
            headers={
                "X-Request-ID": (
                    "email-link-request"
                ),
            },
            json={
                "email": "user@example.com",
                "challenge_type": "otp",
                "device_id": "web-device",
                "device_name": "Test browser",
            },
        )

    assert response.status_code == 202
    assert calls == [
        {
            "email": "user@example.com",
            "challenge_type": "otp",
            "device_id": "web-device",
            "device_name": "Test browser",
            "tenant_id": tenant_id,
            "requested_user_id": user_id,
        }
    ]
    assert response.json()["data"][
        "challenge_id"
    ] == str(challenge_id)


@pytest.mark.asyncio
async def test_email_identity_conflict_is_sanitized():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_email_auth_service,
    )
    from services.api_email_identity import (
        ApiEmailIdentityConflictError,
    )

    class ConflictingEmailAuthService:
        async def verify_challenge(
            self,
            **kwargs,
        ):
            raise ApiEmailIdentityConflictError(
                "private existing user details"
            )

    application = create_app()
    application.dependency_overrides[
        get_api_email_auth_service
    ] = lambda: ConflictingEmailAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/email/verify",
            headers={
                "X-Request-ID": (
                    "email-conflict-request"
                ),
            },
            json={
                "challenge_id": str(uuid4()),
                "email": "user@example.com",
                "secret": "123456",
                "language_code": "uk",
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "email_identity_conflict",
            "message": (
                "Email identity is already "
                "linked to another account."
            ),
            "request_id": (
                "email-conflict-request"
            ),
        }
    }
    assert (
        "private existing user details"
        not in response.text
    )


@pytest.mark.asyncio
async def test_missing_email_challenge_is_security_audited():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest

    from services.api_email_auth import (
        ApiEmailAuthService,
        ApiEmailAuthVerificationError,
    )

    challenge_id = uuid4()
    now = datetime(
        2026,
        9,
        3,
        12,
        0,
        tzinfo=UTC,
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        get_pending_challenge_for_update=(
            AsyncMock(return_value=None)
        ),
    )
    events = SimpleNamespace(
        create_event=AsyncMock(),
    )

    service = ApiEmailAuthService(
        session=session,
        challenge_repository=repository,
        challenge_codec=object(),
        email_delivery=object(),
        event_repository=events,
        code_ttl_seconds=600,
        max_attempts=5,
        request_limit=5,
        request_window_seconds=900,
    )

    with pytest.raises(
        ApiEmailAuthVerificationError
    ):
        await service.verify_challenge(
            challenge_id=challenge_id,
            email="user@example.com",
            secret="123456",
            language_code="uk",
            now=now,
        )

    events.create_event.assert_awaited_once_with(
        event_type="api_email_auth_failed",
        tenant_id=None,
        user_id=None,
        entity_type=(
            "api_email_auth_challenge"
        ),
        entity_id=challenge_id,
        payload={
            "auth_method": "email",
            "reason": "challenge_not_found",
        },
        platform="api",
    )
    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()

    payload = (
        events.create_event
        .await_args.kwargs["payload"]
    )
    assert "email" not in payload
    assert "secret" not in payload
    assert "challenge_hash" not in payload
