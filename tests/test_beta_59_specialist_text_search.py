from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.specialist import (
    SpecialistRepository,
)


class FakeSession:
    def __init__(self):
        self.statement = None

    async def execute(
        self,
        statement,
    ):
        self.statement = statement
        return SimpleNamespace(
            scalar_one_or_none=(
                lambda: None
            ),
        )


@pytest.mark.asyncio
async def test_city_text_search_excludes_null_names():
    session = FakeSession()
    repository = SpecialistRepository(
        session
    )

    result = await (
        repository.find_active_city_in_text(
            "сантехнік"
        )
    )

    assert result is None
    assert session.statement is not None

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized_sql = " ".join(
        sql.split()
    ).lower()

    assert (
        normalized_sql.count(
            " is not null"
        )
        == 8
    )
    assert (
        normalized_sql.count(
            "length(trim("
        )
        == 8
    )


def test_text_search_repository_keeps_all_city_names():
    source = open(
        "database/repositories/specialist.py",
        encoding="utf-8",
    ).read()

    for field in (
        "City.name",
        "City.name_ru",
        "City.name_en",
        "City.name_pt",
        "City.name_uk",
        "City.name_pl",
        "City.name_de",
        "City.name_nl",
    ):
        assert field in source
