import ast
from pathlib import Path


ADMIN_PATH = Path("handlers/admin.py")


def load_admin():
    source = ADMIN_PATH.read_text(
        encoding="utf-8"
    )
    return source, ast.parse(source)


def function_source(
    source,
    tree,
    function_name,
):
    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and item.name == function_name
    )
    return ast.get_source_segment(
        source,
        node,
    )


def test_telegram_role_scope_mutation_routes_are_removed():
    _, tree = load_admin()

    forbidden_functions = {
        "super_admin_role_grant_start",
        "super_admin_role_revoke_start",
        "super_admin_role_grant_receive",
        "super_admin_role_revoke_receive",
        "super_admin_role_cancel",
        "super_admin_role_confirm",
        "super_admin_role_final_confirm",
        "super_admin_role_execute",
        "ask_super_admin_scope_add",
        "receive_super_admin_scope_add",
        "execute_super_admin_scope_add",
        "cancel_super_admin_scope_add",
        "ask_super_admin_scope_revoke",
        (
            "receive_super_admin_"
            "scope_revoke_reason"
        ),
        "execute_super_admin_scope_revoke",
        "cancel_super_admin_scope_revoke",
        "ask_role_grant",
        "receive_role_grant",
        "ask_role_revoke",
        "receive_role_revoke",
    }

    defined = {
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

    assert not (
        forbidden_functions & defined
    ), sorted(
        forbidden_functions & defined
    )


def test_telegram_handler_has_no_role_scope_writes():
    _, tree = load_admin()

    forbidden_methods = {
        "grant_super_admin_user_role",
        "revoke_super_admin_user_role",
        "add_super_admin_role_scope",
        "revoke_super_admin_role_scope",
        "grant_admin_role",
        "revoke_admin_role",
    }

    called_methods = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(
            node.func,
            ast.Attribute,
        )
    }

    assert not (
        forbidden_methods & called_methods
    ), sorted(
        forbidden_methods & called_methods
    )


def test_root_only_callbacks_are_explicitly_rejected():
    source, tree = load_admin()

    defined = {
        node.name
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
    }

    assert (
        "reject_root_only_admin_mutation"
        in defined
    )
    assert (
        "ROOT_ONLY_ADMIN_CALLBACKS"
        in source
    )

    required_callbacks = {
        "SA_ROLE_GRANT",
        "SA_ROLE_REVOKE",
        "SA_SCOPE_ADD",
        "SA_SCOPE_ADD_CONFIRM",
        "SA_SCOPE_REVOKE_CONFIRM",
        "ADM_ROLE_GRANT",
        "ADM_ROLE_REVOKE",
    }

    for callback in required_callbacks:
        assert callback in source


def test_role_scope_keyboards_are_read_only():
    source, tree = load_admin()
    governance_source = Path(
        "handlers/admin_governance.py"
    ).read_text(encoding="utf-8")
    governance_tree = ast.parse(
        governance_source
    )

    role_keyboard = function_source(
        source,
        tree,
        "admin_roles_keyboard",
    )
    scope_card_keyboard = function_source(
        governance_source,
        governance_tree,
        (
            "super_admin_role_"
            "scope_card_keyboard"
        ),
    )
    scopes_keyboard = function_source(
        governance_source,
        governance_tree,
        "super_admin_role_scopes_keyboard",
    )

    assert "ADM_ROLE_GRANT" not in role_keyboard
    assert "ADM_ROLE_REVOKE" not in role_keyboard

    for callback in (
        "SA_SCOPE_ADD",
        "SA_SCOPE_REVOKE",
    ):
        assert callback not in scope_card_keyboard
        assert callback not in scopes_keyboard


def test_root_only_mutation_fsm_states_removed():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    fsm_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "AdminModerationFSM"
    )

    defined_states = {
        target.id
        for node in fsm_class.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    forbidden_states = {
        "entering_role_grant",
        "entering_role_revoke",
        "entering_super_admin_role_grant",
        "confirming_super_admin_role_grant",
        (
            "confirming_super_admin_"
            "role_grant_final"
        ),
        "entering_super_admin_role_revoke",
        "confirming_super_admin_role_revoke",
        (
            "confirming_super_admin_"
            "role_revoke_final"
        ),
        "entering_super_admin_scope_add",
        "confirming_super_admin_scope_add",
        "entering_super_admin_scope_revoke",
        "confirming_super_admin_scope_revoke",
    }

    assert not (
        defined_states & forbidden_states
    )
