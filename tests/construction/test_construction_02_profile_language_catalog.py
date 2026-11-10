"""CON-011 local SQLite transactions and API mapping, not native/RLS acceptance."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Boolean, Column, MetaData, String, Table, Uuid, create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from api.app import create_app
from api.auth import get_api_identity_service, get_current_actor
from database.models import User, UserLanguageSetting
from database.repositories.translation import TranslationRepository, InterfaceLanguageValidationError
from services.api_identity import ApiActorContext, ApiIdentityService
from services.translation import TranslationService, TranslationError, TranslationLanguageValidationError
from services.user_settings import UserSettingsService


@pytest.fixture
def language_write():
    c = NS(
        user=uuid4(), other=uuid4(), tenant=uuid4(), fail=None,
        commits=0, rollbacks=0, statements=[],
    )
    metadata = MetaData()
    c.users = Table(
        "users", metadata,
        Column("id", Uuid, primary_key=True),
        Column("status", String),
        Column("language_code", String),
    )
    c.languages = Table(
        "languages", metadata,
        Column("code", String, primary_key=True),
        Column("is_active", Boolean),
    )
    c.tenants = Table(
        "tenants", metadata,
        Column("id", Uuid, primary_key=True),
        Column("default_language", String),
    )
    c.audit = Table(
        "local_language_audit", metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid),
        Column("user_id", Uuid),
        Column("value", String),
        Column("source", String),
        Column("platform", String),
    )
    c.preferences = UserLanguageSetting.__table__.to_metadata(metadata)
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    c.sync = Session(engine, expire_on_commit=False)

    c.sync.execute(c.users.insert(), [
        {"id": c.user, "status": "active", "language_code": "ru"},
        {"id": c.other, "status": "active", "language_code": "pt"},
    ])
    c.sync.execute(c.languages.insert(), [
        {"code": code, "is_active": active}
        for code, active in (
            ("ru", True), ("pt", True), ("en", True),
            ("es", True), ("it", True), ("ja", True), ("fr", False),
        )
    ])
    c.sync.execute(
        c.tenants.insert().values(id=c.tenant, default_language="pt")
    )
    c.sync.commit()

    class Bridge:
        async def execute(self, statement):
            c.statements.append(statement)
            return c.sync.execute(statement)

        async def get(self, model, key):
            assert model is User
            row = c.sync.execute(
                select(c.users).where(c.users.c.id == key)
            ).mappings().one_or_none()
            return NS(**row) if row else None

        def add(self, value):
            c.sync.add(value)

        async def flush(self):
            c.sync.flush()

        async def commit(self):
            c.sync.flush()
            if c.fail == "commit":
                raise RuntimeError("private commit failure")
            c.sync.commit()
            c.commits += 1

        async def rollback(self):
            c.sync.rollback()
            c.rollbacks += 1

    c.db = Bridge()
    c.provider = NS(
        provider_name="unused",
        translate=AsyncMock(
            side_effect=AssertionError("No translation provider call is allowed.")
        ),
    )
    c.repo = TranslationRepository(c.db)
    c.service = TranslationService(c.repo, provider=c.provider)

    # SQL collaborators use one real local SQLite transaction.
    # No production data, unrelated ORM columns or external providers.
    async def update_user(*, user_id, language_code):
        assert user_id == c.user
        c.sync.execute(
            c.users.update()
            .where(c.users.c.id == user_id)
            .values(language_code=language_code)
        )
        if c.fail == "user":
            raise RuntimeError("private user failure")

    async def audit(**values):
        assert values["event_type"] == "settings_changed"
        assert values["entity_type"] == "user"
        assert values["entity_id"] == c.user
        c.sync.execute(c.audit.insert().values(
            id=uuid4(),
            tenant_id=values["tenant_id"],
            user_id=values["user_id"],
            value=values["payload"]["value"],
            source=values["payload"]["source"],
            platform=values["platform"],
        ))
        if c.fail == "audit":
            raise RuntimeError("private audit failure")

    c.service.users = NS(
        session=c.db,
        update_language_code=AsyncMock(side_effect=update_user),
    )
    c.service.events = NS(
        session=c.db,
        create_event=AsyncMock(side_effect=audit),
    )
    c.actor = ApiActorContext(
        c.user, c.tenant, None, ("client",), "ru", None, "active"
    )
    c.identity = ApiIdentityService(
        c.db,
        user_service=NS(),
        user_repository=NS(),
        event_repository=NS(),
        translation_service=c.service,
    )
    try:
        yield c
    finally:
        c.sync.close()
        engine.dispose()


def snapshot(c):
    return [
        c.sync.execute(select(table)).all()
        for table in (c.users, c.preferences, c.tenants, c.audit)
    ]


async def change(c, code):
    return await c.service.update_interface_language(
        tenant_id=c.tenant,
        user_id=c.user,
        language_code=code,
        source="api_profile",
        platform="api",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["es", "it", "ja", "en"])
async def test_active_global_catalog_is_extensible_and_does_not_change_organization(
    language_write, code,
):
    c = language_write
    result = await change(c, " " + code.upper() + " ")
    assert result.interface_language == code
    assert c.sync.scalar(
        select(c.users.c.language_code).where(c.users.c.id == c.user)
    ) == code
    assert c.sync.scalar(
        select(c.users.c.language_code).where(c.users.c.id == c.other)
    ) == "pt"
    assert c.sync.scalar(select(c.tenants.c.default_language)) == "pt"

    events = c.sync.execute(select(c.audit)).mappings().all()
    assert len(events) == 1 and events[0]["value"] == code
    assert events[0]["source"] == "api_profile"
    assert events[0]["platform"] == "api"
    assert c.commits == 1 and c.rollbacks == 0
    c.provider.translate.assert_not_awaited()

    sql = str(c.statements[0].compile(dialect=postgresql.dialect()))
    assert "languages.is_active IS true" in sql and "FOR SHARE" in sql
    assert "tenant_languages" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["fr", "xx", "", "x" * 11])
async def test_unavailable_or_invalid_language_never_falls_back_or_creates_settings(
    language_write, code,
):
    c = language_write
    before = snapshot(c)
    with pytest.raises(TranslationLanguageValidationError):
        await change(c, code)
    assert snapshot(c) == before
    assert c.commits == 0
    c.service.users.update_language_code.assert_not_awaited()
    c.service.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["user", "audit", "commit"])
async def test_mirror_profile_and_mandatory_audit_roll_back_together(
    language_write, failure,
):
    c = language_write
    before = snapshot(c)
    c.fail = failure
    with pytest.raises(TranslationError) as error:
        await change(c, "it")
    assert "private" not in str(error.value)
    assert snapshot(c) == before
    assert c.commits == 0 and c.rollbacks == 1


@pytest.mark.asyncio
async def test_interface_change_preserves_existing_message_preferences(language_write):
    c = language_write
    c.sync.add(UserLanguageSetting(
        user_id=c.user,
        interface_language="ru",
        message_language="en",
        translation_mode="off",
        auto_translate_enabled=False,
        show_original_button=False,
    ))
    c.sync.commit()
    result = await change(c, "es")
    assert result.interface_language == "es"
    assert result.message_language == "en"
    assert result.translation_mode == "off"
    assert not result.auto_translate_enabled
    assert result.show_original_button is False


@pytest.mark.asyncio
async def test_repository_checks_active_catalog_even_without_service(language_write):
    c = language_write
    with pytest.raises(InterfaceLanguageValidationError):
        await c.repo.update_language_settings(
            user_id=c.user, interface_language="fr",
        )
    assert c.sync.scalar(select(c.preferences.c.id)) is None


@pytest.mark.asyncio
async def test_settings_initialization_preserves_canonical_interface_language(language_write):
    c = language_write
    c.sync.execute(
        c.users.update().where(c.users.c.id == c.user).values(language_code="es")
    )
    c.sync.commit()
    result = await c.repo.get_language_settings(c.user)
    assert result.interface_language == "es"
    c.sync.rollback()


@pytest.mark.asyncio
async def test_legacy_settings_ui_uses_profile_not_stale_preference():
    user = NS(id=uuid4(), tenant_id=uuid4(), language_code="it")
    translation = NS(
        get_language_settings_view=AsyncMock(
            return_value=NS(interface_language="ru")
        )
    )
    service = UserSettingsService(
        None,
        users=NS(get_user_by_telegram_id=AsyncMock(return_value=user)),
        translation=translation,
        privacy=NS(),
        legal=NS(),
        specialists=NS(),
    )
    context = await service.get_context(platform_user_id=123)
    assert context.interface_language == "it"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [("es", 200), ("it", 200), ("fr", 422), ("xx", 422)],
)
async def test_existing_patch_me_maps_catalog_results(language_write, code, expected):
    c = language_write
    app = create_app()
    app.dependency_overrides[get_current_actor] = lambda: c.actor
    app.dependency_overrides[get_api_identity_service] = lambda: c.identity

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            "/api/v1/me", json={"language_code": code},
        )

    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["data"]["language_code"] == code
    else:
        assert response.json()["error"]["code"] == "profile_validation_error"
        assert c.commits == 0
