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


def _unregistered_router_symbols(modules, bot_tree):
    """Resolve imported aliases and nested router inclusion from the bot root."""
    trees = {
        ".".join(path.with_suffix("").parts).removesuffix(".__init__"): tree
        for path, (_, tree) in modules.items()
    }
    trees["bot"] = bot_tree
    aliases, factories, declared = {}, {}, set()

    def router_call(node):
        return isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id == "Router"
            or isinstance(node.func, ast.Attribute) and node.func.attr == "Router"
        )

    def targets(node):
        if isinstance(node, ast.Assign):
            return node.targets
        if isinstance(node, ast.AnnAssign):
            return [node.target]
        return []

    for module, tree in trees.items():
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for item in node.names:
                    aliases[module + "." + (item.asname or item.name)] = (
                        (node.module or "") + "." + item.name
                    )
            elif isinstance(node, ast.Import):
                for item in node.names:
                    aliases[module + "." + (item.asname or item.name.split(".")[0])] = (
                        item.name if item.asname else item.name.split(".")[0]
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = {
                    target.id
                    for child in node.body
                    if router_call(getattr(child, "value", None))
                    for target in targets(child) if isinstance(target, ast.Name)
                }
                returned = {
                    child.value.id for child in node.body
                    if isinstance(child, ast.Return)
                    and isinstance(child.value, ast.Name)
                    and child.value.id in local
                }
                if len(returned) == 1:
                    factories[module + "." + node.name] = (
                        module + "." + node.name + "." + next(iter(returned))
                    )

    def resolve(module, node):
        if isinstance(node, ast.Name):
            symbol = module + "." + node.id
        elif isinstance(node, ast.Attribute):
            base = resolve(module, node.value)
            symbol = base + "." + node.attr if base else None
        elif isinstance(node, ast.Call):
            return factories.get(resolve(module, node.func))
        else:
            return None
        seen = set()
        while symbol in aliases and symbol not in seen:
            seen.add(symbol)
            symbol = aliases[symbol]
        return symbol

    for module, tree in trees.items():
        for node in tree.body:
            for target in targets(node):
                if not isinstance(target, ast.Name):
                    continue
                symbol = module + "." + target.id
                value = getattr(node, "value", None)
                if router_call(value):
                    declared.add(symbol)
                elif isinstance(value, ast.Call):
                    factory = resolve(module, value)
                    if factory:
                        aliases[symbol] = factory
                        declared.add(factory)
                elif isinstance(value, (ast.Name, ast.Attribute)):
                    resolved = resolve(module, value)
                    if resolved:
                        aliases[symbol] = resolved

    edges, roots, unresolved = defaultdict(set), set(), set()
    for module, tree in trees.items():
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"include_router", "include_routers"}
            ):
                continue
            parent = resolve(module, node.func.value)
            for argument in node.args:
                child = resolve(module, argument)
                if child not in declared:
                    unresolved.add(f"{module}:{node.lineno}: unresolved child {child}")
                if parent == "bot.dp":
                    roots.add(child)
                elif parent in declared:
                    edges[parent].add(child)
                else:
                    unresolved.add(f"{module}:{node.lineno}: unresolved parent {parent}")

    reachable, pending = set(), list(roots)
    while pending:
        symbol = pending.pop()
        if symbol in reachable:
            continue
        reachable.add(symbol)
        pending.extend(edges[symbol])
    return sorted((declared - reachable) | unresolved)


def test_all_declared_routers_are_registered():
    modules = load_modules()
    bot_tree = ast.parse(Path("bot.py").read_text(encoding="utf-8-sig"))
    missing = _unregistered_router_symbols(modules, bot_tree)
    assert not missing, missing


def test_router_graph_detects_detached_router_with_same_local_name():
    modules = {
        Path("handlers/a.py"): ("", ast.parse("router = Router()\n")),
        Path("handlers/b.py"): ("", ast.parse("router = Router()\n")),
    }
    bot_tree = ast.parse(
        "from handlers.a import router as first\n"
        "dp.include_router(first)\n"
    )
    assert _unregistered_router_symbols(modules, bot_tree) == ["handlers.b.router"]


def test_router_graph_follows_nested_import_aliases():
    modules = {
        Path("handlers/child.py"): ("", ast.parse("date_router = Router()\n")),
        Path("handlers/parent.py"): ("", ast.parse(
            "from handlers.child import date_router as dates\n"
            "router = Router()\n"
            "router.include_router(dates)\n"
        )),
    }
    bot_tree = ast.parse(
        "from handlers.parent import router as parent\n"
        "dp.include_router(parent)\n"
    )
    assert _unregistered_router_symbols(modules, bot_tree) == []


def test_router_graph_checks_exported_factory_router():
    modules = {
        Path("handlers/factory.py"): ("", ast.parse(
            "def build_router():\n"
            "    router = Router()\n"
            "    return router\n"
            "exported = build_router()\n"
        )),
    }
    bot_tree = ast.parse(
        "from handlers.factory import exported as root\n"
        "dp.include_router(root)\n"
    )
    assert _unregistered_router_symbols(modules, bot_tree) == []
    assert _unregistered_router_symbols(modules, ast.parse("")) == [
        "handlers.factory.build_router.router",
    ]


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

    # Programmatic aiogram observer registration also makes a handler reachable.
    for path, (_, tree) in modules.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in {
                    "message", "callback_query", "inline_query",
                    "chosen_inline_result", "edited_message", "channel_post",
                }
                and node.args
                and isinstance(node.args[0], ast.Name)
            ):
                calls[node.args[0].id].append((str(path), node.lineno))

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
