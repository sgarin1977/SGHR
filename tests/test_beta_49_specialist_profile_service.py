from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist import (
    MAX_PROFESSIONS_PER_CATEGORY,
    MAX_SPECIALIST_CATEGORIES,
)

from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsProfileNotFoundError,
    SpecialistCabinetsUserNotFoundError,
)
from services.specialist_profile import (
    SpecialistProfileNotFoundError,
    SpecialistProfileService,
    SpecialistProfileUserNotFoundError,
    SpecialistProfileSelectionError,
    SpecialistProfileProfessionLimitError,
    SpecialistProfileProfessionNotFoundError,
)


class FakeCabinets:
    def __init__(
        self,
        *,
        actor=None,
        error=None,
    ):
        self.actor = actor
        self.error = error
        self.calls = []

    async def require_actor(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.actor


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.active_cabinet = None

    async def get_active_professional_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("moderation", kwargs)
        )
        return self.active_cabinet

    async def list_active_professions_by_category(
        self,
        category_id,
        *,
        limit,
    ):
        self.calls.append(
            (
                "professions",
                {
                    "category_id": category_id,
                    "limit": limit,
                },
            )
        )
        return getattr(
            self,
            "professions",
            ("profession",),
        )

    async def get_active_category(
        self,
        category_id,
    ):
        self.calls.append(
            (
                "category",
                {
                    "category_id": (
                        category_id
                    ),
                },
            )
        )
        return getattr(
            self,
            "category",
            None,
        )


class FakeSpecialists:
    def __init__(self):
        self.calls = []
        self.profile = None
        self.visibility = "platform_only"
        self.moderation_status = "draft"
        self.submission_changed = True
        self.languages = ["uk"]
        self.skills = SimpleNamespace(
            skills=(),
            selected_ids=(),
        )

    async def get_active_cabinet_profile(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("profile", kwargs)
        )
        return self.profile

    async def get_profile_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("visibility", kwargs)
        )
        return self.visibility

    async def get_active_cabinet_moderation_status(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("moderation_status", kwargs)
        )
        return self.moderation_status

    async def submit_active_professional_cabinet_for_moderation(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("submit", kwargs)
        )
        return self.submission_changed

    async def update_profile_visibility(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("set_visibility", kwargs)
        )
        self.visibility = kwargs[
            "visibility"
        ]

    async def get_languages_for_editing(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("languages", kwargs)
        )
        return self.languages

    async def get_skills_for_editing(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("skills", kwargs)
        )
        return self.skills

    async def update_languages(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("save_languages", kwargs)
        )
        return (
            ["ru"],
            kwargs["language_codes"],
            True,
        )

    async def update_skills(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("save_skills", kwargs)
        )
        return (
            [],
            kwargs["skill_ids"],
            True,
        )

    def toggle_language_selection(
        self,
        *,
        selected_codes,
        language_code,
    ):
        self.calls.append(
            (
                "toggle_language",
                {
                    "selected_codes": (
                        selected_codes
                    ),
                    "language_code": (
                        language_code
                    ),
                },
            )
        )
        selected = list(selected_codes)

        if language_code in selected:
            selected.remove(language_code)
        else:
            selected.append(language_code)

        return selected

    async def update_work_format(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("save_work_format", kwargs)
        )
        return (
            "specialist",
            "remote",
            kwargs["work_format"],
            True,
        )

    async def record_blocked_profile_change(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("blocked_change", kwargs)
        )

    async def update_location_from_candidate(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("save_location", kwargs)
        )
        return "saved-place"

    async def update_country_from_candidate(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("save_country", kwargs)
        )
        return None

    async def list_active_categories_for_profile_editor(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("categories", kwargs)
        )
        return ("category",)

    async def get_profile_profession_selections(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("selections", kwargs)
        )
        return [
            {
                "category_id": "category",
                "profession_id": (
                    "profession"
                ),
            }
        ]

    async def replace_profile_professions(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("save_professions", kwargs)
        )
        return "saved-professions"

    async def update_profile_with_audit(
        self,
        data,
    ):
        self.calls.append(
            ("save_basic_profile", data)
        )
        return SimpleNamespace(
            specialist_id=(
                data.specialist_id
            ),
            changed=True,
        )


def actor():
    return SpecialistCabinetsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        specialist_id=uuid4(),
        language="uk",
    )


def build_service(
    *,
    current_actor=None,
    error=None,
):
    cabinets = FakeCabinets(
        actor=current_actor,
        error=error,
    )
    service = SpecialistProfileService(
        object(),
        cabinets=cabinets,
        repository=FakeRepository(),
        specialists=FakeSpecialists(),
    )
    return service, cabinets


@pytest.mark.asyncio
async def test_require_actor_reuses_cabinet_scope():
    current_actor = actor()
    service, cabinets = build_service(
        current_actor=current_actor
    )

    result = await service.require_actor(
        platform_user_id=123
    )

    assert result is current_actor
    assert cabinets.calls == [
        {
            "platform_user_id": 123,
        }
    ]


@pytest.mark.asyncio
async def test_missing_profile_fails_closed():
    service, cabinets = build_service(
        error=(
            SpecialistCabinetsProfileNotFoundError(
                "Missing specialist."
            )
        )
    )

    with pytest.raises(
        SpecialistProfileNotFoundError,
        match="Specialist profile not found",
    ):
        await service.require_actor(
            platform_user_id=456
        )

    assert cabinets.calls == [
        {
            "platform_user_id": 456,
        }
    ]


def test_profile_service_owns_dependencies():
    service, _ = build_service(
        current_actor=actor()
    )

    assert isinstance(
        service.repository,
        FakeRepository,
    )
    assert isinstance(
        service.specialists,
        FakeSpecialists,
    )
    assert isinstance(
        service.cabinets,
        FakeCabinets,
    )


def build_read_service():
    current_actor = actor()
    cabinets = FakeCabinets(
        actor=current_actor
    )
    repository = FakeRepository()
    specialists = FakeSpecialists()
    service = SpecialistProfileService(
        object(),
        cabinets=cabinets,
        repository=repository,
        specialists=specialists,
    )
    return (
        service,
        current_actor,
        repository,
        specialists,
    )


@pytest.mark.asyncio
async def test_get_moderation_uses_actor_scope():
    (
        service,
        current_actor,
        repository,
        _,
    ) = build_read_service()
    cabinet = SimpleNamespace(
        id=uuid4(),
        moderation_status="draft",
    )
    repository.active_cabinet = cabinet

    result = await service.get_moderation(
        platform_user_id=123
    )

    assert result.actor is current_actor
    assert result.result is cabinet
    assert repository.calls == [
        (
            "moderation",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_active_profile_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    profile = SimpleNamespace(id=uuid4())
    specialists.profile = profile

    result = await service.get_active_profile(
        platform_user_id=456
    )

    assert result.actor is current_actor
    assert result.result is profile
    assert specialists.calls == [
        (
            "profile",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
                "language": (
                    current_actor.language
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_visibility_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    specialists.visibility = "private"
    specialists.moderation_status = (
        "published"
    )

    result = await service.get_visibility(
        platform_user_id=789
    )

    assert result.actor is current_actor
    assert result.result.visibility == "private"
    assert (
        result.result.moderation_status
        == "published"
    )
    assert specialists.calls == [
        (
            "visibility",
            {
                "user_id": (
                    current_actor.user_id
                ),
            },
        ),
        (
            "moderation_status",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
            },
        ),
    ]


@pytest.mark.asyncio
async def test_missing_user_fails_closed():
    service, cabinets = build_service(
        error=(
            SpecialistCabinetsUserNotFoundError(
                "Missing user."
            )
        )
    )

    with pytest.raises(
        SpecialistProfileUserNotFoundError,
        match="User context not found",
    ):
        await service.require_actor(
            platform_user_id=987
        )

    assert cabinets.calls == [
        {
            "platform_user_id": 987,
        }
    ]


def test_profile_read_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_specialist_moderation": (
            "get_moderation"
        ),
        "show_specialist_profile_visibility": (
            "get_visibility"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert service_method in called_methods

        forbidden = {
            "SpecialistRepository",
            "SpecialistService",
            (
                "get_current_specialist_"
                "for_telegram"
            ),
        }
        assert not (
            called_names & forbidden
        )

        block = ast.get_source_segment(
            source,
            node,
        ) or ""
        assert (
            "profile_action.actor.language"
            in block
        )
        assert (
            "profile_action.result"
            in block
        )


def test_active_profile_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "show_specialist_profile_menu",
        (
            "show_specialist_card_"
            "full_description"
        ),
        "view_specialist_profile",
    )

    for function_name in function_names:
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            "get_active_profile"
            in called_methods
        )

        forbidden = {
            "SpecialistRepository",
            "SpecialistService",
            (
                "get_current_specialist_"
                "for_telegram"
            ),
        }
        assert not (
            called_names & forbidden
        )

        block = ast.get_source_segment(
            source,
            node,
        ) or ""

        assert (
            "profile_action.actor.language"
            in block
        )
        assert (
            "profile = profile_action.result"
            in block
        )


@pytest.mark.asyncio
async def test_submit_moderation_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    specialists.submission_changed = False

    result = await service.submit_moderation(
        platform_user_id=123
    )

    assert result.actor is current_actor
    assert result.result is False
    assert specialists.calls == [
        (
            "submit",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_set_visibility_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    specialists.moderation_status = (
        "published"
    )

    result = await service.set_visibility(
        platform_user_id=456,
        visibility="private",
    )

    assert result.actor is current_actor
    assert result.result.visibility == "private"
    assert (
        result.result.moderation_status
        == "published"
    )
    assert specialists.calls == [
        (
            "set_visibility",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
                "visibility": "private",
            },
        ),
        (
            "moderation_status",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
            },
        ),
    ]


def test_profile_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        (
            "submit_specialist_cabinet_"
            "for_moderation"
        ): "submit_moderation",
        "set_specialist_profile_visibility": (
            "set_visibility"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert service_method in called_methods

        forbidden = {
            "SpecialistRepository",
            "SpecialistService",
            (
                "get_current_specialist_"
                "for_telegram"
            ),
        }
        assert not (
            called_names & forbidden
        )

        block = ast.get_source_segment(
            source,
            node,
        ) or ""
        assert (
            "profile_action.actor.language"
            in block
        )
        assert (
            "profile_action.result"
            in block
        )


def test_profile_entry_handlers_use_application_actor():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "confirm_specialist_profile_delete",
        "edit_specialist_profile_menu",
    )

    for function_name in function_names:
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            "require_actor"
            in called_methods
        )

        forbidden = {
            "SpecialistRepository",
            "SpecialistService",
            (
                "get_current_specialist_"
                "for_telegram"
            ),
        }
        assert not (
            called_names & forbidden
        )

        block = (
            ast.get_source_segment(
                source,
                node,
            )
            or ""
        )
        assert (
            "profile_actor.language"
            in block
        )

        # Actor identity is authorization
        # context, not Telegram FSM state.
        assert (
            "profile_actor.specialist_id"
            not in block
        )
        assert (
            "profile_actor.user_id"
            not in block
        )
        assert (
            "profile_actor.tenant_id"
            not in block
        )
        assert (
            "cabinet_specialist_id"
            not in block
        )
        assert (
            "cabinet_user_id"
            not in block
        )
        assert (
            "cabinet_tenant_id"
            not in block
        )


@pytest.mark.asyncio
async def test_get_languages_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    specialists.languages = ["uk", "en"]

    result = await service.get_languages(
        platform_user_id=123
    )

    assert result.actor is current_actor
    assert result.result == ["uk", "en"]
    assert specialists.calls == [
        (
            "languages",
            {
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_skills_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    skills = SimpleNamespace(
        skills=("skill",),
        selected_ids=(uuid4(),),
    )
    specialists.skills = skills

    result = await service.get_skills(
        platform_user_id=456,
        limit=30,
    )

    assert result.actor is current_actor
    assert result.result is skills
    assert specialists.calls == [
        (
            "skills",
            {
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor.specialist_id
                ),
                "language": (
                    current_actor.language
                ),
                "limit": 30,
            },
        )
    ]




def test_profile_language_and_skill_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "ask_edit_specialist_languages": (
            "get_languages"
        ),
        "show_specialist_skills": (
            "get_skills"
        ),
        "toggle_specialist_skill": (
            "get_skills"
        ),
    }

    for function_name, method_name in expected.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert method_name in called_methods
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert "UUID" not in called_names
        assert "profile_action.actor" in block


@pytest.mark.asyncio
async def test_save_languages_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await service.save_languages(
        platform_user_id=123,
        language_codes=["uk", "en"],
    )

    assert result.actor is current_actor
    assert result.result == (
        ["ru"],
        ["uk", "en"],
        True,
    )
    assert specialists.calls == [
        (
            "save_languages",
            {
                "tenant_id": current_actor.tenant_id,
                "user_id": current_actor.user_id,
                "specialist_id": (
                    current_actor.specialist_id
                ),
                "language_codes": [
                    "uk",
                    "en",
                ],
            },
        )
    ]


@pytest.mark.asyncio
async def test_save_skills_parses_ids_and_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    first_skill_id = uuid4()
    second_skill_id = uuid4()

    result = await service.save_skills(
        platform_user_id=456,
        skill_ids=[
            str(first_skill_id),
            second_skill_id,
        ],
    )

    assert result.actor is current_actor
    assert result.result == (
        [],
        [
            first_skill_id,
            second_skill_id,
        ],
        True,
    )
    assert specialists.calls == [
        (
            "save_skills",
            {
                "tenant_id": current_actor.tenant_id,
                "user_id": current_actor.user_id,
                "specialist_id": (
                    current_actor.specialist_id
                ),
                "skill_ids": [
                    first_skill_id,
                    second_skill_id,
                ],
            },
        )
    ]


@pytest.mark.asyncio
async def test_save_skills_rejects_invalid_ids():
    (
        service,
        _,
        _,
        specialists,
    ) = build_read_service()

    with pytest.raises(
        SpecialistProfileSelectionError,
        match="Invalid skill selection",
    ):
        await service.save_skills(
            platform_user_id=789,
            skill_ids=["not-a-uuid"],
        )

    assert specialists.calls == []


def test_profile_language_and_skill_save_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "save_specialist_languages": (
            "save_languages"
        ),
        "save_specialist_skills": (
            "save_skills"
        ),
    }

    for function_name, method_name in expected.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name
            == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            method_name
            in called_methods
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert "UUID" not in called_names
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert (
            "cabinet_user_id"
            not in block
        )
        assert (
            "cabinet_tenant_id"
            not in block
        )
        assert (
            "cabinet_specialist_id"
            not in block
        )
        assert (
            "platform_user_id"
            in block
        )
        assert (
            "profile_action.actor"
            in block
        )


@pytest.mark.asyncio
async def test_toggle_language_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await service.toggle_language(
        platform_user_id=321,
        selected_codes=["uk"],
        language_code="en",
    )

    assert result.actor is current_actor
    assert result.result == [
        "uk",
        "en",
    ]
    assert specialists.calls == [
        (
            "toggle_language",
            {
                "selected_codes": [
                    "uk",
                ],
                "language_code": "en",
            },
        )
    ]


def test_language_toggle_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "toggle_specialist_language"
    )
    block = "\n".join(
        lines[
            node.lineno - 1:
            node.end_lineno
        ]
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert (
        "SpecialistProfileService"
        in called_names
    )
    assert (
        "toggle_language"
        in called_methods
    )
    assert (
        "SpecialistService"
        not in called_names
    )
    assert (
        "SpecialistRepository"
        not in called_names
    )
    assert (
        "get_current_specialist_for_telegram"
        not in called_names
    )
    assert "UUID" not in called_names
    assert (
        "platform_user_id"
        in block
    )
    assert (
        "profile_action.actor"
        in block
    )


@pytest.mark.asyncio
async def test_open_work_format_requires_actor():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await service.open_work_format(
        platform_user_id=123
    )

    assert result.actor is current_actor
    assert result.result is None
    assert specialists.calls == []


@pytest.mark.asyncio
async def test_save_work_format_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await service.save_work_format(
        platform_user_id=456,
        work_format="mixed",
    )

    assert result.actor is current_actor
    assert result.result == (
        "specialist",
        "remote",
        "mixed",
        True,
    )
    assert specialists.calls == [
        (
            "save_work_format",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor
                    .specialist_id
                ),
                "work_format": "mixed",
            },
        )
    ]


def test_work_format_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "ask_edit_specialist_work_format": (
            "open_work_format"
        ),
        "set_edit_specialist_work_format": (
            "save_work_format"
        ),
    }

    for function_name, method_name in expected.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name
            == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            method_name
            in called_methods
        )
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert "UUID" not in called_names
        assert (
            "platform_user_id"
            in block
        )
        assert (
            "profile_action.actor"
            in block
        )


@pytest.mark.asyncio
async def test_record_blocked_change_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await (
        service.record_blocked_change(
            platform_user_id=123,
            field="location",
            source="stale_fsm_state",
        )
    )

    assert result.actor is current_actor
    assert result.result is None
    assert specialists.calls == [
        (
            "blocked_change",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor
                    .specialist_id
                ),
                "field": "location",
                "source": (
                    "stale_fsm_state"
                ),
            },
        )
    ]


def test_blocked_profile_change_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    function_names = (
        "block_critical_profile_edit",
        (
            "block_critical_profile_"
            "edit_message"
        ),
    )

    for function_name in function_names:
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name
            == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            "record_blocked_change"
            in called_methods
        )
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert "UUID" not in called_names
        assert (
            "platform_user_id"
            in block
        )
        assert (
            "record_blocked_profile_change"
            not in block
        )

    message_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == (
            "block_critical_profile_"
            "edit_message"
        )
    )
    message_block = "\n".join(
        lines[
            message_node.lineno - 1:
            message_node.end_lineno
        ]
    )
    assert (
        '"stale_fsm_state"'
        in message_block
    )


@pytest.mark.asyncio
async def test_save_location_candidate_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    candidate = {
        "name": "Kyiv",
        "country_code": "UA",
    }

    result = await (
        service.save_location_candidate(
            platform_user_id=123,
            candidate=candidate,
        )
    )

    assert result.actor is current_actor
    assert result.result == "saved-place"
    assert specialists.calls == [
        (
            "save_location",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor
                    .specialist_id
                ),
                "candidate": candidate,
                "language": (
                    current_actor.language
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_save_country_candidate_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    candidate = {
        "name": "Ukraine",
        "country_code": "UA",
    }

    result = await (
        service.save_country_candidate(
            platform_user_id=456,
            candidate=candidate,
        )
    )

    assert result.actor is current_actor
    assert result.result is None
    assert specialists.calls == [
        (
            "save_country",
            {
                "tenant_id": (
                    current_actor.tenant_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "specialist_id": (
                    current_actor
                    .specialist_id
                ),
                "candidate": candidate,
                "language": (
                    current_actor.language
                ),
            },
        )
    ]


def test_location_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "choose_specialist_location_update": (
            "save_location_candidate"
        ),
        "choose_specialist_country_update": (
            "save_country_candidate"
        ),
    }

    for function_name, method_name in expected.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name
            == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            method_name
            in called_methods
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert "UUID" not in called_names
        assert (
            "cabinet_user_id"
            not in block
        )
        assert (
            "cabinet_tenant_id"
            not in block
        )
        assert (
            "cabinet_specialist_id"
            not in block
        )
        assert (
            "platform_user_id"
            in block
        )
        assert (
            "profile_action.actor"
            in block
        )
        assert "index < 0" in block


@pytest.mark.asyncio
async def test_open_profession_editor_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await (
        service.open_profession_editor(
            platform_user_id=123,
            limit=50,
        )
    )

    assert result.actor is current_actor
    assert result.result.categories == (
        "category",
    )
    assert result.result.selections == [
        {
            "category_id": "category",
            "profession_id": (
                "profession"
            ),
        }
    ]
    assert specialists.calls == [
        (
            "categories",
            {"limit": 50},
        ),
        (
            "selections",
            {
                "specialist_id": (
                    current_actor
                    .specialist_id
                ),
                "language": (
                    current_actor.language
                ),
            },
        ),
    ]


@pytest.mark.asyncio
async def test_list_profession_categories_requires_actor():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await (
        service.list_profession_categories(
            platform_user_id=456,
            limit=25,
        )
    )

    assert result.actor is current_actor
    assert result.result == (
        "category",
    )
    assert specialists.calls == [
        (
            "categories",
            {"limit": 25},
        )
    ]


@pytest.mark.asyncio
async def test_list_professions_for_category_parses_id():
    (
        service,
        current_actor,
        repository,
        _,
    ) = build_read_service()
    category_id = uuid4()

    result = await (
        service.list_professions_for_category(
            platform_user_id=789,
            category_id=str(
                category_id
            ),
            limit=30,
        )
    )

    assert result.actor is current_actor
    assert result.result == (
        "profession",
    )
    assert repository.calls == [
        (
            "professions",
            {
                "category_id": (
                    category_id
                ),
                "limit": 30,
            },
        )
    ]


@pytest.mark.asyncio
async def test_invalid_profession_category_fails_closed():
    (
        service,
        _,
        repository,
        _,
    ) = build_read_service()

    with pytest.raises(
        SpecialistProfileSelectionError,
        match=(
            "Invalid category selection"
        ),
    ):
        await (
            service
            .list_professions_for_category(
                platform_user_id=111,
                category_id="invalid",
            )
        )

    assert repository.calls == []


@pytest.mark.asyncio
async def test_save_professions_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()
    selections = [
        {
            "category_id": "category",
            "profession_id": (
                "profession"
            ),
        }
    ]

    result = await service.save_professions(
        platform_user_id=222,
        profession_selections=(
            selections
        ),
    )

    assert result.actor is current_actor
    assert (
        result.result
        == "saved-professions"
    )
    assert specialists.calls == [
        (
            "save_professions",
            {
                "specialist_id": (
                    current_actor
                    .specialist_id
                ),
                "user_id": (
                    current_actor.user_id
                ),
                "profession_selections": (
                    selections
                ),
            },
        )
    ]


def test_profession_save_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == (
            "save_specialist_"
            "professions_update"
        )
    )
    block = "\n".join(
        lines[
            node.lineno - 1:
            node.end_lineno
        ]
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert (
        "SpecialistProfileService"
        in called_names
    )
    assert (
        "save_professions"
        in called_methods
    )
    assert (
        "SpecialistRepository"
        not in called_names
    )
    assert (
        "SpecialistService"
        not in called_names
    )
    assert (
        "get_current_specialist_for_telegram"
        not in called_names
    )
    assert "UUID" not in called_names
    assert (
        "cabinet_user_id"
        not in block
    )
    assert (
        "cabinet_tenant_id"
        not in block
    )
    assert (
        "cabinet_specialist_id"
        not in block
    )
    assert (
        "platform_user_id"
        in block
    )
    assert (
        "profile_action.actor"
        in block
    )


def test_category_screen_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "ask_edit_specialist_category": (
            "open_profession_editor"
        ),
        "change_specialist_category_page": (
            "list_profession_categories"
        ),
        "return_to_specialist_categories": (
            "list_profession_categories"
        ),
    }

    for function_name, method_name in expected.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name
            == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            method_name
            in called_methods
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert "UUID" not in called_names
        assert (
            "cabinet_user_id"
            not in block
        )
        assert (
            "cabinet_tenant_id"
            not in block
        )
        assert (
            "cabinet_specialist_id"
            not in block
        )
        assert (
            "platform_user_id"
            in block
        )
        assert (
            "profile_action.actor"
            in block
        )


@pytest.mark.asyncio
async def test_open_profession_category_uses_active_data():
    (
        service,
        current_actor,
        repository,
        _,
    ) = build_read_service()
    category_id = uuid4()
    category = SimpleNamespace(
        id=category_id
    )
    profession = SimpleNamespace(
        id=uuid4(),
        category_id=category_id,
    )
    repository.category = category
    repository.professions = [
        profession
    ]

    result = await (
        service.open_profession_category(
            platform_user_id=123,
            category_id=str(
                category_id
            ),
            limit=50,
        )
    )

    assert result.actor is current_actor
    assert (
        result.result.category
        is category
    )
    assert result.result.professions == [
        profession
    ]
    assert repository.calls == [
        (
            "category",
            {
                "category_id": (
                    category_id
                ),
            },
        ),
        (
            "professions",
            {
                "category_id": (
                    category_id
                ),
                "limit": 50,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_toggle_profession_uses_server_index():
    (
        service,
        current_actor,
        repository,
        _,
    ) = build_read_service()
    category_id = uuid4()
    category = SimpleNamespace(
        id=category_id
    )
    profession = SimpleNamespace(
        id=uuid4(),
        category_id=category_id,
    )
    repository.category = category
    repository.professions = [
        profession
    ]

    result = await service.toggle_profession(
        platform_user_id=456,
        category_id=category_id,
        profession_index=0,
        selected_professions=[],
    )

    assert result.actor is current_actor
    assert (
        result.result.profession
        is profession
    )
    assert (
        result.result.operation
        == "add"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "selections"),
    [
        (
            "categories",
            [
                {
                    "category_id": str(
                        uuid4()
                    ),
                    "profession_id": str(
                        uuid4()
                    ),
                }
                for _ in range(
                    MAX_SPECIALIST_CATEGORIES
                )
            ],
        ),
        (
            "per_category",
            None,
        ),
    ],
)
async def test_toggle_profession_enforces_limits(
    reason,
    selections,
):
    (
        service,
        _,
        repository,
        _,
    ) = build_read_service()
    category_id = uuid4()
    category = SimpleNamespace(
        id=category_id
    )
    profession = SimpleNamespace(
        id=uuid4(),
        category_id=category_id,
    )
    repository.category = category
    repository.professions = [
        profession
    ]

    if selections is None:
        selections = [
            {
                "category_id": str(
                    category_id
                ),
                "profession_id": str(
                    uuid4()
                ),
            }
            for _ in range(
                MAX_PROFESSIONS_PER_CATEGORY
            )
        ]

    with pytest.raises(
        SpecialistProfileProfessionLimitError
    ) as error:
        await service.toggle_profession(
            platform_user_id=789,
            category_id=category_id,
            profession_index=0,
            selected_professions=(
                selections
            ),
        )

    assert error.value.reason == reason


def test_profession_screen_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "choose_specialist_category_update": (
            "open_profession_category"
        ),
        "change_specialist_profession_page": (
            "list_professions_for_category"
        ),
        "choose_specialist_profession_update": (
            "toggle_profession"
        ),
    }

    for function_name, method_name in expected.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name
            == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "SpecialistProfileService"
            in called_names
        )
        assert (
            method_name
            in called_methods
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )
        assert "UUID" not in called_names
        assert (
            "cabinet_profession_limit_error_key"
            not in block
        )
        assert (
            "platform_user_id"
            in block
        )
        assert (
            "profile_action.actor"
            in block
        )


@pytest.mark.asyncio
async def test_save_basic_profile_uses_actor_scope():
    (
        service,
        current_actor,
        _,
        specialists,
    ) = build_read_service()

    result = await (
        service.save_basic_profile(
            platform_user_id=123,
            display_name="Updated",
            short_description=None,
            contact_text=None,
        )
    )

    assert result.actor is current_actor
    assert result.result.changed is True
    assert (
        result.result.specialist_id
        == current_actor.specialist_id
    )

    assert len(
        specialists.calls
    ) == 1
    call_name, update_data = (
        specialists.calls[0]
    )
    assert (
        call_name
        == "save_basic_profile"
    )
    assert (
        update_data.tenant_id
        == current_actor.tenant_id
    )
    assert (
        update_data.user_id
        == current_actor.user_id
    )
    assert (
        update_data.specialist_id
        == current_actor.specialist_id
    )
    assert (
        update_data.display_name
        == "Updated"
    )
    assert (
        update_data.short_description
        is None
    )
    assert (
        update_data.contact_text
        is None
    )


def test_basic_profile_save_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == (
            "save_specialist_"
            "profile_update"
        )
    )
    block = "\n".join(
        lines[
            node.lineno - 1:
            node.end_lineno
        ]
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert (
        "SpecialistProfileService"
        in called_names
    )
    assert (
        "save_basic_profile"
        in called_methods
    )
    assert (
        "SpecialistRepository"
        not in called_names
    )
    assert (
        "SpecialistService"
        not in called_names
    )
    assert (
        "SpecialistProfileUpdateData"
        not in called_names
    )
    assert "UUID" not in called_names
    assert (
        "cabinet_user_id"
        not in block
    )
    assert (
        "cabinet_tenant_id"
        not in block
    )
    assert (
        "cabinet_specialist_id"
        not in block
    )
    assert (
        "platform_user_id"
        in block
    )
    assert (
        "profile_action.actor"
        in block
    )


def test_billing_handler_domain_has_no_direct_business_dependencies():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden = {
        "SpecialistRepository",
        "TranslationRepository",
        "SpecialistService",
        "TranslationService",
        "UserService",
    }

    violations = []

    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if not isinstance(
            node.func,
            ast.Name,
        ):
            continue

        if (
            node.func.id.endswith(
                "Repository"
            )
            or node.func.id
            in forbidden
        ):
            violations.append(
                (
                    node.lineno,
                    node.func.id,
                )
            )

    assert not violations, violations

    language_helper = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == (
            "get_billing_"
            "interface_language"
        )
    )
    helper_calls = {
        call.func.id
        for call
        in ast.walk(language_helper)
        if isinstance(
            call,
            ast.Call,
        )
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    helper_methods = {
        call.func.attr
        for call
        in ast.walk(language_helper)
        if isinstance(
            call,
            ast.Call,
        )
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert (
        "UserSettingsService"
        in helper_calls
    )
    assert (
        "get_context"
        in helper_methods
    )


def test_billing_fsm_does_not_store_profile_actor_identity():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_keys = {
        "cabinet_user_id",
        "cabinet_tenant_id",
        "cabinet_specialist_id",
    }
    writes = []

    for call in ast.walk(tree):
        if not (
            isinstance(
                call,
                ast.Call,
            )
            and isinstance(
                call.func,
                ast.Attribute,
            )
            and call.func.attr
            == "update_data"
        ):
            continue

        for keyword in call.keywords:
            if (
                keyword.arg
                in forbidden_keys
            ):
                writes.append(
                    (
                        call.lineno,
                        keyword.arg,
                    )
                )

    assert not writes, writes



def test_profile_visibility_back_callback_is_routed():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    keyboard = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "profile_status_visibility_keyboard"
    )
    keyboard_block = (
        ast.get_source_segment(
            source,
            keyboard,
        )
        or ""
    )

    assert "CAB_PROFILE_VIEW" in keyboard_block

    handler = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "view_specialist_profile"
    )
    decorators = "\n".join(
        ast.unparse(decorator)
        for decorator
        in handler.decorator_list
    )
    handler_block = (
        ast.get_source_segment(
            source,
            handler,
        )
        or ""
    )

    assert "CAB_PROFILE_VIEW" in decorators
    assert (
        "specialist_profile_keyboard"
        in handler_block
    )
