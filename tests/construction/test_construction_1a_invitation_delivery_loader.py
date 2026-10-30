from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy.dialects import postgresql

from database.models import ConstructionBackgroundJobRecord, ConstructionInvitation
from database.repositories.construction import ConstructionInvitationRepository
from database.repositories.job_queue import JobLeaseLostError
from services.construction.construction_invitation_delivery import (
    ConstructionInvitationDeliveryTokenError, ConstructionInvitationTokenCodec,
)
from services.construction.construction_invitation_delivery_loader import (
    ConstructionInvitationDeliveryLoader,
    build_construction_invitation_delivery_loader,
)
from services.construction.construction_invitations import hash_construction_invitation_token


@pytest.fixture
def delivery():
    state = SimpleNamespace(
        now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
        token="private-invitation-delivery-token",
        key=Fernet.generate_key().decode("ascii"),
        statements=[], before_invitation=None,
    )
    state.codec = ConstructionInvitationTokenCodec(encryption_key=state.key)
    state.invitation = ConstructionInvitation(
        id=uuid4(), tenant_id=uuid4(), created_by_user_id=uuid4(),
        email="person@example.com", role="ADMIN", status="pending",
        expires_at=state.now + timedelta(days=7),
        grant_expires_at=None, token_used_at=None, row_version=1,
        token_hash=hash_construction_invitation_token(state.token),
        delivery_metadata={},
    )
    state.job = ConstructionBackgroundJobRecord(
        id=uuid4(), tenant_id=state.invitation.tenant_id,
        user_id=uuid4(), resource_type="construction_invitation",
        resource_id=state.invitation.id, job_type="INVITATION_EMAIL",
        status="RUNNING",
        idempotency_key=f"invitation:{state.invitation.id}:{state.invitation.token_hash}",
        attempts=1, available_at=state.now, progress=0,
        lease_owner="mail-worker",
        lease_expires_at=state.now + timedelta(minutes=5),
        error_code=None, result_reference=None,
        created_at=state.now, updated_at=state.now,
    )
    state.invitation.delivery_metadata = {
        "job_id": str(state.job.id),
        "token_ciphertext": state.codec.encrypt(
            tenant_id=state.invitation.tenant_id,
            invitation_id=state.invitation.id, token=state.token,
        ),
    }

    class Session:
        def __init__(self):
            self.in_transaction = Mock(return_value=True)
            self.commit = AsyncMock(side_effect=AssertionError("Unexpected commit"))
            self.rollback = AsyncMock(side_effect=AssertionError("Unexpected rollback"))
            self.flush = AsyncMock(side_effect=AssertionError("Unexpected flush"))

        async def execute(self, statement):
            state.statements.append(statement)
            model = statement.column_descriptions[0]["entity"]
            if model is ConstructionBackgroundJobRecord:
                row = state.job
            elif model is ConstructionInvitation:
                if state.before_invitation is not None:
                    state.before_invitation()
                row = state.invitation
            else:
                raise AssertionError("Unexpected database query")
            return SimpleNamespace(scalar_one_or_none=lambda: row)

    state.session = Session()
    state.repo = ConstructionInvitationRepository(state.session)
    state.codec.decrypt = Mock(wraps=state.codec.decrypt)
    state.loader = ConstructionInvitationDeliveryLoader(
        session=state.session, invitation_repository=state.repo,
        token_codec=state.codec, lease_owner="mail-worker",
        clock=lambda: state.now,
    )
    state.tenant_id = state.invitation.tenant_id
    state.job_id = state.job.id
    return state


def unchanged_transaction(state):
    state.session.commit.assert_not_awaited()
    state.session.rollback.assert_not_awaited()
    state.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_loader_reads_current_rows_with_tenant_locks(delivery):
    state = delivery
    metadata = deepcopy(state.invitation.delivery_metadata)
    material = await state.loader.load(
        tenant_id=state.tenant_id, job_id=state.job_id,
    )
    assert material.token == state.token
    assert material.invitation_id == state.invitation.id
    assert material.email == "person@example.com"
    assert state.token not in repr(material)
    assert state.invitation.delivery_metadata == metadata
    assert state.invitation.status == "pending"
    assert state.invitation.row_version == 1
    assert len(state.statements) == 2
    for statement, table, target in (
        (state.statements[0], "construction_background_jobs", state.job_id),
        (state.statements[1], "construction_invitations", state.invitation.id),
    ):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled).lower()
        assert "for update" in sql
        assert f"{table}.tenant_id =" in str(statement.whereclause).lower()
        assert f"{table}.id =" in str(statement.whereclause).lower()
        assert state.tenant_id in compiled.params.values()
        assert target in compiled.params.values()
        assert statement.get_execution_options()["populate_existing"] is True
    unchanged_transaction(state)


@pytest.mark.asyncio
async def test_resend_committed_while_waiting_rejects_old_job_without_decryption(delivery):
    state = delivery

    def resend():
        new_token = "replacement-private-token"
        state.invitation.token_hash = hash_construction_invitation_token(new_token)
        state.invitation.delivery_metadata = {
            "job_id": str(uuid4()),
            "token_ciphertext": state.codec.encrypt(
                tenant_id=state.tenant_id,
                invitation_id=state.invitation.id, token=new_token,
            ),
        }

    state.before_invitation = resend
    with pytest.raises(ConstructionInvitationDeliveryTokenError) as error:
        await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    state.codec.decrypt.assert_not_called()
    assert state.token not in str(error.value)
    assert "replacement-private-token" not in str(error.value)
    unchanged_transaction(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "accepted", "declined", "revoked", "expired", "expiry", "used",
])
async def test_current_invitation_state_is_checked_after_lock_wait(delivery, change):
    state = delivery

    def concurrent_change():
        if change == "expiry":
            state.invitation.expires_at = state.now
        elif change == "used":
            state.invitation.token_used_at = state.now
        else:
            state.invitation.status = change

    state.before_invitation = concurrent_change
    with pytest.raises(ConstructionInvitationDeliveryTokenError):
        await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    state.codec.decrypt.assert_not_called()
    unchanged_transaction(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "cancelled", "expired_while_waiting"])
async def test_lost_lease_stays_distinct_and_never_decrypts(delivery, change):
    state = delivery
    if change == "owner":
        state.job.lease_owner = "other-worker"
    elif change == "cancelled":
        state.job.status = "CANCELLED"
    else:
        state.before_invitation = lambda: setattr(
            state, "now", state.job.lease_expires_at,
        )
    with pytest.raises(JobLeaseLostError):
        await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    state.codec.decrypt.assert_not_called()
    unchanged_transaction(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["job", "invitation"])
async def test_missing_current_row_never_decrypts(delivery, missing):
    state = delivery
    setattr(state, missing, None)
    with pytest.raises(ConstructionInvitationDeliveryTokenError):
        await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    assert len(state.statements) == (1 if missing == "job" else 2)
    state.codec.decrypt.assert_not_called()
    unchanged_transaction(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "job_id"])
async def test_invalid_identifiers_do_not_query_database(delivery, field):
    state = delivery
    arguments = {"tenant_id": state.tenant_id, "job_id": state.job_id}
    arguments[field] = "invalid"
    with pytest.raises(ConstructionInvitationDeliveryTokenError):
        await state.loader.load(**arguments)
    assert state.statements == []
    state.codec.decrypt.assert_not_called()


@pytest.mark.asyncio
async def test_loader_requires_caller_transaction(delivery):
    state = delivery
    state.session.in_transaction.return_value = False
    with pytest.raises(ValueError, match="active transaction"):
        await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    assert state.statements == []
    unchanged_transaction(state)


@pytest.mark.asyncio
async def test_naive_clock_does_not_decrypt(delivery):
    state = delivery
    state.now = state.now.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    state.codec.decrypt.assert_not_called()
    unchanged_transaction(state)


def test_loader_rejects_repository_from_another_session(delivery):
    state = delivery
    with pytest.raises(ValueError, match="shared-session"):
        ConstructionInvitationDeliveryLoader(
            session=state.session,
            invitation_repository=ConstructionInvitationRepository(object()),
            token_codec=state.codec, lease_owner="mail-worker",
            clock=lambda: state.now,
        )


@pytest.mark.asyncio
async def test_factory_uses_shared_session_and_existing_codec(delivery):
    state = delivery
    loader = build_construction_invitation_delivery_loader(
        session=state.session, encryption_key=state.key,
        lease_owner="mail-worker", clock=lambda: state.now,
    )
    assert loader.invitation_repository.session is state.session
    material = await loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    assert material.token == state.token
    unchanged_transaction(state)
