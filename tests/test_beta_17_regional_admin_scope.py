from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import column

from database.repositories.admin_scope import (
    AdminScopeAccessError,
    AdminScopeContext,
    AdminScopeRepository,
)
from database.repositories.billing import (
    BILLING_ADMIN_ROLES,
    BillingAccessError,
    BillingRepository,
)
from database.repositories.moderation import (
    BLOCK_USER_ROLES,
    ModerationAccessError,
    ModerationRepository,
)


def build_context(
    *,
    countries=(),
    languages=(),
    is_global=False,
):
    return AdminScopeContext(
        admin_user_id=uuid4(),
        tenant_id=uuid4(),
        is_global=is_global,
        country_ids=frozenset(countries),
        language_codes=frozenset(languages),
    )


def test_country_or_language_or_and_between_types():
    portugal = uuid4()
    spain = uuid4()
    germany = uuid4()

    context = build_context(
        countries=(portugal, spain),
        languages=("ru", "uk"),
    )

    assert context.allows(
        country_id=portugal,
        language_code="ru",
    )
    assert context.allows(
        country_id=spain,
        language_code="UK",
    )

    assert not context.allows(
        country_id=germany,
        language_code="ru",
    )
    assert not context.allows(
        country_id=portugal,
        language_code="en",
    )


def test_country_only_scope():
    portugal = uuid4()

    context = build_context(
        countries=(portugal,),
    )

    assert context.allows(
        country_id=portugal,
        language_code=None,
    )
    assert context.allows(
        country_id=portugal,
        language_code="en",
    )
    assert not context.allows(
        country_id=None,
        language_code="ru",
    )
    assert not context.allows(
        country_id=uuid4(),
        language_code="ru",
    )


def test_language_only_scope():
    context = build_context(
        languages=("ru", "uk"),
    )

    assert context.allows(
        country_id=None,
        language_code="ru",
    )
    assert context.allows(
        country_id=None,
        language_code=" UK ",
    )
    assert not context.allows(
        country_id=None,
        language_code=None,
    )
    assert not context.allows(
        country_id=uuid4(),
        language_code="en",
    )


def test_scope_access_is_fail_closed():
    empty_context = build_context()

    assert not empty_context.has_regional_access
    assert not empty_context.allows(
        country_id=uuid4(),
        language_code="ru",
    )

    country_context = build_context(
        countries=(uuid4(),),
    )
    language_context = build_context(
        languages=("ru",),
    )

    assert str(
        country_context.sql_predicate(
            country_column=None,
            language_column=column(
                "language_code"
            ),
        )
    ) == "false"

    assert str(
        language_context.sql_predicate(
            country_column=column(
                "country_id"
            ),
            language_column=None,
        )
    ) == "false"


def test_global_super_admin_allows_unknown_data():
    context = build_context(
        is_global=True,
    )

    assert context.allows(
        country_id=None,
        language_code=None,
    )
    assert str(
        context.sql_predicate()
    ) == "true"


def test_sql_predicate_contains_both_dimensions():
    context = build_context(
        countries=(uuid4(), uuid4()),
        languages=("ru", "uk"),
    )

    predicate = context.sql_predicate(
        country_column=column(
            "country_id"
        ),
        language_column=column(
            "language_code"
        ),
    )
    compiled = str(predicate).lower()

    assert "country_id is not null" in compiled
    assert "country_id in" in compiled
    assert "language_code is not null" in compiled
    assert "lower(language_code) in" in compiled
    assert " and " in compiled


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeScopeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.execute_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1

        if not self.responses:
            raise AssertionError(
                "Unexpected repository query"
            )

        return FakeResult(
            self.responses.pop(0)
        )


def admin_role_rows(role_id):
    return [
        SimpleNamespace(
            id=role_id,
            role="admin",
        )
    ]


async def test_repository_loads_active_scopes():
    role_id = uuid4()
    portugal = uuid4()

    session = FakeScopeSession(
        [
            admin_role_rows(role_id),
            [
                SimpleNamespace(
                    scope_type="country",
                    country_id=portugal,
                    language_code=None,
                ),
                SimpleNamespace(
                    scope_type="language",
                    country_id=None,
                    language_code="UK",
                ),
                SimpleNamespace(
                    scope_type="country",
                    country_id=None,
                    language_code=None,
                ),
            ],
        ]
    )

    context = await AdminScopeRepository(
        session
    ).get_context(
        admin_user_id=uuid4(),
        tenant_id=uuid4(),
    )

    assert not context.is_global
    assert context.country_ids == frozenset(
        {portugal}
    )
    assert context.language_codes == frozenset(
        {"uk"}
    )
    assert session.execute_calls == 2


async def test_repository_reloads_scopes_dynamically():
    role_id = uuid4()
    portugal = uuid4()
    spain = uuid4()

    session = FakeScopeSession(
        [
            admin_role_rows(role_id),
            [
                SimpleNamespace(
                    scope_type="country",
                    country_id=portugal,
                    language_code=None,
                ),
            ],
            admin_role_rows(role_id),
            [
                SimpleNamespace(
                    scope_type="country",
                    country_id=spain,
                    language_code=None,
                ),
                SimpleNamespace(
                    scope_type="language",
                    country_id=None,
                    language_code="nl",
                ),
            ],
        ]
    )
    repository = AdminScopeRepository(session)
    admin_user_id = uuid4()
    tenant_id = uuid4()

    first = await repository.get_context(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
    )
    second = await repository.get_context(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
    )

    assert first.country_ids == frozenset(
        {portugal}
    )
    assert not first.language_codes

    assert second.country_ids == frozenset(
        {spain}
    )
    assert second.language_codes == frozenset(
        {"nl"}
    )


async def test_admin_without_scope_has_no_access():
    role_id = uuid4()
    session = FakeScopeSession(
        [
            admin_role_rows(role_id),
            [],
        ]
    )

    context = await AdminScopeRepository(
        session
    ).get_context(
        admin_user_id=uuid4(),
        tenant_id=uuid4(),
    )

    assert not context.is_global
    assert not context.has_regional_access
    assert not context.allows(
        country_id=uuid4(),
        language_code="ru",
    )


async def test_non_admin_context_is_rejected():
    session = FakeScopeSession([[]])

    with pytest.raises(
        AdminScopeAccessError,
        match="Administrative access required",
    ):
        await AdminScopeRepository(
            session
        ).get_context(
            admin_user_id=uuid4(),
            tenant_id=uuid4(),
        )

    assert session.execute_calls == 1


async def test_super_admin_context_is_global():
    session = FakeScopeSession(
        [
            [
                SimpleNamespace(
                    id=uuid4(),
                    role="super_admin",
                )
            ]
        ]
    )

    context = await AdminScopeRepository(
        session
    ).get_context(
        admin_user_id=uuid4(),
        tenant_id=uuid4(),
    )

    assert context.is_global
    assert context.allows(
        country_id=None,
        language_code=None,
    )
    assert session.execute_calls == 1


async def test_regional_admin_has_no_finance_access():
    assert BILLING_ADMIN_ROLES == {
        "super_admin",
        "finance_admin",
    }

    repository = BillingRepository(
        SimpleNamespace()
    )
    repository.get_billing_admin_roles = (
        AsyncMock(return_value=set())
    )

    tenant_id = uuid4()

    with pytest.raises(
        BillingAccessError,
        match="Billing admin access denied",
    ):
        await repository.require_billing_admin(
            uuid4(),
            tenant_id=tenant_id,
        )

    repository.get_billing_admin_roles = (
        AsyncMock(
            return_value={"finance_admin"}
        )
    )

    assert await repository.require_billing_admin(
        uuid4(),
        tenant_id=tenant_id,
    ) == {"finance_admin"}


async def test_super_admin_cannot_mutate_roles_or_scopes():
    assert BLOCK_USER_ROLES == {
        "super_admin"
    }

    repository = ModerationRepository(
        SimpleNamespace()
    )
    actor_id = uuid4()

    calls = [
        repository.grant_admin_role(
            admin_user_id=actor_id,
            tenant_id=uuid4(),
            target_platform_user_id="123",
            role="admin",
            reason="Must use Root CLI",
        ),
        repository.revoke_admin_role(
            admin_user_id=actor_id,
            tenant_id=uuid4(),
            target_platform_user_id="123",
            role="admin",
            reason="Must use Root CLI",
        ),
        repository.grant_super_admin_user_role(
            admin_user_id=actor_id,
            tenant_id=uuid4(),
            target_user_id=uuid4(),
            role="moderator",
            reason="Must use Root CLI",
        ),
        repository.revoke_super_admin_user_role(
            admin_user_id=actor_id,
            tenant_id=uuid4(),
            target_user_id=uuid4(),
            role="moderator",
            reason="Must use Root CLI",
        ),
        repository.add_super_admin_role_scope(
            admin_user_id=actor_id,
            tenant_id=uuid4(),
            user_id=uuid4(),
            role="admin",
            scope_type="country",
            scope_value="PT",
            reason="Must use Root CLI",
        ),
        repository.revoke_super_admin_role_scope(
            admin_user_id=actor_id,
            tenant_id=uuid4(),
            scope_id=uuid4(),
            reason="Must use Root CLI",
        ),
    ]

    for call in calls:
        with pytest.raises(
            ModerationAccessError,
            match="Root CLI",
        ):
            await call
