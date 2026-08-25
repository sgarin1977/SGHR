import ast

import pytest
from pathlib import Path


SERVICE_PATH = Path(
    "services/geo_search.py"
)
REPOSITORY_PATH = Path(
    "database/repositories/search.py"
)


def get_method(
    source,
    class_name,
    method_name,
):
    tree = ast.parse(source)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == class_name
    )

    return next(
        node
        for node in class_node.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == method_name
    )


def test_search_enrichment_uses_one_batch_call():
    source = SERVICE_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_method(
        source,
        "GeoSearchService",
        "_enrich_search_results",
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )

    awaits = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Await)
    ]

    assert (
        "get_search_enrichment_by_cabinet_ids"
        in block
    )
    assert len(awaits) == 1

    for forbidden in (
        "get_city_name(",
        "get_category_name(",
        "get_profession_name(",
        "get_language_codes_for_specialist(",
    ):
        assert forbidden not in block


def test_search_enrichment_repository_is_batched():
    source = REPOSITORY_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_method(
        source,
        "SpecialistSearchRepository",
        (
            "get_search_enrichment_"
            "by_cabinet_ids"
        ),
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )

    execute_calls = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(
            item.func,
            ast.Attribute,
        )
        and item.func.attr == "execute"
    ]

    assert len(execute_calls) == 1
    assert ".in_(" in block
    assert "SpecialistLanguage" in block
    assert "ProfessionalCabinet" in block


@pytest.mark.asyncio
async def test_search_enrichment_maps_batch_data():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.geo_search import (
        GeoSearchService,
    )

    cabinet_id = uuid4()
    specialist_id = uuid4()
    calls = []

    class FakeRepository:
        async def get_search_enrichment_by_cabinet_ids(
            self,
            cabinet_ids,
            language,
        ):
            calls.append(
                (
                    cabinet_ids,
                    language,
                )
            )
            return {
                cabinet_id: {
                    "city_name": "Kyiv",
                    "category_name": "Repairs",
                    "profession_name": "Plumber",
                    "languages": [
                        "en",
                        "uk",
                    ],
                }
            }

    service = object.__new__(
        GeoSearchService
    )
    service.repository = FakeRepository()

    result = SimpleNamespace(
        specialist=SimpleNamespace(
            id=specialist_id,
        ),
        professional_cabinet=(
            SimpleNamespace(
                id=cabinet_id,
            )
        ),
        city_name=None,
        category_name=None,
        profession_name=None,
        languages=[],
    )

    returned = await (
        service._enrich_search_results(
            [result],
            language="en",
        )
    )

    assert returned == [result]
    assert calls == [
        (
            [cabinet_id],
            "en",
        )
    ]
    assert result.city_name == "Kyiv"
    assert result.category_name == "Repairs"
    assert result.profession_name == "Plumber"
    assert result.languages == [
        "en",
        "uk",
    ]
