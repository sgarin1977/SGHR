from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_contact_request_uses_actor_scope():
    from services.user_dialogs import (
        UserDialogContact,
        UserDialogsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    profession_id = uuid4()
    expected = SimpleNamespace(
        contact_request_id=uuid4(),
        thread_id=uuid4(),
    )
    calls = []

    class FakeChats:
        async def start_contact_chat(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserDialogsService(
        object(),
        settings=object(),
        users=object(),
        chats=FakeChats(),
        specialists=object(),
        user_repository=object(),
        moderation=object(),
        translation=object(),
    )

    action = await (
        service.create_contact_request_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            specialist_id=specialist_id,
            profession_id=profession_id,
            message="Need a consultation.",
        )
    )

    assert isinstance(
        action,
        UserDialogContact,
    )
    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert action.actor.language == "uk"
    assert action.chat is expected

    assert calls == [
        {
            "tenant_id": tenant_id,
            "from_user_id": user_id,
            "specialist_id": specialist_id,
            "profession_id": profession_id,
            "message": "Need a consultation.",
            "original_language": "uk",
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_contact_request_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    profession_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
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

    class FakeUserDialogsService:
        async def create_contact_request_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                chat=SimpleNamespace(
                    contact_request_id=(
                        contact_request_id
                    ),
                    thread_id=thread_id,
                    was_existing=False,
                    message_masked=True,
                    thread_restricted=False,
                    notification_id=uuid4(),
                    contact_token="private-token",
                    detection_types=[
                        "private_detection",
                    ],
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: FakeUserDialogsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/contact-requests",
            headers={
                "X-Request-ID": (
                    "contact-request-create"
                ),
                "Idempotency-Key": (
                    "contact-request-create-001"
                ),
            },
            json={
                "specialist_id": str(
                    specialist_id
                ),
                "profession_id": str(
                    profession_id
                ),
                "message": (
                    "Need a consultation."
                ),
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "specialist_id": specialist_id,
            "profession_id": profession_id,
            "message": "Need a consultation.",
            "idempotency_key": (
                "contact-request-create-001"
            ),
        }
    ]

    assert response.json() == {
        "data": {
            "contact_request_id": str(
                contact_request_id
            ),
            "thread_id": str(thread_id),
            "was_existing": False,
            "message_masked": True,
            "thread_restricted": False,
        },
        "meta": {},
        "request_id": (
            "contact-request-create"
        ),
    }

    assert "private-token" not in response.text
    assert "private_detection" not in response.text


@pytest.mark.asyncio
async def test_contact_request_requires_authentication():
    import httpx

    from api.app import create_app

    request_id = "contact-request-auth"
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/contact-requests",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "specialist_id": str(uuid4()),
                "profession_id": str(uuid4()),
                "message": "Need a consultation.",
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": request_id,
        },
    }


@pytest.mark.asyncio
async def test_contact_request_rejects_actor_fields():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )
    calls = []

    class ForbiddenService:
        async def create_contact_request_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Service must not be called "
                "with client actor fields."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/contact-requests",
            json={
                "specialist_id": str(uuid4()),
                "profession_id": str(uuid4()),
                "message": "Need a consultation.",
                "user_id": str(uuid4()),
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert (
        response.json()["error"]["code"]
        == "validation_error"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_contact_request_rate_limit_is_sanitized():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.contact_chat import (
        ContactChatRateLimitError,
    )

    request_id = "contact-request-rate-limit"

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class LimitedService:
        async def create_contact_request_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatRateLimitError(
                "PRIVATE RATE LIMIT DETAILS"
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: LimitedService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/contact-requests",
            headers={
                "X-Request-ID": request_id,
                "Idempotency-Key": "contact-rate-limit-001",
            },
            json={
                "specialist_id": str(uuid4()),
                "profession_id": str(uuid4()),
                "message": "Need a consultation.",
            },
        )

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": (
                "contact_request_rate_limit"
            ),
            "message": (
                "Too many contact requests."
            ),
            "request_id": request_id,
        },
    }
    assert "PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_contact_request_domain_error_is_sanitized():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.contact_chat import (
        ContactChatError,
    )

    request_id = "contact-request-invalid"

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class InvalidService:
        async def create_contact_request_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "PRIVATE CABINET OR MESSAGE DETAILS"
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: InvalidService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/contact-requests",
            headers={
                "X-Request-ID": request_id,
                "Idempotency-Key": "contact-domain-error-001",
            },
            json={
                "specialist_id": str(uuid4()),
                "profession_id": str(uuid4()),
                "message": "Need a consultation.",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": (
                "contact_request_validation_error"
            ),
            "message": (
                "Contact request is not valid."
            ),
            "request_id": request_id,
        },
    }
    assert "PRIVATE" not in response.text


def test_contact_request_create_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        "/api/v1/contact-requests"
    ]["post"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    request_schema = (
        operation["requestBody"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert request_schema == {
        "$ref": (
            "#/components/schemas/"
            "ContactRequestCreateRequest"
        )
    }

    response_schema = (
        operation["responses"]["201"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "ContactRequestCreatedResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    request_fields = set(
        components[
            "ContactRequestCreateRequest"
        ]["properties"]
    )
    assert request_fields == {
        "specialist_id",
        "profession_id",
        "message",
    }
    assert "user_id" not in request_fields
    assert "tenant_id" not in request_fields

    response_fields = set(
        components[
            "ContactRequestCreatedData"
        ]["properties"]
    )
    assert response_fields == {
        "contact_request_id",
        "thread_id",
        "was_existing",
        "message_masked",
        "thread_restricted",
    }


@pytest.mark.asyncio
async def test_contact_request_repository_list_uses_actor_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    class FakeResult:
        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ContactChatRepository(
        session
    )

    result = await (
        repository
        .list_contact_requests_for_client(
            tenant_id=tenant_id,
            user_id=user_id,
            language="uk",
            limit=20,
            offset=0,
        )
    )

    assert result == []
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    sql = " ".join(sql.lower().split())

    assert (
        f"contact_requests.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"contact_requests.from_user_id = "
        f"'{user_id}'"
        in sql
    )


@pytest.mark.asyncio
async def test_neutral_contact_request_list_uses_actor_scope():
    from services.user_dialogs import (
        UserContactRequestsPage,
        UserDialogsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    items = [
        SimpleNamespace(
            contact_request_id=uuid4(),
        ),
        SimpleNamespace(
            contact_request_id=uuid4(),
        ),
        SimpleNamespace(
            contact_request_id=uuid4(),
        ),
    ]
    calls = []

    class FakeChats:
        async def list_client_requests(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return items

    service = UserDialogsService(
        object(),
        settings=object(),
        users=object(),
        chats=FakeChats(),
        specialists=object(),
        user_repository=object(),
        moderation=object(),
        translation=object(),
    )

    page = await (
        service.list_contact_requests_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            page=1,
            page_size=2,
        )
    )

    assert isinstance(
        page,
        UserContactRequestsPage,
    )
    assert page.actor.user_id == user_id
    assert page.actor.tenant_id == tenant_id
    assert page.actor.language == "uk"
    assert page.items == items[:2]
    assert page.page == 1
    assert page.has_next is True

    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "limit": 3,
            "offset": 2,
        }
    ]


@pytest.mark.asyncio
async def test_contact_request_list_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from api.pagination import (
        encode_page_cursor,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
    created_at = datetime(
        2026,
        9,
        2,
        10,
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
    )

    item = SimpleNamespace(
        contact_request_id=contact_request_id,
        thread_id=thread_id,
        specialist_name="Test Specialist",
        profession_name="Consultant",
        message="Need a consultation.",
        status="new",
        created_at=created_at,
        tenant_id=uuid4(),
        from_user_id=uuid4(),
        extra_metadata={
            "private": True,
        },
    )

    class FakeUserDialogsService:
        async def list_contact_requests_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                items=[item],
                page=0,
                has_next=True,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: FakeUserDialogsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/contact-requests",
            params={
                "limit": 20,
            },
            headers={
                "X-Request-ID": (
                    "contact-request-list"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "page": 0,
            "page_size": 20,
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(
                    contact_request_id
                ),
                "thread_id": str(thread_id),
                "specialist_name": (
                    "Test Specialist"
                ),
                "profession_name": (
                    "Consultant"
                ),
                "message": (
                    "Need a consultation."
                ),
                "status": "new",
                "created_at": (
                    "2026-09-02T10:00:00Z"
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
            "contact-request-list"
        ),
    }

    assert "tenant_id" not in response.text
    assert "from_user_id" not in response.text
    assert "extra_metadata" not in response.text


@pytest.mark.asyncio
async def test_contact_request_list_requires_authentication():
    import httpx

    from api.app import create_app

    request_id = "contact-request-list-auth"
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/contact-requests",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": request_id,
        },
    }


@pytest.mark.asyncio
async def test_contact_request_detail_repository_uses_actor_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()

    class FakeResult:
        def one_or_none(self):
            return None

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ContactChatRepository(
        session
    )

    result = await (
        repository
        .get_contact_request_detail_for_client(
            tenant_id=tenant_id,
            user_id=user_id,
            contact_request_id=(
                contact_request_id
            ),
            language="uk",
        )
    )

    assert result is None
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    sql = " ".join(sql.lower().split())

    assert (
        f"contact_requests.id = "
        f"'{contact_request_id}'"
        in sql
    )
    assert (
        f"contact_requests.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"contact_requests.from_user_id = "
        f"'{user_id}'"
        in sql
    )


@pytest.mark.asyncio
async def test_neutral_contact_request_detail_uses_actor_scope():
    from services.user_dialogs import (
        UserDialogDetail,
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()
    expected = SimpleNamespace(
        contact_request_id=(
            contact_request_id
        ),
    )
    calls = []

    class FakeChats:
        async def get_client_request_detail(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserDialogsService(
        object(),
        settings=object(),
        users=object(),
        chats=FakeChats(),
        specialists=object(),
        user_repository=object(),
        moderation=object(),
        translation=object(),
    )

    action = await (
        service.get_contact_request_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            language="uk",
            contact_request_id=(
                contact_request_id
            ),
        )
    )

    assert isinstance(action, UserDialogDetail)
    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert action.actor.language == "uk"
    assert action.detail is expected

    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "contact_request_id": (
                contact_request_id
            ),
        }
    ]


@pytest.mark.asyncio
async def test_contact_request_detail_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
    created_at = datetime(
        2026,
        9,
        2,
        11,
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
    )

    detail = SimpleNamespace(
        contact_request_id=contact_request_id,
        thread_id=thread_id,
        specialist_name="Test Specialist",
        profession_name="Consultant",
        message="Need a consultation.",
        status="accepted",
        created_at=created_at,
    )

    class FakeUserDialogsService:
        async def get_contact_request_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                detail=detail,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: FakeUserDialogsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/contact-requests/"
                f"{contact_request_id}"
            ),
            headers={
                "X-Request-ID": (
                    "contact-request-detail"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "contact_request_id": (
                contact_request_id
            ),
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(contact_request_id),
            "thread_id": str(thread_id),
            "specialist_name": (
                "Test Specialist"
            ),
            "profession_name": "Consultant",
            "message": "Need a consultation.",
            "status": "accepted",
            "created_at": (
                "2026-09-02T11:00:00Z"
            ),
        },
        "meta": {},
        "request_id": (
            "contact-request-detail"
        ),
    }


@pytest.mark.asyncio
async def test_contact_request_detail_hides_missing_or_foreign_request():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.contact_chat import (
        ContactChatError,
    )

    request_id = "contact-request-hidden"
    contact_request_id = uuid4()

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class MissingService:
        async def get_contact_request_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "PRIVATE FOREIGN TENANT DETAILS"
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: MissingService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/contact-requests/"
                f"{contact_request_id}"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": (
                "contact_request_not_found"
            ),
            "message": (
                "Contact request was not found."
            ),
            "request_id": request_id,
        },
    }
    assert "PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_neutral_contact_request_cancel_uses_actor_scope():
    from services.user_dialogs import (
        UserContactRequestAction,
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()
    expected = SimpleNamespace(
        contact_request_id=(
            contact_request_id
        ),
        thread_id=uuid4(),
        status="cancelled",
        thread_status="closed",
    )
    calls = []

    class FakeChats:
        async def cancel_contact_request(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserDialogsService(
        object(),
        settings=object(),
        users=object(),
        chats=FakeChats(),
        specialists=object(),
        user_repository=object(),
        moderation=object(),
        translation=object(),
    )

    action = await (
        service.cancel_contact_request_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            language="uk",
            contact_request_id=(
                contact_request_id
            ),
        )
    )

    assert isinstance(
        action,
        UserContactRequestAction,
    )
    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert action.actor.language == "uk"
    assert action.result is expected

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "contact_request_id": (
                contact_request_id
            ),
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_contact_request_cancel_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
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

    class FakeUserDialogsService:
        async def cancel_contact_request_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=SimpleNamespace(
                    contact_request_id=(
                        contact_request_id
                    ),
                    thread_id=thread_id,
                    status="cancelled",
                    thread_status="closed",
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: FakeUserDialogsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/contact-requests/"
                f"{contact_request_id}/cancel"
            ),
            headers={
                "X-Request-ID": (
                    "contact-request-cancel"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "contact_request_id": (
                contact_request_id
            ),
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(contact_request_id),
            "thread_id": str(thread_id),
            "status": "cancelled",
            "thread_status": "closed",
        },
        "meta": {},
        "request_id": (
            "contact-request-cancel"
        ),
    }




@pytest.mark.asyncio
async def test_contact_request_replays_idempotent_result_without_duplicate():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    profession_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
    reserve_calls = []
    chat_calls = []

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            reserve_calls.append(kwargs)
            return SimpleNamespace(
                is_replay=True,
                response_status=201,
                response_payload={
                    "contact_request_id": str(
                        contact_request_id
                    ),
                    "thread_id": str(thread_id),
                    "was_existing": False,
                    "message_masked": True,
                    "thread_restricted": False,
                },
            )

        async def complete(self, **kwargs):
            raise AssertionError(
                "Replay must not be completed again."
            )

        async def commit(self):
            raise AssertionError(
                "Replay must not be committed again."
            )

    class FakeChats:
        async def start_contact_chat(
            self,
            **kwargs,
        ):
            chat_calls.append(kwargs)
            raise AssertionError(
                "Replay must not create another "
                "contact request or dialog."
            )

    service = UserDialogsService(
        object(),
        settings=object(),
        users=object(),
        chats=FakeChats(),
        specialists=object(),
        user_repository=object(),
        moderation=object(),
        translation=object(),
        idempotency=FakeIdempotency(),
    )

    result = await (
        service.create_contact_request_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            specialist_id=specialist_id,
            profession_id=profession_id,
            message="Need a consultation.",
            idempotency_key=(
                "same-contact-request-key"
            ),
        )
    )

    assert reserve_calls == [
        {
            "tenant_id": tenant_id,
            "principal_type": "user",
            "principal_id": user_id,
            "operation": (
                "contact_request.create"
            ),
            "idempotency_key": (
                "same-contact-request-key"
            ),
            "payload": {
                "specialist_id": str(
                    specialist_id
                ),
                "profession_id": str(
                    profession_id
                ),
                "message": (
                    "Need a consultation."
                ),
            },
        }
    ]
    assert chat_calls == []

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.chat.contact_request_id == (
        contact_request_id
    )
    assert result.chat.thread_id == thread_id
    assert result.chat.was_existing is False
    assert result.chat.message_masked is True
    assert result.chat.thread_restricted is False



@pytest.mark.asyncio
async def test_contact_request_create_commits_domain_and_idempotency_response_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    profession_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
    reservation_record = object()
    events = []
    chat_calls = []

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            events.append("reserve")
            return SimpleNamespace(
                record=reservation_record,
                is_replay=False,
                response_status=None,
                response_payload=None,
            )

        async def complete(self, **kwargs):
            events.append("complete")
            assert (
                kwargs["reservation"].record
                is reservation_record
            )
            assert kwargs["response_status"] == 201
            assert kwargs["response_payload"] == {
                "contact_request_id": str(
                    contact_request_id
                ),
                "thread_id": str(thread_id),
                "was_existing": False,
                "message_masked": True,
                "thread_restricted": False,
            }

        async def commit(self):
            events.append("commit")

    class FakeChats:
        async def start_contact_chat(
            self,
            **kwargs,
        ):
            events.append("create")
            chat_calls.append(kwargs)
            return SimpleNamespace(
                contact_request_id=(
                    contact_request_id
                ),
                thread_id=thread_id,
                was_existing=False,
                message_masked=True,
                thread_restricted=False,
                contact_token="private-token",
                detection_types=[
                    "private-detection",
                ],
                notification_id=uuid4(),
            )

    service = UserDialogsService(
        object(),
        settings=object(),
        users=object(),
        chats=FakeChats(),
        specialists=object(),
        user_repository=object(),
        moderation=object(),
        translation=object(),
        idempotency=FakeIdempotency(),
    )

    result = await (
        service.create_contact_request_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            specialist_id=specialist_id,
            profession_id=profession_id,
            message="Need a consultation.",
            idempotency_key=(
                "atomic-contact-request-key"
            ),
        )
    )

    assert result.chat.contact_request_id == (
        contact_request_id
    )
    assert events == [
        "reserve",
        "create",
        "complete",
        "commit",
    ]
    assert len(chat_calls) == 1
    assert chat_calls[0]["commit"] is False



@pytest.mark.asyncio
async def test_contact_chat_start_forwards_deferred_commit():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    profession_id = uuid4()
    expected = SimpleNamespace(
        was_existing=False,
    )
    calls = []

    class TestContactChatService(
        ContactChatService
    ):
        async def create_contact_request(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = TestContactChatService(None)

    result = await service.start_contact_chat(
        tenant_id=tenant_id,
        from_user_id=user_id,
        specialist_id=specialist_id,
        profession_id=profession_id,
        message="Need a consultation.",
        original_language="uk",
        platform="api",
        commit=False,
    )

    assert result is expected
    assert calls == [
        {
            "tenant_id": tenant_id,
            "from_user_id": user_id,
            "specialist_id": specialist_id,
            "profession_id": profession_id,
            "message": "Need a consultation.",
            "original_language": "uk",
            "platform": "api",
            "commit": False,
        }
    ]



@pytest.mark.asyncio
async def test_contact_request_creation_forwards_deferred_commit_to_repository(
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
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
    contact_request_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()
    notification_id = uuid4()
    calls = []

    class FakeRepository:
        session = object()

        async def get_active_contact_request_for_pair(
            self,
            **kwargs,
        ):
            return None

        async def create_contact_request_with_thread(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                SimpleNamespace(
                    id=contact_request_id,
                    extra_metadata={
                        "contact_token": (
                            "private-token"
                        ),
                    },
                ),
                SimpleNamespace(id=thread_id),
                SimpleNamespace(id=message_id),
                SimpleNamespace(id=notification_id),
            )

    class FakeDetectionService:
        def __init__(self, repository):
            self.repository = repository

        async def process_message(self, value):
            assert value == message_id
            return SimpleNamespace(
                is_masked=False,
                detected_types=[],
                thread_restricted=False,
            )

    monkeypatch.setattr(
        contact_module,
        "ContactDetectionService",
        FakeDetectionService,
    )

    repository = FakeRepository()
    service = ContactChatService(repository)
    service.rate_limit_service = None
    service._get_contact_cabinet_context = (
        AsyncMock(
            return_value=SimpleNamespace(
                specialist_id=specialist_id,
                specialist_user_id=(
                    specialist_user_id
                ),
                professional_cabinet_id=(
                    cabinet_id
                ),
                profession_id=profession_id,
            )
        )
    )

    result = await service.create_contact_request(
        tenant_id=tenant_id,
        from_user_id=user_id,
        specialist_id=specialist_id,
        profession_id=profession_id,
        message="Need a consultation.",
        original_language="uk",
        platform="api",
        commit=False,
    )

    assert result.contact_request_id == (
        contact_request_id
    )
    assert calls == [
        {
            "tenant_id": tenant_id,
            "from_user_id": user_id,
            "specialist_id": specialist_id,
            "profession_id": profession_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "specialist_user_id": (
                specialist_user_id
            ),
            "message": "Need a consultation.",
            "original_language": "uk",
            "platform": "api",
            "commit": False,
        }
    ]



@pytest.mark.asyncio
async def test_contact_request_repository_supports_deferred_commit():
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from database.repositories.contact import (
        ContactChatRepository,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush_count = 0
            self.commit_count = 0

        def add(self, value):
            if getattr(value, "id", None) is None:
                value.id = uuid4()
            self.added.append(value)

        def add_all(self, values):
            for value in values:
                self.add(value)

        async def flush(self):
            self.flush_count += 1

        async def commit(self):
            self.commit_count += 1

    session = FakeSession()
    repository = ContactChatRepository(session)
    repository._create_translation_job_if_needed = (
        AsyncMock()
    )

    result = await (
        repository
        .create_contact_request_with_thread(
            tenant_id=uuid4(),
            from_user_id=uuid4(),
            specialist_id=uuid4(),
            profession_id=uuid4(),
            professional_cabinet_id=uuid4(),
            specialist_user_id=uuid4(),
            message="Need a consultation.",
            original_language="uk",
            platform="api",
            commit=False,
        )
    )

    assert len(result) == 4
    assert session.flush_count == 3
    assert session.commit_count == 0
    repository._create_translation_job_if_needed.assert_awaited_once()



@pytest.mark.asyncio
async def test_existing_contact_chat_message_forwards_deferred_commit():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    profession_id = uuid4()
    contact_request_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()
    notification_id = uuid4()
    create_calls = []
    message_calls = []

    class TestContactChatService(
        ContactChatService
    ):
        async def create_contact_request(
            self,
            **kwargs,
        ):
            create_calls.append(kwargs)
            return SimpleNamespace(
                contact_request_id=(
                    contact_request_id
                ),
                thread_id=thread_id,
                specialist_user_id=uuid4(),
                contact_token="private-token",
                was_existing=True,
            )

        async def send_thread_message(
            self,
            **kwargs,
        ):
            message_calls.append(kwargs)
            return SimpleNamespace(
                message_id=message_id,
                notification_id=notification_id,
                message_masked=False,
                detection_types=[],
                thread_restricted=False,
            )

    service = TestContactChatService(None)

    result = await service.start_contact_chat(
        tenant_id=tenant_id,
        from_user_id=user_id,
        specialist_id=specialist_id,
        profession_id=profession_id,
        message="Follow-up consultation.",
        original_language="uk",
        commit=False,
    )

    assert result.was_existing is True
    assert create_calls[0]["commit"] is False
    assert message_calls == [
        {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "sender_user_id": user_id,
            "text": "Follow-up consultation.",
            "original_language": "uk",
            "commit": False,
        }
    ]



@pytest.mark.asyncio
async def test_contact_message_service_forwards_deferred_commit_to_repository(
    monkeypatch,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import services.contact_chat as contact_module
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    thread_id = uuid4()
    sender_user_id = uuid4()
    receiver_user_id = uuid4()
    message_id = uuid4()
    notification_id = uuid4()
    calls = []

    class FakeRepository:
        session = object()

        async def create_thread_message(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                SimpleNamespace(
                    id=thread_id,
                    status="in_discussion",
                ),
                SimpleNamespace(
                    id=message_id,
                    sender_user_id=sender_user_id,
                    receiver_user_id=receiver_user_id,
                ),
                SimpleNamespace(
                    id=notification_id,
                ),
            )

    class FakeDetectionService:
        def __init__(self, repository):
            self.repository = repository

        async def process_message(self, value):
            assert value == message_id
            return SimpleNamespace(
                is_masked=False,
                detected_types=[],
                thread_restricted=False,
            )

    monkeypatch.setattr(
        contact_module,
        "ContactDetectionService",
        FakeDetectionService,
    )

    service = ContactChatService(
        FakeRepository()
    )
    service.rate_limit_service = None

    result = await service.send_thread_message(
        tenant_id=tenant_id,
        thread_id=thread_id,
        sender_user_id=sender_user_id,
        text="Follow-up consultation.",
        original_language="uk",
        platform="api",
        commit=False,
    )

    assert result.message_id == message_id
    assert calls == [
        {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "sender_user_id": sender_user_id,
            "original_text": (
                "Follow-up consultation."
            ),
            "original_language": "uk",
            "message_metadata": None,
            "platform": "api",
            "commit": False,
        }
    ]



@pytest.mark.asyncio
async def test_contact_message_repository_supports_deferred_commit():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    thread_id = uuid4()
    sender_user_id = uuid4()
    receiver_user_id = uuid4()
    specialist_id = uuid4()

    thread = SimpleNamespace(
        id=thread_id,
        tenant_id=tenant_id,
        client_user_id=sender_user_id,
        specialist_id=specialist_id,
        status="open",
    )
    specialist = SimpleNamespace(
        id=specialist_id,
        user_id=receiver_user_id,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush_count = 0
            self.commit_count = 0

        def add(self, value):
            if getattr(value, "id", None) is None:
                value.id = uuid4()
            self.added.append(value)

        async def flush(self):
            self.flush_count += 1

        async def commit(self):
            self.commit_count += 1

        async def get(self, model, entity_id):
            assert entity_id == specialist_id
            return specialist

    session = FakeSession()
    repository = ContactChatRepository(session)
    repository.get_thread_for_user = AsyncMock(
        return_value=thread
    )
    repository._is_user_blacklisted_or_blocked = (
        AsyncMock(return_value=False)
    )
    repository._get_conversation_participant = (
        AsyncMock(return_value=None)
    )
    repository._create_translation_job_if_needed = (
        AsyncMock()
    )

    result = await repository.create_thread_message(
        tenant_id=tenant_id,
        thread_id=thread_id,
        sender_user_id=sender_user_id,
        original_text="Follow-up consultation.",
        original_language="uk",
        message_metadata=None,
        platform="api",
        commit=False,
    )

    assert len(result) == 3
    assert thread.status == "in_discussion"
    assert session.flush_count == 1
    assert session.commit_count == 0
    repository._create_translation_job_if_needed.assert_awaited_once()



def test_user_dialogs_dependency_injects_shared_idempotency_service(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_idempotency import (
        ApiIdempotencyService,
    )

    monkeypatch.setenv(
        "API_IDEMPOTENCY_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )

    session = object()
    service = get_user_dialogs_service(
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
async def test_contact_request_maps_idempotency_key_reuse_to_exact_conflict():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_dialogs_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.api_idempotency import (
        ApiIdempotencyKeyReusedError,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserDialogsService:
        async def create_contact_request_for_user(
            self,
            **kwargs,
        ):
            raise ApiIdempotencyKeyReusedError(
                "Private request hash details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: FakeUserDialogsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/contact-requests",
            headers={
                "X-Request-ID": (
                    "contact-idempotency-conflict"
                ),
                "Idempotency-Key": (
                    "reused-contact-key-001"
                ),
            },
            json={
                "specialist_id": str(uuid4()),
                "profession_id": str(uuid4()),
                "message": "Different request body.",
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == (
        "IDEMPOTENCY_KEY_REUSED"
    )
    assert body["error"]["message"] == (
        "Idempotency key was reused with "
        "a different request."
    )
    assert body["error"]["request_id"] == (
        "contact-idempotency-conflict"
    )
    assert "Private request hash" not in response.text
