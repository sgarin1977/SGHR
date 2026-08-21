from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_support import (
    AdminSupportAccessError,
    AdminSupportService,
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
    def __init__(self, roles):
        self.roles = set(roles)
        self.calls = []

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id,
    ):
        self.calls.append(
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            }
        )
        return set(self.roles)


def build_service(*, user, roles):
    users = FakeUsers(user)
    moderation = FakeModeration(roles)

    service = AdminSupportService(
        object(),
        users=users,
        moderation=moderation,
        support=object(),
    )
    return service, users, moderation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    (
        None,
        SimpleNamespace(
            id=uuid4(),
            tenant_id=None,
        ),
    ),
)
async def test_missing_actor_or_tenant_fails_closed(
    user,
):
    service, _, moderation = build_service(
        user=user,
        roles={"support"},
    )

    with pytest.raises(
        AdminSupportAccessError,
        match="Support access denied",
    ):
        await service.require_staff_actor(
            platform_user_id=123,
        )

    assert moderation.calls == []


@pytest.mark.asyncio
async def test_role_check_is_tenant_bound():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, users, moderation = build_service(
        user=user,
        roles={"support"},
    )

    actor = await service.require_staff_actor(
        platform_user_id="telegram-123",
    )

    assert actor.user_id == user.id
    assert actor.tenant_id == user.tenant_id
    assert actor.roles == frozenset({"support"})
    assert users.calls == ["telegram-123"]
    assert moderation.calls == [
        {
            "user_id": user.id,
            "tenant_id": user.tenant_id,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ("admin", "super_admin", "moderator"),
)
async def test_staff_panel_requires_support_role(
    role,
):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, _ = build_service(
        user=user,
        roles={role},
    )

    with pytest.raises(AdminSupportAccessError):
        await service.require_staff_actor(
            platform_user_id=123,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ("support", "admin", "super_admin"),
)
async def test_support_stats_roles_are_allowed(role):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, _ = build_service(
        user=user,
        roles={role},
    )

    actor = await service.require_stats_actor(
        platform_user_id=123,
    )

    assert role in actor.roles


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ("admin", "super_admin"),
)
async def test_escalated_admin_roles_are_allowed(role):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, _ = build_service(
        user=user,
        roles={role},
    )

    actor = await service.require_admin_actor(
        platform_user_id=123,
    )

    assert role in actor.roles


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ("support", "moderator", "finance_admin"),
)
async def test_escalated_admin_access_rejects_other_roles(
    role,
):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, _ = build_service(
        user=user,
        roles={role},
    )

    with pytest.raises(AdminSupportAccessError):
        await service.require_admin_actor(
            platform_user_id=123,
        )


def test_admin_support_service_has_no_handler_dependency():
    from pathlib import Path

    source = Path(
        "services/admin_support.py"
    ).read_text(encoding="utf-8")

    assert "handlers." not in source
    assert "CallbackQuery" not in source
    assert "FSMContext" not in source

class FakeSupportReads:
    def __init__(self):
        self.calls = []

    async def list_admin_escalated_tickets(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return "escalated-page"

    async def get_admin_escalated_ticket_view(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return "escalated-view"


def build_admin_read_service(role):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    support = FakeSupportReads()
    service = AdminSupportService(
        object(),
        users=FakeUsers(user),
        moderation=FakeModeration({role}),
        support=support,
    )
    return service, user, support


@pytest.mark.asyncio
async def test_list_admin_escalated_tickets_is_tenant_bound():
    service, user, support = (
        build_admin_read_service("admin")
    )

    result = await service.list_admin_escalated_tickets(
        platform_user_id=123,
        page=3,
        page_size=7,
    )

    assert result == "escalated-page"
    assert support.calls == [
        (
            "list",
            {
                "tenant_id": user.tenant_id,
                "admin_user_id": user.id,
                "page": 3,
                "page_size": 7,
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_admin_escalated_ticket_is_tenant_bound():
    service, user, support = (
        build_admin_read_service("super_admin")
    )
    ticket_id = uuid4()

    result = await service.get_admin_escalated_ticket(
        platform_user_id=123,
        ticket_id=ticket_id,
    )

    assert result == "escalated-view"
    assert support.calls == [
        (
            "get",
            {
                "tenant_id": user.tenant_id,
                "admin_user_id": user.id,
                "ticket_id": ticket_id,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    (
        (
            "list_admin_escalated_tickets",
            {"page": 0, "page_size": 5},
        ),
        (
            "get_admin_escalated_ticket",
            {"ticket_id": uuid4()},
        ),
    ),
)
async def test_escalated_reads_reject_support_role(
    method_name,
    kwargs,
):
    service, _, support = (
        build_admin_read_service("support")
    )

    with pytest.raises(AdminSupportAccessError):
        await getattr(service, method_name)(
            platform_user_id=123,
            **kwargs,
        )

    assert support.calls == []

def test_escalated_support_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_support.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "open_admin_escalated_tickets": (
            "list_admin_escalated_tickets"
        ),
        "open_admin_escalated_ticket_card": (
            "get_admin_escalated_ticket"
        ),
        "ask_admin_ticket_action_reason": (
            "require_admin_actor"
        ),
        "execute_admin_ticket_action": (
            "execute_admin_action"
        ),
        "show_super_admin_support_read_only_cabinet": (
            "open_impersonated_support_cabinet"
        ),
        "super_admin_read_only_admin_support": (
            "list_impersonated_admin_tickets"
        ),
        "super_admin_read_only_admin_support_open": (
            "get_impersonated_admin_ticket"
        ),
        "super_admin_read_only_support_list": (
            "list_impersonated_staff_tickets"
        ),
        "super_admin_read_only_support_open_ticket": (
            "get_impersonated_staff_ticket"
        ),
        "open_support_staff_menu": (
            "open_staff_menu"
        ),
        "receive_support_ticket_search": (
            "search_staff_tickets"
        ),
        "list_support_tickets_by_status": (
            "list_staff_tickets"
        ),
        "ask_support_ticket_search": (
            "require_staff_actor"
        ),
        "show_support_ticket_filters": (
            "require_staff_actor"
        ),
        "show_support_ticket": (
            "get_staff_ticket"
        ),
        "take_support_ticket": (
            "assign_staff_ticket"
        ),
        "assign_support_ticket": (
            "assign_staff_ticket"
        ),
        "receive_support_reply": (
            "add_staff_reply"
        ),
        "update_support_ticket_status_from_admin": (
            "update_staff_ticket_status"
        ),
        "ask_support_ticket_escalation_reason": (
            "require_staff_actor"
        ),
        "receive_support_ticket_escalation_reason": (
            "escalate_staff_ticket"
        ),
        "ask_support_reply": (
            "require_staff_actor"
        ),
        "show_support_staff_stats": (
            "get_staff_stats"
        ),
        "support_staff_stats_filter_pending": (
            "require_stats_actor"
        ),
    }

    for function_name, service_method in expected.items():
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = ast.get_source_segment(
            source,
            node,
        )

        assert "AdminSupportService(" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "SupportRepository(" not in block
        assert "ModerationRepository(" not in block

class FakeSupportActions:
    def __init__(self):
        self.calls = []

    async def assign_admin_escalated_ticket(
        self,
        **kwargs,
    ):
        self.calls.append(("assign", kwargs))
        return SimpleNamespace(status="in_progress")

    async def resolve_admin_escalated_ticket(
        self,
        **kwargs,
    ):
        self.calls.append(("resolve", kwargs))
        return SimpleNamespace(status="resolved")


def build_admin_action_service(role):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    support = FakeSupportActions()
    service = AdminSupportService(
        object(),
        users=FakeUsers(user),
        moderation=FakeModeration({role}),
        support=support,
    )
    return service, user, support


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_status"),
    (
        ("assign", "in_progress"),
        ("resolve", "resolved"),
    ),
)
async def test_admin_ticket_action_is_tenant_bound(
    action,
    expected_status,
):
    service, user, support = (
        build_admin_action_service("admin")
    )
    ticket_id = uuid4()

    result = await service.execute_admin_action(
        platform_user_id=123,
        ticket_id=ticket_id,
        action=action,
        reason="Integration test",
    )

    assert result.actor.user_id == user.id
    assert result.actor.tenant_id == user.tenant_id
    assert result.result.status == expected_status
    assert support.calls == [
        (
            action,
            {
                "tenant_id": user.tenant_id,
                "admin_user_id": user.id,
                "ticket_id": ticket_id,
                "reason": "Integration test",
            },
        )
    ]


@pytest.mark.asyncio
async def test_unknown_admin_ticket_action_is_rejected():
    from services.support import SupportServiceError

    service, _, support = (
        build_admin_action_service("super_admin")
    )

    with pytest.raises(
        SupportServiceError,
        match="Unsupported admin support action",
    ):
        await service.execute_admin_action(
            platform_user_id=123,
            ticket_id=uuid4(),
            action="delete",
            reason="Invalid action",
        )

    assert support.calls == []


@pytest.mark.asyncio
async def test_support_role_cannot_execute_admin_action():
    service, _, support = (
        build_admin_action_service("support")
    )

    with pytest.raises(AdminSupportAccessError):
        await service.execute_admin_action(
            platform_user_id=123,
            ticket_id=uuid4(),
            action="resolve",
            reason="Not allowed",
        )

    assert support.calls == []

class FakeSupportMenuSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class FakeSupportMenuUsers(FakeUsers):
    def __init__(self, user, available_roles):
        super().__init__(user)
        self.available_roles = available_roles
        self.role_context_calls = []

    async def get_role_switch_context(
        self,
        platform_user_id,
    ):
        self.role_context_calls.append(
            platform_user_id
        )
        return SimpleNamespace(
            available_roles=self.available_roles
        )


class FakeSupportMenuDomain:
    def __init__(self):
        self.calls = []

    async def get_staff_ticket_counts(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return {
            "open": 3,
            "in_progress": 2,
            "resolved": 5,
        }


class FakeSupportEvents:
    def __init__(self):
        self.calls = []

    async def create_event(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("available_roles", "expected_switch"),
    (
        (("support",), False),
        (("client", "support"), True),
    ),
)
async def test_open_staff_menu_owns_business_flow(
    available_roles,
    expected_switch,
):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    session = FakeSupportMenuSession()
    users = FakeSupportMenuUsers(
        user,
        available_roles,
    )
    support = FakeSupportMenuDomain()
    events = FakeSupportEvents()

    service = AdminSupportService(
        session,
        users=users,
        moderation=FakeModeration({"support"}),
        support=support,
        events=events,
    )

    result = await service.open_staff_menu(
        platform_user_id=456,
    )

    assert result.actor.user_id == user.id
    assert result.counts == {
        "open": 3,
        "in_progress": 2,
        "resolved": 5,
    }
    assert result.show_role_switch is expected_switch
    assert support.calls == [
        {
            "tenant_id": user.tenant_id,
            "staff_user_id": user.id,
            "statuses": {
                "open",
                "in_progress",
                "resolved",
            },
        }
    ]
    assert users.role_context_calls == [456]
    assert events.calls == [
        {
            "event_type": "support_menu",
            "tenant_id": user.tenant_id,
            "user_id": user.id,
            "entity_type": "support_ticket",
            "entity_id": None,
            "payload": {
                "source": "support_staff_menu",
                "counts": result.counts,
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_admin_cannot_open_support_staff_menu():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    session = FakeSupportMenuSession()
    users = FakeSupportMenuUsers(
        user,
        ("admin",),
    )
    support = FakeSupportMenuDomain()
    events = FakeSupportEvents()

    service = AdminSupportService(
        session,
        users=users,
        moderation=FakeModeration({"admin"}),
        support=support,
        events=events,
    )

    with pytest.raises(AdminSupportAccessError):
        await service.open_staff_menu(
            platform_user_id=456,
        )

    assert support.calls == []
    assert events.calls == []
    assert session.commits == 0

class FakeSupportQueueDomain:
    def __init__(self):
        self.calls = []
        self.tickets = [
            SimpleNamespace(id=uuid4())
            for _ in range(4)
        ]

    async def search_staff_tickets(
        self,
        **kwargs,
    ):
        self.calls.append(("search", kwargs))
        return self.tickets[:2]

    async def list_staff_tickets(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return self.tickets


def build_staff_queue_service(role="support"):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    session = FakeSupportMenuSession()
    users = FakeSupportMenuUsers(
        user,
        (role,),
    )
    support = FakeSupportQueueDomain()
    events = FakeSupportEvents()
    service = AdminSupportService(
        session,
        users=users,
        moderation=FakeModeration({role}),
        support=support,
        events=events,
    )
    return service, user, session, support, events


@pytest.mark.asyncio
async def test_search_staff_tickets_owns_audit_flow():
    service, user, session, support, events = (
        build_staff_queue_service()
    )

    result = await service.search_staff_tickets(
        platform_user_id=456,
        query="  ticket-123  ",
        limit=7,
    )

    assert result.query == "ticket-123"
    assert len(result.tickets) == 2
    assert support.calls == [
        (
            "search",
            {
                "tenant_id": user.tenant_id,
                "staff_user_id": user.id,
                "query": "ticket-123",
                "limit": 7,
                "offset": 0,
            },
        )
    ]
    assert events.calls[0]["event_type"] == (
        "ticket_search"
    )
    assert events.calls[0]["payload"] == {
        "query": "ticket-123",
        "count": 2,
    }
    assert session.commits == 1


@pytest.mark.asyncio
async def test_list_staff_tickets_owns_pagination():
    service, user, session, support, events = (
        build_staff_queue_service()
    )

    result = await service.list_staff_tickets(
        platform_user_id=456,
        statuses={"open"},
        view="open",
        page=2,
        page_size=3,
    )

    assert len(result.tickets) == 3
    assert result.page == 2
    assert result.has_next is True
    assert support.calls == [
        (
            "list",
            {
                "tenant_id": user.tenant_id,
                "staff_user_id": user.id,
                "statuses": {"open"},
                "limit": 4,
                "offset": 6,
            },
        )
    ]
    assert events.calls[0]["event_type"] == (
        "ticket_list"
    )
    assert events.calls[0]["payload"] == {
        "source": "support_staff",
        "view": "open",
        "page": 2,
        "count": 3,
        "has_next": True,
    }
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    (
        (
            "search_staff_tickets",
            {"query": "ticket", "limit": 5},
        ),
        (
            "list_staff_tickets",
            {
                "statuses": {"open"},
                "view": "open",
                "page": 0,
                "page_size": 5,
            },
        ),
    ),
)
async def test_admin_cannot_read_support_staff_queue(
    method_name,
    kwargs,
):
    service, _, session, support, events = (
        build_staff_queue_service("admin")
    )

    with pytest.raises(AdminSupportAccessError):
        await getattr(service, method_name)(
            platform_user_id=456,
            **kwargs,
        )

    assert support.calls == []
    assert events.calls == []
    assert session.commits == 0

class FakeSupportTicketDomain:
    def __init__(self):
        self.calls = []

    async def get_staff_ticket_view(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return "staff-ticket-view"


def build_staff_ticket_service(role):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    support = FakeSupportTicketDomain()
    service = AdminSupportService(
        object(),
        users=FakeUsers(user),
        moderation=FakeModeration({role}),
        support=support,
    )
    return service, user, support


@pytest.mark.asyncio
async def test_get_staff_ticket_is_tenant_bound():
    service, user, support = (
        build_staff_ticket_service("support")
    )
    ticket_id = uuid4()

    result = await service.get_staff_ticket(
        platform_user_id=456,
        ticket_id=ticket_id,
    )

    assert result == "staff-ticket-view"
    assert support.calls == [
        {
            "tenant_id": user.tenant_id,
            "staff_user_id": user.id,
            "ticket_id": ticket_id,
        }
    ]


@pytest.mark.asyncio
async def test_admin_cannot_open_staff_ticket():
    service, _, support = (
        build_staff_ticket_service("admin")
    )

    with pytest.raises(AdminSupportAccessError):
        await service.get_staff_ticket(
            platform_user_id=456,
            ticket_id=uuid4(),
        )

    assert support.calls == []

class FakeSupportMutations:
    def __init__(self):
        self.calls = []
        self.ticket = SimpleNamespace(
            id=uuid4(),
            status="in_progress",
        )
        self.reply = SimpleNamespace(
            recipient_chat_id=123456,
        )

    async def assign_ticket(self, **kwargs):
        self.calls.append(("assign", kwargs))
        return self.ticket

    async def add_staff_message(self, **kwargs):
        self.calls.append(("reply", kwargs))
        return self.reply


class FakeSupportAudit:
    def __init__(self):
        self.calls = []

    async def log_event(self, **kwargs):
        self.calls.append(kwargs)


def build_staff_mutation_service(role="support"):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    session = FakeSupportMenuSession()
    support = FakeSupportMutations()
    audit = FakeSupportAudit()
    service = AdminSupportService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration({role}),
        support=support,
        events=FakeSupportEvents(),
        audit=audit,
    )
    return service, user, session, support, audit


@pytest.mark.asyncio
async def test_assign_staff_ticket_owns_audit_flow():
    service, user, session, support, audit = (
        build_staff_mutation_service()
    )
    ticket_id = uuid4()

    action = await service.assign_staff_ticket(
        platform_user_id=456,
        ticket_id=ticket_id,
    )

    assert action.actor.user_id == user.id
    assert action.result is support.ticket
    assert support.calls == [
        (
            "assign",
            {
                "tenant_id": user.tenant_id,
                "staff_user_id": user.id,
                "ticket_id": ticket_id,
            },
        )
    ]
    assert audit.calls == [
        {
            "tenant_id": user.tenant_id,
            "user_id": user.id,
            "event_type": "ticket_assigned",
            "entity_type": "support_ticket",
            "entity_id": support.ticket.id,
            "payload": {
                "status": "in_progress",
            },
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_add_staff_reply_is_tenant_bound():
    service, user, session, support, audit = (
        build_staff_mutation_service()
    )
    ticket_id = uuid4()

    action = await service.add_staff_reply(
        platform_user_id=456,
        ticket_id=ticket_id,
        message_text="Test reply",
    )

    assert action.actor.tenant_id == user.tenant_id
    assert action.result is support.reply
    assert support.calls == [
        (
            "reply",
            {
                "tenant_id": user.tenant_id,
                "staff_user_id": user.id,
                "ticket_id": ticket_id,
                "message_text": "Test reply",
            },
        )
    ]
    assert audit.calls == []
    assert session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    (
        (
            "assign_staff_ticket",
            {"ticket_id": uuid4()},
        ),
        (
            "add_staff_reply",
            {
                "ticket_id": uuid4(),
                "message_text": "Denied",
            },
        ),
    ),
)
async def test_admin_cannot_mutate_staff_ticket(
    method_name,
    kwargs,
):
    service, _, session, support, audit = (
        build_staff_mutation_service("admin")
    )

    with pytest.raises(AdminSupportAccessError):
        await getattr(service, method_name)(
            platform_user_id=456,
            **kwargs,
        )

    assert support.calls == []
    assert audit.calls == []
    assert session.commits == 0

class FakeSupportStatusDomain:
    def __init__(self):
        self.calls = []

    async def update_ticket_status(
        self,
        **kwargs,
    ):
        self.calls.append(("status", kwargs))
        return SimpleNamespace(
            id=kwargs["ticket_id"],
            status=kwargs["status"],
        )

    async def escalate_ticket_to_admin(
        self,
        **kwargs,
    ):
        self.calls.append(("escalate", kwargs))
        return SimpleNamespace(
            id=kwargs["ticket_id"],
            priority="P1",
        )


class FakeSupportStatusAudit:
    def __init__(self):
        self.actions = []
        self.events = []

    async def log_admin_action(self, **kwargs):
        self.actions.append(kwargs)

    async def log_event(self, **kwargs):
        self.events.append(kwargs)


def build_status_service(role="support"):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    session = FakeSupportMenuSession()
    support = FakeSupportStatusDomain()
    audit = FakeSupportStatusAudit()
    service = AdminSupportService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration({role}),
        support=support,
        events=FakeSupportEvents(),
        audit=audit,
    )
    return service, user, session, support, audit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_event_count"),
    (
        ("closed", 1),
        ("resolved", 2),
    ),
)
async def test_update_staff_status_owns_audit_flow(
    status,
    expected_event_count,
):
    service, user, session, support, audit = (
        build_status_service()
    )
    ticket_id = uuid4()

    action = await service.update_staff_ticket_status(
        platform_user_id=456,
        ticket_id=ticket_id,
        status=status,
    )

    assert action.result.status == status
    assert support.calls[0][1] == {
        "tenant_id": user.tenant_id,
        "staff_user_id": user.id,
        "ticket_id": ticket_id,
        "status": status,
    }
    assert audit.actions[0]["action_type"] == (
        f"support_ticket_{status}"
    )
    assert audit.actions[0]["target_id"] == ticket_id
    assert audit.events[0]["event_type"] == (
        f"support_ticket_{status}"
    )
    assert len(audit.events) == expected_event_count

    if status == "resolved":
        assert audit.events[1]["event_type"] == (
            "resolved"
        )

    assert session.commits == 1


@pytest.mark.asyncio
async def test_escalate_staff_ticket_owns_audit_flow():
    service, user, session, support, audit = (
        build_status_service()
    )
    ticket_id = uuid4()

    action = await service.escalate_staff_ticket(
        platform_user_id=456,
        ticket_id=ticket_id,
        reason="Needs Admin review",
    )

    assert action.result.priority == "P1"
    assert support.calls == [
        (
            "escalate",
            {
                "tenant_id": user.tenant_id,
                "staff_user_id": user.id,
                "ticket_id": ticket_id,
                "reason": "Needs Admin review",
            },
        )
    ]
    assert audit.events == [
        {
            "tenant_id": user.tenant_id,
            "user_id": user.id,
            "event_type": "ticket_escalated",
            "entity_type": "support_ticket",
            "entity_id": ticket_id,
            "payload": {
                "priority": "P1",
            },
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    (
        (
            "update_staff_ticket_status",
            {
                "ticket_id": uuid4(),
                "status": "closed",
            },
        ),
        (
            "escalate_staff_ticket",
            {
                "ticket_id": uuid4(),
                "reason": "Denied",
            },
        ),
    ),
)
async def test_admin_cannot_update_staff_ticket(
    method_name,
    kwargs,
):
    service, _, session, support, audit = (
        build_status_service("admin")
    )

    with pytest.raises(AdminSupportAccessError):
        await getattr(service, method_name)(
            platform_user_id=456,
            **kwargs,
        )

    assert support.calls == []
    assert audit.actions == []
    assert audit.events == []
    assert session.commits == 0

class FakeSupportStatsDomain:
    def __init__(self):
        self.calls = []

    async def get_staff_ticket_stats(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return {
            "total": 12,
            "open": 4,
        }


def build_stats_service(role):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    session = FakeSupportMenuSession()
    support = FakeSupportStatsDomain()
    events = FakeSupportEvents()
    service = AdminSupportService(
        session,
        users=FakeUsers(user),
        moderation=FakeModeration({role}),
        support=support,
        events=events,
        audit=FakeSupportAudit(),
    )
    return service, user, session, support, events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ("support", "admin", "super_admin"),
)
async def test_get_staff_stats_owns_audit_flow(role):
    service, user, session, support, events = (
        build_stats_service(role)
    )

    action = await service.get_staff_stats(
        platform_user_id=456,
    )

    assert action.actor.user_id == user.id
    assert action.result == {
        "total": 12,
        "open": 4,
    }
    assert support.calls == [
        {
            "tenant_id": user.tenant_id,
            "staff_user_id": user.id,
        }
    ]
    assert events.calls == [
        {
            "event_type": "stats_viewed",
            "tenant_id": user.tenant_id,
            "user_id": user.id,
            "entity_type": "support_ticket",
            "entity_id": None,
            "payload": {
                "source": "support_staff_stats",
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_moderator_cannot_view_support_stats():
    service, _, session, support, events = (
        build_stats_service("moderator")
    )

    with pytest.raises(AdminSupportAccessError):
        await service.get_staff_stats(
            platform_user_id=456,
        )

    assert support.calls == []
    assert events.calls == []
    assert session.commits == 0


def test_support_panel_entry_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "show_admin_panel"
    )
    block = ast.get_source_segment(
        source,
        node,
    )

    assert "AdminPanelService(" in block
    assert "open_panel" in block
    assert "AdminSupportService(" not in block
    assert "SupportService(" not in block
    assert "SupportRepository(" not in block
    assert "EventRepository(" not in block


class FakeImpersonatedModeration:
    def __init__(self, roles_by_user):
        self.roles_by_user = roles_by_user
        self.calls = []

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id,
    ):
        self.calls.append(
            (user_id, tenant_id)
        )
        return set(
            self.roles_by_user.get(
                user_id,
                set(),
            )
        )


class FakeImpersonatedSupportDomain:
    def __init__(self):
        self.calls = []

    async def list_admin_escalated_tickets(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list_admin", kwargs)
        )
        return SimpleNamespace(
            tickets=("admin-ticket",),
            page=kwargs["page"],
            has_next=False,
        )

    async def get_admin_escalated_ticket_view(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("get_admin", kwargs)
        )
        return "admin-ticket-view"

    async def list_staff_tickets(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list_staff", kwargs)
        )
        return [
            "staff-ticket-1",
            "staff-ticket-2",
            "staff-ticket-3",
        ]

    async def get_staff_ticket_view(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("get_staff", kwargs)
        )
        return "staff-ticket-view"


def build_impersonated_support_service(
    *,
    actor_roles,
    target_roles,
):
    tenant_id = uuid4()
    actor_id = uuid4()
    target_id = uuid4()
    actor = SimpleNamespace(
        id=actor_id,
        tenant_id=tenant_id,
    )
    support = FakeImpersonatedSupportDomain()
    moderation = FakeImpersonatedModeration(
        {
            actor_id: set(actor_roles),
            target_id: set(target_roles),
        }
    )
    service = AdminSupportService(
        object(),
        users=FakeUsers(actor),
        moderation=moderation,
        support=support,
        events=object(),
        audit=object(),
    )

    return (
        service,
        support,
        moderation,
        tenant_id,
        actor_id,
        target_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("effective_role", "target_roles"),
    [
        ("admin", {"admin"}),
        ("support", {"support"}),
    ],
)
async def test_impersonated_support_actor_is_validated(
    effective_role,
    target_roles,
):
    (
        service,
        support,
        moderation,
        tenant_id,
        actor_id,
        target_id,
    ) = build_impersonated_support_service(
        actor_roles={"super_admin"},
        target_roles=target_roles,
    )

    actor = await service.require_impersonated_actor(
        platform_user_id=123,
        effective_user_id=target_id,
        effective_role=effective_role,
    )

    assert actor.user_id == actor_id
    assert actor.tenant_id == tenant_id
    assert moderation.calls == [
        (actor_id, tenant_id),
        (target_id, tenant_id),
    ]
    assert support.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_roles", "target_roles"),
    [
        ({"admin"}, {"support"}),
        ({"super_admin"}, set()),
    ],
)
async def test_impersonated_support_fails_closed(
    actor_roles,
    target_roles,
):
    (
        service,
        support,
        _moderation,
        _tenant_id,
        _actor_id,
        target_id,
    ) = build_impersonated_support_service(
        actor_roles=actor_roles,
        target_roles=target_roles,
    )

    with pytest.raises(
        AdminSupportAccessError
    ):
        await service.require_impersonated_actor(
            platform_user_id=123,
            effective_user_id=target_id,
            effective_role="support",
        )

    assert support.calls == []


@pytest.mark.asyncio
async def test_impersonated_admin_support_reads_delegate():
    (
        service,
        support,
        _moderation,
        tenant_id,
        _actor_id,
        target_id,
    ) = build_impersonated_support_service(
        actor_roles={"super_admin"},
        target_roles={"admin"},
    )
    ticket_id = uuid4()

    page = await service.list_impersonated_admin_tickets(
        platform_user_id=123,
        effective_admin_user_id=target_id,
        page=2,
        page_size=5,
    )
    card = await service.get_impersonated_admin_ticket(
        platform_user_id=123,
        effective_admin_user_id=target_id,
        ticket_id=ticket_id,
    )

    assert page.tickets == ("admin-ticket",)
    assert card == "admin-ticket-view"
    assert support.calls == [
        (
            "list_admin",
            {
                "tenant_id": tenant_id,
                "admin_user_id": target_id,
                "page": 2,
                "page_size": 5,
            },
        ),
        (
            "get_admin",
            {
                "tenant_id": tenant_id,
                "admin_user_id": target_id,
                "ticket_id": ticket_id,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_impersonated_staff_support_list_paginates():
    (
        service,
        support,
        _moderation,
        tenant_id,
        _actor_id,
        target_id,
    ) = build_impersonated_support_service(
        actor_roles={"super_admin"},
        target_roles={"support"},
    )

    result = await service.list_impersonated_staff_tickets(
        platform_user_id=123,
        effective_staff_user_id=target_id,
        statuses={"open"},
        view="open",
        page=3,
        page_size=2,
    )

    assert result.tickets == (
        "staff-ticket-1",
        "staff-ticket-2",
    )
    assert result.page == 3
    assert result.has_next is True
    assert support.calls == [
        (
            "list_staff",
            {
                "tenant_id": tenant_id,
                "staff_user_id": target_id,
                "statuses": {"open"},
                "limit": 3,
                "offset": 6,
            },
        )
    ]


@pytest.mark.asyncio
async def test_impersonated_staff_ticket_read_delegates():
    (
        service,
        support,
        _moderation,
        tenant_id,
        _actor_id,
        target_id,
    ) = build_impersonated_support_service(
        actor_roles={"super_admin"},
        target_roles={"support"},
    )
    ticket_id = uuid4()

    result = await service.get_impersonated_staff_ticket(
        platform_user_id=123,
        effective_staff_user_id=target_id,
        ticket_id=ticket_id,
    )

    assert result == "staff-ticket-view"
    assert support.calls == [
        (
            "get_staff",
            {
                "tenant_id": tenant_id,
                "staff_user_id": target_id,
                "ticket_id": ticket_id,
            },
        )
    ]


class FakeSupportCabinetModeration(
    FakeImpersonatedModeration
):
    def __init__(self, roles_by_user):
        super().__init__(roles_by_user)
        self.cabinet_calls = []

    async def get_support_read_only_cabinet(
        self,
        **kwargs,
    ):
        self.cabinet_calls.append(kwargs)
        return "support-read-only-cabinet"


@pytest.mark.asyncio
async def test_impersonated_support_cabinet_delegates():
    tenant_id = uuid4()
    actor_id = uuid4()
    target_id = uuid4()
    actor = SimpleNamespace(
        id=actor_id,
        tenant_id=tenant_id,
    )
    moderation = FakeSupportCabinetModeration(
        {
            actor_id: {"super_admin"},
            target_id: {"support"},
        }
    )
    support = FakeImpersonatedSupportDomain()
    service = AdminSupportService(
        object(),
        users=FakeUsers(actor),
        moderation=moderation,
        support=support,
        events=object(),
        audit=object(),
    )

    result = await (
        service.open_impersonated_support_cabinet(
            platform_user_id=123,
            effective_staff_user_id=target_id,
        )
    )

    assert result == "support-read-only-cabinet"
    assert moderation.calls == [
        (actor_id, tenant_id),
        (target_id, tenant_id),
    ]
    assert moderation.cabinet_calls == [
        {
            "admin_user_id": actor_id,
            "tenant_id": tenant_id,
            "target_user_id": target_id,
        }
    ]
    assert support.calls == []


@pytest.mark.asyncio
async def test_impersonated_support_cabinet_fails_closed():
    tenant_id = uuid4()
    actor_id = uuid4()
    target_id = uuid4()
    actor = SimpleNamespace(
        id=actor_id,
        tenant_id=tenant_id,
    )
    moderation = FakeSupportCabinetModeration(
        {
            actor_id: {"super_admin"},
            target_id: set(),
        }
    )
    service = AdminSupportService(
        object(),
        users=FakeUsers(actor),
        moderation=moderation,
        support=FakeImpersonatedSupportDomain(),
        events=object(),
        audit=object(),
    )

    with pytest.raises(
        AdminSupportAccessError
    ):
        await (
            service.open_impersonated_support_cabinet(
                platform_user_id=123,
                effective_staff_user_id=target_id,
            )
        )

    assert moderation.cabinet_calls == []


def test_support_presentation_uses_support_specific_navigation():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_support.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    cabinet_node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name
        == "show_super_admin_support_read_only_cabinet"
    )
    cabinet_block = ast.get_source_segment(
        source,
        cabinet_node,
    )

    assert (
        "super_admin_impersonation_support_cabinet"
        in cabinet_block
    )
    assert (
        "super_admin_read_only_support_menu_keyboard"
        in cabinet_block
    )
    assert (
        "super_admin_read_only_moderator_menu_keyboard"
        not in cabinet_block
    )
    assert "cabinet.open_tickets" in cabinet_block
    assert "cabinet.in_progress_tickets" in cabinet_block
    assert "cabinet.resolved_tickets" in cabinet_block

    ticket_node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "show_support_ticket"
    )
    ticket_block = ast.get_source_segment(
        source,
        ticket_node,
    )

    assert (
        "support_staff_menu_keyboard"
        in ticket_block
    )
    assert "admin_panel_keyboard" not in ticket_block


def test_admin_support_router_is_independent():
    import ast
    from pathlib import Path

    support_source = Path(
        "handlers/admin_support.py"
    ).read_text(encoding="utf-8")
    admin_source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    bot_source = Path(
        "bot.py"
    ).read_text(encoding="utf-8")

    from aiogram import Router
    from handlers.admin_support import (
        admin_support_router,
    )

    assert isinstance(
        admin_support_router,
        Router,
    )
    assert "admin_support_router = Router()" in support_source
    assert "class AdminSupportFSM" in support_source
    assert "AdminModerationFSM" not in support_source
    assert "from handlers.admin import" not in support_source
    assert "AdminSupportService" in support_source

    moved_routes = (
        "ask_admin_ticket_action_reason",
        "ask_support_reply",
        "ask_support_ticket_escalation_reason",
        "ask_support_ticket_search",
        "assign_support_ticket",
        "close_support_ticket",
        "execute_admin_ticket_action",
        "list_support_tickets_by_status",
        "open_admin_escalated_ticket_card",
        "open_admin_escalated_tickets",
        "open_support_staff_menu",
        "receive_support_reply",
        "receive_support_ticket_escalation_reason",
        "receive_support_ticket_search",
        "resolve_support_ticket",
        "show_support_staff_stats",
        "show_support_ticket_filters",
        "super_admin_read_only_admin_support",
        "super_admin_read_only_admin_support_open",
        "super_admin_read_only_support_home",
        "super_admin_read_only_support_list",
        "super_admin_read_only_support_open_ticket",
        "support_staff_stats_filter_pending",
        "take_support_ticket",
        "view_support_ticket",
    )

    for function_name in moved_routes:
        assert f"def {function_name}" in support_source
        assert f"def {function_name}" not in admin_source

    support_tree = ast.parse(support_source)
    direct_calls = set()

    for item in ast.walk(support_tree):
        if not isinstance(item, ast.Call):
            continue

        if isinstance(item.func, ast.Name):
            direct_calls.add(item.func.id)
        elif isinstance(item.func, ast.Attribute):
            direct_calls.add(item.func.attr)

    assert "SupportRepository" not in direct_calls
    assert "SupportService" not in direct_calls
    assert "ModerationRepository" not in direct_calls
    assert "get_admin_user_context" not in direct_calls

    for state_name in (
        "entering_admin_ticket_action_reason",
        "entering_support_escalation_reason",
        "entering_support_reply",
        "entering_support_search",
    ):
        assert state_name in support_source
        assert state_name not in admin_source

    assert (
        "from handlers.admin_support import ("
        in admin_source
    )
    assert (
        "admin_support_router"
        in bot_source
    )
    assert (
        bot_source.index(
            "dp.include_router(\n"
            "        admin_support_router"
        )
        < bot_source.index(
            "dp.include_router(admin_router)"
        )
    )
