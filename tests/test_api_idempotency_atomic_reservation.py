from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql

from database.repositories.api_idempotency import ApiIdempotencyRepository
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
    ApiIdempotencyResponseCodec,
    ApiIdempotencyService,
    hash_request_payload,
)


def result(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


def session_for(*values):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[result(value) for value in values]),
        commit=AsyncMock(), rollback=AsyncMock(),
    )


def fields():
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    return dict(
        tenant_id=uuid4(), principal_type="user", principal_id=uuid4(),
        operation="construction.invitation.resend",
        key_hash="a" * 64, request_hash="b" * 64,
        now=now, expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_atomic_reservation_scopes_conflict_and_resets_only_expired_response():
    expected = object()
    session = session_for(expected)
    request = fields()
    record, is_new = await ApiIdempotencyRepository(session).reserve_record(**request)

    assert record is expected
    assert is_new is True
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))
    assert (
        "ON CONFLICT (tenant_id, principal_type, principal_id, operation, key_hash)"
        " DO UPDATE SET"
    ) in sql
    assert "WHERE api_idempotency_records.expires_at <=" in sql
    assert "RETURNING" in sql
    for name in ("response_status", "response_ciphertext", "completed_at"):
        assert name + " = NULL" in sql
    assert "status = 'processing'" in sql
    assert str(request["tenant_id"]) in sql
    assert str(request["principal_id"]) in sql
    assert request["operation"] in sql
    assert request["key_hash"] in sql
    assert statement.get_execution_options()["populate_existing"] is True
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_reservation_is_locked_and_refreshed_without_replacement():
    winner = object()
    session = session_for(None, winner)
    request = fields()
    record, is_new = await ApiIdempotencyRepository(session).reserve_record(**request)

    assert record is winner
    assert is_new is False
    assert session.execute.await_count == 2
    lookup = session.execute.await_args_list[1].args[0]
    sql = str(lookup.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))
    assert "FOR UPDATE" in sql
    assert "api_idempotency_records.expires_at >" in sql
    assert str(request["tenant_id"]) in sql
    assert str(request["principal_id"]) in sql
    assert request["operation"] in sql
    assert request["key_hash"] in sql
    assert lookup.get_execution_options()["populate_existing"] is True
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_unresolved_reservation_fails_without_new_insert_or_commit():
    session = session_for(None, None)
    with pytest.raises(RuntimeError, match="could not be resolved"):
        await ApiIdempotencyRepository(session).reserve_record(**fields())
    assert session.execute.await_count == 2
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["fresh", "completed", "processing", "conflict"])
async def test_service_uses_atomic_repository_and_preserves_replay_contract(state):
    request = fields()
    payload = {"invitation_id": str(uuid4()), "expected_row_version": 4}
    saved_response = {"id": payload["invitation_id"], "row_version": 6}
    codec = ApiIdempotencyResponseCodec(
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    record = SimpleNamespace(
        request_hash=hash_request_payload(
            payload if state != "conflict" else {"different": True}
        ),
        status="processing" if state in ("fresh", "processing") else "completed",
        response_status=200,
        response_ciphertext=codec.encrypt(saved_response),
    )
    before = dict(vars(record))
    session = session_for(record) if state == "fresh" else session_for(None, record)
    service = ApiIdempotencyService(
        repository=ApiIdempotencyRepository(session), response_codec=codec,
    )
    arguments = dict(
        tenant_id=request["tenant_id"], principal_type="user",
        principal_id=request["principal_id"], operation=request["operation"],
        idempotency_key="atomic-resend-key", payload=payload, now=request["now"],
    )

    if state == "conflict":
        with pytest.raises(ApiIdempotencyKeyReusedError):
            await service.reserve(**arguments)
    else:
        reservation = await service.reserve(**arguments)
        assert reservation.record is record
        assert reservation.is_replay is (state != "fresh")
        if state == "completed":
            assert reservation.response_status == 200
            assert reservation.response_payload == saved_response
        else:
            assert reservation.response_status is None
            assert reservation.response_payload is None

    assert vars(record) == before
    assert session.execute.await_count == (1 if state == "fresh" else 2)
    statement = session.execute.await_args_list[0].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "ON CONFLICT" in str(compiled)
    assert compiled.params["request_hash"] == hash_request_payload(payload)
    assert compiled.params["expires_at"] == request["now"] + timedelta(hours=24)
    assert "atomic-resend-key" not in compiled.params.values()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
