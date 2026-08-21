from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_finance import (
    AdminFinanceAccessError,
    AdminFinanceService,
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


class FakeBilling:
    def __init__(self):
        self.calls = []
        self.payments = [
            SimpleNamespace(id=uuid4())
        ]
        self.card = SimpleNamespace(
            payment_id=uuid4()
        )
        self.mark_result = SimpleNamespace(
            approval_required=False
        )

    async def list_pending_manual_payments(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list", kwargs)
        )
        return self.payments

    async def get_pending_manual_payment_card(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("get", kwargs)
        )
        return self.card

    async def mark_payment_paid(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("mark", kwargs)
        )
        return self.mark_result


def build_service(user):
    users = FakeUsers(user)
    billing = FakeBilling()

    service = AdminFinanceService(
        SimpleNamespace(),
        users=users,
        billing=billing,
    )

    return service, users, billing


@pytest.mark.asyncio
async def test_unknown_actor_fails_closed():
    service, _, billing = build_service(
        None
    )

    with pytest.raises(
        AdminFinanceAccessError
    ):
        await service.get_pending_payment_card(
            platform_user_id=100,
            payment_id=uuid4(),
        )

    assert billing.calls == []


@pytest.mark.asyncio
async def test_actor_without_tenant_fails_closed():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=None,
    )
    service, _, billing = build_service(
        user
    )

    with pytest.raises(
        AdminFinanceAccessError
    ):
        await service.mark_payment_paid(
            platform_user_id=100,
            payment_id=uuid4(),
            reason="Test reason",
        )

    assert billing.calls == []


@pytest.mark.asyncio
async def test_pending_list_uses_actor_context():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, users, billing = build_service(
        user
    )

    result = await service.list_pending_payments(
        platform_user_id=200,
        limit=5,
        offset=10,
    )

    assert result is billing.payments
    assert users.requested_ids == [200]
    assert billing.calls == [
        (
            "list",
            {
                "admin_user_id": user.id,
                "tenant_id": user.tenant_id,
                "limit": 5,
                "offset": 10,
            },
        )
    ]


@pytest.mark.asyncio
async def test_payment_card_uses_actor_context():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, billing = build_service(
        user
    )
    payment_id = uuid4()

    result = (
        await service
        .get_pending_payment_card(
            platform_user_id=300,
            payment_id=payment_id,
        )
    )

    assert result is billing.card
    assert billing.calls == [
        (
            "get",
            {
                "admin_user_id": user.id,
                "tenant_id": user.tenant_id,
                "payment_id": payment_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_mark_paid_returns_actor_and_result():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, billing = build_service(
        user
    )
    payment_id = uuid4()

    action = await service.mark_payment_paid(
        platform_user_id=400,
        payment_id=payment_id,
        reason="Verified payment",
    )

    assert action.actor.user_id == user.id
    assert (
        action.actor.tenant_id
        == user.tenant_id
    )
    assert action.result is billing.mark_result
    assert billing.calls == [
        (
            "mark",
            {
                "admin_user_id": user.id,
                "tenant_id": user.tenant_id,
                "payment_id": payment_id,
                "reason": "Verified payment",
            },
        )
    ]


def test_admin_finance_router_uses_application_service():
    source = open(
        "handlers/admin_finance.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()

    assert "AdminFinanceService" in source
    assert "AdminFinanceAccessError" in source
    assert "BillingRepository" not in source
    assert "BillingService(" not in source
    assert "from handlers.admin import" not in source

    assert (
        "AdminFinanceService"
        not in admin_source
    )
    assert (
        "receive_mark_payment_paid_reason"
        not in admin_source
    )
