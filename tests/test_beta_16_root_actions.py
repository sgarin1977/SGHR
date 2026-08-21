from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from services.root_actions import RootActionService
from services.root_recovery import (
    ROOT_ACTION_TYPES,
    ROOT_MANAGED_ADMIN_ROLES,
    RootActionError,
    RootRecoveryService,
)
from services.root_role_actions import (
    RootRoleActionExecutor,
)
from services.root_security import RootSecurity


class FakeNestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.flushes = 0

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1

    def begin_nested(self):
        return FakeNestedTransaction()


class FakeRepository:
    def __init__(self, action):
        self.session = FakeSession()
        self.action = action
        self.events = []
        self.get_action_calls = 0

    async def get_action(
        self,
        *,
        action_id,
        root_session_id,
        for_update=False,
    ):
        self.get_action_calls += 1

        if (
            self.action
            and self.action.id == action_id
            and self.action.root_session_id
            == root_session_id
        ):
            return self.action

        return None

    async def create_security_event(
        self,
        **values,
    ):
        self.events.append(values)


@pytest.fixture
def root_security():
    return RootSecurity(
        encryption_key=(
            Fernet.generate_key().decode("ascii")
        ),
        totp_issuer="SGHR Test",
    )


def build_root_session():
    return SimpleNamespace(
        id=uuid4(),
        root_identity_id=uuid4(),
        tenant_id=uuid4(),
        state="active",
    )


def build_action(
    root_session,
    root_security,
    *,
    confirmation_token="confirmation-token",
    expires_delta=timedelta(minutes=5),
    action_type="restore_super_admin",
    action_payload=None,
):
    return SimpleNamespace(
        id=uuid4(),
        root_session_id=root_session.id,
        tenant_id=root_session.tenant_id,
        action_type=action_type,
        target_user_id=uuid4(),
        action_payload=action_payload or {},
        reason="Restore administrative access",
        confirmation_token_hash=(
            root_security.hash_token(
                confirmation_token
            )
        ),
        state="pending_confirmation",
        expires_at=(
            datetime.now(timezone.utc)
            + expires_delta
        ),
        confirmed_at=None,
        executed_at=None,
    )


def build_service(
    repository,
    root_security,
    root_session,
):
    service = RootActionService(
        repository,
        root_security,
    )
    service.recovery_service = SimpleNamespace(
        require_active_session=AsyncMock(
            return_value=root_session
        )
    )
    service.executor = SimpleNamespace(
        execute=AsyncMock(
            return_value={
                "role": "super_admin",
                "status": "active",
            }
        )
    )
    return service


def test_only_release_root_actions_are_allowed():
    assert ROOT_ACTION_TYPES == {
        "restore_super_admin",
        "grant_administrative_role",
        "revoke_administrative_role",
        "change_regional_admin_scopes",
    }

    assert ROOT_MANAGED_ADMIN_ROLES == {
        "super_admin",
        "admin",
        "moderator",
        "support",
        "finance_admin",
        "content_manager",
        "advertiser",
    }

    for role in ROOT_MANAGED_ADMIN_ROLES:
        normalized = (
            RootRecoveryService
            .normalize_action_payload(
                action_type=(
                    "grant_administrative_role"
                ),
                action_payload={"role": role},
            )
        )
        assert normalized == {"role": role}

    scopes = (
        RootRecoveryService
        .normalize_action_payload(
            action_type=(
                "change_regional_admin_scopes"
            ),
            action_payload={
                "country_codes": [
                    "pt",
                    " PT ",
                    "es",
                ],
                "language_codes": [
                    "UK",
                    " uk ",
                    "ru",
                ],
            },
        )
    )

    assert scopes == {
        "country_codes": ["ES", "PT"],
        "language_codes": ["ru", "uk"],
    }

    assert (
        RootRecoveryService
        .normalize_action_payload(
            action_type=(
                "change_regional_admin_scopes"
            ),
            action_payload={
                "country_codes": [],
                "language_codes": [],
            },
        )
        == {
            "country_codes": [],
            "language_codes": [],
        }
    )

    with pytest.raises(ValueError):
        RootRecoveryService.normalize_action_payload(
            action_type="grant_administrative_role",
            action_payload={"role": "client"},
        )

async def test_invalid_confirmation_never_executes(
    root_security,
):
    root_session = build_root_session()
    action = build_action(
        root_session,
        root_security,
    )
    repository = FakeRepository(action)
    service = build_service(
        repository,
        root_security,
        root_session,
    )

    with pytest.raises(
        RootActionError,
        match="Invalid confirmation token",
    ):
        await service.confirm_and_execute(
            session_token="active-session",
            action_id=action.id,
            confirmation_token="wrong-token",
        )

    assert action.state == (
        "pending_confirmation"
    )
    assert action.confirmed_at is None
    assert action.executed_at is None
    service.executor.execute.assert_not_awaited()
    assert repository.events[-1][
        "reason_code"
    ] == "invalid_confirmation_token"


async def test_expired_action_never_executes(
    root_security,
):
    root_session = build_root_session()
    action = build_action(
        root_session,
        root_security,
        expires_delta=timedelta(seconds=-1),
    )
    repository = FakeRepository(action)
    service = build_service(
        repository,
        root_security,
        root_session,
    )

    with pytest.raises(
        RootActionError,
        match="Root action expired",
    ):
        await service.confirm_and_execute(
            session_token="active-session",
            action_id=action.id,
            confirmation_token=(
                "confirmation-token"
            ),
        )

    assert action.state == "expired"
    assert action.confirmed_at is None
    assert action.executed_at is None
    service.executor.execute.assert_not_awaited()
    assert repository.events[-1][
        "event_type"
    ] == "root_action_expired"


async def test_valid_confirmation_executes_once(
    root_security,
):
    root_session = build_root_session()
    action = build_action(
        root_session,
        root_security,
    )
    repository = FakeRepository(action)
    service = build_service(
        repository,
        root_security,
        root_session,
    )

    result = await service.confirm_and_execute(
        session_token="active-session",
        action_id=action.id,
        confirmation_token=(
            "confirmation-token"
        ),
    )

    assert result.state == "executed"
    assert action.state == "executed"
    assert action.confirmed_at is not None
    assert action.executed_at is not None
    service.executor.execute.assert_awaited_once()

    execute_call = (
        service.executor.execute.await_args.kwargs
    )
    assert execute_call["action"] is action
    assert execute_call[
        "root_identity_id"
    ] == root_session.root_identity_id

    assert [
        event["event_type"]
        for event in repository.events
    ] == [
        "root_action_confirmed",
        "root_super_admin_recovered",
    ]
    assert repository.session.flushes == 1
    assert repository.session.commits == 1


@pytest.mark.parametrize(
    ("action_type", "event_type"),
    (
        (
            "grant_administrative_role",
            "root_administrative_role_granted",
        ),
        (
            "revoke_administrative_role",
            "root_administrative_role_revoked",
        ),
    ),
)
async def test_administrative_role_actions_are_audited(
    root_security,
    action_type,
    event_type,
):
    root_session = build_root_session()
    action = build_action(
        root_session,
        root_security,
        action_type=action_type,
        action_payload={"role": "moderator"},
    )
    repository = FakeRepository(action)
    service = build_service(
        repository,
        root_security,
        root_session,
    )
    service.executor.execute = AsyncMock(
        return_value={
            "role": "moderator",
            "status": (
                "active"
                if action_type.startswith("grant")
                else "revoked"
            ),
        }
    )

    result = await service.confirm_and_execute(
        session_token="active-session",
        action_id=action.id,
        confirmation_token=(
            "confirmation-token"
        ),
    )

    assert result.state == "executed"
    assert [
        event["event_type"]
        for event in repository.events
    ] == [
        "root_action_confirmed",
        event_type,
    ]

    final_event = repository.events[-1]
    assert final_event["success"] is True
    assert final_event["payload"][
        "action_type"
    ] == action_type
    assert final_event["payload"][
        "result"
    ]["role"] == "moderator"


async def test_cancelled_action_never_executes(
    root_security,
):
    root_session = build_root_session()
    action = build_action(
        root_session,
        root_security,
    )
    repository = FakeRepository(action)
    service = build_service(
        repository,
        root_security,
        root_session,
    )

    await service.cancel_action(
        session_token="active-session",
        action_id=action.id,
    )

    assert action.state == "cancelled"
    assert action.confirmed_at is None
    assert action.executed_at is None
    service.executor.execute.assert_not_awaited()
    assert repository.events == [
        {
            "root_identity_id": (
                root_session.root_identity_id
            ),
            "root_session_id": root_session.id,
            "root_action_id": action.id,
            "tenant_id": root_session.tenant_id,
            "target_user_id": (
                action.target_user_id
            ),
            "event_type": (
                "root_action_cancelled"
            ),
            "success": True,
            "payload": {
                "action_type": (
                    action.action_type
                )
            },
        }
    ]


class FakeRoleRepository:
    def __init__(
        self,
        *,
        target_user,
        roles=None,
        active_scopes=None,
        countries=None,
        languages=None,
        active_role_holders=2,
    ):
        self.session = FakeSession()
        self.target_user = target_user
        self.roles = roles or {}
        self.active_scopes = list(
            active_scopes or []
        )
        self.countries = countries or {}
        self.languages = languages or {}
        self.created_scope_values = []
        self.create_user_role_calls = 0
        self.active_role_holders = (
            active_role_holders
        )

    async def count_active_role_holders(
        self,
        *,
        tenant_id,
        role,
    ):
        return self.active_role_holders

    async def get_target_user(
        self,
        *,
        tenant_id,
        user_id,
        for_update=False,
    ):
        if (
            self.target_user.id == user_id
            and self.target_user.tenant_id
            == tenant_id
        ):
            return self.target_user

        return None

    async def get_latest_user_role(
        self,
        *,
        tenant_id,
        user_id,
        role,
        for_update=False,
    ):
        return self.roles.get(role)

    async def create_user_role(
        self,
        *,
        tenant_id,
        user_id,
        role,
        granted_at,
    ):
        self.create_user_role_calls += 1
        mapping = SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            status="active",
            granted_by=None,
            granted_at=granted_at,
        )
        self.roles[role] = mapping
        return mapping

    async def resolve_active_countries(
        self,
        country_codes,
    ):
        return {
            code: self.countries[code]
            for code in country_codes
            if code in self.countries
        }

    async def resolve_active_languages(
        self,
        language_codes,
    ):
        return {
            code: self.languages[code]
            for code in language_codes
            if code in self.languages
        }

    async def list_active_role_scopes(
        self,
        *,
        tenant_id,
        user_id,
        role,
        for_update=False,
    ):
        return [
            scope
            for scope in self.active_scopes
            if scope.status == "active"
        ]

    async def create_country_scope(
        self,
        **values,
    ):
        self.created_scope_values.append(
            ("country", values)
        )
        scope = SimpleNamespace(
            id=uuid4(),
            status="active",
            **values,
        )
        self.active_scopes.append(scope)
        return scope

    async def create_language_scope(
        self,
        **values,
    ):
        self.created_scope_values.append(
            ("language", values)
        )
        scope = SimpleNamespace(
            id=uuid4(),
            status="active",
            **values,
        )
        self.active_scopes.append(scope)
        return scope


def build_executor_action(
    *,
    tenant_id,
    target_user_id,
    action_type,
    payload=None,
):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        target_user_id=target_user_id,
        action_type=action_type,
        action_payload=payload or {},
        reason="Root Recovery action test",
        state="confirmed",
    )


def build_target_user():
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        status="active",
    )


async def test_restore_super_admin_is_idempotent():
    target = build_target_user()
    original_granted_at = datetime(
        2026,
        1,
        1,
    )
    original_granted_by = uuid4()
    existing_role = SimpleNamespace(
        id=uuid4(),
        tenant_id=target.tenant_id,
        user_id=target.id,
        role="super_admin",
        status="active",
        granted_by=original_granted_by,
        granted_at=original_granted_at,
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={
            "super_admin": existing_role
        },
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type="restore_super_admin",
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=uuid4(),
        now=datetime.now(timezone.utc),
    )

    assert result["status"] == "active"
    assert repository.create_user_role_calls == 0
    assert existing_role.granted_at == (
        original_granted_at
    )
    assert existing_role.granted_by == (
        original_granted_by
    )


@pytest.mark.parametrize(
    "role",
    sorted(ROOT_MANAGED_ADMIN_ROLES),
)
async def test_root_grants_every_administrative_role(
    role,
):
    target = build_target_user()
    repository = FakeRoleRepository(
        target_user=target,
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type="grant_administrative_role",
        payload={"role": role},
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=uuid4(),
        now=datetime.now(timezone.utc),
    )

    assert result["role"] == role
    assert result["status"] == "active"
    assert repository.roles[role].status == "active"


async def test_root_scope_change_records_root_actor():
    target = build_target_user()
    root_identity_id = uuid4()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={"admin": role},
        countries={
            "PT": SimpleNamespace(id=uuid4())
        },
        languages={
            "ru": SimpleNamespace()
        },
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "change_regional_admin_scopes"
        ),
        payload={
            "country_codes": ["PT"],
            "language_codes": ["ru"],
        },
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=root_identity_id,
        now=datetime.now(timezone.utc),
    )

    assert result["country_codes"] == ["PT"]
    assert result["language_codes"] == ["ru"]
    assert len(
        repository.created_scope_values
    ) == 2

    for _, values in (
        repository.created_scope_values
    ):
        assert values[
            "root_identity_id"
        ] == root_identity_id
        assert values["reason"] == action.reason

async def test_scope_change_preserves_history():
    target = build_target_user()
    root_identity_id = uuid4()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    old_scopes = [
        SimpleNamespace(
            id=uuid4(),
            status="active",
            revoked_by=None,
            revoked_by_root_identity_id=None,
            revoked_at=None,
        ),
        SimpleNamespace(
            id=uuid4(),
            status="active",
            revoked_by=None,
            revoked_by_root_identity_id=None,
            revoked_at=None,
        ),
    ]
    repository = FakeRoleRepository(
        target_user=target,
        roles={"admin": role},
        active_scopes=old_scopes,
        countries={
            "ES": SimpleNamespace(id=uuid4())
        },
        languages={
            "uk": SimpleNamespace()
        },
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "change_regional_admin_scopes"
        ),
        payload={
            "country_codes": ["ES"],
            "language_codes": ["uk"],
        },
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=root_identity_id,
        now=datetime.now(timezone.utc),
    )

    assert result["revoked_scopes"] == 2

    for scope in old_scopes:
        assert scope.status == "revoked"
        assert scope.revoked_by is None
        assert (
            scope.revoked_by_root_identity_id
            == root_identity_id
        )
        assert scope.revoked_at is not None

    assert len(
        repository.created_scope_values
    ) == 2


async def test_revoke_admin_role_revokes_scopes():
    target = build_target_user()
    root_identity_id = uuid4()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    active_scope = SimpleNamespace(
        id=uuid4(),
        status="active",
        revoked_by=None,
        revoked_by_root_identity_id=None,
        revoked_at=None,
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={"admin": role},
        active_scopes=[active_scope],
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "revoke_administrative_role"
        ),
        payload={"role": "admin"},
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=root_identity_id,
        now=datetime.now(timezone.utc),
    )

    assert role.status == "revoked"
    assert active_scope.status == "revoked"
    assert (
        active_scope
        .revoked_by_root_identity_id
        == root_identity_id
    )
    assert active_scope.revoked_at is not None
    assert result["revoked_scopes"] == 1

async def test_unknown_scope_is_rejected():
    target = build_target_user()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={"admin": role},
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "change_regional_admin_scopes"
        ),
        payload={
            "country_codes": ["XX"],
            "language_codes": [],
        },
    )

    with pytest.raises(
        RootActionError,
        match="Unknown or inactive country scope",
    ):
        await RootRoleActionExecutor(
            repository
        ).execute(
            action=action,
            root_identity_id=uuid4(),
            now=datetime.now(timezone.utc),
        )

    assert role.status == "active"
    assert repository.create_user_role_calls == 0
    assert not repository.created_scope_values


async def test_empty_scope_change_revokes_all_scopes():
    target = build_target_user()
    root_identity_id = uuid4()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    old_scope = SimpleNamespace(
        id=uuid4(),
        status="active",
        revoked_by=None,
        revoked_by_root_identity_id=None,
        revoked_at=None,
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={"admin": role},
        active_scopes=[old_scope],
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "change_regional_admin_scopes"
        ),
        payload={
            "country_codes": [],
            "language_codes": [],
        },
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=root_identity_id,
        now=datetime.now(timezone.utc),
    )

    assert result["revoked_scopes"] == 1
    assert result["created_scope_ids"] == []
    assert old_scope.status == "revoked"
    assert (
        old_scope.revoked_by_root_identity_id
        == root_identity_id
    )


async def test_last_super_admin_cannot_be_revoked():
    target = build_target_user()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={"super_admin": role},
        active_role_holders=1,
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "revoke_administrative_role"
        ),
        payload={"role": "super_admin"},
    )

    with pytest.raises(
        RootActionError,
        match="last active Super Admin",
    ):
        await RootRoleActionExecutor(
            repository
        ).execute(
            action=action,
            root_identity_id=uuid4(),
            now=datetime.now(timezone.utc),
        )

    assert role.status == "active"


async def test_non_last_super_admin_can_be_revoked():
    target = build_target_user()
    role = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    repository = FakeRoleRepository(
        target_user=target,
        roles={"super_admin": role},
        active_role_holders=2,
    )
    action = build_executor_action(
        tenant_id=target.tenant_id,
        target_user_id=target.id,
        action_type=(
            "revoke_administrative_role"
        ),
        payload={"role": "super_admin"},
    )

    result = await RootRoleActionExecutor(
        repository
    ).execute(
        action=action,
        root_identity_id=uuid4(),
        now=datetime.now(timezone.utc),
    )

    assert role.status == "revoked"
    assert result["role"] == "super_admin"
    assert result["status"] == "revoked"

@pytest.mark.asyncio
async def test_cli_rolls_back_before_cleanup():
    from scripts.root_recovery_cli import (
        cleanup_cli_after_exit,
    )

    events = []

    class FailedSession:
        async def rollback(self):
            events.append("rollback")

    class ActiveCli:
        async def cleanup(self):
            assert events == ["rollback"]
            events.append("cleanup")

    await cleanup_cli_after_exit(
        cli=ActiveCli(),
        session=FailedSession(),
    )

    assert events == [
        "rollback",
        "cleanup",
    ]
