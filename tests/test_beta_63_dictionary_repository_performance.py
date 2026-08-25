import ast
from pathlib import Path
from uuid import uuid4

import pytest


SOURCE_PATH = Path(
    "database/repositories/dictionaries.py"
)


def get_async_function(
    tree: ast.Module,
    function_name: str,
) -> ast.AsyncFunctionDef:
    repositories = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "DictionaryRepository"
    ]

    assert len(repositories) == 1, (
        "DictionaryRepository: expected 1, "
        f"found {len(repositories)}"
    )

    matches = [
        node
        for node in repositories[0].body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name == function_name
    ]

    assert len(matches) == 1, (
        f"{function_name}: expected 1, "
        f"found {len(matches)}"
    )
    return matches[0]


def loop_calls_method(
    function: ast.AsyncFunctionDef,
    method_name: str,
) -> bool:
    for loop in ast.walk(function):
        if not isinstance(
            loop,
            (ast.For, ast.AsyncFor),
        ):
            continue

        for node in ast.walk(loop):
            if not isinstance(node, ast.Call):
                continue

            if not isinstance(
                node.func,
                ast.Attribute,
            ):
                continue

            if node.func.attr == method_name:
                return True

    return False


@pytest.mark.parametrize(
    (
        "function_name",
        "forbidden_method",
    ),
    [
        (
            "find_categories_by_title_for_admin",
            "get_category_for_admin",
        ),
        (
            "list_professions_by_category_for_admin",
            "get_profession_for_admin",
        ),
        (
            "find_professions_by_title_for_admin",
            "get_profession_for_admin",
        ),
    ],
)
def test_dictionary_lists_do_not_fetch_rows_one_by_one(
    function_name,
    forbidden_method,
):
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)
    function = get_async_function(
        tree,
        function_name,
    )

    assert not loop_calls_method(
        function,
        forbidden_method,
    ), (
        f"{function_name} performs an N+1 query "
        f"through {forbidden_method}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "method_name",
        "kwargs",
    ),
    [
        (
            "find_categories_by_title_for_admin",
            {
                "title": "Plumbing",
                "limit": 10,
            },
        ),
        (
            "list_professions_by_category_for_admin",
            {
                "category_id": uuid4(),
                "limit": 500,
                "offset": 0,
            },
        ),
        (
            "find_professions_by_title_for_admin",
            {
                "title": "Plumber",
                "limit": 10,
            },
        ),
    ],
)
async def test_dictionary_list_operation_executes_one_statement(
    method_name,
    kwargs,
):
    from database.repositories.dictionaries import (
        DictionaryRepository,
    )

    class EmptyResult:
        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return EmptyResult()

    session = FakeSession()
    repository = DictionaryRepository(session)

    result = await getattr(
        repository,
        method_name,
    )(**kwargs)

    assert result == []
    assert len(session.statements) == 1
