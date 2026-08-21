from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_reviews import (
    AdminReviewsAccessError,
    AdminReviewsDecisionError,
    AdminReviewsService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)
        return self.user


class FakeModeration:
    def __init__(self):
        self.roles_by_user = {}
        self.role_calls = []

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id,
    ):
        self.role_calls.append(
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            }
        )
        return set(
            self.roles_by_user.get(
                user_id,
                set(),
            )
        )


class FakeReviews:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def __getattr__(self, name):
        async def method(**kwargs):
            self.calls.append((name, kwargs))
            return self.responses.get(name, name)

        return method


def build_service(*, roles):
    actor_id = uuid4()
    tenant_id = uuid4()
    users = FakeUsers(
        SimpleNamespace(
            id=actor_id,
            tenant_id=tenant_id,
        )
    )
    moderation = FakeModeration()
    reviews = FakeReviews()

    moderation.roles_by_user[
        actor_id
    ] = set(roles)

    service = AdminReviewsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        reviews=reviews,
    )

    return (
        service,
        users,
        moderation,
        reviews,
        actor_id,
        tenant_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        SimpleNamespace(
            id=uuid4(),
            tenant_id=None,
        ),
    ],
)
async def test_missing_actor_fails_closed(user):
    users = FakeUsers(user)
    moderation = FakeModeration()
    reviews = FakeReviews()
    service = AdminReviewsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        reviews=reviews,
    )

    with pytest.raises(
        AdminReviewsAccessError,
        match="access denied",
    ):
        await service.list_pending_reviews(
            platform_user_id=123,
        )

    assert not moderation.role_calls
    assert not reviews.calls


@pytest.mark.asyncio
async def test_unrelated_role_fails_closed():
    (
        service,
        _,
        _,
        reviews,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(
        AdminReviewsAccessError
    ):
        await service.get_pending_review(
            platform_user_id=123,
            review_id=uuid4(),
        )

    assert not reviews.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles",
    [
        {"admin"},
        {"moderator"},
        {"super_admin"},
    ],
)
async def test_moderation_roles_are_allowed(
    roles,
):
    (
        service,
        _,
        moderation,
        reviews,
        actor_id,
        tenant_id,
    ) = build_service(roles=roles)

    expected = [object()]
    reviews.responses[
        "list_pending_reviews"
    ] = expected

    result = await service.list_pending_reviews(
        platform_user_id=123,
        page=2,
        page_size=4,
    )

    assert result is expected
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        }
    ]
    assert reviews.calls == [
        (
            "list_pending_reviews",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "page": 2,
                "page_size": 4,
            },
        )
    ]


@pytest.mark.asyncio
async def test_review_card_is_tenant_bound():
    (
        service,
        _,
        _,
        reviews,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

    review_id = uuid4()
    expected = object()
    reviews.responses[
        "get_pending_review_for_moderation"
    ] = expected

    result = await service.get_pending_review(
        platform_user_id=123,
        review_id=review_id,
        language="uk",
    )

    assert result is expected
    assert reviews.calls == [
        (
            (
                "get_pending_review_"
                "for_moderation"
            ),
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "review_id": review_id,
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        "published",
        "hidden",
    ],
)
async def test_moderation_is_tenant_bound(
    status,
):
    (
        service,
        _,
        _,
        reviews,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

    review_id = uuid4()
    expected = object()
    reviews.responses[
        "moderate_review"
    ] = expected

    action = await service.moderate_review(
        platform_user_id=123,
        review_id=review_id,
        status=status,
        reason="Valid moderation reason",
    )

    assert action.actor.user_id == actor_id
    assert action.actor.tenant_id == tenant_id
    assert action.result is expected
    assert reviews.calls == [
        (
            "moderate_review",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "review_id": review_id,
                "status": status,
                "reason": (
                    "Valid moderation reason"
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_unknown_decision_is_rejected():
    (
        service,
        users,
        moderation,
        reviews,
        _,
        _,
    ) = build_service(roles={"moderator"})

    with pytest.raises(
        AdminReviewsDecisionError,
        match="Unsupported",
    ):
        await service.moderate_review(
            platform_user_id=123,
            review_id=uuid4(),
            status="deleted",
            reason="Invalid decision",
        )

    assert not users.calls
    assert not moderation.role_calls
    assert not reviews.calls


@pytest.mark.asyncio
async def test_impersonation_requires_super_admin():
    (
        service,
        _,
        moderation,
        reviews,
        _,
        _,
    ) = build_service(roles={"admin"})

    with pytest.raises(
        AdminReviewsAccessError
    ):
        await (
            service
            .list_impersonated_pending_reviews(
                platform_user_id=123,
                effective_moderator_user_id=(
                    uuid4()
                ),
            )
        )

    assert len(moderation.role_calls) == 1
    assert not reviews.calls


@pytest.mark.asyncio
async def test_impersonation_rejects_wrong_target():
    (
        service,
        _,
        moderation,
        reviews,
        _,
        _,
    ) = build_service(roles={"super_admin"})

    target_id = uuid4()
    moderation.roles_by_user[
        target_id
    ] = {"support"}

    with pytest.raises(
        AdminReviewsAccessError
    ):
        await (
            service
            .list_impersonated_pending_reviews(
                platform_user_id=123,
                effective_moderator_user_id=(
                    target_id
                ),
            )
        )

    assert len(moderation.role_calls) == 2
    assert not reviews.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_role",
    [
        "admin",
        "moderator",
    ],
)
async def test_impersonated_queue_is_tenant_bound(
    target_role,
):
    (
        service,
        _,
        moderation,
        reviews,
        _,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    target_id = uuid4()
    moderation.roles_by_user[
        target_id
    ] = {target_role}
    expected = [object()]
    reviews.responses[
        "list_pending_reviews"
    ] = expected

    result = (
        await service
        .list_impersonated_pending_reviews(
            platform_user_id=123,
            effective_moderator_user_id=(
                target_id
            ),
            page=3,
            page_size=5,
        )
    )

    assert result is expected
    assert reviews.calls == [
        (
            "list_pending_reviews",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": target_id,
                "page": 3,
                "page_size": 5,
            },
        )
    ]


@pytest.mark.asyncio
async def test_impersonated_card_is_tenant_bound():
    (
        service,
        _,
        moderation,
        reviews,
        _,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    target_id = uuid4()
    review_id = uuid4()
    expected = object()

    moderation.roles_by_user[
        target_id
    ] = {"moderator"}
    reviews.responses[
        "get_pending_review_for_moderation"
    ] = expected

    result = (
        await service
        .get_impersonated_pending_review(
            platform_user_id=123,
            effective_moderator_user_id=(
                target_id
            ),
            review_id=review_id,
            language="pt",
        )
    )

    assert result is expected
    assert reviews.calls == [
        (
            (
                "get_pending_review_"
                "for_moderation"
            ),
            {
                "tenant_id": tenant_id,
                "moderator_user_id": target_id,
                "review_id": review_id,
                "language": "pt",
            },
        )
    ]


def test_review_read_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_reviews.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "open_pending_reviews_page": (
            "list_pending_reviews"
        ),
        "show_review": (
            "get_pending_review"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        function_source = ast.get_source_segment(
            source,
            node,
        )
        direct_calls = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        assert (
            "AdminReviewsService"
            in direct_calls
        )
        assert service_method in function_source
        assert (
            "get_admin_user_context"
            not in direct_calls
        )
        assert (
            "ReviewRepository"
            not in direct_calls
        )
        assert (
            "ReviewService"
            not in direct_calls
        )


def test_impersonated_review_read_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_reviews.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        (
            "super_admin_read_only_"
            "moderator_reviews"
        ): (
            "list_impersonated_pending_reviews"
        ),
        (
            "show_super_admin_read_only_review"
        ): (
            "get_impersonated_pending_review"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        function_source = ast.get_source_segment(
            source,
            node,
        )
        direct_calls = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        assert (
            "AdminReviewsService"
            in direct_calls
        )
        assert service_method in function_source
        assert (
            "get_admin_user_context"
            not in direct_calls
        )
        assert (
            "ReviewRepository"
            not in direct_calls
        )
        assert (
            "ReviewService"
            not in direct_calls
        )


def test_review_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_reviews.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = (
        "approve_pending_review",
        "receive_review_moderation_reason",
    )

    for function_name in functions:
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        function_source = ast.get_source_segment(
            source,
            node,
        )
        direct_calls = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        assert (
            "AdminReviewsService"
            in direct_calls
        )
        assert (
            "moderate_review"
            in function_source
        )
        assert (
            "get_admin_user_context"
            not in direct_calls
        )
        assert (
            "ReviewRepository"
            not in direct_calls
        )
        assert (
            "ReviewService"
            not in direct_calls
        )


def test_all_review_handlers_are_application_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_reviews.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    review_functions = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and "review" in node.name.lower()
    ]

    assert review_functions

    forbidden_calls = {
        "get_admin_user_context",
        "ReviewRepository",
        "ReviewService",
        "ModerationRepository",
        "ModerationService",
        "UserService",
    }

    for node in review_functions:
        direct_calls = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }
        found = (
            direct_calls
            & forbidden_calls
        )

        assert not found, (
            node.name,
            sorted(found),
        )
