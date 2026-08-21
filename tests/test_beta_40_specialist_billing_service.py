from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_billing import (
    SpecialistBillingAccessError,
    SpecialistBillingService,
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


class FakeTranslations:
    def __init__(self, language="en"):
        self.language = language
        self.calls = []

    async def resolve_interface_language(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.language


class FakeBilling:
    def __init__(self):
        self.calls = []
        self.features = [
            SimpleNamespace(code="top")
        ]
        self.invoice_result = SimpleNamespace(
            invoice=SimpleNamespace(id=uuid4())
        )
        self.payment_result = SimpleNamespace(
            payment=SimpleNamespace(id=uuid4())
        )

    async def list_paid_features(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list", kwargs)
        )
        return self.features

    async def create_manual_invoice(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("invoice", kwargs)
        )
        return self.invoice_result

    async def claim_manual_payment(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("claim", kwargs)
        )
        return self.payment_result

    async def record_unavailable_feature_opened(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("placeholder", kwargs)
        )


def build_service(
    user,
    *,
    language="en",
):
    users = FakeUsers(user)
    translations = FakeTranslations(language)
    billing = FakeBilling()

    service = SpecialistBillingService(
        SimpleNamespace(),
        users=users,
        translations=translations,
        billing=billing,
    )

    return (
        service,
        users,
        translations,
        billing,
    )


def make_user(
    *,
    tenant_id=None,
    language_code="ru",
):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=(
            tenant_id
            if tenant_id is not None
            else uuid4()
        ),
        language_code=language_code,
    )


@pytest.mark.asyncio
async def test_unknown_actor_fails_closed():
    (
        service,
        users,
        translations,
        billing,
    ) = build_service(None)

    with pytest.raises(
        SpecialistBillingAccessError
    ):
        await service.open_panel(
            platform_user_id=100,
            fallback_language="ru",
        )

    assert users.calls == [100]
    assert translations.calls == []
    assert billing.calls == []


@pytest.mark.asyncio
async def test_actor_without_tenant_fails_closed():
    user = make_user()
    user.tenant_id = None

    service, _, translations, billing = (
        build_service(user)
    )

    with pytest.raises(
        SpecialistBillingAccessError
    ):
        await service.list_paid_features(
            platform_user_id=101,
            fallback_language="ru",
        )

    assert translations.calls == []
    assert billing.calls == []


@pytest.mark.asyncio
async def test_actor_uses_interface_language():
    user = make_user(language_code="de")

    service, users, translations, _ = (
        build_service(
            user,
            language="ua",
        )
    )

    actor = await service.open_panel(
        platform_user_id=102,
        fallback_language="ru",
    )

    assert actor.user_id == user.id
    assert actor.tenant_id == user.tenant_id
    assert actor.language == "uk"
    assert users.calls == [102]
    assert translations.calls == [
        {
            "user_id": user.id,
            "fallback_language": "de",
        }
    ]


@pytest.mark.asyncio
async def test_paid_features_use_actor_tenant():
    user = make_user()
    service, _, _, billing = build_service(user)

    result = await service.list_paid_features(
        platform_user_id=103,
        fallback_language="ru",
    )

    assert result.actor.user_id == user.id
    assert result.features is billing.features
    assert billing.calls == [
        (
            "list",
            {
                "tenant_id": user.tenant_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_invoice_uses_actor_context():
    user = make_user(language_code="en")
    service, _, _, billing = build_service(user)

    action = await service.create_manual_invoice(
        platform_user_id=104,
        fallback_language="ru",
        feature_code="top",
    )

    assert (
        action.result
        is billing.invoice_result
    )
    assert billing.calls == [
        (
            "invoice",
            {
                "tenant_id": user.tenant_id,
                "payer_user_id": user.id,
                "feature_code": "top",
                "language": "en",
            },
        )
    ]


@pytest.mark.asyncio
async def test_payment_claim_uses_actor_context():
    user = make_user()
    service, _, _, billing = build_service(user)
    invoice_id = uuid4()

    action = await service.claim_manual_payment(
        platform_user_id=105,
        fallback_language="ru",
        invoice_id=invoice_id,
    )

    assert (
        action.result
        is billing.payment_result
    )
    assert billing.calls == [
        (
            "claim",
            {
                "tenant_id": user.tenant_id,
                "payer_user_id": user.id,
                "invoice_id": invoice_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_placeholder_event_uses_actor():
    user = make_user()
    service, _, _, billing = build_service(user)

    actor = await (
        service
        .record_unavailable_feature_opened(
            platform_user_id=106,
            fallback_language="ru",
            feature="promotion",
            source="specialist_cabinet",
        )
    )

    assert actor.user_id == user.id
    assert billing.calls == [
        (
            "placeholder",
            {
                "tenant_id": user.tenant_id,
                "user_id": user.id,
                "feature": "promotion",
                "source": "specialist_cabinet",
            },
        )
    ]


def test_service_owns_billing_dependencies():
    source = open(
        "services/specialist_billing.py",
        encoding="utf-8",
    ).read()

    assert "UserService" in source
    assert "TranslationService" in source
    assert "BillingService" in source
    assert "BillingRepository" in source
    assert "TranslationRepository" in source

def test_billing_panel_reads_use_application_service():
    import ast

    source = open(
        "handlers/specialist_billing.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    for function_name in (
        "show_billing_panel",
        "list_billing_features",
        "create_billing_invoice",
        "claim_billing_payment",
        "beta_disabled",
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
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }

        assert (
            "SpecialistBillingService"
            in block
        )
        assert (
            "get_billing_user_context"
            not in block
        )
        assert (
            "get_billing_interface_language"
            not in block
        )
        assert "BillingRepository" not in called_names
        assert "BillingService" not in called_names

def test_specialist_billing_router_is_independent():
    import ast

    specialist_source = open(
        "handlers/specialist_billing.py",
        encoding="utf-8",
    ).read()
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "specialist_billing_router = Router()"
        in specialist_source
    )
    assert (
        "from handlers.billing import"
        not in specialist_source
    )

    moved_definitions = (
        "billing_menu_keyboard",
        "paid_features_keyboard",
        "invoice_keyboard",
        "format_feature_button",
        "format_features_text",
        "billing_status_label",
        "format_invoice_text",
        "show_billing_panel",
        "billing_to_menu",
        "list_billing_features",
        "create_billing_invoice",
        "claim_billing_payment",
        "beta_disabled",
    )

    specialist_tree = ast.parse(
        specialist_source
    )
    billing_tree = ast.parse(
        billing_source
    )

    specialist_names = {
        node.name
        for node in specialist_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }
    billing_names = {
        node.name
        for node in billing_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }

    for function_name in moved_definitions:
        assert function_name in specialist_names
        assert function_name not in billing_names

    specialist_position = bot_source.index(
        "dp.include_router(\n"
        "        specialist_billing_router"
    )
    billing_position = bot_source.index(
        "dp.include_router(billing_router)"
    )

    assert specialist_position < billing_position
