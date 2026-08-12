from types import SimpleNamespace
from uuid import uuid4

import pytest

from database.repositories.user import (
    ADMINISTRATIVE_ROLES,
    UserRepository,
)
from scripts.seed_beta_data import (
    ensure_telegram_user,
    seed_admin_users,
)
from services.root_recovery import (
    ROOT_MANAGED_ADMIN_ROLES,
)
from services.user import UserService


class ForbiddenSeedSession:
    def __getattr__(self, name):
        raise AssertionError(
            "Admin seed attempted database access: "
            f"{name}"
        )


def test_telegram_admin_ids_do_not_grant_roles(
    monkeypatch,
):
    monkeypatch.setenv(
        "ADMIN_TELEGRAM_IDS",
        "123456789",
    )

    service = UserService(
        SimpleNamespace()
    )

    assert service.resolve_telegram_role(
        "123456789"
    ) == "client"


def test_existing_super_admin_can_open_panel():
    assert (
        UserService.resolve_staff_panel_role(
            None,
            {"client", "super_admin"},
        )
        == "super_admin"
    )


def test_root_and_user_repository_share_role_set():
    assert ADMINISTRATIVE_ROLES == (
        ROOT_MANAGED_ADMIN_ROLES
    )


@pytest.mark.parametrize(
    "role",
    sorted(ROOT_MANAGED_ADMIN_ROLES),
)
async def test_generic_role_activation_rejects_admin_roles(
    role,
):
    repository = UserRepository(
        SimpleNamespace()
    )

    with pytest.raises(
        PermissionError,
        match="Root CLI",
    ):
        await repository.ensure_active_role(
            user_id=uuid4(),
            tenant_id=uuid4(),
            role=role,
        )


@pytest.mark.parametrize(
    "role",
    sorted(ROOT_MANAGED_ADMIN_ROLES),
)
async def test_telegram_creation_rejects_admin_roles(
    role,
):
    repository = UserRepository(
        SimpleNamespace()
    )

    with pytest.raises(
        PermissionError,
        match="Root CLI",
    ):
        await repository.create_telegram_user_core(
            platform_user_id="123456789",
            username="test",
            first_name="Test",
            last_name="User",
            language_code="en",
            role=role,
        )


async def test_beta_seed_cannot_create_admin_users(
    capsys,
):
    await seed_admin_users(
        ForbiddenSeedSession(),
        str(uuid4()),
    )

    output = capsys.readouterr().out

    assert "managed only through Root CLI" in output

@pytest.mark.parametrize(
    "role",
    sorted(ROOT_MANAGED_ADMIN_ROLES),
)
async def test_raw_seed_rejects_admin_roles(
    role,
):
    with pytest.raises(
        PermissionError,
        match="Root CLI",
    ):
        await ensure_telegram_user(
            ForbiddenSeedSession(),
            tenant_id=str(uuid4()),
            platform_user_id="seed-admin-test",
            role=role,
            username="seed_admin_test",
            first_name="Seed",
            last_name="Admin",
        )

