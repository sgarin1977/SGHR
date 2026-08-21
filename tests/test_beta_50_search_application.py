import ast
from pathlib import Path


def function_contract(function_name):
    source = Path(
        "handlers/search.py"
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

    return (
        block,
        called_names,
        called_methods,
    )


def test_search_interface_language_uses_user_settings():
    (
        block,
        called_names,
        called_methods,
    ) = function_contract(
        "get_interface_language"
    )

    assert (
        "UserSettingsService"
        in called_names
    )
    assert "get_context" in called_methods
    assert "UserService" not in called_names
    assert (
        "TranslationRepository"
        not in called_names
    )
    assert (
        "TranslationService"
        not in called_names
    )
    assert (
        "UserSettingsNotFoundError"
        in block
    )


def test_search_requester_context_uses_user_settings():
    (
        block,
        called_names,
        called_methods,
    ) = function_contract(
        "get_requester_context"
    )

    assert (
        "UserSettingsService"
        in called_names
    )
    assert "get_context" in called_methods
    assert "UserService" not in called_names
    assert (
        "UserSettingsNotFoundError"
        in block
    )
    assert "context.user_id" in block
    assert "context.tenant_id" in block


def test_post_auth_dispatcher_uses_application_services():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "resume_post_auth_action"
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

    assert {
        "UserSettingsService",
        "UserDialogsService",
        "UserFavoritesService",
    } <= called_names

    assert {
        "get_context",
        "open_contact",
        "toggle_favorite",
    } <= called_methods

    assert not (
        called_names
        & {
            "UUID",
            "ContactChatRepository",
            "ContactChatService",
            "FavoriteRepository",
            "FavoriteService",
            "get_requester_context",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "resume_public_portfolio_after_auth" in block
    assert "resume_public_reviews_after_auth" in block
    assert 'action == "report"' in block


def test_search_handler_layer_is_business_clean():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports = {}

    for node in tree.body:
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            module = node.module or ""

            for alias in node.names:
                imports[
                    alias.asname or alias.name
                ] = (
                    f"{module}.{alias.name}"
                )

    application_modules = {
        "services.user_settings",
        "services.user_search",
        "services.user_search_location",
        "services.user_search_portfolio",
        "services.user_search_reviews",
        "services.user_favorites",
        "services.user_complaints",
        "services.user_dialogs",
    }

    forbidden_calls = []
    transactions = []

    for function in (
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    ):
        for call in ast.walk(function):
            if not isinstance(
                call,
                ast.Call,
            ):
                continue

            if isinstance(
                call.func,
                ast.Name,
            ):
                name = call.func.id
                origin = imports.get(
                    name,
                    "",
                )
                module = origin.rsplit(
                    ".",
                    1,
                )[0]

                if origin.startswith(
                    "database.repositories."
                ):
                    forbidden_calls.append(
                        (
                            function.name,
                            name,
                        )
                    )

                if (
                    origin.startswith(
                        "services."
                    )
                    and name.endswith(
                        "Service"
                    )
                    and module
                    not in application_modules
                ):
                    forbidden_calls.append(
                        (
                            function.name,
                            name,
                        )
                    )

            if (
                isinstance(
                    call.func,
                    ast.Attribute,
                )
                and call.func.attr
                in {
                    "commit",
                    "rollback",
                    "flush",
                }
            ):
                transactions.append(
                    (
                        function.name,
                        call.func.attr,
                    )
                )

    definitions = {
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

    assert forbidden_calls == []
    assert transactions == []
    assert (
        "translate_message_for_notification"
        not in definitions
    )
