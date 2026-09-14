from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_cabinets import (
    SpecialistCabinetsService,
)


@pytest.mark.asyncio
async def test_neutral_availability_uses_owned_cabinet_scope():
    user_id = uuid4()
    tenant_id = uuid4()
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

    class FakeRepository:
        async def get_by_user_id(self, value):
            calls.append(("specialist", value))
            return specialist

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return (
                cabinet,
                SimpleNamespace(id=uuid4()),
            )

    class FakeSpecialists:
        async def get_cabinet_availability(
            self,
            **kwargs,
        ):
            calls.append(("availability", kwargs))
            return "available"

    service = SpecialistCabinetsService(
        object(),
        settings=object(),
        repository=FakeRepository(),
        specialists=FakeSpecialists(),
    )

    action = await (
        service.get_availability_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
        )
    )

    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert (
        action.actor.specialist_id
        == specialist_id
    )
    assert action.result == "available"

    assert calls == [
        ("specialist", user_id),
        (
            "cabinet",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "availability",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
    ]


@pytest.mark.asyncio
async def test_domain_availability_checks_cabinet_owner_scope():
    from services.specialist import (
        SpecialistService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
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
        availability_status="busy",
    )

    class FakeRepository:
        async def get_by_user_id(self, value):
            calls.append(("specialist", value))
            return specialist

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return (
                cabinet,
                SimpleNamespace(id=uuid4()),
            )

    service = SpecialistService(
        FakeRepository()
    )

    result = await (
        service.get_cabinet_availability(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                cabinet_id
            ),
        )
    )

    assert result == "busy"
    assert calls == [
        ("specialist", user_id),
        (
            "cabinet",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
    ]


@pytest.mark.asyncio
async def test_specialist_cabinet_availability_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    request_id = (
        "specialist-availability-read"
    )
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
            "specialist.availability.read",
        ),
    )

    class FakeCabinetsService:
        async def get_availability_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result="busy",
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: FakeCabinetsService()

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
                f"{cabinet_id}/availability"
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
        }
    ]
    assert response.json() == {
        "data": {
            "status": "busy",
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_neutral_availability_update_uses_owned_cabinet_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []
    update_result = (
        "available",
        "vacation",
        True,
    )

    specialist = SimpleNamespace(
        id=specialist_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
    )

    class FakeRepository:
        async def get_by_user_id(self, value):
            calls.append(("specialist", value))
            return specialist

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return (
                cabinet,
                SimpleNamespace(id=uuid4()),
            )

    class FakeSpecialists:
        async def update_cabinet_availability(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            return update_result

    service = SpecialistCabinetsService(
        object(),
        settings=object(),
        repository=FakeRepository(),
        specialists=FakeSpecialists(),
    )

    action = await (
        service.set_availability_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
            availability_status="vacation",
            platform="api",
        )
    )

    assert action.result is update_result
    assert calls == [
        ("specialist", user_id),
        (
            "cabinet",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "update",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "availability_status": (
                    "vacation"
                ),
                "platform": "api",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_availability_repository_updates_scoped_cabinet():
    from unittest.mock import AsyncMock

    from database.repositories.specialist import (
        SpecialistRepository,
    )

    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        availability_status="available",
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = SpecialistRepository(
        session
    )
    repository.get_professional_cabinet = (
        AsyncMock(
            return_value=(
                cabinet,
                SimpleNamespace(id=uuid4()),
            )
        )
    )

    result = await (
        repository.update_cabinet_availability(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            availability_status="vacation",
        )
    )

    repository.get_professional_cabinet.assert_awaited_once_with(
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
    )
    assert result is cabinet
    assert (
        cabinet.availability_status
        == "vacation"
    )
    assert cabinet.updated_at is not None
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_domain_availability_update_is_scoped_and_audited(
    monkeypatch,
):
    import services.specialist as specialist_module

    from services.specialist import (
        SpecialistService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
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
            calls.append(("commit",))

        async def rollback(self):
            calls.append(("rollback",))

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def get_by_user_id(self, value):
            calls.append(("specialist", value))
            return specialist

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return (
                cabinet,
                SimpleNamespace(id=uuid4()),
            )

        async def update_cabinet_availability(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            cabinet.availability_status = (
                kwargs["availability_status"]
            )
            return cabinet

    class FakeEvents:
        def __init__(self, session):
            assert isinstance(
                session,
                FakeSession,
            )

        async def create_event(self, **kwargs):
            calls.append(("event", kwargs))

    monkeypatch.setattr(
        specialist_module,
        "EventRepository",
        FakeEvents,
    )

    service = SpecialistService(
        FakeRepository()
    )

    result = await (
        service.update_cabinet_availability(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            availability_status="vacation",
            platform="api",
        )
    )

    assert result == (
        "available",
        "vacation",
        True,
    )
    assert calls == [
        ("specialist", user_id),
        (
            "cabinet",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "update",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "availability_status": (
                    "vacation"
                ),
            },
        ),
        (
            "event",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": "change_submitted",
                "entity_type": (
                    "professional_cabinet"
                ),
                "entity_id": cabinet_id,
                "payload": {
                    "field": (
                        "availability_status"
                    ),
                    "before": "available",
                    "after": "vacation",
                },
                "platform": "api",
            },
        ),
        ("commit",),
    ]


@pytest.mark.asyncio
async def test_specialist_cabinet_availability_update_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    request_id = (
        "specialist-availability-update"
    )
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
            "specialist.availability.write",
        ),
    )

    class FakeCabinetsService:
        async def set_availability_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=(
                    "available",
                    "vacation",
                    True,
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: FakeCabinetsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/availability"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "status": "vacation",
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
            "availability_status": (
                "vacation"
            ),
            "platform": "api",
        }
    ]
    assert response.json() == {
        "data": {
            "status": "vacation",
            "changed": True,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_specialist_availability_update_requires_write_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
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
            "specialist.availability.read",
        ),
    )

    class ForbiddenCabinetsService:
        async def set_availability_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Availability mutation must "
                "not run without write permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: ForbiddenCabinetsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/availability"
            ),
            json={
                "status": "busy",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_availability_update_rejects_unknown_status():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
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
            "specialist.availability.write",
        ),
    )

    class ForbiddenCabinetsService:
        async def set_availability_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Invalid status must not "
                "reach the service."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: ForbiddenCabinetsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/availability"
            ),
            json={
                "status": "hidden_private_status",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "validation_error"
    )
    assert calls == []


def test_specialist_availability_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operations = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/availability"
        )
    ]

    get_operation = operations["get"]
    put_operation = operations["put"]

    assert get_operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]
    assert put_operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    get_response = (
        get_operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert get_response == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistAvailabilityResponse"
        )
    }

    request_schema = (
        put_operation["requestBody"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert request_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistAvailabilityUpdateRequest"
        )
    }

    put_response = (
        put_operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert put_response == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistAvailabilityUpdateResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistAvailabilityUpdateRequest"
        ]["properties"]
    ) == {
        "status",
    }
    assert set(
        components[
            "SpecialistAvailabilityUpdateData"
        ]["properties"]
    ) == {
        "status",
        "changed",
    }

