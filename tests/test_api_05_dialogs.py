from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_dialog_repository_list_uses_actor_scope():
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

    result = await repository.list_threads_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        participant_role="client",
        view="active",
        language="uk",
        limit=20,
        offset=0,
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
        f"conversation_threads.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"conversation_participants.user_id = "
        f"'{user_id}'"
        in sql
    )
    assert (
        "conversation_participants."
        "participant_role = 'client'"
        in sql
    )


@pytest.mark.asyncio
async def test_dialog_unread_count_uses_actor_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    class FakeResult:
        def scalar_one(self):
            return 0

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
        .count_unread_messages_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            participant_role="client",
        )
    )

    assert result == 0
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
        f"conversation_threads.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"conversation_participants.user_id = "
        f"'{user_id}'"
        in sql
    )
    assert (
        "conversation_participants."
        "participant_role = 'client'"
        in sql
    )


@pytest.mark.asyncio
async def test_neutral_dialog_list_uses_actor_scope():
    from types import SimpleNamespace

    from services.user_dialogs import (
        UserDialogsPage,
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    items = [
        SimpleNamespace(thread_id=uuid4()),
        SimpleNamespace(thread_id=uuid4()),
        SimpleNamespace(thread_id=uuid4()),
    ]
    calls = []

    class FakeChats:
        async def list_client_threads(
            self,
            **kwargs,
        ):
            calls.append(
                ("list", kwargs)
            )
            return items

        async def count_unread_messages(
            self,
            **kwargs,
        ):
            calls.append(
                ("unread", kwargs)
            )
            return 4

        async def record_messages_opened(
            self,
            **kwargs,
        ):
            calls.append(
                ("audit", kwargs)
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
    )

    page = await service.list_dialogs_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        language="uk",
        role="client",
        view="active",
        page=1,
        page_size=2,
        search_query="consultant",
    )

    assert isinstance(page, UserDialogsPage)
    assert page.actor.user_id == user_id
    assert page.actor.tenant_id == tenant_id
    assert page.actor.language == "uk"
    assert page.items == items[:2]
    assert page.unread_messages == 4
    assert page.page == 1
    assert page.has_next is True
    assert page.show_role_switch is False

    assert calls == [
        (
            "list",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "view": "active",
                "limit": 3,
                "offset": 2,
                "language": "uk",
                "search_query": "consultant",
            },
        ),
        (
            "unread",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "participant_role": "client",
            },
        ),
        (
            "audit",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "participant_role": "client",
                "view": "active",
                "page": 1,
                "items_count": 2,
                "platform": "api",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_me_dialogs_endpoint_uses_current_actor():
    from datetime import UTC, datetime
    from types import SimpleNamespace

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

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    last_message_at = datetime(
        2026,
        9,
        2,
        12,
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
        thread_id=thread_id,
        specialist_name="Test Specialist",
        profession_name="Consultant",
        last_message_text="See you tomorrow.",
        last_message_at=last_message_at,
        unread_count=2,
        status="open",
        tenant_id=uuid4(),
    )

    class FakeUserDialogsService:
        async def list_dialogs_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                items=[item],
                unread_messages=2,
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
            "/api/v1/me/dialogs",
            params={
                "view": "active",
                "q": "consultant",
                "limit": 20,
            },
            headers={
                "X-Request-ID": "dialog-list",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "role": "client",
            "view": "active",
            "page": 0,
            "page_size": 20,
            "search_query": "consultant",
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(thread_id),
                "counterparty_name": (
                    "Test Specialist"
                ),
                "profession_name": "Consultant",
                "last_message_text": (
                    "See you tomorrow."
                ),
                "last_message_at": (
                    "2026-09-02T12:00:00Z"
                ),
                "unread_count": 2,
                "status": "open",
            }
        ],
        "meta": {
            "next_cursor": (
                encode_page_cursor(1)
            ),
            "has_more": True,
            "unread_messages": 2,
        },
        "request_id": "dialog-list",
    }

    assert "tenant_id" not in response.text



@pytest.mark.asyncio
async def test_me_dialogs_require_authentication():
    import httpx

    from api.app import create_app

    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me/dialogs",
            headers={
                "X-Request-ID": (
                    "dialogs-auth-required"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": (
                "dialogs-auth-required"
            ),
        },
    }


@pytest.mark.asyncio
async def test_me_dialogs_reject_unassigned_role():
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

    class ForbiddenService:
        async def list_dialogs_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Dialog service must not be called "
                "for an unassigned role."
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
        response = await client.get(
            "/api/v1/me/dialogs",
            params={
                "role": "specialist",
            },
            headers={
                "X-Request-ID": (
                    "dialogs-role-denied"
                ),
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "dialog_role_denied",
            "message": (
                "Dialog role access denied."
            ),
            "request_id": (
                "dialogs-role-denied"
            ),
        },
    }


@pytest.mark.asyncio
async def test_dialog_repository_detail_uses_actor_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()

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
        repository.get_thread_detail_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            language="uk",
            messages_limit=50,
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
        f"conversation_threads.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"conversation_threads.id = "
        f"'{thread_id}'"
        in sql
    )
    assert (
        f"conversation_threads.client_user_id = "
        f"'{user_id}'"
        in sql
        or (
            f"specialists.user_id = "
            f"'{user_id}'"
            in sql
        )
    )


@pytest.mark.asyncio
async def test_neutral_dialog_detail_uses_actor_scope():
    from types import SimpleNamespace

    from services.user_dialogs import (
        UserDialogDetail,
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    expected_detail = SimpleNamespace(
        thread_id=thread_id,
    )
    calls = []

    class FakeChats:
        async def get_thread_detail_for_viewer(
            self,
            **kwargs,
        ):
            calls.append(
                ("detail", kwargs)
            )
            return expected_detail

        async def mark_thread_read(
            self,
            **kwargs,
        ):
            calls.append(
                ("read", kwargs)
            )
            return 2

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

    result = await service.get_dialog_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        language="uk",
        role="client",
    )

    assert isinstance(result, UserDialogDetail)
    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert result.actor.language == "uk"
    assert result.detail is expected_detail

    assert calls == [
        (
            "detail",
            {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "participant_role": "client",
                "language": "uk",
                "platform": "api",
            },
        ),
        (
            "read",
            {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "user_id": user_id,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_dialog_detail_endpoint_uses_current_actor():
    from datetime import UTC, datetime
    from types import SimpleNamespace

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
    thread_id = uuid4()
    contact_request_id = uuid4()
    order_id = uuid4()
    created_at = datetime(
        2026,
        9,
        2,
        14,
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

    detail = SimpleNamespace(
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        specialist_name="Test Specialist",
        client_name="Private Client",
        profession_name="Consultant",
        request_text="Need a consultation.",
        request_status="accepted",
        thread_status="open",
        active_order_id=order_id,
        active_order_status="confirmed",
        active_order_created_by=uuid4(),
        show_original_button=True,
        tenant_id=tenant_id,
        messages=[
            SimpleNamespace(
                text="Перекладене повідомлення",
                original_text="Original message",
                is_sent_by_viewer=False,
                is_system=False,
                created_at=created_at,
                used_translation=True,
                attachment=None,
                detection_types=[
                    "private_detection",
                ],
            ),
        ],
    )

    class FakeUserDialogsService:
        async def get_dialog_for_user(
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
            f"/api/v1/dialogs/{thread_id}",
            params={
                "role": "client",
            },
            headers={
                "X-Request-ID": (
                    "dialog-detail"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "language": "uk",
            "role": "client",
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(thread_id),
            "contact_request_id": (
                str(contact_request_id)
            ),
            "counterparty_name": (
                "Test Specialist"
            ),
            "profession_name": "Consultant",
            "request_text": (
                "Need a consultation."
            ),
            "request_status": "accepted",
            "status": "open",
            "active_order_id": str(order_id),
            "active_order_status": "confirmed",
            "show_original_button": True,
            "messages": [
                {
                    "text": (
                        "Перекладене повідомлення"
                    ),
                    "original_text": (
                        "Original message"
                    ),
                    "is_sent_by_viewer": False,
                    "is_system": False,
                    "created_at": (
                        "2026-09-02T14:30:00Z"
                    ),
                    "used_translation": True,
                    "attachment": None,
                },
            ],
        },
        "meta": {},
        "request_id": "dialog-detail",
    }

    assert "tenant_id" not in response.text
    assert "active_order_created_by" not in response.text
    assert "detection_types" not in response.text


@pytest.mark.asyncio
async def test_dialog_detail_requires_authentication():
    import httpx

    from api.app import create_app

    thread_id = uuid4()
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/v1/dialogs/{thread_id}",
            headers={
                "X-Request-ID": (
                    "dialog-detail-auth"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": (
                "dialog-detail-auth"
            ),
        },
    }


@pytest.mark.asyncio
async def test_dialog_detail_hides_missing_or_foreign_thread():
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

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class HiddenDialogService:
        async def get_dialog_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "Foreign tenant thread exists "
                "but actor is not a participant."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: HiddenDialogService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/v1/dialogs/{uuid4()}",
            headers={
                "X-Request-ID": (
                    "dialog-detail-hidden"
                ),
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "dialog_not_found",
            "message": "Dialog not found.",
            "request_id": (
                "dialog-detail-hidden"
            ),
        },
    }

    assert "tenant" not in response.text.lower()
    assert "participant" not in response.text.lower()


@pytest.mark.asyncio
async def test_neutral_dialog_message_uses_actor_scope():
    from types import SimpleNamespace

    from services.user_dialogs import (
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    message_id = uuid4()
    calls = []

    expected_result = SimpleNamespace(
        thread_id=thread_id,
        message_id=message_id,
        thread_status="in_discussion",
        message_masked=False,
        thread_restricted=False,
    )

    class FakeChats:
        async def send_thread_message(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected_result

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

    action = await service.send_dialog_message_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        language="uk",
        text="Hello from Web.",
        attachment=None,
    )

    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert action.actor.language == "uk"
    assert action.result is expected_result

    assert calls == [
        {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "sender_user_id": user_id,
            "text": "Hello from Web.",
            "original_language": "uk",
            "attachment": None,
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_dialog_message_endpoint_uses_current_actor():
    from types import SimpleNamespace

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
    thread_id = uuid4()
    message_id = uuid4()
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

    result = SimpleNamespace(
        thread_id=thread_id,
        message_id=message_id,
        notification_id=uuid4(),
        sender_user_id=user_id,
        receiver_user_id=uuid4(),
        thread_status="in_discussion",
        message_masked=False,
        detection_types=[
            "private_detection",
        ],
        thread_restricted=False,
    )

    class FakeUserDialogsService:
        async def send_dialog_message_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=result,
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
                f"/api/v1/dialogs/"
                f"{thread_id}/messages"
            ),
            headers={
                "X-Request-ID": (
                    "dialog-message-create"
                ),
            },
            json={
                "text": "Hello from Web.",
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "language": "uk",
            "text": "Hello from Web.",
            "attachment": None,
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(message_id),
            "dialog_id": str(thread_id),
            "status": "in_discussion",
            "message_masked": False,
            "thread_restricted": False,
        },
        "meta": {},
        "request_id": (
            "dialog-message-create"
        ),
    }

    private_fields = (
        "notification_id",
        "sender_user_id",
        "receiver_user_id",
        "detection_types",
    )
    for field in private_fields:
        assert field not in response.text


@pytest.mark.asyncio
async def test_dialog_message_requires_authentication():
    import httpx

    from api.app import create_app

    thread_id = uuid4()
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                f"/api/v1/dialogs/"
                f"{thread_id}/messages"
            ),
            headers={
                "X-Request-ID": (
                    "dialog-message-auth"
                ),
            },
            json={
                "text": "Unauthorized message.",
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": (
                "dialog-message-auth"
            ),
        },
    }


@pytest.mark.asyncio
async def test_dialog_message_rejects_actor_fields():
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

    class ForbiddenService:
        async def send_dialog_message_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Service must not be called when "
                "actor fields are supplied."
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
            (
                f"/api/v1/dialogs/"
                f"{uuid4()}/messages"
            ),
            json={
                "text": "Attempted spoof.",
                "user_id": str(uuid4()),
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_dialog_message_hides_missing_or_foreign_thread():
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
        ContactChatThreadNotFoundError,
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

    class HiddenDialogService:
        async def send_dialog_message_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatThreadNotFoundError(
                "Foreign tenant thread exists "
                "but actor is not a participant."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: HiddenDialogService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                f"/api/v1/dialogs/"
                f"{uuid4()}/messages"
            ),
            headers={
                "X-Request-ID": (
                    "dialog-message-hidden"
                ),
            },
            json={
                "text": "Hidden target.",
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "dialog_not_found",
            "message": "Dialog not found.",
            "request_id": (
                "dialog-message-hidden"
            ),
        },
    }

    assert "tenant" not in response.text.lower()
    assert "participant" not in response.text.lower()


@pytest.mark.asyncio
async def test_dialog_message_rate_limit_is_sanitized():
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

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class RateLimitedService:
        async def send_dialog_message_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatRateLimitError(
                "Internal limit=20, window=60, "
                "storage_key=private."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: RateLimitedService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                f"/api/v1/dialogs/"
                f"{uuid4()}/messages"
            ),
            headers={
                "X-Request-ID": (
                    "dialog-message-rate-limit"
                ),
            },
            json={
                "text": "Rate limited message.",
            },
        )

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": (
                "dialog_message_rate_limited"
            ),
            "message": (
                "Too many message requests."
            ),
            "request_id": (
                "dialog-message-rate-limit"
            ),
        },
    }

    assert "limit=20" not in response.text
    assert "storage_key" not in response.text


@pytest.mark.asyncio
async def test_empty_dialog_message_is_sanitized():
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

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class InvalidMessageService:
        async def send_dialog_message_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "Internal attachment validation "
                "or empty-message details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: InvalidMessageService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                f"/api/v1/dialogs/"
                f"{uuid4()}/messages"
            ),
            headers={
                "X-Request-ID": (
                    "dialog-message-invalid"
                ),
            },
            json={
                "text": "",
                "attachment": None,
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "dialog_message_invalid",
            "message": (
                "Dialog message is not valid."
            ),
            "request_id": (
                "dialog-message-invalid"
            ),
        },
    }

    assert "attachment validation" not in response.text
    assert "empty-message" not in response.text


@pytest.mark.asyncio
async def test_neutral_dialog_finish_reuses_existing_flow():
    from types import SimpleNamespace

    from services.user_dialogs import (
        UserDialogCompletion,
        UserDialogsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    expected_result = SimpleNamespace(
        thread_id=thread_id,
        action="requested",
        contact_request_id=(
            contact_request_id
        ),
        requested_for_user_id=uuid4(),
        requested_for_role="specialist",
    )

    class FakeChats:
        async def finish_thread(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected_result

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

    action = await service.finish_dialog_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        language="uk",
    )

    assert isinstance(
        action,
        UserDialogCompletion,
    )
    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert action.actor.language == "uk"
    assert action.result is expected_result
    assert action.receiver_chat_id is None

    assert calls == [
        {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "actor_user_id": user_id,
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_dialog_finish_lookup_uses_actor_scope():
    from types import SimpleNamespace

    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    calls = []

    class FakeRepository:
        session = object()

        async def get_completion_requester_id(
            self,
            *,
            tenant_id,
            thread_id,
            user_id,
        ):
            calls.append(
                (
                    "lookup",
                    {
                        "tenant_id": tenant_id,
                        "thread_id": thread_id,
                        "user_id": user_id,
                    },
                )
            )
            return None

        async def request_thread_completion(
            self,
            **kwargs,
        ):
            calls.append(
                ("request", kwargs)
            )
            return (
                SimpleNamespace(
                    id=thread_id,
                    context_id=uuid4(),
                ),
                SimpleNamespace(
                    user_id=uuid4(),
                    payload={
                        "requested_for_role": (
                            "specialist"
                        ),
                    },
                ),
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.finish_thread(
        tenant_id=tenant_id,
        thread_id=thread_id,
        actor_user_id=user_id,
        platform="api",
    )

    assert result.action == "requested"
    assert calls[0] == (
        "lookup",
        {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "user_id": user_id,
        },
    )
    assert calls[1] == (
        "request",
        {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "actor_user_id": user_id,
            "platform": "api",
        },
    )


@pytest.mark.asyncio
async def test_dialog_completion_confirmation_uses_actor_scope():
    from types import SimpleNamespace

    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    requester_id = uuid4()
    actor_user_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    class FakeRepository:
        session = object()

        async def get_completion_requester_id(
            self,
            **kwargs,
        ):
            calls.append(
                ("lookup", kwargs)
            )
            return str(requester_id)

        async def complete_thread(
            self,
            *,
            tenant_id,
            thread_id,
            actor_user_id,
            platform,
        ):
            calls.append(
                (
                    "complete",
                    {
                        "tenant_id": tenant_id,
                        "thread_id": thread_id,
                        "actor_user_id": (
                            actor_user_id
                        ),
                        "platform": platform,
                    },
                )
            )
            return SimpleNamespace(
                id=thread_id,
                context_id=contact_request_id,
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.finish_thread(
        tenant_id=tenant_id,
        thread_id=thread_id,
        actor_user_id=actor_user_id,
        platform="api",
    )

    assert result.action == "completed"
    assert result.thread_id == thread_id
    assert result.contact_request_id == (
        contact_request_id
    )

    assert calls == [
        (
            "lookup",
            {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "user_id": actor_user_id,
            },
        ),
        (
            "complete",
            {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "actor_user_id": (
                    actor_user_id
                ),
                "platform": "api",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_dialog_finish_endpoint_uses_current_actor():
    from types import SimpleNamespace

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
    thread_id = uuid4()
    contact_request_id = uuid4()
    requested_for_user_id = uuid4()
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

    result = SimpleNamespace(
        thread_id=thread_id,
        action="requested",
        contact_request_id=(
            contact_request_id
        ),
        requested_for_user_id=(
            requested_for_user_id
        ),
        requested_for_role="specialist",
        notification_id=uuid4(),
    )

    class FakeUserDialogsService:
        async def finish_dialog_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=result,
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
            f"/api/v1/dialogs/{thread_id}/finish",
            headers={
                "X-Request-ID": (
                    "dialog-finish"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "language": "uk",
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(thread_id),
            "action": "requested",
            "contact_request_id": (
                str(contact_request_id)
            ),
            "requested_for_role": (
                "specialist"
            ),
        },
        "meta": {},
        "request_id": "dialog-finish",
    }

    assert "requested_for_user_id" not in response.text
    assert "notification_id" not in response.text


@pytest.mark.asyncio
async def test_dialog_finish_requires_authentication():
    import httpx

    from api.app import create_app

    thread_id = uuid4()
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/dialogs/{thread_id}/finish",
            headers={
                "X-Request-ID": (
                    "dialog-finish-auth"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": (
                "dialog-finish-auth"
            ),
        },
    }


@pytest.mark.asyncio
async def test_dialog_finish_preserves_typed_not_found():
    from database.repositories.contact import (
        ContactThreadNotFoundError,
    )
    from services.contact_chat import (
        ContactChatService,
        ContactChatThreadNotFoundError,
    )

    class FakeRepository:
        session = object()

        async def get_completion_requester_id(
            self,
            **kwargs,
        ):
            raise ContactThreadNotFoundError(
                "Foreign tenant thread."
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    with pytest.raises(
        ContactChatThreadNotFoundError,
    ):
        await service.finish_thread(
            tenant_id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            platform="api",
        )


@pytest.mark.asyncio
async def test_dialog_finish_hides_missing_or_foreign_thread():
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
        ContactChatThreadNotFoundError,
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

    class HiddenDialogService:
        async def finish_dialog_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatThreadNotFoundError(
                "Foreign tenant participant."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_dialogs_service
    ] = lambda: HiddenDialogService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/dialogs/{uuid4()}/finish",
            headers={
                "X-Request-ID": (
                    "dialog-finish-hidden"
                ),
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "dialog_not_found",
            "message": "Dialog not found.",
            "request_id": (
                "dialog-finish-hidden"
            ),
        },
    }

    assert "tenant" not in response.text.lower()
    assert "participant" not in response.text.lower()


@pytest.mark.asyncio
async def test_dialog_finish_requester_cannot_self_confirm():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    calls = []

    class FakeRepository:
        session = object()

        async def get_completion_requester_id(
            self,
            **kwargs,
        ):
            calls.append(
                ("lookup", kwargs)
            )
            return str(user_id)

        async def complete_thread(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Requester must not confirm "
                "their own completion request."
            )

        async def request_thread_completion(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "A duplicate completion request "
                "must not be created."
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.finish_thread(
        tenant_id=tenant_id,
        thread_id=thread_id,
        actor_user_id=user_id,
        platform="api",
    )

    assert result.thread_id == thread_id
    assert result.action == "pending"
    assert result.contact_request_id is None
    assert result.requested_for_user_id is None

    assert calls == [
        (
            "lookup",
            {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "user_id": user_id,
            },
        ),
    ]
