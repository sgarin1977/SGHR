import ast
from collections import defaultdict
from pathlib import Path


def load_modules():
    paths = sorted(
        [
            *Path("handlers").rglob("*.py"),
            *Path("fsm").rglob("*.py"),
        ]
    )

    return {
        path: (
            source := path.read_text(
                encoding="utf-8-sig"
            ),
            ast.parse(source),
        )
        for path in paths
    }


def string_constants(tree):
    values = {}

    for node in tree.body:
        if not isinstance(
            node,
            (ast.Assign, ast.AnnAssign),
        ):
            continue

        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
        )

        if not (
            isinstance(
                node.value,
                ast.Constant,
            )
            and isinstance(
                node.value.value,
                str,
            )
        ):
            continue

        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = (
                    node.value.value
                )

    return values


def string_value(node, constants):
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    ):
        return node.value

    if isinstance(node, ast.Name):
        return constants.get(node.id)

    return None


def callback_prefix(node, constants):
    value = string_value(
        node,
        constants,
    )
    if value is not None:
        return "exact", value

    if isinstance(node, ast.JoinedStr):
        prefix = ""

        for item in node.values:
            if (
                isinstance(
                    item,
                    ast.Constant,
                )
                and isinstance(
                    item.value,
                    str,
                )
            ):
                prefix += item.value
                continue
            break

        if prefix:
            return "prefix", prefix

    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
    ):
        left = callback_prefix(
            node.left,
            constants,
        )
        if left:
            return "prefix", left[1]

    return None


def callback_routes(modules):
    exact = defaultdict(list)
    prefixes = defaultdict(list)

    for path, (_, tree) in modules.items():
        constants = string_constants(tree)

        for function in tree.body:
            if not isinstance(
                function,
                ast.AsyncFunctionDef,
            ):
                continue

            for decorator in (
                function.decorator_list
            ):
                decorator_text = ast.unparse(
                    decorator
                )

                if (
                    "callback_query"
                    not in decorator_text
                ):
                    continue

                has_state_filter = (
                    "StateFilter"
                    in decorator_text
                )
                owner = (
                    str(path),
                    function.name,
                    has_state_filter,
                )

                for node in ast.walk(
                    decorator
                ):
                    if (
                        isinstance(
                            node,
                            ast.Compare,
                        )
                        and len(node.ops) == 1
                        and isinstance(
                            node.ops[0],
                            ast.Eq,
                        )
                        and ast.unparse(
                            node.left
                        ).endswith("F.data")
                    ):
                        value = string_value(
                            node.comparators[0],
                            constants,
                        )
                        if value is not None:
                            exact[value].append(
                                owner
                            )

                    if not (
                        isinstance(
                            node,
                            ast.Call,
                        )
                        and isinstance(
                            node.func,
                            ast.Attribute,
                        )
                    ):
                        continue

                    if (
                        node.func.attr
                        == "startswith"
                        and node.args
                    ):
                        value = string_value(
                            node.args[0],
                            constants,
                        )
                        if value is not None:
                            prefixes[
                                value
                            ].append(owner)

                    if (
                        node.func.attr == "in_"
                        and node.args
                        and isinstance(
                            node.args[0],
                            (
                                ast.Set,
                                ast.List,
                                ast.Tuple,
                            ),
                        )
                    ):
                        for item in (
                            node.args[0].elts
                        ):
                            value = (
                                string_value(
                                    item,
                                    constants,
                                )
                            )
                            if value is not None:
                                exact[
                                    value
                                ].append(
                                    owner
                                )

    return exact, prefixes


def test_all_declared_routers_are_registered():
    modules = load_modules()
    declared = set()

    for _, tree in modules.values():
        for node in tree.body:
            if not isinstance(
                node,
                (ast.Assign, ast.AnnAssign),
            ):
                continue

            targets = (
                node.targets
                if isinstance(
                    node,
                    ast.Assign,
                )
                else [node.target]
            )
            value = node.value

            if not (
                isinstance(value, ast.Call)
                and (
                    (
                        isinstance(
                            value.func,
                            ast.Name,
                        )
                        and value.func.id
                        == "Router"
                    )
                    or (
                        isinstance(
                            value.func,
                            ast.Attribute,
                        )
                        and value.func.attr
                        == "Router"
                    )
                )
            ):
                continue

            for target in targets:
                if isinstance(
                    target,
                    ast.Name,
                ):
                    declared.add(
                        target.id
                    )

    bot_tree = ast.parse(
        Path("bot.py").read_text(
            encoding="utf-8-sig"
        )
    )
    registered = {
        call.args[0].id
        for call in ast.walk(bot_tree)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
        and call.func.attr
        == "include_router"
        and call.args
        and isinstance(
            call.args[0],
            ast.Name,
        )
    }

    assert not (
        declared - registered
    ), sorted(declared - registered)


def test_event_helpers_are_reachable_or_decorated():
    modules = load_modules()
    calls = defaultdict(list)

    for path, (_, tree) in modules.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Name,
                )
            ):
                calls[node.func.id].append(
                    (str(path), node.lineno)
                )

    allowed_compatibility_helpers = {
        "send_specialist_cabinet_message",
    }
    unreachable = []

    for path, (_, tree) in modules.items():
        for node in tree.body:
            if not (
                isinstance(
                    node,
                    ast.AsyncFunctionDef,
                )
                and not node.decorator_list
            ):
                continue

            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            annotations = [
                (
                    ast.unparse(
                        argument.annotation
                    )
                    if argument.annotation
                    else ""
                )
                for argument in arguments
            ]

            if not any(
                (
                    "CallbackQuery"
                    in annotation
                    or annotation == "Message"
                )
                for annotation in annotations
            ):
                continue

            if (
                not calls[node.name]
                and node.name
                not in allowed_compatibility_helpers
            ):
                unreachable.append(
                    (
                        str(path),
                        node.lineno,
                        node.name,
                    )
                )

    assert not unreachable, unreachable


def test_static_and_dynamic_buttons_have_routes():
    modules = load_modules()
    exact, prefixes = callback_routes(
        modules
    )
    missing = []

    for path, (_, tree) in modules.items():
        constants = string_constants(tree)

        for node in ast.walk(tree):
            if not isinstance(
                node,
                ast.Call,
            ):
                continue

            function_name = (
                node.func.id
                if isinstance(
                    node.func,
                    ast.Name,
                )
                else (
                    node.func.attr
                    if isinstance(
                        node.func,
                        ast.Attribute,
                    )
                    else ""
                )
            )

            if (
                function_name
                != "InlineKeyboardButton"
            ):
                continue

            keyword = next(
                (
                    item
                    for item in node.keywords
                    if item.arg
                    == "callback_data"
                ),
                None,
            )
            if keyword is None:
                continue

            value = callback_prefix(
                keyword.value,
                constants,
            )
            if value is None:
                continue

            kind, callback = value

            if kind == "exact":
                covered = (
                    callback in exact
                    or any(
                        callback.startswith(
                            prefix
                        )
                        for prefix in prefixes
                    )
                )
            else:
                covered = (
                    any(
                        item.startswith(
                            callback
                        )
                        for item in exact
                    )
                    or any(
                        (
                            callback.startswith(
                                prefix
                            )
                            or prefix.startswith(
                                callback
                            )
                        )
                        for prefix in prefixes
                    )
                )

            if not covered:
                missing.append(
                    (
                        str(path),
                        node.lineno,
                        callback,
                    )
                )

    assert not missing, missing


def test_unconditional_exact_callbacks_are_unique():
    modules = load_modules()
    exact, _ = callback_routes(
        modules
    )
    duplicates = {}

    for callback, owners in exact.items():
        unique_owners = list(
            dict.fromkeys(owners)
        )

        if len(unique_owners) < 2:
            continue

        if any(
            has_state_filter
            for _, _, has_state_filter
            in unique_owners
        ):
            continue

        duplicates[callback] = (
            unique_owners
        )

    assert not duplicates, duplicates


def test_aiogram_event_models_are_not_mutated():
    modules = load_modules()
    mutations = []

    for path, (_, tree) in modules.items():
        for function in ast.walk(tree):
            if not isinstance(
                function,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):
                continue

            event_arguments = {
                argument.arg
                for argument in [
                    *function.args.posonlyargs,
                    *function.args.args,
                    *function.args.kwonlyargs,
                ]
                if argument.annotation
                and (
                    "CallbackQuery"
                    in ast.unparse(
                        argument.annotation
                    )
                    or ast.unparse(
                        argument.annotation
                    )
                    == "Message"
                )
            }

            for node in ast.walk(
                function
            ):
                if isinstance(
                    node,
                    ast.Assign,
                ):
                    targets = node.targets
                elif isinstance(
                    node,
                    (
                        ast.AnnAssign,
                        ast.AugAssign,
                    ),
                ):
                    targets = [node.target]
                else:
                    continue

                for target in targets:
                    for item in ast.walk(
                        target
                    ):
                        if (
                            isinstance(
                                item,
                                ast.Attribute,
                            )
                            and isinstance(
                                item.value,
                                ast.Name,
                            )
                            and item.value.id
                            in event_arguments
                        ):
                            mutations.append(
                                (
                                    str(path),
                                    item.lineno,
                                    ast.unparse(
                                        item
                                    ),
                                )
                            )

    assert not mutations, mutations
