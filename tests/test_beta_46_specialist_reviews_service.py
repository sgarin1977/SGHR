from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_cabinets import (
    SpecialistCabinetsProfileNotFoundError,
)
from services.specialist_reviews import (
    SpecialistReviewsService,
)


class FakeCabinets:
    def __init__(
        self,
        actor=None,
        error=None,
    ):
        self.actor = actor
        self.error = error
        self.platform_user_ids = []

    async def require_actor(
        self,
        *,
        platform_user_id,
    ):
        self.platform_user_ids.append(
            platform_user_id
        )

        if self.error:
            raise self.error

        return self.actor


class FakeReviews:
    def __init__(self):
        self.calls = []

    async def list_public_reviews_for_viewer(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return "review-page"


def actor():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        specialist_id=uuid4(),
        language="uk",
    )


@pytest.mark.asyncio
async def test_list_reviews_uses_actor_scope():
    current_actor = actor()
    cabinets = FakeCabinets(current_actor)
    reviews = FakeReviews()
    service = SpecialistReviewsService(
        object(),
        cabinets=cabinets,
        reviews=reviews,
    )

    result = await service.list_reviews(
        platform_user_id=123,
        page=2,
        page_size=7,
    )

    assert result.actor is current_actor
    assert result.result == "review-page"
    assert cabinets.platform_user_ids == [123]
    assert reviews.calls == [
        {
            "tenant_id": (
                current_actor.tenant_id
            ),
            "specialist_id": (
                current_actor.specialist_id
            ),
            "viewer_user_id": (
                current_actor.user_id
            ),
            "page": 2,
            "page_size": 7,
            "source": "specialist_cabinet",
        }
    ]


@pytest.mark.asyncio
async def test_list_reviews_normalizes_pagination():
    cabinets = FakeCabinets(actor())
    reviews = FakeReviews()
    service = SpecialistReviewsService(
        object(),
        cabinets=cabinets,
        reviews=reviews,
    )

    await service.list_reviews(
        platform_user_id=456,
        page=-5,
        page_size=0,
    )

    assert reviews.calls[0]["page"] == 0
    assert reviews.calls[0]["page_size"] == 1


@pytest.mark.asyncio
async def test_missing_profile_fails_before_review_read():
    reviews = FakeReviews()
    service = SpecialistReviewsService(
        object(),
        cabinets=FakeCabinets(
            error=(
                SpecialistCabinetsProfileNotFoundError(
                    "Specialist profile not found."
                )
            ),
        ),
        reviews=reviews,
    )

    with pytest.raises(
        SpecialistCabinetsProfileNotFoundError
    ):
        await service.list_reviews(
            platform_user_id=789,
        )

    assert reviews.calls == []

def test_reviews_renderer_uses_application_service():
    import ast

    source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "render_specialist_reviews_cabinet"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
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

    assert (
        "SpecialistReviewsService"
        in called_names
    )
    assert "list_reviews" in called_methods
    assert "ReviewRepository" not in (
        called_names
    )
    assert "ReviewService" not in called_names
    assert (
        "get_current_specialist_for_telegram"
        not in called_names
    )

    block = ast.get_source_segment(
        source,
        node,
    )

    assert "tenant_id=" not in block
    assert "specialist_id=" not in block
    assert "viewer_user_id=" not in block
