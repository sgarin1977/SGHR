from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_audit import (
    AdminAuditAccessError,
    AdminAuditService,
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
    def __init__(self, roles=None):
        self.roles = set(
            roles or set()
        )
        self.roles_by_user = {}
        self.calls = []
        self.regional_page = object()
        self.regional_card = object()
        self.global_page = object()
        self.global_detail = object()

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id,
    ):
        self.calls.append(
            (
                "roles",
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                },
            )
        )
        return self.roles_by_user.get(
            user_id,
            self.roles,
        )

    async def open_admin_audit(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("regional_page", kwargs)
        )
        return self.regional_page

    async def get_admin_audit_card(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("regional_card", kwargs)
        )
        return self.regional_card

    async def open_super_admin_audit(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("global_page", kwargs)
        )
        return self.global_page

    async def get_super_admin_audit_event_detail(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("global_detail", kwargs)
        )
        return self.global_detail


def build_service(
    *,
    user,
    roles=None,
):
    users = FakeUsers(user)
    moderation = FakeModeration(roles)

    service = AdminAuditService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    return service, users, moderation


@pytest.mark.asyncio
async def test_unknown_actor_fails_closed():
    service, _, moderation = build_service(
        user=None,
        roles={"admin"},
    )

    with pytest.raises(
        AdminAuditAccessError
    ):
        await service.open_regional_audit(
            platform_user_id=100,
            target_type="all",
            page=0,
        )

    assert moderation.calls == []


@pytest.mark.asyncio
async def test_actor_without_tenant_fails_closed():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=None,
    )
    service, _, moderation = build_service(
        user=user,
        roles={"admin"},
    )

    with pytest.raises(
        AdminAuditAccessError
    ):
        await service.get_regional_audit_card(
            platform_user_id=100,
            action_id=uuid4(),
        )

    assert moderation.calls == []


@pytest.mark.asyncio
async def test_actor_without_admin_role_fails_closed():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, moderation = build_service(
        user=user,
        roles=set(),
    )

    with pytest.raises(
        AdminAuditAccessError
    ):
        await service.open_super_admin_audit(
            platform_user_id=200,
            target_type="all",
            page=0,
        )

    assert moderation.calls == [
        (
            "roles",
            {
                "user_id": user.id,
                "tenant_id": user.tenant_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_regional_audit_is_tenant_bound():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, moderation = build_service(
        user=user,
        roles={"admin"},
    )
    action_id = uuid4()

    page = await service.open_regional_audit(
        platform_user_id=300,
        target_type="complaint",
        page=2,
        page_size=7,
    )
    card = (
        await service
        .get_regional_audit_card(
            platform_user_id=300,
            action_id=action_id,
        )
    )

    assert page is moderation.regional_page
    assert card is moderation.regional_card
    assert (
        "regional_page",
        {
            "admin_user_id": user.id,
            "tenant_id": user.tenant_id,
            "target_type": "complaint",
            "page": 2,
            "page_size": 7,
        },
    ) in moderation.calls
    assert (
        "regional_card",
        {
            "admin_user_id": user.id,
            "tenant_id": user.tenant_id,
            "action_id": action_id,
        },
    ) in moderation.calls


@pytest.mark.asyncio
async def test_super_admin_audit_uses_global_contract():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, moderation = build_service(
        user=user,
        roles={"super_admin"},
    )
    action_id = uuid4()

    page = (
        await service
        .open_super_admin_audit(
            platform_user_id=400,
            target_type="role",
            page=1,
            page_size=5,
        )
    )
    detail = (
        await service
        .get_super_admin_audit_detail(
            platform_user_id=400,
            action_id=action_id,
        )
    )

    assert page is moderation.global_page
    assert detail is moderation.global_detail
    assert (
        "global_page",
        {
            "admin_user_id": user.id,
            "target_type": "role",
            "page": 1,
            "page_size": 5,
        },
    ) in moderation.calls
    assert (
        "global_detail",
        {
            "admin_user_id": user.id,
            "action_id": action_id,
        },
    ) in moderation.calls


@pytest.mark.asyncio
async def test_role_specific_audit_access_fails_closed():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )

    moderator_service, _, moderator = (
        build_service(
            user=user,
            roles={"moderator"},
        )
    )

    with pytest.raises(
        AdminAuditAccessError
    ):
        await moderator_service.open_regional_audit(
            platform_user_id=500,
            target_type="all",
            page=0,
        )

    assert not any(
        name == "regional_page"
        for name, _ in moderator.calls
    )

    admin_service, _, admin_moderation = (
        build_service(
            user=user,
            roles={"admin"},
        )
    )

    with pytest.raises(
        AdminAuditAccessError
    ):
        await admin_service.open_super_admin_audit(
            platform_user_id=500,
            target_type="all",
            page=0,
        )

    assert not any(
        name == "global_page"
        for name, _ in admin_moderation.calls
    )


def test_regional_audit_handler_uses_application_service():
    source = open(
        "handlers/admin_audit.py",
        encoding="utf-8",
    ).read()
    tree = __import__("ast").parse(source)
    function_names = {
        "open_admin_audit_filter",
        "open_admin_audit_details",
        "open_admin_audit_queue",
    }
    blocks = []

    for node in tree.body:
        if (
            isinstance(
                node,
                (
                    __import__("ast").FunctionDef,
                    __import__("ast").AsyncFunctionDef,
                ),
            )
            and node.name in function_names
        ):
            blocks.append(
                __import__("ast")
                .get_source_segment(
                    source,
                    node,
                )
                or ""
            )

    combined = "\n".join(blocks)

    assert len(blocks) == 3
    assert "AdminAuditService" in combined
    assert "AdminAuditAccessError" in combined
    assert "ModerationRepository(" not in combined
    assert "ModerationService(" not in combined
    assert "get_admin_user_context(" not in combined


def test_global_audit_handler_uses_application_service():
    source = open(
        "handlers/admin_audit.py",
        encoding="utf-8",
    ).read()
    tree = __import__("ast").parse(source)
    function_names = {
        "open_super_admin_audit_filter",
        "open_super_admin_audit_queue",
        "open_super_admin_audit_details",
    }
    blocks = []

    for node in tree.body:
        if (
            isinstance(
                node,
                (
                    __import__("ast").FunctionDef,
                    __import__("ast").AsyncFunctionDef,
                ),
            )
            and node.name in function_names
        ):
            blocks.append(
                __import__("ast")
                .get_source_segment(
                    source,
                    node,
                )
                or ""
            )

    combined = "\n".join(blocks)

    assert len(blocks) == 3
    assert "AdminAuditService" in combined
    assert "AdminAuditAccessError" in combined
    assert "ModerationRepository(" not in combined
    assert "ModerationService(" not in combined
    assert "get_admin_user_context(" not in combined


@pytest.mark.asyncio
async def test_impersonated_audit_requires_super_admin_actor():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    effective_admin_id = uuid4()
    service, _, moderation = build_service(
        user=user,
        roles={"admin"},
    )
    moderation.roles_by_user[
        effective_admin_id
    ] = {"admin"}

    with pytest.raises(
        AdminAuditAccessError
    ):
        await service.open_impersonated_admin_audit(
            platform_user_id=600,
            effective_admin_user_id=(
                effective_admin_id
            ),
            target_type="all",
            page=0,
        )

    assert not any(
        name == "regional_page"
        for name, _ in moderation.calls
    )


@pytest.mark.asyncio
async def test_impersonated_audit_requires_active_admin_target():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    effective_admin_id = uuid4()
    service, _, moderation = build_service(
        user=user,
        roles={"super_admin"},
    )
    moderation.roles_by_user[
        effective_admin_id
    ] = {"client"}

    with pytest.raises(
        AdminAuditAccessError
    ):
        await service.get_impersonated_admin_audit_card(
            platform_user_id=700,
            effective_admin_user_id=(
                effective_admin_id
            ),
            action_id=uuid4(),
        )

    assert not any(
        name == "regional_card"
        for name, _ in moderation.calls
    )


@pytest.mark.asyncio
async def test_impersonated_audit_uses_effective_admin_scope():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    effective_admin_id = uuid4()
    action_id = uuid4()
    service, _, moderation = build_service(
        user=user,
        roles={"super_admin"},
    )
    moderation.roles_by_user[
        effective_admin_id
    ] = {"admin"}

    page = (
        await service
        .open_impersonated_admin_audit(
            platform_user_id=800,
            effective_admin_user_id=(
                effective_admin_id
            ),
            target_type="complaint",
            page=3,
            page_size=5,
        )
    )
    card = (
        await service
        .get_impersonated_admin_audit_card(
            platform_user_id=800,
            effective_admin_user_id=(
                effective_admin_id
            ),
            action_id=action_id,
        )
    )

    assert page is moderation.regional_page
    assert card is moderation.regional_card

    assert (
        "regional_page",
        {
            "admin_user_id": (
                effective_admin_id
            ),
            "tenant_id": user.tenant_id,
            "target_type": "complaint",
            "page": 3,
            "page_size": 5,
        },
    ) in moderation.calls

    assert (
        "regional_card",
        {
            "admin_user_id": (
                effective_admin_id
            ),
            "tenant_id": user.tenant_id,
            "action_id": action_id,
        },
    ) in moderation.calls


def test_impersonated_audit_handler_uses_application_service():
    source = open(
        "handlers/admin_audit.py",
        encoding="utf-8",
    ).read()
    tree = __import__("ast").parse(source)
    function_names = {
        "super_admin_read_only_admin_audit_queue",
        "super_admin_read_only_admin_audit_open",
    }
    blocks = []

    for node in tree.body:
        if (
            isinstance(
                node,
                (
                    __import__("ast").FunctionDef,
                    __import__("ast").AsyncFunctionDef,
                ),
            )
            and node.name in function_names
        ):
            blocks.append(
                __import__("ast")
                .get_source_segment(
                    source,
                    node,
                )
                or ""
            )

    combined = "\n".join(blocks)

    assert len(blocks) == 2
    assert "AdminAuditService" in combined
    assert (
        "open_impersonated_admin_audit"
        in combined
    )
    assert (
        "get_impersonated_admin_audit_card"
        in combined
    )
    assert "ModerationRepository(" not in combined
    assert "ModerationService(" not in combined
    assert "get_admin_user_context(" not in combined
