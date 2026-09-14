from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_skill_list_uses_owned_cabinet_scope():
    from services.specialist_skills import (
        SpecialistSkillsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        profession_id=uuid4(),
    )
    skills = SimpleNamespace(
        skills=(),
        selected_ids=(),
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return SimpleNamespace(
                actor=actor,
                result=cabinet,
            )

    class FakeSpecialists:
        async def get_skills_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("skills", kwargs))
            return skills

    service = SpecialistSkillsService(
        object(),
        cabinets=FakeCabinets(),
        specialists=FakeSpecialists(),
    )

    result = await service.list_skills_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        limit=30,
    )

    assert result.actor is actor
    assert result.result is skills
    assert calls == [
        (
            "cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "skills",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "language": "uk",
                "limit": 30,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_domain_skill_list_checks_cabinet_owner_scope():
    from services.specialist import (
        SpecialistService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    profession_id = uuid4()
    selected_id = uuid4()
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
        profession_id=profession_id,
    )
    profession = SimpleNamespace(
        id=profession_id,
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
            return cabinet, profession

        async def list_skills_for_profession(
            self,
            **kwargs,
        ):
            calls.append(("available", kwargs))
            return []

        async def list_cabinet_skill_ids(
            self,
            **kwargs,
        ):
            calls.append(("selected", kwargs))
            return [selected_id]

    service = SpecialistService(
        FakeRepository()
    )

    result = await service.get_skills_for_cabinet(
        tenant_id=tenant_id,
        user_id=user_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
        language="uk",
        limit=30,
    )

    assert result.skills == ()
    assert result.selected_ids == (
        selected_id,
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
            "available",
            {
                "profession_id": profession_id,
                "limit": 30,
            },
        ),
        (
            "selected",
            {
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
    ]


@pytest.mark.asyncio
async def test_specialist_cabinet_skills_use_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_skills_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist import (
        SpecialistSkillOption,
        SpecialistSkillsEditData,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    skill_id = uuid4()
    request_id = "specialist-skills-list"
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
            "specialist.skills.read",
        ),
    )

    class FakeSpecialistSkills:
        async def list_skills_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=SpecialistSkillsEditData(
                    skills=(
                        SpecialistSkillOption(
                            id=skill_id,
                            name="Python",
                        ),
                    ),
                    selected_ids=(skill_id,),
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_skills_service
    ] = lambda: FakeSpecialistSkills()

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
                f"{cabinet_id}/skills"
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
            "limit": 30,
        }
    ]
    assert response.json() == {
        "data": {
            "items": [
                {
                    "id": str(skill_id),
                    "name": "Python",
                    "is_selected": True,
                }
            ],
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_specialist_cabinet_skills_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_skills_service,
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

    class ForbiddenSkillsService:
        async def list_skills_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Skills service must not be called "
                "without read permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_skills_service
    ] = lambda: ForbiddenSkillsService()

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
                f"{uuid4()}/skills"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_cabinet_skills_require_authentication():
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
                f"{uuid4()}/skills"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_specialist_cabinet_skills_hide_missing_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_skills_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
    )

    request_id = "specialist-skills-missing"
    private_detail = "foreign tenant cabinet"
    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.skills.read",
        ),
    )

    class MissingSkillsService:
        async def list_skills_for_user(
            self,
            **kwargs,
        ):
            raise SpecialistCabinetsSelectionError(
                private_detail
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_skills_service
    ] = lambda: MissingSkillsService()

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
                f"{uuid4()}/skills"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "cabinet_not_found",
            "message": (
                "Professional cabinet "
                "was not found."
            ),
            "request_id": request_id,
        },
    }
    assert private_detail not in response.text


def test_specialist_skills_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/skills"
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
    } <= set(parameters)

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistSkillListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistSkillItem"
        ]["properties"]
    ) == {
        "id",
        "name",
        "is_selected",
    }


@pytest.mark.asyncio
async def test_neutral_skill_update_uses_owned_cabinet_scope():
    from services.specialist_skills import (
        SpecialistSkillsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    skill_ids = [uuid4(), uuid4()]
    calls = []
    update_result = (
        (),
        tuple(skill_ids),
        True,
    )

    actor = SimpleNamespace(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return SimpleNamespace(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeSpecialists:
        async def update_skills_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            return update_result

    service = SpecialistSkillsService(
        object(),
        cabinets=FakeCabinets(),
        specialists=FakeSpecialists(),
    )

    action = await service.update_skills_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        skill_ids=skill_ids,
        platform="api",
    )

    assert action.actor is actor
    assert action.result is update_result
    assert calls == [
        (
            "cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
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
                "skill_ids": skill_ids,
                "platform": "api",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_domain_skill_update_checks_scope_and_allowlist(
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
    profession_id = uuid4()
    allowed_id = uuid4()
    rejected_id = uuid4()
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
        profession_id=profession_id,
    )
    profession = SimpleNamespace(
        id=profession_id,
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
            return cabinet, profession

        async def list_skills_for_profession(
            self,
            **kwargs,
        ):
            calls.append(("allowed", kwargs))
            return [
                SimpleNamespace(id=allowed_id),
            ]

        async def list_cabinet_skill_ids(
            self,
            **kwargs,
        ):
            calls.append(("before", kwargs))
            return []

        async def replace_cabinet_skills(
            self,
            **kwargs,
        ):
            calls.append(("replace", kwargs))

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

    result = await service.update_skills_for_cabinet(
        tenant_id=tenant_id,
        user_id=user_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
        skill_ids=[
            allowed_id,
            rejected_id,
            allowed_id,
        ],
        platform="api",
    )

    assert result == (
        [],
        [allowed_id],
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
            "allowed",
            {
                "profession_id": profession_id,
                "limit": 100,
            },
        ),
        (
            "before",
            {
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "replace",
            {
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "skill_ids": [allowed_id],
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
                    "field": "skills",
                    "before": [],
                    "after": [str(allowed_id)],
                },
                "platform": "api",
            },
        ),
        ("commit",),
    ]


@pytest.mark.asyncio
async def test_specialist_cabinet_skills_update_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_skills_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    first_skill_id = uuid4()
    second_skill_id = uuid4()
    request_id = "specialist-skills-update"
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
            "specialist.skills.write",
        ),
    )

    class FakeSpecialistSkills:
        async def update_skills_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=(
                    (),
                    (
                        first_skill_id,
                        second_skill_id,
                    ),
                    True,
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_skills_service
    ] = lambda: FakeSpecialistSkills()

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
                f"{cabinet_id}/skills"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "skill_ids": [
                    str(first_skill_id),
                    str(second_skill_id),
                ],
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
            "skill_ids": [
                first_skill_id,
                second_skill_id,
            ],
            "platform": "api",
        }
    ]
    assert response.json() == {
        "data": {
            "selected_ids": [
                str(first_skill_id),
                str(second_skill_id),
            ],
            "changed": True,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_specialist_cabinet_skills_update_requires_write_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_skills_service,
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
            "specialist.skills.read",
        ),
    )

    class ForbiddenSkillsService:
        async def update_skills_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Skills mutation must not run "
                "without write permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_skills_service
    ] = lambda: ForbiddenSkillsService()

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
                f"{uuid4()}/skills"
            ),
            json={
                "skill_ids": [],
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_skills_update_rejects_actor_fields():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_skills_service,
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
            "specialist.skills.write",
        ),
    )

    class ForbiddenSkillsService:
        async def update_skills_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Invalid payload must not "
                "reach the service."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_skills_service
    ] = lambda: ForbiddenSkillsService()

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
                f"{uuid4()}/skills"
            ),
            json={
                "skill_ids": [],
                "user_id": str(uuid4()),
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "validation_error"
    )
    assert calls == []


def test_specialist_skills_update_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/skills"
        )
    ]["put"]

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
            "SpecialistSkillsUpdateRequest"
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
            "SpecialistSkillsUpdateResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistSkillsUpdateRequest"
        ]["properties"]
    ) == {
        "skill_ids",
    }
    assert set(
        components[
            "SpecialistSkillsUpdateData"
        ]["properties"]
    ) == {
        "selected_ids",
        "changed",
    }

