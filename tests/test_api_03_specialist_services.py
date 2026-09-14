from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_services import (
    SpecialistServicesService,
)


class FakeSpecialistRepository:
    def __init__(self, specialist):
        self.specialist = specialist
        self.user_ids = []

    async def get_by_user_id(self, user_id):
        self.user_ids.append(user_id)
        return self.specialist


@pytest.mark.asyncio
async def test_neutral_services_actor_uses_user_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()

    repository = FakeSpecialistRepository(
        SimpleNamespace(
            id=specialist_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
    )

    service = SpecialistServicesService(
        object(),
        users=object(),
        translations=object(),
        repository=repository,
        specialist=object(),
    )

    actor = await service.require_user_actor(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
    )

    assert repository.user_ids == [user_id]
    assert actor.user_id == user_id
    assert actor.tenant_id == tenant_id
    assert actor.specialist_id == specialist_id
    assert actor.language == "uk"

@pytest.mark.asyncio
async def test_neutral_services_actor_rejects_foreign_tenant():
    from services.specialist_services import (
        SpecialistServicesAccessError,
    )

    user_id = uuid4()
    requested_tenant_id = uuid4()

    repository = FakeSpecialistRepository(
        SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            tenant_id=uuid4(),
        )
    )

    service = SpecialistServicesService(
        object(),
        users=object(),
        translations=object(),
        repository=repository,
        specialist=object(),
    )

    with pytest.raises(
        SpecialistServicesAccessError
    ):
        await service.require_user_actor(
            user_id=user_id,
            tenant_id=requested_tenant_id,
            language="uk",
        )

    assert repository.user_ids == [user_id]

@pytest.mark.asyncio
async def test_neutral_service_list_uses_owned_cabinet_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    items = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

    repository = FakeSpecialistRepository(
        SimpleNamespace(
            id=specialist_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
    )

    class FakeSpecialistDomain:
        def __init__(self):
            self.calls = []

        async def list_service_items_page_for_cabinet(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return len(items), items

    specialist = FakeSpecialistDomain()

    service = SpecialistServicesService(
        object(),
        users=object(),
        translations=object(),
        repository=repository,
        specialist=specialist,
    )

    page = await service.list_services_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        page=1,
        page_size=25,
        platform="api",
    )

    assert page.actor.user_id == user_id
    assert page.actor.tenant_id == tenant_id
    assert page.actor.specialist_id == specialist_id
    assert page.total == 2
    assert page.items is items

    assert specialist.calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "specialist_id": specialist_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "page": 1,
            "page_size": 25,
            "platform": "api",
        }
    ]

@pytest.mark.asyncio
async def test_domain_service_list_checks_cabinet_owner_scope():
    from services.specialist import (
        SpecialistService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    items = [
        SimpleNamespace(id=uuid4()),
    ]

    class FakeSession:
        def __init__(self):
            self.events = []
            self.flushes = 0
            self.commits = 0
            self.rollbacks = 0

        def add(self, event):
            self.events.append(event)

        async def flush(self):
            self.flushes += 1

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeOwnedCabinetRepository:
        def __init__(self):
            self.session = FakeSession()
            self.calls = []

        async def get_by_user_id(self, value):
            self.calls.append(
                ("get_specialist", value)
            )
            return SimpleNamespace(
                id=specialist_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            self.calls.append(
                ("get_cabinet", kwargs)
            )
            return (
                SimpleNamespace(
                    id=cabinet_id,
                    specialist_id=specialist_id,
                    tenant_id=tenant_id,
                ),
                SimpleNamespace(id=uuid4()),
            )

        async def list_specialist_services_page(
            self,
            **kwargs,
        ):
            self.calls.append(
                ("list_services", kwargs)
            )
            return len(items), items

    repository = FakeOwnedCabinetRepository()
    service = SpecialistService(repository)

    result = await (
        service.list_service_items_page_for_cabinet(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
            professional_cabinet_id=cabinet_id,
            page=1,
            page_size=25,
            platform="api",
        )
    )

    assert result == (1, items)
    assert repository.calls == [
        (
            "get_specialist",
            user_id,
        ),
        (
            "get_cabinet",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "list_services",
            {
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "limit": 25,
                "offset": 25,
            },
        ),
    ]

    assert repository.session.commits == 1
    assert repository.session.rollbacks == 0
    assert len(repository.session.events) == 1

    event = repository.session.events[0]
    assert event.event_type == "service_list"
    assert event.platform == "api"
    assert event.tenant_id == tenant_id
    assert event.user_id == user_id

@pytest.mark.asyncio
async def test_domain_service_list_rejects_foreign_cabinet():
    from services.specialist import (
        SpecialistRegistrationError,
        SpecialistService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class ForeignCabinetRepository:
        def __init__(self):
            self.session = FakeSession()
            self.list_calls = []

        async def get_by_user_id(self, value):
            assert value == user_id
            return SimpleNamespace(
                id=specialist_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            assert kwargs == {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            }
            return None

        async def list_specialist_services_page(
            self,
            **kwargs,
        ):
            self.list_calls.append(kwargs)
            raise AssertionError(
                "Foreign cabinet services "
                "must not be queried."
            )

    repository = ForeignCabinetRepository()
    service = SpecialistService(repository)

    with pytest.raises(
        SpecialistRegistrationError
    ):
        await (
            service
            .list_service_items_page_for_cabinet(
                tenant_id=tenant_id,
                user_id=user_id,
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
                page=0,
                page_size=25,
                platform="api",
            )
        )

    assert repository.list_calls == []
    assert repository.session.commits == 0

@pytest.mark.asyncio
async def test_specialist_cabinet_services_use_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_services_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    service_id = uuid4()
    request_id = "specialist-services-list"
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.services.read",
        ),
    )

    class FakeSpecialistServices:
        async def list_services_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                total=1,
                items=[
                    SimpleNamespace(
                        id=service_id,
                        title="Consultation",
                        description="Online session",
                        price_from=50,
                        price_to=100,
                        currency="EUR",
                        price_unit="service",
                        status="active",
                    )
                ],
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_services_service
    ] = lambda: FakeSpecialistServices()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/services"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "page": 0,
            "page_size": 25,
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(service_id),
                "title": "Consultation",
                "description": "Online session",
                "price_from": 50.0,
                "price_to": 100.0,
                "currency": "EUR",
                "price_unit": "service",
                "status": "active",
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }

@pytest.mark.asyncio
async def test_specialist_cabinet_services_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_services_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    calls = []

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(),
    )

    class ForbiddenService:
        async def list_services_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Service must not be called "
                "without read permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_services_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/services"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []

@pytest.mark.asyncio
async def test_specialist_cabinet_services_require_authentication():
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
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/services"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )

@pytest.mark.asyncio
async def test_specialist_services_use_cursor_pagination():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_services_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    cabinet_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.services.read",
        ),
    )

    items = [
        SimpleNamespace(
            id=uuid4(),
            title=f"Service {index}",
            description=None,
            price_from=None,
            price_to=None,
            currency="EUR",
            price_unit="service",
            status="active",
        )
        for index in range(25)
    ]

    class FakeSpecialistServices:
        async def list_services_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                total=51,
                items=items,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_services_service
    ] = lambda: FakeSpecialistServices()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/services"
                "?limit=25&cursor=MQ"
            )
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": actor.user_id,
            "tenant_id": actor.tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "page": 1,
            "page_size": 25,
            "platform": "api",
        }
    ]

    body = response.json()

    assert len(body["data"]) == 25
    assert body["meta"] == {
        "next_cursor": "Mg",
        "has_more": True,
    }

@pytest.mark.asyncio
async def test_specialist_services_reject_invalid_cursor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_services_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    calls = []

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.services.read",
        ),
    )

    class ForbiddenService:
        async def list_services_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Service must not receive "
                "an invalid cursor."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_services_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/services"
                "?cursor=A"
            )
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "invalid_cursor"
    )
    assert calls == []

@pytest.mark.asyncio
async def test_specialist_services_hide_foreign_cabinet_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_services_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist import (
        SpecialistRegistrationError,
    )

    private_details = (
        "foreign tenant database cabinet details"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.services.read",
        ),
    )

    class ForeignCabinetService:
        async def list_services_for_user(
            self,
            **kwargs,
        ):
            raise SpecialistRegistrationError(
                private_details
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_services_service
    ] = lambda: ForeignCabinetService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/services"
            )
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "cabinet_not_found"
    )
    assert private_details not in response.text

def test_specialist_services_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/services"
        )
    ]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    parameters = {
        parameter["name"]: parameter
        for parameter in operation[
            "parameters"
        ]
    }

    assert {
        "cabinet_id",
        "limit",
        "cursor",
    } <= set(parameters)

    assert parameters["cabinet_id"]["in"] == (
        "path"
    )
    assert parameters["limit"]["in"] == "query"
    assert parameters["cursor"]["in"] == "query"

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistServiceListResponse"
        )
    }
