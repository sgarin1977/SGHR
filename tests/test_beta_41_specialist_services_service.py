from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_services import (
    SpecialistServicesAccessError,
    SpecialistServicesService,
)


class FakeUsers:
    def __init__(self, context):
        self.context = context
        self.calls = []

    async def get_specialist_context_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)
        return self.context


class FakeTranslations:
    def __init__(self, language="en"):
        self.language = language
        self.calls = []

    async def resolve_interface_language(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.language


class FakeSpecialistService:
    def __init__(self):
        self.calls = []
        self.items = [
            SimpleNamespace(id=uuid4())
        ]
        self.edit_item = SimpleNamespace(
            service_id=uuid4()
        )
        self.saved_result = SimpleNamespace(
            id=uuid4()
        )
        self.toggle_result = (
            SimpleNamespace(id=uuid4()),
            "active",
            "paused",
        )
        self.delete_result = (
            SimpleNamespace(id=uuid4()),
            "active",
        )

    async def list_service_items_page_for_viewer(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return 7, self.items

    async def get_service_item_for_editing(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return self.edit_item

    async def save_service_item(
        self,
        data,
    ):
        self.calls.append(("save", data))
        return self.saved_result

    async def toggle_service_item_status(
        self,
        **kwargs,
    ):
        self.calls.append(("toggle", kwargs))
        return self.toggle_result

    async def delete_service_item(
        self,
        **kwargs,
    ):
        self.calls.append(("delete", kwargs))
        return self.delete_result


def make_context(
    *,
    specialist=True,
    tenant=True,
    language_code="ru",
):
    user = SimpleNamespace(
        id=uuid4(),
        language_code=language_code,
    )

    return SimpleNamespace(
        user=user,
        specialist=(
            SimpleNamespace(id=uuid4())
            if specialist
            else None
        ),
        tenant_id=(
            uuid4()
            if tenant
            else None
        ),
    )


def build_service(
    context,
    *,
    language="en",
):
    users = FakeUsers(context)
    translations = FakeTranslations(language)
    specialist = FakeSpecialistService()

    service = SpecialistServicesService(
        SimpleNamespace(),
        users=users,
        translations=translations,
        specialist=specialist,
    )

    return (
        service,
        users,
        translations,
        specialist,
    )


@pytest.mark.asyncio
async def test_unknown_user_fails_closed():
    (
        service,
        users,
        translations,
        specialist,
    ) = build_service(None)

    with pytest.raises(
        SpecialistServicesAccessError
    ) as error:
        await service.require_actor(
            platform_user_id=100,
            fallback_language="ru",
        )

    assert error.value.reason == "user_not_found"
    assert users.calls == [100]
    assert translations.calls == []
    assert specialist.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_specialist", "has_tenant"),
    [
        (False, True),
        (True, False),
    ],
)
async def test_invalid_specialist_context_fails_closed(
    has_specialist,
    has_tenant,
):
    context = make_context(
        specialist=has_specialist,
        tenant=has_tenant,
    )
    service, _, _, specialist = build_service(
        context
    )

    with pytest.raises(
        SpecialistServicesAccessError
    ) as error:
        await service.require_actor(
            platform_user_id=101,
            fallback_language="ru",
        )

    assert (
        error.value.reason
        == "specialist_not_found"
    )
    assert specialist.calls == []


@pytest.mark.asyncio
async def test_actor_is_resolved_from_telegram_context():
    context = make_context(
        language_code="de"
    )
    (
        service,
        users,
        translations,
        _,
    ) = build_service(
        context,
        language="ua",
    )

    actor = await service.require_actor(
        platform_user_id=102,
        fallback_language="ru",
    )

    assert actor.user_id == context.user.id
    assert (
        actor.specialist_id
        == context.specialist.id
    )
    assert actor.tenant_id == context.tenant_id
    assert actor.language == "uk"
    assert users.calls == [102]
    assert translations.calls == [
        {
            "user_id": context.user.id,
            "fallback_language": "de",
        }
    ]


@pytest.mark.asyncio
async def test_list_services_uses_actor_context():
    context = make_context()
    service, _, _, specialist = build_service(
        context
    )

    page = await service.list_services(
        platform_user_id=103,
        fallback_language="ru",
        page=-5,
        page_size=0,
    )

    assert page.total == 7
    assert page.items is specialist.items
    assert specialist.calls == [
        (
            "list",
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user.id,
                "specialist_id": (
                    context.specialist.id
                ),
                "page": 0,
                "page_size": 1,
            },
        )
    ]


@pytest.mark.asyncio
async def test_edit_uses_actor_context():
    context = make_context()
    service, _, _, specialist = build_service(
        context
    )
    service_id = uuid4()

    result = (
        await service.get_service_for_editing(
            platform_user_id=104,
            fallback_language="ru",
            service_id=service_id,
        )
    )

    assert result.item is specialist.edit_item
    assert specialist.calls == [
        (
            "get",
            {
                "user_id": context.user.id,
                "specialist_id": (
                    context.specialist.id
                ),
                "service_id": service_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_save_uses_authoritative_actor_ids():
    context = make_context()
    service, _, _, specialist = build_service(
        context
    )
    service_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()

    action = await service.save_service(
        platform_user_id=105,
        fallback_language="ru",
        service_id=service_id,
        category_id=category_id,
        profession_id=profession_id,
        title="Consultation",
        description="Detailed consultation",
        price_from=10.0,
        price_to=20.0,
        currency="EUR",
    )

    assert (
        action.result
        is specialist.saved_result
    )

    operation, data = specialist.calls[0]

    assert operation == "save"
    assert data.user_id == context.user.id
    assert data.tenant_id == context.tenant_id
    assert (
        data.specialist_id
        == context.specialist.id
    )
    assert data.service_id == service_id
    assert data.category_id == category_id
    assert data.profession_id == profession_id
    assert data.title == "Consultation"
    assert data.price_from == 10.0
    assert data.price_to == 20.0
    assert data.currency == "EUR"


@pytest.mark.asyncio
async def test_toggle_uses_actor_context():
    context = make_context()
    service, _, _, specialist = build_service(
        context
    )
    service_id = uuid4()

    action = await service.toggle_service_status(
        platform_user_id=106,
        fallback_language="ru",
        service_id=service_id,
    )

    assert (
        action.result
        is specialist.toggle_result
    )
    assert specialist.calls == [
        (
            "toggle",
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user.id,
                "specialist_id": (
                    context.specialist.id
                ),
                "service_id": service_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_delete_uses_actor_context():
    context = make_context()
    service, _, _, specialist = build_service(
        context
    )
    service_id = uuid4()

    action = await service.delete_service(
        platform_user_id=107,
        fallback_language="ru",
        service_id=service_id,
    )

    assert (
        action.result
        is specialist.delete_result
    )
    assert specialist.calls == [
        (
            "delete",
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user.id,
                "specialist_id": (
                    context.specialist.id
                ),
                "service_id": service_id,
            },
        )
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10", (10.0, None)),
        ("10,5", (10.5, None)),
        ("10-20", (10.0, 20.0)),
        ("10,5 - 20,75", (10.5, 20.75)),
    ],
)
def test_parse_price(value, expected):
    assert (
        SpecialistServicesService.parse_price(
            value
        )
        == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "invalid",
        "-10",
        "20-10",
        "10--20",
    ],
)
def test_invalid_price_fails_closed(value):
    with pytest.raises(ValueError):
        SpecialistServicesService.parse_price(
            value
        )


def test_service_owns_domain_dependencies():
    source = open(
        "services/specialist_services.py",
        encoding="utf-8",
    ).read()

    assert "UserService" in source
    assert "TranslationService" in source
    assert "SpecialistRepository" in source
    assert "SpecialistService" in source
    assert "SpecialistServiceItemData" in source

def test_service_read_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_services.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    for function_name in (
        "specialist_services_entry",
        "add_specialist_service",
        "edit_specialist_service",
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
            and isinstance(call.func, ast.Name)
        }

        assert (
            "SpecialistServicesService"
            in block
        )
        assert (
            "get_current_specialist_for_telegram"
            not in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )

def test_service_mutation_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_services.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    for function_name in (
        "confirm_specialist_service",
        "pause_specialist_service",
        "delete_specialist_service",
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
            and isinstance(call.func, ast.Name)
        }

        assert (
            "SpecialistServicesService"
            in block
        )
        assert (
            "get_current_specialist_for_telegram"
            not in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )
        assert (
            "SpecialistRepository"
            not in called_names
        )
        assert (
            "SpecialistService"
            not in called_names
        )

    confirm_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "confirm_specialist_service"
    )
    confirm_block = "\n".join(
        lines[
            confirm_node.lineno - 1:
            confirm_node.end_lineno
        ]
    )

    assert 'data.get("service_tenant_id")' not in confirm_block
    assert 'data.get("service_user_id")' not in confirm_block
    assert 'data.get("service_specialist_id")' not in confirm_block

def test_price_parser_is_service_owned():
    import ast

    source = open(
        "handlers/specialist_services.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.FunctionDef,
        )
        and item.name == "parse_service_price"
    )

    called_attributes = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
    }

    assert "parse_price" in called_attributes

    forbidden_calls = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in {
            "float",
            "ValueError",
        }
    }

    assert not forbidden_calls

def test_service_form_handlers_use_actor_service():
    import ast

    source = open(
        "handlers/specialist_services.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    function_names = (
        "receive_service_title",
        "receive_service_description",
        "skip_service_price",
        "receive_service_price",
        "ask_delete_specialist_service",
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
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        assert (
            "require_specialist_services_actor"
            in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )

    helper = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "require_specialist_services_actor"
    )
    helper_block = "\n".join(
        lines[
            helper.lineno - 1:
            helper.end_lineno
        ]
    )

    assert (
        "SpecialistServicesService"
        in helper_block
    )
    assert "SpecialistRepository" not in helper_block
    assert "SpecialistService(" not in helper_block

def test_specialist_services_owns_its_fsm():
    import ast

    services_source = open(
        "handlers/specialist_services.py",
        encoding="utf-8",
    ).read()
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()

    services_tree = ast.parse(
        services_source
    )
    billing_tree = ast.parse(
        billing_source
    )

    services_classes = {
        node.name: node
        for node in services_tree.body
        if isinstance(node, ast.ClassDef)
    }
    billing_classes = {
        node.name: node
        for node in billing_tree.body
        if isinstance(node, ast.ClassDef)
    }

    assert (
        "SpecialistServicesFSM"
        in services_classes
    )
    assert (
        "SpecialistCabinetFSM"
        in billing_classes
    )
    assert (
        "SpecialistServicesFSM"
        not in billing_classes
    )

    expected_states = {
        "entering_service_title",
        "entering_service_description",
        "entering_service_price",
        "confirming_service",
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
        assigned_names(
            services_classes[
                "SpecialistServicesFSM"
            ]
        )
        == expected_states
    )
    assert not (
        assigned_names(
            billing_classes[
                "SpecialistCabinetFSM"
            ]
        )
        & expected_states
    )

    for node in ast.walk(services_tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in expected_states
        ):
            assert isinstance(
                node.value,
                ast.Name,
            )
            assert (
                node.value.id
                == "SpecialistServicesFSM"
            )


def test_billing_input_helper_is_shared():
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    common_source = open(
        "handlers/billing_common.py",
        encoding="utf-8",
    ).read()

    assert (
        "def replace_billing_input_screen"
        not in billing_source
    )
    assert (
        "from handlers.billing_common import"
        in billing_source
    )
    assert (
        "async def replace_billing_input_screen"
        in common_source
    )
    assert (
        "from handlers.billing import"
        not in common_source
    )

def test_specialist_services_router_is_independent():
    import ast

    services_source = open(
        "handlers/specialist_services.py",
        encoding="utf-8",
    ).read()
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "specialist_services_router = Router()"
        in services_source
    )
    assert (
        "from handlers.billing import"
        not in services_source
    )
    assert (
        "SpecialistServicesService"
        in services_source
    )
    assert "SpecialistRepository(" not in services_source

    moved_names = {
        "SpecialistServicesFSM",
        "require_specialist_services_actor",
        "specialist_services_entry",
        "add_specialist_service",
        "edit_specialist_service",
        "receive_service_title",
        "receive_service_description",
        "skip_service_price",
        "receive_service_price",
        "confirm_specialist_service",
        "pause_specialist_service",
        "ask_delete_specialist_service",
        "delete_specialist_service",
    }

    services_tree = ast.parse(
        services_source
    )
    billing_tree = ast.parse(
        billing_source
    )

    services_names = {
        node.name
        for node in services_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }
    billing_names = {
        node.name
        for node in billing_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }

    assert moved_names <= services_names
    assert not (
        moved_names & billing_names
    )

    services_position = bot_source.index(
        "dp.include_router(\n"
        "        specialist_services_router"
    )
    billing_position = bot_source.index(
        "dp.include_router(billing_router)"
    )

    assert services_position < billing_position
