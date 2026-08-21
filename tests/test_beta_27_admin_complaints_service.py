from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_complaints import (
    AdminComplaintsAccessError,
    AdminComplaintsService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.requested_ids = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.requested_ids.append(
            platform_user_id
        )
        return self.user


class FakeModeration:
    def __init__(self):
        self.roles_by_user = {}
        self.role_calls = []
        self.calls = []

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

    def __getattr__(self, name):
        async def method(**kwargs):
            self.calls.append(
                (name, kwargs)
            )
            return name

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
    moderation.roles_by_user[
        actor_id
    ] = set(roles)

    service = AdminComplaintsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    return (
        service,
        users,
        moderation,
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
    service = AdminComplaintsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    with pytest.raises(
        AdminComplaintsAccessError,
        match="access denied",
    ):
        await service.open_complaints_queue(
            platform_user_id=123,
            statuses={"open"},
        )

    assert not moderation.role_calls
    assert not moderation.calls


@pytest.mark.asyncio
async def test_unrelated_role_fails_closed():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(
        AdminComplaintsAccessError
    ):
        await service.get_complaint_card(
            platform_user_id=123,
            complaint_id=uuid4(),
        )

    assert not moderation.calls


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
        actor_id,
        tenant_id,
    ) = build_service(roles=roles)

    await service.open_complaints_queue(
        platform_user_id=123,
        statuses={"new", "open"},
        page=2,
        page_size=4,
    )

    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        }
    ]
    assert moderation.calls == [
        (
            "open_complaints_queue",
            {
                "moderator_user_id": actor_id,
                "tenant_id": tenant_id,
                "statuses": {"new", "open"},
                "page": 2,
                "page_size": 4,
            },
        )
    ]


@pytest.mark.asyncio
async def test_complaint_card_is_tenant_bound():
    (
        service,
        _,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})
    complaint_id = uuid4()

    result = await service.get_complaint_card(
        platform_user_id=123,
        complaint_id=complaint_id,
    )

    assert result == (
        "get_moderator_complaint_card"
    )
    assert moderation.calls == [
        (
            "get_moderator_complaint_card",
            {
                "moderator_user_id": actor_id,
                "tenant_id": tenant_id,
                "complaint_id": complaint_id,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
        "actor_argument",
    ),
    [
        (
            "take_complaint",
            {
                "complaint_id": uuid4(),
            },
            "take_complaint",
            "moderator_user_id",
        ),
        (
            "escalate_complaint",
            {
                "complaint_id": uuid4(),
                "reason": "Needs Admin review",
            },
            "escalate_complaint_to_admin",
            "moderator_user_id",
        ),
        (
            "resolve_complaint",
            {
                "complaint_id": uuid4(),
                "status": "resolved",
                "reason": "Resolved correctly",
            },
            "resolve_complaint",
            "admin_user_id",
        ),
    ],
)
async def test_complaint_actions_are_tenant_bound(
    service_method,
    arguments,
    moderation_method,
    actor_argument,
):
    (
        service,
        _,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

    action = await getattr(
        service,
        service_method,
    )(
        platform_user_id=123,
        **arguments,
    )

    assert action.actor.user_id == actor_id
    assert action.actor.tenant_id == tenant_id
    assert action.result == moderation_method

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs[actor_argument] == actor_id
    assert kwargs["tenant_id"] == tenant_id

    for key, value in arguments.items():
        assert kwargs[key] == value


@pytest.mark.asyncio
async def test_impersonation_requires_super_admin():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"admin"})

    with pytest.raises(
        AdminComplaintsAccessError
    ):
        await (
            service
            .open_impersonated_complaints_queue(
                platform_user_id=123,
                effective_moderator_user_id=(
                    uuid4()
                ),
                statuses={"open"},
            )
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_impersonation_rejects_unrelated_target():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"super_admin"})
    effective_user_id = uuid4()
    moderation.roles_by_user[
        effective_user_id
    ] = {"support"}

    with pytest.raises(
        AdminComplaintsAccessError,
        match="Impersonated",
    ):
        await (
            service
            .get_impersonated_complaint_card(
                platform_user_id=123,
                effective_moderator_user_id=(
                    effective_user_id
                ),
                complaint_id=uuid4(),
            )
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effective_role",
    [
        "admin",
        "moderator",
    ],
)
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
    ),
    [
        (
            "open_impersonated_complaints_queue",
            {
                "statuses": {"in_review"},
                "page": 3,
                "page_size": 5,
            },
            "open_complaints_queue",
        ),
        (
            "get_impersonated_complaint_card",
            {
                "complaint_id": uuid4(),
            },
            "get_moderator_complaint_card",
        ),
    ],
)
async def test_impersonated_reads_are_tenant_bound(
    service_method,
    arguments,
    moderation_method,
    effective_role,
):
    (
        service,
        _,
        moderation,
        _,
        tenant_id,
    ) = build_service(roles={"super_admin"})
    effective_user_id = uuid4()
    moderation.roles_by_user[
        effective_user_id
    ] = {effective_role}

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=123,
        effective_moderator_user_id=(
            effective_user_id
        ),
        **arguments,
    )

    assert result == moderation_method

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert (
        kwargs["moderator_user_id"]
        == effective_user_id
    )
    assert kwargs["tenant_id"] == tenant_id

    assert moderation.role_calls[-1] == {
        "user_id": effective_user_id,
        "tenant_id": tenant_id,
    }

def test_complaint_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_complaints.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "open_complaints_queue": (
            "open_complaints_queue"
        ),
        "show_complaint": (
            "get_complaint_card"
        ),
        "take_complaint_from_queue": (
            "take_complaint"
        ),
        "receive_complaint_resolution_reason": (
            "resolve_complaint"
        ),
        "confirm_complaint_admin_escalation": (
            "escalate_complaint"
        ),
        "super_admin_read_only_moderator_complaints": (
            "open_impersonated_complaints_queue"
        ),
        "super_admin_read_only_moderator_complaint": (
            "get_impersonated_complaint_card"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        assert "AdminComplaintsService(" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block


def test_complaint_reason_rule_is_service_enforced():
    import inspect

    from services.moderation import (
        ModerationService,
    )

    source = inspect.getsource(
        ModerationService._require_reason
    )

    assert "len(normalized) < 3" in source
    assert "raise ModerationError" in source
