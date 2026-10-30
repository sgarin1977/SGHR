from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitation_acceptance_context import (
    ConstructionInvitationAcceptanceContext,
)
from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationRecipientDeniedError,
    ConstructionInvitationTokenDeniedError,
    ConstructionInvitationValidationError,
    hash_construction_invitation_token,
)


@pytest.fixture
def context_case():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    actor = SimpleNamespace(
        user_id=UUID(int=1), tenant_id=UUID(int=9), status="active",
        permissions=(),
    )
    invitation = SimpleNamespace(
        id=UUID(int=3), tenant_id=UUID(int=2), email=" Person@EXAMPLE.COM ",
        status="pending", expires_at=now + timedelta(days=7),
        token_hash=hash_construction_invitation_token("private-test-token"),
        token_used_at=None, row_version=7, user_id=None,
        delivery_metadata={"ciphertext": "private-test-ciphertext"},
    )
    case = SimpleNamespace(
        actor=actor, invitation=invitation, now=now, order=[], calls=[],
        verified=True, proof_action=None,
    )
    session = SimpleNamespace(
        in_transaction=Mock(return_value=True),
        commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock(),
    )

    async def lock(**kwargs):
        case.order.append("lock")
        return case.invitation

    async def proof(**kwargs):
        case.order.append("proof")
        if case.proof_action is not None:
            case.proof_action()
        return (
            case.verified
            if kwargs == {"user_id": UUID(int=1), "email": "person@example.com"}
            else False
        )

    async def execute(statement, params):
        case.order.append("context")
        case.calls.append((statement, params))

    session.execute = AsyncMock(side_effect=execute)
    case.session = session
    case.repository = SimpleNamespace(
        session=session, get_invitation_for_update=AsyncMock(side_effect=lock),
    )
    case.reader = SimpleNamespace(
        session=object(), has_verified_email=AsyncMock(side_effect=proof),
    )
    case.context = ConstructionInvitationAcceptanceContext(
        session=session, repository=case.repository,
        identity_reader=case.reader, now_provider=lambda: case.now,
    )
    return case


async def prepare(case, **overrides):
    args = dict(
        actor=case.actor, tenant_id=UUID(int=2), invitation_id=UUID(int=3),
        expected_row_version=7, token="private-test-token",
    )
    args.update(overrides)
    return await case.context.prepare_pending_acceptance(**args)


def assert_no_lifecycle(case):
    case.session.commit.assert_not_awaited()
    case.session.rollback.assert_not_awaited()
    case.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_acceptance_context_verifies_before_binding_target(context_case):
    case = context_case
    before = vars(case.invitation).copy()
    result = await prepare(case)
    assert result is case.invitation
    assert vars(result) == before
    assert case.order == ["lock", "proof", "context"]
    case.repository.get_invitation_for_update.assert_awaited_once_with(
        tenant_id=UUID(int=2), invitation_id=UUID(int=3),
    )
    case.reader.has_verified_email.assert_awaited_once_with(
        user_id=UUID(int=1), email="person@example.com",
    )
    statement, params = case.calls[0]
    assert params == {
        "tenant_id": str(UUID(int=2)), "recipient_id": str(UUID(int=1)),
        "invitation_id": str(UUID(int=3)),
    }
    sql = " ".join(str(statement).split())
    assert sql.count("set_config(") == 3
    assert sql.count(", true)") == 3
    for name in (
        "app.current_tenant_id", "app.current_invitation_recipient_id",
        "app.current_invitation_id",
    ):
        assert name in sql
    assert "private-test" not in sql + repr(params)
    assert "person@" not in sql + repr(params)
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [
    None, SimpleNamespace(user_id=UUID(int=1), status="suspended"),
    SimpleNamespace(user_id=str(UUID(int=1)), status="active"),
])
async def test_acceptance_context_denies_invalid_identity(context_case, actor):
    case = context_case
    with pytest.raises(ConstructionInvitationRecipientDeniedError):
        await prepare(case, actor=actor)
    assert case.order == []
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "invitation_id"])
async def test_acceptance_context_requires_typed_context(context_case, field):
    case = context_case
    with pytest.raises(ConstructionInvitationValidationError):
        await prepare(case, **{field: str(UUID(int=2))})
    assert case.order == []
    assert_no_lifecycle(case)


@pytest.mark.asyncio
async def test_acceptance_context_requires_existing_transaction(context_case):
    case = context_case
    case.session.in_transaction.return_value = False
    with pytest.raises(ConstructionInvitationValidationError):
        await prepare(case)
    assert case.order == []
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["missing", "tenant_id", "id"])
async def test_acceptance_context_denies_wrong_locked_row(context_case, mismatch):
    case = context_case
    if mismatch == "missing":
        case.invitation = None
    else:
        setattr(case.invitation, mismatch, UUID(int=99))
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await prepare(case)
    assert case.order == ["lock"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "declined", "expired", "revoked"])
async def test_acceptance_context_denies_terminal_invitation(context_case, status):
    case = context_case
    case.invitation.status = status
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await prepare(case)
    assert case.order == ["lock"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wrong_token", "used_token", "expired"])
async def test_acceptance_context_denies_unavailable_token(context_case, failure):
    case = context_case
    overrides = {}
    if failure == "wrong_token":
        overrides["token"] = "wrong-private-token"
    elif failure == "used_token":
        case.invitation.token_used_at = case.now
    else:
        case.invitation.expires_at = case.now
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await prepare(case, **overrides)
    assert case.order == ["lock"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, 1, "true"])
async def test_acceptance_context_requires_canonical_email_proof(context_case, verified):
    case = context_case
    case.verified = verified
    with pytest.raises(ConstructionInvitationRecipientDeniedError):
        await prepare(case)
    assert case.order == ["lock", "proof"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
async def test_acceptance_context_denies_another_recipient(context_case):
    case = context_case
    case.actor.user_id = UUID(int=99)
    with pytest.raises(ConstructionInvitationRecipientDeniedError):
        await prepare(case)
    assert case.order == ["lock", "proof"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, True, 0, 6])
async def test_acceptance_context_rejects_stale_version(context_case, version):
    case = context_case
    with pytest.raises(ConstructionRowVersionConflictError) as exc:
        await prepare(case, expected_row_version=version)
    assert exc.value.status_code == 409
    assert exc.value.code == "ROW_VERSION_CONFLICT"
    assert case.order == ["lock", "proof"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
async def test_acceptance_context_rechecks_expiry_after_identity(context_case):
    case = context_case
    case.proof_action = lambda: setattr(case, "now", case.invitation.expires_at)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await prepare(case)
    assert case.order == ["lock", "proof"]
    assert_no_lifecycle(case)


@pytest.mark.asyncio
async def test_acceptance_context_binds_same_verified_user(context_case):
    case = context_case
    case.proof_action = lambda: setattr(case.actor, "user_id", UUID(int=99))
    await prepare(case)
    assert case.calls[0][1]["recipient_id"] == str(UUID(int=1))
    assert_no_lifecycle(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["lock", "proof", "context"])
async def test_acceptance_context_sanitizes_dependency_errors(context_case, stage):
    case = context_case
    target = {
        "lock": case.repository.get_invitation_for_update,
        "proof": case.reader.has_verified_email,
        "context": case.session.execute,
    }[stage]
    target.side_effect = RuntimeError("private-test-token private-test-ciphertext")
    with pytest.raises(ConstructionInvitationOperationError) as exc:
        await prepare(case)
    assert str(exc.value) == "Invitation acceptance preparation failed."
    assert exc.value.__suppress_context__ is True
    assert case.session.execute.await_count == (1 if stage == "context" else 0)
    assert_no_lifecycle(case)


@pytest.mark.parametrize("bad_wiring", ["repository", "identity_session", "clock"])
def test_acceptance_context_rejects_invalid_wiring(context_case, bad_wiring):
    case = context_case
    if bad_wiring == "repository":
        case.repository.session = object()
    if bad_wiring == "identity_session":
        case.reader.session = case.session
    clock = 42 if bad_wiring == "clock" else lambda: case.now
    with pytest.raises(ValueError):
        ConstructionInvitationAcceptanceContext(
            session=case.session, repository=case.repository,
            identity_reader=case.reader, now_provider=clock,
        )


def test_acceptance_context_factory_uses_same_invitation_session(context_case):
    from services.construction.construction_invitation_acceptance_context import (
        build_construction_invitation_acceptance_context,
    )
    from database.repositories.construction import ConstructionInvitationRepository
    case = context_case
    context = build_construction_invitation_acceptance_context(
        session=case.session, identity_reader=case.reader,
        now_provider=lambda: case.now,
    )
    assert isinstance(context.repository, ConstructionInvitationRepository)
    assert context.repository.session is case.session
    assert context.identity_reader is case.reader
