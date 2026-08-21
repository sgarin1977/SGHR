from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_search_reviews import (
    UserSearchReviewsAccessError,
    UserSearchReviewsSelectionError,
    UserSearchReviewsService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
)


class FakeSettings:
    def __init__(
        self,
        *,
        context=None,
        error=None,
    ):
        self.context = context
        self.error = error
        self.calls = []

    async def get_context(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.context


class FakeReviews:
    def __init__(self):
        self.calls = []
        self.page = SimpleNamespace(
            reviews=[],
            page=0,
            has_previous=False,
            has_next=False,
        )

    async def list_public_reviews_for_viewer(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.page


def actor_context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    context=None,
    error=None,
):
    settings = FakeSettings(
        context=context,
        error=error,
    )
    reviews = FakeReviews()
    service = UserSearchReviewsService(
        object(),
        settings=settings,
        reviews=reviews,
    )
    return service, settings, reviews


@pytest.mark.asyncio
async def test_reviews_require_actor():
    service, _, reviews = build_service(
        error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        UserSearchReviewsAccessError,
        match="viewer",
    ):
        await service.open_reviews(
            platform_user_id=123,
            specialist_id=uuid4(),
        )

    assert reviews.calls == []


@pytest.mark.asyncio
async def test_reviews_use_actor_scope():
    context = actor_context()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    service, settings, reviews = (
        build_service(context=context)
    )

    result = await service.open_reviews(
        platform_user_id=456,
        specialist_id=str(specialist_id),
        professional_cabinet_id=(
            str(cabinet_id)
        ),
        page=2,
        page_size=7,
    )

    assert result.actor.user_id == (
        context.user_id
    )
    assert result.actor.tenant_id == (
        context.tenant_id
    )
    assert result.actor.language == "uk"
    assert result.review_page is reviews.page

    assert settings.calls == [
        {
            "platform_user_id": 456,
        }
    ]
    assert reviews.calls == [
        {
            "tenant_id": context.tenant_id,
            "specialist_id": specialist_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "viewer_user_id": (
                context.user_id
            ),
            "page": 2,
            "page_size": 7,
        }
    ]


@pytest.mark.asyncio
async def test_negative_reviews_page_is_normalized():
    service, _, reviews = build_service(
        context=actor_context()
    )

    await service.open_reviews(
        platform_user_id=123,
        specialist_id=uuid4(),
        page=-4,
        page_size=99,
    )

    assert reviews.calls[0]["page"] == 0
    assert reviews.calls[0]["page_size"] == 10


@pytest.mark.asyncio
async def test_invalid_reviews_id_fails_closed():
    service, _, reviews = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserSearchReviewsSelectionError,
        match="Invalid specialist",
    ):
        await service.open_reviews(
            platform_user_id=123,
            specialist_id="invalid",
        )

    assert reviews.calls == []


@pytest.mark.asyncio
async def test_invalid_reviews_page_fails_closed():
    service, _, reviews = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserSearchReviewsSelectionError,
        match="Invalid reviews page",
    ):
        await service.open_reviews(
            platform_user_id=123,
            specialist_id=uuid4(),
            page="invalid",
        )

    assert reviews.calls == []


def test_public_review_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "resume_public_reviews_after_auth",
        "render_selected_specialist_reviews",
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

        assert (
            "UserSearchReviewsService"
            in called_names
        )
        assert "open_reviews" in called_methods

        assert not (
            called_names
            & {
                "UUID",
                "ReviewRepository",
                "ReviewService",
                "get_requester_context",
            }
        )

    callback_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "render_selected_specialist_reviews"
    )
    callback_block = ast.get_source_segment(
        source,
        callback_node,
    ) or ""

    assert "store_post_auth_action" in callback_block
    assert 'action="reviews"' in callback_block


@pytest.mark.asyncio
async def test_contact_review_uses_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_search_reviews import (
        UserSearchReviewsActor,
        UserSearchReviewsService,
    )

    actor = UserSearchReviewsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    contact_request_id = uuid4()
    thread_id = uuid4()
    review = SimpleNamespace(id=uuid4())

    class FakeReviewWrites:
        def __init__(self):
            self.calls = []

        async def create_contact_review(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return review

    class FakeChats:
        def __init__(self):
            self.calls = []

        async def archive_thread_after_review(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    reviews = FakeReviewWrites()
    chats = FakeChats()
    service = object.__new__(
        UserSearchReviewsService
    )
    service.reviews = reviews
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        assert platform_user_id == 456
        return actor

    service.require_actor = require_actor

    result = await (
        service.create_contact_review(
            platform_user_id=456,
            contact_request_id=(
                str(contact_request_id)
            ),
            rating="5",
            text="Excellent",
            thread_id=str(thread_id),
        )
    )

    assert result.actor is actor
    assert result.review is review
    assert result.thread_archived is True
    assert reviews.calls == [
        {
            "tenant_id": actor.tenant_id,
            "reviewer_user_id": (
                actor.user_id
            ),
            "contact_request_id": (
                contact_request_id
            ),
            "rating": 5,
            "text": "Excellent",
        }
    ]
    assert chats.calls == [
        {
            "thread_id": thread_id,
            "user_id": actor.user_id,
        }
    ]


@pytest.mark.asyncio
async def test_invalid_contact_review_fails_before_write():
    from uuid import uuid4

    from services.user_search_reviews import (
        UserSearchReviewsActor,
        UserSearchReviewsSelectionError,
        UserSearchReviewsService,
    )

    actor = UserSearchReviewsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )

    class FakeWrites:
        def __init__(self):
            self.calls = []

        async def create_contact_review(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

        async def archive_thread_after_review(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    reviews = FakeWrites()
    chats = FakeWrites()
    service = object.__new__(
        UserSearchReviewsService
    )
    service.reviews = reviews
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        return actor

    service.require_actor = require_actor

    with pytest.raises(
        UserSearchReviewsSelectionError,
        match="Invalid contact request",
    ):
        await service.create_contact_review(
            platform_user_id=123,
            contact_request_id="invalid",
            rating=5,
        )

    assert reviews.calls == []
    assert chats.calls == []


def test_create_review_handler_uses_application_service():
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
        == "create_review_from_state"
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

    assert (
        "UserSearchReviewsService"
        in called_names
    )
    assert (
        "create_contact_review"
        in called_methods
    )

    assert not (
        called_names
        & {
            "UUID",
            "ReviewRepository",
            "ReviewService",
            "ContactChatRepository",
            "ContactChatService",
            "get_requester_context",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "show_review_flow_screen" in block
    assert "review_action.actor.user_id" in block
