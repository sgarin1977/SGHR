from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_cabinets import (
    SpecialistCabinetsProfileNotFoundError,
    SpecialistCabinetsSelectionError,
    SpecialistCabinetsService,
    SpecialistCabinetsUserNotFoundError,
)
from services.user_settings import (
    UserSettingsNotFoundError,
)


class FakeSettings:
    def __init__(self, context=None, error=None):
        self.context = context
        self.error = error

    async def get_context(self, **kwargs):
        if self.error:
            raise self.error
        return self.context


class FakeRepository:
    def __init__(self, specialist=None):
        self.specialist = specialist
        self.user_ids = []

    async def get_by_user_id(self, user_id):
        self.user_ids.append(user_id)
        return self.specialist


class FakeSpecialists:
    def __init__(self):
        self.calls = []
        self.open_result = "cabinet-context"

    async def open_specialist_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(("open", kwargs))
        return self.open_result

    async def list_professional_cabinet_options(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return ["cabinet"]

    async def switch_active_professional_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(("switch", kwargs))
        return "switched"

    async def list_professional_cabinet_categories(
        self,
        **kwargs,
    ):
        self.calls.append(("categories", kwargs))
        return ["category"]

    async def list_professional_cabinet_professions(
        self,
        **kwargs,
    ):
        self.calls.append(("professions", kwargs))
        return ["profession"]

    async def get_active_cabinet_availability(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("availability", kwargs)
        )
        return "available"

    async def update_availability(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("set_availability", kwargs)
        )

    async def create_professional_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(("create", kwargs))
        return "created"


def actor_context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    context=None,
    specialist=None,
    settings_error=None,
):
    repository = FakeRepository(specialist)
    specialists = FakeSpecialists()
    service = SpecialistCabinetsService(
        object(),
        settings=FakeSettings(
            context,
            settings_error,
        ),
        repository=repository,
        specialists=specialists,
    )
    return service, repository, specialists


@pytest.mark.asyncio
async def test_require_actor_resolves_specialist():
    context = actor_context()
    specialist = SimpleNamespace(id=uuid4())
    service, repository, _ = build_service(
        context=context,
        specialist=specialist,
    )

    actor = await service.require_actor(
        platform_user_id=123
    )

    assert actor.user_id == context.user_id
    assert actor.specialist_id == specialist.id
    assert actor.language == "uk"
    assert repository.user_ids == [context.user_id]


@pytest.mark.asyncio
async def test_missing_user_is_denied():
    service, _, _ = build_service(
        settings_error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        SpecialistCabinetsUserNotFoundError
    ):
        await service.require_actor(
            platform_user_id=123
        )


@pytest.mark.asyncio
async def test_missing_specialist_is_denied():
    service, _, _ = build_service(
        context=actor_context(),
        specialist=None,
    )

    with pytest.raises(
        SpecialistCabinetsProfileNotFoundError
    ):
        await service.require_actor(
            platform_user_id=123
        )


@pytest.mark.asyncio
async def test_list_cabinets_uses_actor_scope():
    context = actor_context()
    specialist = SimpleNamespace(id=uuid4())
    service, _, domain = build_service(
        context=context,
        specialist=specialist,
    )

    result = await service.list_cabinets(
        platform_user_id=123
    )

    assert result.result == ["cabinet"]
    assert domain.calls[0][1] == {
        "tenant_id": context.tenant_id,
        "user_id": context.user_id,
        "specialist_id": specialist.id,
        "language": "uk",
    }


@pytest.mark.asyncio
async def test_switch_cabinet_validates_uuid():
    service, _, domain = build_service(
        context=actor_context(),
        specialist=SimpleNamespace(id=uuid4()),
    )
    cabinet_id = uuid4()

    result = await service.switch_cabinet(
        platform_user_id=123,
        professional_cabinet_id=str(cabinet_id),
    )

    assert result.result == "switched"
    assert (
        domain.calls[0][1]
        ["professional_cabinet_id"]
        == cabinet_id
    )


@pytest.mark.asyncio
async def test_invalid_switch_id_fails_closed():
    service, _, domain = build_service(
        context=actor_context(),
        specialist=SimpleNamespace(id=uuid4()),
    )

    with pytest.raises(
        SpecialistCabinetsSelectionError
    ):
        await service.switch_cabinet(
            platform_user_id=123,
            professional_cabinet_id="invalid",
        )

    assert domain.calls == []


@pytest.mark.asyncio
async def test_category_and_profession_reads():
    service, _, domain = build_service(
        context=actor_context(),
        specialist=SimpleNamespace(id=uuid4()),
    )
    category_id = uuid4()

    categories = await service.list_categories(
        platform_user_id=123
    )
    professions = await service.list_professions(
        platform_user_id=123,
        category_id=str(category_id),
    )

    assert categories.result == ["category"]
    assert professions.result == ["profession"]
    assert (
        domain.calls[1][1]["category_id"]
        == category_id
    )


@pytest.mark.asyncio
async def test_create_cabinet_uses_actor_scope():
    context = actor_context()
    specialist = SimpleNamespace(id=uuid4())
    service, _, domain = build_service(
        context=context,
        specialist=specialist,
    )
    category_id = uuid4()
    profession_id = uuid4()

    result = await service.create_cabinet(
        platform_user_id=123,
        category_id=category_id,
        profession_id=str(profession_id),
    )

    assert result.result == "created"
    assert domain.calls[0][1] == {
        "tenant_id": context.tenant_id,
        "user_id": context.user_id,
        "specialist_id": specialist.id,
        "category_id": category_id,
        "profession_id": profession_id,
        "language": "uk",
    }


def test_cabinet_list_and_switch_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_cabinets.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    expected = {
        "show_professional_cabinets": (
            "list_cabinets"
        ),
        "switch_professional_cabinet": (
            "switch_cabinet"
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

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }
        attributes = {
            item.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
        }

        assert "SpecialistCabinetsService" in called_names
        assert method_name in attributes
        assert "SpecialistRepository" not in called_names
        assert "SpecialistService" not in called_names
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert (
            "get_billing_interface_language"
            not in called_names
        )


def test_cabinet_category_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_cabinets.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    for function_name in (
        "start_professional_cabinet_creation",
        "change_professional_cabinet_category_page",
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
        attributes = {
            item.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
        }

        assert "SpecialistCabinetsService" in called_names
        assert "list_categories" in attributes
        assert "SpecialistRepository" not in called_names
        assert "SpecialistService" not in called_names
        assert (
            "get_current_specialist_for_telegram"
            not in called_names
        )
        assert (
            "get_billing_interface_language"
            not in called_names
        )


def test_cabinet_profession_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_cabinets.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    for function_name in (
        "select_professional_cabinet_category",
        "change_professional_cabinet_profession_page",
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
        attributes = {
            item.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
        }

        assert "SpecialistCabinetsService" in called_names
        assert "list_professions" in attributes
        assert "SpecialistRepository" not in called_names
        assert "SpecialistService" not in called_names
        assert (
            "get_billing_interface_language"
            not in called_names
        )


def test_cabinet_creation_handler_uses_application_service():
    import ast

    source = open(
        "handlers/specialist_cabinets.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "create_selected_professional_cabinet"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }
    attributes = {
        item.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Attribute)
    }

    assert "SpecialistCabinetsService" in called_names
    assert "create_cabinet" in attributes
    assert "SpecialistRepository" not in called_names
    assert "SpecialistService" not in called_names
    assert (
        "get_current_specialist_for_telegram"
        not in called_names
    )
    assert (
        "get_billing_interface_language"
        not in called_names
    )
    assert "UUID" not in called_names


@pytest.mark.asyncio
async def test_open_cabinet_uses_interface_language():
    context = actor_context()
    service, repository, domain = build_service(
        context=context,
        specialist=None,
    )

    result = await service.open_cabinet(
        platform_user_id=123
    )

    assert result.language == "uk"
    assert result.context == "cabinet-context"
    assert repository.user_ids == []
    assert domain.calls == [
        (
            "open",
            {
                "telegram_id": 123,
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
async def test_open_cabinet_requires_user_context():
    service, _, domain = build_service(
        settings_error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        SpecialistCabinetsUserNotFoundError
    ):
        await service.open_cabinet(
            platform_user_id=123
        )

    assert domain.calls == []


def test_cabinet_payload_uses_application_service():
    import ast

    source = open(
        "handlers/specialist_cabinet_common.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "build_specialist_cabinet_payload"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }
    attributes = {
        item.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Attribute)
    }

    assert "SpecialistCabinetsService" in called_names
    assert "open_cabinet" in attributes
    assert "SpecialistRepository" not in called_names
    assert "SpecialistService" not in called_names
    assert (
        "get_billing_interface_language"
        not in called_names
    )

def test_shared_specialist_cabinet_ui_is_owned_by_common_module():
    import ast
    from pathlib import Path

    files = {
        "billing": Path(
            "handlers/billing.py"
        ),
        "common": Path(
            (
                "handlers/"
                "specialist_cabinet_common.py"
            )
        ),
        "start": Path(
            "handlers/start.py"
        ),
        "registration": Path(
            "fsm/specialist_form.py"
        ),
    }
    sources = {
        name: path.read_text(encoding="utf-8")
        for name, path in files.items()
    }
    trees = {
        name: ast.parse(source)
        for name, source in sources.items()
    }

    moved_names = {
        "cabinet_menu_keyboard",
        "specialist_cabinet_publication_text",
        "format_specialist_cabinet_text",
        "build_specialist_cabinet_payload",
        "show_specialist_cabinet",
        "send_specialist_cabinet_message",
    }

    def definitions(tree):
        return {
            node.name
            for node in tree.body
            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
        }

    assert moved_names <= definitions(
        trees["common"]
    )
    assert not (
        moved_names & definitions(
            trees["billing"]
        )
    )

    common_import_modules = {
        node.module
        for node in ast.walk(trees["common"])
        if isinstance(node, ast.ImportFrom)
    }
    assert "handlers.billing" not in (
        common_import_modules
    )

    for owner in (
        "billing",
        "start",
        "registration",
    ):
        modules = {
            node.module
            for node in ast.walk(trees[owner])
            if isinstance(node, ast.ImportFrom)
            and any(
                alias.name
                == "show_specialist_cabinet"
                for alias in node.names
            )
        }

        assert modules == {
            (
                "handlers."
                "specialist_cabinet_common"
            )
        }, (owner, modules)

def test_professional_cabinets_owns_its_fsm():
    import ast

    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    cabinet_source = open(
        "handlers/specialist_cabinets.py",
        encoding="utf-8",
    ).read()

    billing_tree = ast.parse(billing_source)
    cabinet_tree = ast.parse(cabinet_source)

    billing_classes = {
        node.name: node
        for node in billing_tree.body
        if isinstance(node, ast.ClassDef)
    }
    cabinet_classes = {
        node.name: node
        for node in cabinet_tree.body
        if isinstance(node, ast.ClassDef)
    }

    expected_states = {
        "adding_cabinet_category",
        "adding_cabinet_profession",
    }

    def assigned_names(class_node):
        return {
            target.id
            for node in class_node.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

    assert (
        "SpecialistProfessionalCabinetsFSM"
        not in billing_classes
    )
    assert (
        "SpecialistProfessionalCabinetsFSM"
        in cabinet_classes
    )
    assert assigned_names(
        cabinet_classes[
            "SpecialistProfessionalCabinetsFSM"
        ]
    ) == expected_states

    assert not (
        assigned_names(
            billing_classes[
                "SpecialistCabinetFSM"
            ]
        )
        & expected_states
    )

    references = {
        state_name: 0
        for state_name in expected_states
    }

    for node in ast.walk(cabinet_tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr not in expected_states:
            continue

        assert isinstance(node.value, ast.Name)
        assert (
            node.value.id
            == "SpecialistProfessionalCabinetsFSM"
        )
        references[node.attr] += 1

    assert all(
        count > 0
        for count in references.values()
    )

    for node in ast.walk(billing_tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in expected_states


def test_professional_cabinets_router_is_independent():
    import ast
    from pathlib import Path

    cabinet_source = Path(
        "handlers/specialist_cabinets.py"
    ).read_text(encoding="utf-8")
    billing_source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    start_source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    bot_source = Path(
        "bot.py"
    ).read_text(encoding="utf-8")

    cabinet_tree = ast.parse(cabinet_source)
    billing_tree = ast.parse(billing_source)
    start_tree = ast.parse(start_source)

    moved_names = {
        "SpecialistProfessionalCabinetsFSM",
        "replace_billing_callback_screen",
        "format_professional_cabinets_text",
        "professional_cabinets_keyboard",
        (
            "professional_cabinet_"
            "add_selection_keyboard"
        ),
        "show_professional_cabinets",
        "switch_professional_cabinet",
        "start_professional_cabinet_creation",
        (
            "change_professional_cabinet_"
            "category_page"
        ),
        (
            "select_professional_cabinet_"
            "category"
        ),
        (
            "change_professional_cabinet_"
            "profession_page"
        ),
        "create_selected_professional_cabinet",
    }

    def definitions(tree):
        return {
            node.name
            for node in tree.body
            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                ),
            )
        }

    assert moved_names <= definitions(
        cabinet_tree
    )
    assert not (
        moved_names & definitions(billing_tree)
    )

    assert (
        "specialist_cabinets_router = Router()"
        in cabinet_source
    )
    assert (
        "from handlers.billing import"
        not in cabinet_source
    )
    assert (
        "SpecialistCabinetsService"
        in cabinet_source
    )

    old_start_imports = [
        node
        for node in ast.walk(start_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "handlers.billing"
        and any(
            alias.name
            == "show_professional_cabinets"
            for alias in node.names
        )
    ]
    new_start_imports = [
        node
        for node in ast.walk(start_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        == "handlers.specialist_cabinets"
        and any(
            alias.name
            == "show_professional_cabinets"
            for alias in node.names
        )
    ]

    assert not old_start_imports
    assert len(new_start_imports) == 2

    assert (
        "from handlers.specialist_cabinets "
        "import specialist_cabinets_router"
        in bot_source
    )

    cabinet_position = bot_source.index(
        "dp.include_router("
        "specialist_cabinets_router)"
    )
    billing_position = bot_source.index(
        "dp.include_router(billing_router)"
    )
    assert cabinet_position < billing_position

@pytest.mark.asyncio
async def test_availability_operations_use_actor_scope():
    context = actor_context()
    specialist = SimpleNamespace(id=uuid4())
    service, _, domain = build_service(
        context=context,
        specialist=specialist,
    )

    opened = await service.get_availability(
        platform_user_id=123
    )
    saved = await service.set_availability(
        platform_user_id=123,
        availability_status="vacation",
    )

    assert opened.result == "available"
    assert saved.result == "vacation"

    assert domain.calls[0] == (
        "availability",
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "specialist_id": specialist.id,
        },
    )
    assert domain.calls[1] == (
        "set_availability",
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "specialist_id": specialist.id,
            "availability_status": "vacation",
        },
    )


@pytest.mark.asyncio
async def test_availability_ignores_external_actor_ids():
    context = actor_context()
    specialist = SimpleNamespace(id=uuid4())
    service, _, domain = build_service(
        context=context,
        specialist=specialist,
    )

    await service.set_availability(
        platform_user_id=456,
        availability_status="busy",
    )

    call = domain.calls[0][1]

    assert call["tenant_id"] == context.tenant_id
    assert call["user_id"] == context.user_id
    assert (
        call["specialist_id"]
        == specialist.id
    )

def test_availability_handlers_use_application_service():
    import ast

    source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    expected = {
        "show_specialist_availability": (
            "get_availability"
        ),
        "set_specialist_availability": (
            "set_availability"
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
            "SpecialistCabinetsService"
            in called_names
        )
        assert service_method in called_methods
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

    set_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "set_specialist_availability"
    )

    set_block = ast.get_source_segment(
        source,
        set_node,
    )

    assert "state.get_data(" not in set_block
    assert "cabinet_tenant_id" not in set_block
    assert "cabinet_user_id" not in set_block
    assert "cabinet_specialist_id" not in set_block
    assert "UUID(" not in set_block


@pytest.mark.asyncio
async def test_has_profile_returns_true_for_actor():
    context = actor_context()
    specialist = SimpleNamespace(
        id=uuid4()
    )
    (
        service,
        repository,
        _,
    ) = build_service(
        context=context,
        specialist=specialist,
    )

    result = await service.has_profile(
        platform_user_id=123
    )

    assert result is True
    assert repository.user_ids == [
        context.user_id
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "specialist", "error"),
    [
        (
            actor_context(),
            None,
            None,
        ),
        (
            None,
            None,
            UserSettingsNotFoundError(),
        ),
    ],
)
async def test_has_profile_returns_false_when_missing(
    context,
    specialist,
    error,
):
    (
        service,
        _,
        _,
    ) = build_service(
        context=context,
        specialist=specialist,
        settings_error=error,
    )

    result = await service.has_profile(
        platform_user_id=456
    )

    assert result is False


def test_start_specialist_cabinet_lookup_uses_application_service():
    import ast
    from pathlib import Path

    start_source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    billing_source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    start_tree = ast.parse(
        start_source
    )
    billing_tree = ast.parse(
        billing_source
    )

    node = next(
        item
        for item in start_tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == (
            "main_menu_"
            "specialist_cabinets"
        )
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
        "SpecialistCabinetsService"
        in called_names
    )
    assert "has_profile" in called_methods
    assert (
        "get_current_specialist_for_telegram"
        not in called_names
    )
    assert (
        "platform_user_id"
        in ast.get_source_segment(
            start_source,
            node,
        )
    )

    billing_definitions = {
        item.name
        for item in billing_tree.body
        if isinstance(
            item,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }
    assert (
        "get_current_specialist_for_telegram"
        not in billing_definitions
    )
