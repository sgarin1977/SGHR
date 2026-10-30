"""Exact verified-email selection and existing issuance transaction/replay."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select, update

from database.models import (
    ApiEmailAuthChallenge, ConstructionClient, EventLog, User, UserAccount,
)
from database.repositories.construction.construction_client_link_email import (
    ConstructionClientLinkEmailRepository,
)
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_client_link_email import (
    ClientLinkPlatformUserNotFoundError, normalize_client_link_email,
)
from services.construction.construction_client_link_issuance_workflow import (
    ConstructionClientLinkIssuanceWorkflow,
)
from services.construction.construction_clients import (
    ConstructionClientOperationError, ConstructionClientValidationError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError

from test_construction_1a_client_link_issuance_workflow import (
    issuance_pipeline, snapshot,
)
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import (
    sql_confirmation, seed_request,
)
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db

EMAIL = "person@example.com"

@pytest.mark.parametrize("value", [
    " PERSON@EXAMPLE.COM ", "person@example.com",
])
def test_email_normalization_uses_existing_platform_invitation_rules(value):
    assert normalize_client_link_email(value) == EMAIL

@pytest.mark.parametrize("value", [
    None, "", "person", "person@", "person@example.com\nBcc:other@example.com",
])
def test_invalid_selector_is_rejected(value):
    with pytest.raises(ConstructionClientValidationError):
        normalize_client_link_email(value)

@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "otp", "magic_link", "login", "unverified", "pending", "wrong_user",
    "wrong_email", "wrong_requested_user", "missing_used_at",
    "inactive", "telegram_contact", "inactive_account", "prefix",
    "ambiguous",
])
async def test_native_local_query_requires_exact_canonical_verified_identity(scenario):
    user_id, other_user, challenge_id = uuid4(), uuid4(), uuid4()
    metadata = MetaData()
    fields = {
        User: "id status",
        UserAccount: "id user_id platform platform_user_id email status",
        ApiEmailAuthChallenge: "id requested_user_id email status used_at challenge_type",
        EventLog: "id user_id entity_id entity_type event_type platform",
    }
    tables = {}
    for model, names in fields.items():
        original = model.__table__
        tables[model] = Table(
            original.name, metadata,
            *[Column(name, original.c[name].type) for name in names.split()],
            schema=original.schema,
        )
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(tables[User].insert(), {
                "id": user_id,
                "status": "suspended" if scenario == "inactive" else "active",
            })
            connection.execute(tables[UserAccount].insert(), {
                "id": uuid4(), "user_id": user_id,
                "platform": "telegram" if scenario == "telegram_contact" else "email",
                "platform_user_id": EMAIL, "email": EMAIL,
                "status": "unlinked" if scenario == "inactive_account" else "active",
            })
            connection.execute(tables[ApiEmailAuthChallenge].insert(), {
                "id": challenge_id,
                "requested_user_id": None if scenario == "login" else (
                    other_user if scenario == "wrong_requested_user" else user_id
                ),
                "email": "other@example.com" if scenario == "wrong_email" else EMAIL,
                "status": "pending" if scenario == "pending" else "used",
                "used_at": None if scenario == "missing_used_at"
                else datetime.now(timezone.utc),
                "challenge_type": "magic_link" if scenario == "magic_link" else "otp",
            })
            if scenario != "unverified":
                connection.execute(tables[EventLog].insert(), {
                    "id": uuid4(), "entity_id": challenge_id,
                    "user_id": other_user if scenario == "wrong_user" else user_id,
                    "entity_type": "api_email_auth_challenge",
                    "event_type": "api_email_auth_succeeded", "platform": "api",
                })
            if scenario == "ambiguous":
                other_challenge = uuid4()
                connection.execute(tables[User].insert(), {
                    "id": other_user, "status": "active",
                })
                connection.execute(tables[UserAccount].insert(), {
                    "id": uuid4(), "user_id": other_user, "platform": "email",
                    "platform_user_id": EMAIL, "email": EMAIL, "status": "active",
                })
                connection.execute(tables[ApiEmailAuthChallenge].insert(), {
                    "id": other_challenge, "requested_user_id": other_user,
                    "email": EMAIL, "status": "used",
                    "used_at": datetime.now(timezone.utc), "challenge_type": "otp",
                })
                connection.execute(tables[EventLog].insert(), {
                    "id": uuid4(), "entity_id": other_challenge,
                    "user_id": other_user, "entity_type": "api_email_auth_challenge",
                    "event_type": "api_email_auth_succeeded", "platform": "api",
                })

            class Session:
                no_autoflush = nullcontext()
                async def execute(self, statement):
                    return connection.execute(statement)

            found = await ConstructionClientLinkEmailRepository(
                Session(),
            ).find_verified_user_id(
                email="pers@example.com" if scenario == "prefix" else EMAIL,
            )
            expected = user_id if scenario in {"otp", "magic_link", "login"} else None
            assert found == expected
    finally:
        engine.dispose()

@pytest.fixture
async def email_pipeline(issuance_pipeline):
    p = issuance_pipeline
    p.email_reader = NS(
        session=p.issue_identity.session,
        find_verified_user_id=AsyncMock(return_value=p.c.target),
    )
    p.workflow = ConstructionClientLinkIssuanceWorkflow(
        session=p.c.session, issuance_service=p.issue_service,
        idempotency_service=p.idempotency, token_deriver=p.deriver,
        now_provider=lambda: p.c.now, target_email_reader=p.email_reader,
    )
    return p

async def issue(p, **changes):
    c = p.c
    if not c.sync.in_transaction():
        c.sync.begin()
    fields = dict(
        actor=c.actor, access_context=c.context, client_id=c.client,
        target_email=EMAIL, expected_row_version=1,
        idempotency_key="email-client-link-request",
        trace_id="email-link-test",
    )
    fields.update(changes)
    return await p.workflow.request_email_link(**fields)

@pytest.mark.asyncio
async def test_email_issuance_saves_canonical_target_without_linking_client(email_pipeline):
    p, c = email_pipeline, email_pipeline.c
    result = await issue(p, target_email=" PERSON@EXAMPLE.COM ")
    assert result.request.target_user_id == c.target
    assert result.request.expires_at - result.request.issued_at == timedelta(hours=24)
    p.email_reader.find_verified_user_id.assert_awaited_once_with(email=EMAIL)
    client = c.sync.get(ConstructionClient, c.client)
    assert client.linked_user_id is None and client.row_version == 1
    assert len(c.audit_rows) == 1
    c.session.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_retry_preserves_original_target_and_never_resolves_a_new_owner(email_pipeline):
    p = email_pipeline
    first = await issue(p)
    before = snapshot(p)
    p.email_reader.find_verified_user_id.return_value = p.c.foreign_user
    replay = await issue(p, target_email=" PERSON@EXAMPLE.COM ")
    assert replay == first
    assert snapshot(p) == before
    p.email_reader.find_verified_user_id.assert_awaited_once()

@pytest.mark.asyncio
async def test_changed_email_same_key_conflicts_before_another_lookup(email_pipeline):
    p = email_pipeline
    await issue(p)
    before = snapshot(p)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await issue(p, target_email="other@example.com")
    assert snapshot(p) == before
    p.email_reader.find_verified_user_id.assert_awaited_once()

@pytest.mark.asyncio
async def test_missing_verified_target_rolls_back_reservation_and_request(email_pipeline):
    p = email_pipeline
    p.email_reader.find_verified_user_id.return_value = None
    before = snapshot(p)
    with pytest.raises(ClientLinkPlatformUserNotFoundError) as caught:
        await issue(p)
    assert caught.value.code == "PLATFORM_USER_NOT_FOUND"
    assert EMAIL not in str(caught.value)
    assert snapshot(p) == before
    p.c.session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_authorization_failure_prevents_email_lookup(email_pipeline):
    p = email_pipeline
    p.c.access.has_active_platform_membership.return_value = False
    with pytest.raises(ConstructionPermissionDeniedError):
        await issue(p)
    p.email_reader.find_verified_user_id.assert_not_awaited()

@pytest.mark.asyncio
async def test_stale_client_version_prevents_email_lookup(email_pipeline):
    p = email_pipeline
    before = snapshot(p)
    with pytest.raises(ConstructionRowVersionConflictError):
        await issue(p, expected_row_version=2)
    p.email_reader.find_verified_user_id.assert_not_awaited()
    assert snapshot(p) == before

@pytest.mark.asyncio
async def test_missing_key_prevents_email_lookup(email_pipeline):
    p = email_pipeline
    with pytest.raises(ConstructionClientValidationError):
        await issue(p, idempotency_key=None)
    p.email_reader.find_verified_user_id.assert_not_awaited()

@pytest.mark.asyncio
async def test_self_email_uses_existing_explicit_self_link_boundary(email_pipeline):
    p = email_pipeline
    p.email_reader.find_verified_user_id.return_value = p.c.operator
    before = snapshot(p)
    with pytest.raises(ConstructionClientValidationError):
        await issue(p)
    assert snapshot(p) == before

@pytest.mark.asyncio
async def test_replacement_keeps_previous_link_until_confirmation(email_pipeline):
    p, c = email_pipeline, email_pipeline.c
    c.sync.execute(
        update(ConstructionClient.__table__)
        .where(ConstructionClient.id == c.client)
        .values(linked_user_id=c.foreign_user)
    )
    result = await issue(p, previous_user_id=c.foreign_user)
    assert result.request.target_user_id == c.target
    assert result.request.expected_linked_user_id == c.foreign_user
    assert c.sync.get(ConstructionClient, c.client).linked_user_id == c.foreign_user

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_email_issuance_failures_roll_back_business_state(email_pipeline, failure):
    p = email_pipeline
    before = snapshot(p)
    if failure == "audit":
        p.c.events.create_event.side_effect = RuntimeError("private failure")
    elif failure == "complete":
        p.flags.fail_complete = True
    else:
        p.flags.fail_commit = True
    with pytest.raises(ConstructionClientOperationError):
        await issue(p)
    assert snapshot(p) == before

# client_link_exact_verified_email_v1
