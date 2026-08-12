from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pyotp
import pytest
from cryptography.fernet import Fernet
from unittest.mock import ANY

from services.root_recovery import (
    RootAuthenticationError,
    RootRecoveryService,
)
from services.root_security import RootSecurity


class FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class FakeRepository:
    def __init__(
        self,
        *,
        identity=None,
        root_session=None,
        recovery_codes=None,
    ):
        self.session = FakeSession()
        self.identity = identity
        self.root_session = root_session
        self.recovery_codes = recovery_codes or []
        self.events = []
        self.auth_session_created = False

    async def tenant_exists(self, tenant_id):
        return True

    async def get_identity_by_name(
        self,
        identity_name,
        *,
        for_update=False,
    ):
        return self.identity

    async def get_identity(
        self,
        identity_id,
        *,
        for_update=False,
    ):
        return self.identity

    async def expire_stale_sessions(
        self,
        *,
        root_identity_id,
        now,
    ):
        return []

    async def get_open_session(
        self,
        *,
        root_identity_id,
        for_update=False,
    ):
        return None

    async def get_session_by_token_hash(
        self,
        token_hash,
        *,
        for_update=False,
    ):
        return self.root_session

    async def list_available_recovery_codes(
        self,
        *,
        root_identity_id,
        for_update=False,
    ):
        return [
            code
            for code in self.recovery_codes
            if code.used_at is None
        ]

    async def mark_recovery_code_used(
        self,
        *,
        recovery_code,
        root_session_id,
        used_at,
    ):
        recovery_code.used_at = used_at
        recovery_code.used_by_session_id = (
            root_session_id
        )

    async def create_security_event(
        self,
        **values,
    ):
        self.events.append(values)

    async def create_auth_session(
        self,
        **values,
    ):
        self.auth_session_created = True
        raise AssertionError(
            "Authentication session was unexpected"
        )


@pytest.fixture
def root_security():
    return RootSecurity(
        encryption_key=(
            Fernet.generate_key().decode("ascii")
        ),
        totp_issuer="SGHR Test",
        password_min_length=14,
        recovery_code_count=3,
    )


def build_identity(
    root_security,
    *,
    with_totp=True,
):
    now = datetime.now(timezone.utc)
    secret = (
        root_security.generate_totp_secret()
    )

    return SimpleNamespace(
        id=uuid4(),
        status="active",
        password_hash=(
            root_security.hash_password(
                "Correct-Root-Password-123!"
            )
        ),
        totp_secret_encrypted=(
            root_security.encrypt_totp_secret(
                secret
            )
            if with_totp
            else None
        ),
        totp_confirmed_at=(
            now if with_totp else None
        ),
        failed_password_attempts=0,
        locked_until=None,
        last_authenticated_at=None,
        updated_at=now,
    )


def build_mfa_session(identity_id):
    now = datetime.now(timezone.utc)

    return SimpleNamespace(
        id=uuid4(),
        root_identity_id=identity_id,
        tenant_id=uuid4(),
        state="mfa_pending",
        expires_at=(
            now + timedelta(minutes=5)
        ),
        completed_at=None,
        mfa_attempts=0,
        mfa_method=None,
        mfa_verified_at=None,
        last_seen_at=now,
    )


def test_root_crypto_contract(root_security):
    password = "Correct-Root-Password-123!"
    password_hash = (
        root_security.hash_password(password)
    )

    assert password not in password_hash
    assert root_security.verify_password(
        password_hash,
        password,
    )
    assert not root_security.verify_password(
        password_hash,
        "Wrong-Root-Password-456!",
    )

    secret = root_security.generate_totp_secret()
    encrypted = (
        root_security.encrypt_totp_secret(
            secret
        )
    )

    assert encrypted != secret
    assert secret not in encrypted
    assert (
        root_security.decrypt_totp_secret(
            encrypted
        )
        == secret
    )

    current_code = pyotp.TOTP(secret).now()
    assert root_security.verify_totp(
        secret,
        current_code,
    )

    uri = root_security.build_totp_uri(
        identity_name="test-root",
        secret=secret,
    )
    qr_png = root_security.build_qr_png(uri)

    assert uri.startswith("otpauth://totp/")
    assert qr_png.startswith(b"\x89PNG")

    recovery_codes = (
        root_security.generate_recovery_codes()
    )

    assert len(recovery_codes) == 3
    assert len(set(recovery_codes)) == 3

    code_hash = (
        root_security.hash_recovery_code(
            recovery_codes[0]
        )
    )

    assert recovery_codes[0] not in code_hash
    assert root_security.verify_recovery_code(
        code_hash=code_hash,
        code=recovery_codes[0],
    )


async def test_root_without_totp_is_rejected(
    root_security,
):
    identity = build_identity(
        root_security,
        with_totp=False,
    )
    repository = FakeRepository(
        identity=identity
    )
    service = RootRecoveryService(
        repository,
        root_security,
    )

    with pytest.raises(
        RootAuthenticationError,
        match="Root Recovery is unavailable",
    ):
        await service.start_authentication(
            identity_name="test-root",
            password=(
                "Correct-Root-Password-123!"
            ),
            tenant_id=uuid4(),
        )

    assert not repository.auth_session_created
    assert repository.events == [
        {
            "root_identity_id": identity.id,
            "tenant_id": ANY,
            "event_type": (
                "root_password_authentication"
            ),
            "success": False,
            "reason_code": "root_unavailable",
        }
    ]


async def test_recovery_code_cannot_be_reused(
    root_security,
):
    identity = build_identity(root_security)
    plaintext_code = (
        root_security.generate_recovery_codes()[0]
    )
    stored_code = SimpleNamespace(
        code_hash=(
            root_security.hash_recovery_code(
                plaintext_code
            )
        ),
        used_at=None,
        used_by_session_id=None,
    )
    first_session = build_mfa_session(
        identity.id
    )
    repository = FakeRepository(
        identity=identity,
        root_session=first_session,
        recovery_codes=[stored_code],
    )
    service = RootRecoveryService(
        repository,
        root_security,
    )

    result = await service.verify_mfa(
        session_token="first-token",
        code=plaintext_code,
        use_recovery_code=True,
    )

    assert result.state == "reason_pending"
    assert stored_code.used_at is not None
    assert stored_code.used_by_session_id == (
        first_session.id
    )

    second_session = build_mfa_session(
        identity.id
    )
    repository.root_session = second_session

    with pytest.raises(
        RootAuthenticationError,
        match="Invalid Root authentication code",
    ):
        await service.verify_mfa(
            session_token="second-token",
            code=plaintext_code,
            use_recovery_code=True,
        )

    assert second_session.state == "mfa_pending"
    assert second_session.mfa_attempts == 1

    successful = [
        event
        for event in repository.events
        if event["success"] is True
    ]
    failed = [
        event
        for event in repository.events
        if event["success"] is False
    ]

    assert len(successful) == 1
    assert len(failed) == 1
    assert failed[0]["reason_code"] == (
        "invalid_mfa"
    )


async def test_expired_mfa_session_is_rejected(
    root_security,
):
    identity = build_identity(root_security)
    root_session = build_mfa_session(
        identity.id
    )
    root_session.expires_at = (
        datetime.now(timezone.utc)
        - timedelta(seconds=1)
    )
    repository = FakeRepository(
        identity=identity,
        root_session=root_session,
    )
    service = RootRecoveryService(
        repository,
        root_security,
    )

    with pytest.raises(
        RootAuthenticationError,
        match="Root Recovery session expired",
    ):
        await service.verify_mfa(
            session_token="expired-token",
            code="000000",
        )

    assert root_session.state == "expired"
    assert root_session.completed_at is not None
    assert root_session.mfa_attempts == 0
    assert repository.events[0][
        "event_type"
    ] == "root_session_expired"


async def test_empty_root_reason_is_rejected(
    root_security,
):
    identity = build_identity(root_security)
    root_session = build_mfa_session(
        identity.id
    )
    root_session.state = "reason_pending"

    repository = FakeRepository(
        identity=identity,
        root_session=root_session,
    )
    service = RootRecoveryService(
        repository,
        root_security,
    )

    with pytest.raises(
        RootAuthenticationError,
        match="Root Recovery reason is required",
    ):
        await service.activate_session(
            session_token="reason-token",
            reason="   ",
        )

    assert root_session.state == "reason_pending"
    assert not hasattr(root_session, "reason")
    assert repository.events[0] == {
        "root_identity_id": identity.id,
        "root_session_id": root_session.id,
        "tenant_id": root_session.tenant_id,
        "event_type": "root_reason_saved",
        "success": False,
        "reason_code": "reason_required",
    }
