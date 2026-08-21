from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_portfolio import (
    SpecialistPortfolioAccessError,
    SpecialistPortfolioService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)
        return self.user


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


class FakePortfolio:
    def __init__(self):
        self.calls = []
        self.page = SimpleNamespace(
            page=0,
            page_size=5,
            total=1,
            total_pages=1,
            items=(),
        )
        self.deleted_item = SimpleNamespace(
            id=uuid4()
        )
        self.uploaded_item = SimpleNamespace(
            id=uuid4()
        )

    async def list_owner_items_page(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return self.page

    async def delete_owner_item(
        self,
        **kwargs,
    ):
        self.calls.append(("delete", kwargs))
        return self.deleted_item

    async def upload_item(
        self,
        **kwargs,
    ):
        self.calls.append(("upload", kwargs))
        return self.uploaded_item


def make_user(
    *,
    tenant_id=True,
    language_code="ru",
):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=(
            uuid4()
            if tenant_id
            else None
        ),
        language_code=language_code,
    )


def build_service(
    user,
    *,
    language="en",
):
    users = FakeUsers(user)
    translations = FakeTranslations(language)
    portfolio = FakePortfolio()

    service = SpecialistPortfolioService(
        SimpleNamespace(),
        users=users,
        translations=translations,
        portfolio=portfolio,
    )

    return (
        service,
        users,
        translations,
        portfolio,
    )


@pytest.mark.asyncio
async def test_unknown_actor_fails_closed():
    (
        service,
        users,
        translations,
        portfolio,
    ) = build_service(None)

    with pytest.raises(
        SpecialistPortfolioAccessError
    ):
        await service.require_actor(
            platform_user_id=100,
            fallback_language="ru",
        )

    assert users.calls == [100]
    assert translations.calls == []
    assert portfolio.calls == []


@pytest.mark.asyncio
async def test_actor_without_tenant_fails_closed():
    user = make_user(tenant_id=False)
    service, _, translations, portfolio = (
        build_service(user)
    )

    with pytest.raises(
        SpecialistPortfolioAccessError
    ):
        await service.list_owner_items(
            platform_user_id=101,
            fallback_language="ru",
            page=0,
            page_size=5,
        )

    assert translations.calls == []
    assert portfolio.calls == []


@pytest.mark.asyncio
async def test_actor_uses_interface_language():
    user = make_user(language_code="de")
    (
        service,
        users,
        translations,
        _,
    ) = build_service(
        user,
        language="ua",
    )

    actor = await service.require_actor(
        platform_user_id=102,
        fallback_language="ru",
    )

    assert actor.user_id == user.id
    assert actor.tenant_id == user.tenant_id
    assert actor.language == "uk"
    assert users.calls == [102]
    assert translations.calls == [
        {
            "user_id": user.id,
            "fallback_language": "de",
        }
    ]


@pytest.mark.asyncio
async def test_list_uses_actor_context():
    user = make_user()
    service, _, _, portfolio = build_service(
        user
    )

    result = await service.list_owner_items(
        platform_user_id=103,
        fallback_language="ru",
        page=-10,
        page_size=0,
    )

    assert result.page is portfolio.page
    assert result.actor.user_id == user.id
    assert portfolio.calls == [
        (
            "list",
            {
                "tenant_id": user.tenant_id,
                "owner_user_id": user.id,
                "page": 0,
                "page_size": 1,
            },
        )
    ]


@pytest.mark.asyncio
async def test_delete_uses_actor_context():
    user = make_user()
    service, _, _, portfolio = build_service(
        user
    )
    item_id = uuid4()

    action = await service.delete_owner_item(
        platform_user_id=104,
        fallback_language="ru",
        item_id=item_id,
    )

    assert (
        action.result
        is portfolio.deleted_item
    )
    assert portfolio.calls == [
        (
            "delete",
            {
                "tenant_id": user.tenant_id,
                "owner_user_id": user.id,
                "item_id": item_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_upload_uses_actor_context():
    user = make_user()
    service, _, _, portfolio = build_service(
        user
    )

    action = await service.upload_item(
        platform_user_id=105,
        fallback_language="ru",
        filename="work.pdf",
        mime_type="application/pdf",
        content=b"portfolio",
        caption="  My work  ",
    )

    assert (
        action.result
        is portfolio.uploaded_item
    )
    assert portfolio.calls == [
        (
            "upload",
            {
                "tenant_id": user.tenant_id,
                "owner_user_id": user.id,
                "filename": "work.pdf",
                "mime_type": "application/pdf",
                "content": b"portfolio",
                "title": "My work",
                "description": "My work",
            },
        )
    ]


@pytest.mark.asyncio
async def test_empty_caption_uses_filename():
    user = make_user()
    service, _, _, portfolio = build_service(
        user
    )

    await service.upload_item(
        platform_user_id=106,
        fallback_language="ru",
        filename="photo.jpg",
        mime_type="image/jpeg",
        content=b"photo",
        caption="  ",
    )

    _, payload = portfolio.calls[0]

    assert payload["title"] == "photo.jpg"
    assert payload["description"] is None


def test_service_owns_portfolio_dependencies():
    source = open(
        "services/specialist_portfolio.py",
        encoding="utf-8",
    ).read()

    assert "UserService" in source
    assert "TranslationService" in source
    assert "PortfolioRepository" in source
    assert "PortfolioService" in source

def test_portfolio_mutations_use_application_service():
    import ast

    source = open(
        "handlers/specialist_portfolio.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    for function_name in (
        "delete_owner_portfolio_item",
        "confirm_portfolio_upload",
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
            "SpecialistPortfolioService"
            in block
        )
        assert (
            "get_billing_user_context"
            not in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )
        assert (
            "PortfolioRepository"
            not in called_names
        )
        assert (
            "PortfolioService"
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
        == "confirm_portfolio_upload"
    )
    confirm_block = "\n".join(
        lines[
            confirm_node.lineno - 1:
            confirm_node.end_lineno
        ]
    )

    assert (
        'data.get("portfolio_tenant_id")'
        not in confirm_block
    )
    assert (
        'data.get("portfolio_owner_user_id")'
        not in confirm_block
    )

def test_portfolio_read_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_portfolio.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    for function_name in (
        "show_owner_portfolio",
        "show_owner_portfolio_page",
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
            "SpecialistPortfolioService"
            in block
        )
        assert (
            "get_billing_user_context"
            not in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )
        assert (
            "PortfolioRepository"
            not in called_names
        )
        assert (
            "PortfolioService"
            not in called_names
        )

    renderer = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name == "send_owner_portfolio"
    )
    renderer_block = "\n".join(
        lines[
            renderer.lineno - 1:
            renderer.end_lineno
        ]
    )

    assert "get_session" not in renderer_block
    assert "PortfolioRepository" not in renderer_block
    assert "PortfolioService" not in renderer_block
    assert "portfolio_page" in renderer_block

def test_portfolio_form_handlers_use_actor_service():
    import ast

    source = open(
        "handlers/specialist_portfolio.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    function_names = (
        "ask_portfolio_upload",
        "receive_portfolio_file",
        "receive_portfolio_caption",
        "skip_portfolio_caption",
        "reject_invalid_portfolio_message",
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
            "require_specialist_portfolio_actor"
            in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )
        assert (
            "get_billing_user_context"
            not in block
        )

    receive_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "receive_portfolio_file"
    )
    receive_block = "\n".join(
        lines[
            receive_node.lineno - 1:
            receive_node.end_lineno
        ]
    )

    assert "portfolio_tenant_id=" not in receive_block
    assert "portfolio_owner_user_id=" not in receive_block

    helper = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "require_specialist_portfolio_actor"
    )
    helper_block = "\n".join(
        lines[
            helper.lineno - 1:
            helper.end_lineno
        ]
    )

    called_names = {
        call.func.id
        for call in ast.walk(helper)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }

    assert (
        "SpecialistPortfolioService"
        in called_names
    )
    assert "PortfolioRepository" not in called_names
    assert "PortfolioService" not in called_names


def test_specialist_portfolio_owns_its_fsm():
    import ast

    portfolio_source = open(
        "handlers/specialist_portfolio.py",
        encoding="utf-8",
    ).read()
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()

    portfolio_tree = ast.parse(portfolio_source)
    billing_tree = ast.parse(billing_source)

    portfolio_classes = {
        node.name: node
        for node in portfolio_tree.body
        if isinstance(node, ast.ClassDef)
    }
    billing_classes = {
        node.name: node
        for node in billing_tree.body
        if isinstance(node, ast.ClassDef)
    }

    expected_states = {
        "waiting_portfolio_file",
        "entering_portfolio_caption",
        "confirming_portfolio_upload",
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
        expected_states
        == assigned_names(
            portfolio_classes[
                "SpecialistPortfolioFSM"
            ]
        )
    )
    assert (
        "SpecialistPortfolioFSM"
        not in billing_classes
    )
    assert not (
        expected_states
        & assigned_names(
            billing_classes[
                "SpecialistCabinetFSM"
            ]
        )
    )

    portfolio_references = [
        node
        for node in ast.walk(portfolio_tree)
        if isinstance(node, ast.Attribute)
        and node.attr in expected_states
    ]
    billing_references = [
        node
        for node in ast.walk(billing_tree)
        if isinstance(node, ast.Attribute)
        and node.attr in expected_states
    ]

    assert len(portfolio_references) == 7
    assert not billing_references

    for node in portfolio_references:
        assert isinstance(node.value, ast.Name)
        assert (
            node.value.id
            == "SpecialistPortfolioFSM"
        )


def test_cross_feature_cleanup_helper_is_shared():
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    common_source = open(
        "handlers/billing_common.py",
        encoding="utf-8",
    ).read()

    owner_sources = {
        filename: open(
            filename,
            encoding="utf-8",
        ).read()
        for filename in (
            "handlers/specialist_portfolio.py",
            "handlers/user_dialogs.py",
            "handlers/user_favorites.py",
        )
    }

    assert (
        "def clear_cross_feature_messages"
        in common_source
    )
    assert (
        "def clear_cross_feature_messages"
        not in billing_source
    )
    assert (
        "clear_cross_feature_messages"
        not in billing_source
    )

    for filename, source in (
        owner_sources.items()
    ):
        assert (
            "from handlers.billing_common "
            "import"
            in source
        ), filename
        assert (
            "clear_cross_feature_messages"
            in source
        ), filename
        assert (
            "def clear_cross_feature_messages"
            not in source
        ), filename


def test_specialist_portfolio_router_is_independent():
    import ast

    portfolio_source = open(
        "handlers/specialist_portfolio.py",
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

    portfolio_tree = ast.parse(portfolio_source)
    billing_tree = ast.parse(billing_source)

    portfolio_names = {
        getattr(node, "name", None)
        for node in portfolio_tree.body
    }
    billing_names = {
        getattr(node, "name", None)
        for node in billing_tree.body
    }

    moved_names = {
        "SpecialistPortfolioFSM",
        "show_owner_portfolio",
        "show_owner_portfolio_page",
        "ask_portfolio_upload",
        "receive_portfolio_file",
        "receive_portfolio_caption",
        "skip_portfolio_caption",
        "reject_invalid_portfolio_message",
        "delete_owner_portfolio_item",
        "confirm_portfolio_upload",
    }

    assert moved_names <= portfolio_names
    assert not (moved_names & billing_names)
    assert (
        "specialist_portfolio_router = Router()"
        in portfolio_source
    )
    assert (
        "from handlers.billing import"
        not in portfolio_source
    )
    assert (
        "dp.include_router("
        "specialist_portfolio_router"
        ")"
        in bot_source
    )
    assert (
        bot_source.index(
            "dp.include_router("
            "specialist_portfolio_router"
            ")"
        )
        < bot_source.index(
            "dp.include_router(billing_router)"
        )
    )



def test_portfolio_handlers_do_not_mutate_callback_query():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/specialist_portfolio.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    callback_data_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(
            node,
            (
                ast.Assign,
                ast.AnnAssign,
                ast.AugAssign,
            ),
        )
        for target in (
            node.targets
            if isinstance(
                node,
                ast.Assign,
            )
            else [node.target]
        )
        if isinstance(
            target,
            ast.Attribute,
        )
        and isinstance(
            target.value,
            ast.Name,
        )
        and target.value.id == "callback"
        and target.attr == "data"
    ]

    assert callback_data_assignments == []


def test_portfolio_delete_passes_page_explicitly():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/specialist_portfolio.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "delete_owner_portfolio_item"
    )

    calls = [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
        and call.func.id
        == "show_owner_portfolio_page"
    ]

    assert len(calls) == 1

    keywords = {
        keyword.arg: keyword.value
        for keyword
        in calls[0].keywords
    }

    requested_page = keywords.get(
        "requested_page"
    )

    assert isinstance(
        requested_page,
        ast.Name,
    )
    assert requested_page.id == "page"
