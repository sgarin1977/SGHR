import ast
from pathlib import Path


def test_selected_card_back_route_is_registered():
    source = Path(
        "handlers/search.py"
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
        == "back_to_selected_specialist_card"
    )

    decorators = [
        ast.get_source_segment(
            source,
            decorator,
        )
        or ""
        for decorator
        in function.decorator_list
    ]

    assert len(decorators) == 1
    assert (
        "search_router.callback_query"
        in decorators[0]
    )
    assert (
        "search_result_back_to_card:"
        in decorators[0]
    )


def test_review_keyboard_uses_registered_back_route():
    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    keyboard = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == "public_reviews_keyboard"
    )
    block = (
        ast.get_source_segment(
            source,
            keyboard,
        )
        or ""
    )

    assert (
        "search_result_back_to_card:"
        in block
    )
