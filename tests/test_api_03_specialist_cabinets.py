import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_cabinets import (
    SpecialistCabinetsProfileNotFoundError,
    SpecialistCabinetsService,
)


SOURCE_PATH = Path(
    "services/specialist_cabinets.py"
)


class FakeRepository:
    def __init__(self, specialist):
        self.specialist = specialist
        self.user_ids = []

    async def get_by_user_id(self, user_id):
        self.user_ids.append(user_id)
        return self.specialist


class FakeSpecialists:
    def __init__(
        self,
        result=(),
        create_error=None,
    ):
        self.result = result
        self.create_error = create_error
        self.calls = []
        self.create_calls = []
        self.switch_calls = []

    async def list_professional_cabinet_options(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.result

    async def switch_active_professional_cabinet(
        self,
        **kwargs,
    ):
        self.switch_calls.append(kwargs)
        return self.result

    async def create_professional_cabinet(
        self,
        **kwargs,
    ):
        self.create_calls.append(kwargs)

        if self.create_error:
            raise self.create_error

        return self.result


def build_service(
    *,
    specialist,
    result=(),
    create_error=None,
):
    repository = FakeRepository(specialist)
    specialists = FakeSpecialists(
        result,
        create_error=create_error,
    )

    service = SpecialistCabinetsService(
        object(),
        settings=object(),
        repository=repository,
        specialists=specialists,
    )

    return service, repository, specialists


@pytest.mark.asyncio
async def test_neutral_cabinet_list_uses_actor_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinets = (
        SimpleNamespace(id=uuid4()),
    )

    service, repository, specialists = (
        build_service(
            specialist=SimpleNamespace(
                id=specialist_id,
                tenant_id=tenant_id,
            ),
            result=cabinets,
        )
    )

    action = await (
        service.list_cabinets_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
        )
    )

    assert repository.user_ids == [user_id]
    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert (
        action.actor.specialist_id
        == specialist_id
    )
    assert action.actor.language == "uk"
    assert action.result is cabinets

    assert specialists.calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "specialist_id": specialist_id,
            "language": "uk",
        }
    ]


@pytest.mark.asyncio
async def test_neutral_cabinet_list_rejects_foreign_tenant():
    user_id = uuid4()
    tenant_id = uuid4()

    service, repository, specialists = (
        build_service(
            specialist=SimpleNamespace(
                id=uuid4(),
                tenant_id=uuid4(),
            ),
        )
    )

    with pytest.raises(
        SpecialistCabinetsProfileNotFoundError
    ):
        await service.list_cabinets_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
        )

    assert repository.user_ids == [user_id]
    assert specialists.calls == []


def test_neutral_cabinet_list_has_no_transport_id():
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "SpecialistCabinetsService"
    )

    methods = [
        node
        for node in service.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "list_cabinets_for_user"
    ]

    assert len(methods) == 1

    arguments = {
        argument.arg
        for argument in (
            list(methods[0].args.args)
            + list(
                methods[0].args.kwonlyargs
            )
        )
    }

    assert {
        "user_id",
        "tenant_id",
        "language",
    }.issubset(arguments)
    assert "platform_user_id" not in arguments


class FakeApiCabinetsService:
    def __init__(
        self,
        result=(),
        create_result=None,
        create_error=None,
        switch_result=True,
        switch_error=None,
    ):
        self.result = result
        self.create_result = create_result
        self.create_error = create_error
        self.switch_result = switch_result
        self.switch_error = switch_error
        self.calls = []
        self.create_calls = []
        self.switch_calls = []

    async def list_cabinets_for_user(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return SimpleNamespace(
            result=self.result,
        )

    async def switch_cabinet_for_user(
        self,
        **kwargs,
    ):
        self.switch_calls.append(kwargs)

        if self.switch_error:
            raise self.switch_error

        return SimpleNamespace(
            result=self.switch_result,
        )

    async def create_cabinet_for_user(
        self,
        **kwargs,
    ):
        self.create_calls.append(kwargs)

        if self.create_error:
            raise self.create_error

        return SimpleNamespace(
            result=self.create_result,
        )


@pytest.mark.asyncio
async def test_specialist_cabinets_require_authentication():
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
            "/api/v1/specialist/cabinets"
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_specialist_cabinet_endpoint_uses_current_actor():
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
    request_id = "api-specialist-cabinets"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("client", "specialist"),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.cabinets.read",
        ),
    )
    service = FakeApiCabinetsService(
        result=(
            SimpleNamespace(
                id=cabinet_id,
                profession_name="Сантехнік",
                moderation_status="approved",
                availability_status="available",
                is_selected=True,
            ),
        )
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/specialist/cabinets",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert service.calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
        }
    ]

    assert response.json() == {
        "data": {
            "items": [
                {
                    "id": str(cabinet_id),
                    "profession_name": (
                        "Сантехнік"
                    ),
                    "moderation_status": (
                        "approved"
                    ),
                    "availability_status": (
                        "available"
                    ),
                    "is_selected": True,
                }
            ]
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_specialist_cabinets_require_role():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
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
        timezone=None,
        status="active",
    )
    service = FakeApiCabinetsService()

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/specialist/cabinets"
        )

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "specialist_role_required",
        "message": (
            "Specialist role is required."
        ),
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }
    assert service.calls == []


def test_specialist_cabinets_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        "/api/v1/specialist/cabinets"
    ]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCabinetListResponse"
        )
    }


@pytest.mark.asyncio
async def test_neutral_cabinet_create_uses_actor_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    created = SimpleNamespace(id=uuid4())

    service, repository, specialists = (
        build_service(
            specialist=SimpleNamespace(
                id=specialist_id,
                tenant_id=tenant_id,
            ),
            result=created,
        )
    )

    action = await (
        service.create_cabinet_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            category_id=category_id,
            profession_id=profession_id,
            platform="api",
        )
    )

    assert repository.user_ids == [user_id]
    assert action.actor.user_id == user_id
    assert action.actor.tenant_id == tenant_id
    assert (
        action.actor.specialist_id
        == specialist_id
    )
    assert action.result is created

    assert specialists.create_calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "specialist_id": specialist_id,
            "category_id": category_id,
            "profession_id": profession_id,
            "language": "uk",
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_neutral_cabinet_create_rejects_foreign_tenant():
    service, repository, specialists = (
        build_service(
            specialist=SimpleNamespace(
                id=uuid4(),
                tenant_id=uuid4(),
            ),
        )
    )
    user_id = uuid4()

    with pytest.raises(
        SpecialistCabinetsProfileNotFoundError
    ):
        await service.create_cabinet_for_user(
            user_id=user_id,
            tenant_id=uuid4(),
            language="uk",
            category_id=uuid4(),
            profession_id=uuid4(),
            platform="api",
        )

    assert repository.user_ids == [user_id]
    assert specialists.create_calls == []


def test_cabinet_creation_audit_is_channel_neutral():
    source = Path(
        "services/specialist.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SpecialistService"
    )
    method = next(
        node
        for node in service.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "create_professional_cabinet"
    )

    arguments = {
        argument.arg
        for argument in (
            list(method.args.args)
            + list(method.args.kwonlyargs)
        )
    }
    block = (
        ast.get_source_segment(
            source,
            method,
        )
        or ""
    )

    assert "platform" in arguments
    assert "platform=platform" in block
    assert 'platform="telegram"' not in block


@pytest.mark.asyncio
async def test_create_specialist_cabinet_endpoint():
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
    category_id = uuid4()
    profession_id = uuid4()
    cabinet_id = uuid4()
    request_id = "api-create-cabinet"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.cabinets.write",
        ),
    )
    service = FakeApiCabinetsService(
        create_result=SimpleNamespace(
            id=cabinet_id,
            profession_name="Електрик",
            moderation_status="draft",
            availability_status="available",
            is_selected=True,
        )
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/specialist/cabinets",
            json={
                "category_id": str(category_id),
                "profession_id": (
                    str(profession_id)
                ),
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 201
    assert service.create_calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "category_id": category_id,
            "profession_id": profession_id,
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": {
            "item": {
                "id": str(cabinet_id),
                "profession_name": (
                    "Електрик"
                ),
                "moderation_status": "draft",
                "availability_status": (
                    "available"
                ),
                "is_selected": True,
            }
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_create_cabinet_rejects_actor_fields():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
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
            "specialist.cabinets.write",
        ),
    )
    service = FakeApiCabinetsService()

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/specialist/cabinets",
            json={
                "category_id": str(uuid4()),
                "profession_id": str(uuid4()),
                "user_id": str(uuid4()),
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "validation_error"
    )
    assert service.create_calls == []


def test_create_specialist_cabinet_openapi():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        "/api/v1/specialist/cabinets"
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
            "SpecialistCabinetCreateRequest"
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
            "SpecialistCabinetResponse"
        )
    }


@pytest.mark.asyncio
async def test_neutral_create_maps_existing_cabinet():
    from services.specialist import (
        ProfessionalCabinetAlreadyExistsError,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsConflictError,
    )

    tenant_id = uuid4()
    service, _, specialists = build_service(
        specialist=SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
        ),
        create_error=(
            ProfessionalCabinetAlreadyExistsError(
                "private duplicate details"
            )
        ),
    )

    with pytest.raises(
        SpecialistCabinetsConflictError
    ):
        await service.create_cabinet_for_user(
            user_id=uuid4(),
            tenant_id=tenant_id,
            language="uk",
            category_id=uuid4(),
            profession_id=uuid4(),
            platform="api",
        )

    assert len(specialists.create_calls) == 1


@pytest.mark.asyncio
async def test_neutral_create_maps_domain_validation():
    from services.specialist import (
        SpecialistRegistrationError,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsValidationError,
    )

    tenant_id = uuid4()
    service, _, specialists = build_service(
        specialist=SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
        ),
        create_error=SpecialistRegistrationError(
            "private category details"
        ),
    )

    with pytest.raises(
        SpecialistCabinetsValidationError
    ):
        await service.create_cabinet_for_user(
            user_id=uuid4(),
            tenant_id=tenant_id,
            language="uk",
            category_id=uuid4(),
            profession_id=uuid4(),
            platform="api",
        )

    assert len(specialists.create_calls) == 1


@pytest.mark.parametrize(
    (
        "error_name",
        "status_code",
        "error_code",
        "error_message",
    ),
    [
        (
            "conflict",
            409,
            "cabinet_already_exists",
            (
                "Professional cabinet "
                "already exists."
            ),
        ),
        (
            "validation",
            422,
            "cabinet_validation_error",
            (
                "Professional cabinet data "
                "is not valid."
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_cabinet_maps_service_errors(
    error_name,
    status_code,
    error_code,
    error_message,
):
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsConflictError,
        SpecialistCabinetsValidationError,
    )

    error_type = (
        SpecialistCabinetsConflictError
        if error_name == "conflict"
        else SpecialistCabinetsValidationError
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
            "specialist.cabinets.write",
        ),
    )
    private_details = (
        "private database category details"
    )
    service = FakeApiCabinetsService(
        create_error=error_type(
            private_details
        )
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/specialist/cabinets",
            json={
                "category_id": str(uuid4()),
                "profession_id": str(uuid4()),
            },
        )

    assert response.status_code == status_code
    assert response.json()["error"] == {
        "code": error_code,
        "message": error_message,
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }
    assert private_details not in response.text
    assert len(service.create_calls) == 1


@pytest.mark.asyncio
async def test_neutral_cabinet_switch_uses_actor_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()

    service, repository, specialists = (
        build_service(
            specialist=SimpleNamespace(
                id=specialist_id,
                tenant_id=tenant_id,
            ),
            result=True,
        )
    )

    action = await (
        service.switch_cabinet_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=cabinet_id,
            platform="api",
        )
    )

    assert repository.user_ids == [user_id]
    assert action.result is True
    assert specialists.switch_calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "specialist_id": specialist_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_neutral_cabinet_switch_rejects_foreign_tenant():
    user_id = uuid4()
    service, repository, specialists = (
        build_service(
            specialist=SimpleNamespace(
                id=uuid4(),
                tenant_id=uuid4(),
            ),
        )
    )

    with pytest.raises(
        SpecialistCabinetsProfileNotFoundError
    ):
        await service.switch_cabinet_for_user(
            user_id=user_id,
            tenant_id=uuid4(),
            language="uk",
            professional_cabinet_id=uuid4(),
            platform="api",
        )

    assert repository.user_ids == [user_id]
    assert specialists.switch_calls == []


def test_cabinet_switch_audit_is_channel_neutral():
    source = Path(
        "services/specialist.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SpecialistService"
    )
    method = next(
        node
        for node in service.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "switch_active_professional_cabinet"
    )

    arguments = {
        argument.arg
        for argument in (
            list(method.args.args)
            + list(method.args.kwonlyargs)
        )
    }
    block = (
        ast.get_source_segment(
            source,
            method,
        )
        or ""
    )

    assert "platform" in arguments
    assert "platform=platform" in block
    assert 'platform="telegram"' not in block


@pytest.mark.asyncio
async def test_patch_specialist_cabinet_selects_it():
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
    request_id = "api-select-cabinet"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.cabinets.write",
        ),
    )
    service = FakeApiCabinetsService(
        switch_result=True,
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/specialist/"
                f"cabinets/{cabinet_id}"
            ),
            json={
                "is_selected": True,
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert service.switch_calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "platform": "api",
        }
    ]
    assert response.json() == {
        "data": {
            "id": str(cabinet_id),
            "is_selected": True,
            "changed": True,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_patch_cabinet_rejects_false_selection():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
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
            "specialist.cabinets.write",
        ),
    )
    service = FakeApiCabinetsService()

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/specialist/"
                f"cabinets/{uuid4()}"
            ),
            json={
                "is_selected": False,
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert service.switch_calls == []


@pytest.mark.asyncio
async def test_patch_cabinet_hides_missing_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsValidationError,
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
            "specialist.cabinets.write",
        ),
    )
    private_details = "private cabinet lookup"
    service = FakeApiCabinetsService(
        switch_error=(
            SpecialistCabinetsValidationError(
                private_details
            )
        )
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/specialist/"
                f"cabinets/{uuid4()}"
            ),
            json={
                "is_selected": True,
            },
        )

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "cabinet_not_found",
        "message": (
            "Professional cabinet "
            "was not found."
        ),
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }
    assert private_details not in response.text


def test_patch_specialist_cabinet_openapi():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/"
            "cabinets/{cabinet_id}"
        )
    ]["patch"]

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
            "SpecialistCabinetUpdateRequest"
        )
    }

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCabinetSelectionResponse"
        )
    }

@pytest.mark.asyncio
async def test_create_specialist_cabinet_requires_write_permission():
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
        permissions=(),
    )

    class ForbiddenService:
        async def create_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Service must not be called "
                "without write permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/specialist/cabinets",
            json={
                "category_id": str(uuid4()),
                "profession_id": str(uuid4()),
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []

@pytest.mark.asyncio
async def test_patch_specialist_cabinet_requires_write_permission():
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
    cabinet_id = uuid4()

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
        async def switch_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Service must not be called "
                "without write permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}"
            ),
            json={
                "is_selected": True,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []
