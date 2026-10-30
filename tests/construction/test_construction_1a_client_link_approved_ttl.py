"""Approved 24-hour operator confirmation, separate from Telegram's token TTL."""
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from services.api_identity import ApiActorContext
from services.construction.construction_client_link_request_issuance import (
    CLIENT_LINK_CONFIRMATION_TTL,
    ConstructionClientLinkRequestIssuanceService,
    build_construction_client_link_request_issuance_service,
)
from services.construction.construction_client_link_issuance_workflow import build_construction_client_link_issuance_workflow
from services.construction.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from services.construction.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError
from test_construction_1a_client_link_request_issuance import issuance, issue
from test_construction_1a_client_link_issuance_workflow import issuance_pipeline, invoke, snapshot
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation, seed_request
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


def platform():
    return SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())


def test_service_and_workflow_factories_use_approved_ttl_without_environment_setting(issuance):
    c = issuance
    reader = platform()
    service = build_construction_client_link_request_issuance_service(session=c.session, platform_session=reader)
    workflow = build_construction_client_link_issuance_workflow(
        session=c.session, platform_session=reader, encryption_key=Fernet.generate_key().decode())
    assert CLIENT_LINK_CONFIRMATION_TTL == timedelta(hours=24)
    assert service.confirmation_lifetime == workflow.service.confirmation_lifetime == timedelta(hours=24)
    assert service.session is workflow.session is c.session
    assert service.identity.session is workflow.service.identity.session is reader
    c.session.execute.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.parametrize('lifetime', [timedelta(minutes=15), timedelta(hours=23),
    timedelta(hours=25), timedelta(days=2), timedelta(hours=24, microseconds=1)])
def test_unapproved_lifetime_is_rejected_before_reads_or_writes(issuance, lifetime):
    c = issuance
    with pytest.raises(ValueError):
        ConstructionClientLinkRequestIssuanceService(session=c.session,
            request_repository=c.requests, client_repository=c.clients,
            access_repository=c.access, event_repository=c.events,
            identity_reader=c.identity, confirmation_lifetime=lifetime)
    c.session.execute.assert_not_called()
    c.requests.create_request.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_creation_time_plus_exact_24_hours_is_the_stored_expiry(issuance):
    c = issuance
    result = await issue(c)
    assert result.request.expires_at - result.request.issued_at == timedelta(hours=24)
    assert c.requests.create_request.await_args.kwargs['expires_at'] == result.request.expires_at
    assert c.audit_rows[0].payload['expires_at'] == result.request.expires_at.isoformat()
    assert result.request.consumed_at is None


@pytest.mark.asyncio
async def test_instance_attribute_cannot_override_the_approved_new_request_expiry(issuance):
    c = issuance
    c.service.confirmation_lifetime = timedelta(minutes=15)
    result = await issue(c)
    assert result.request.expires_at - result.request.issued_at == timedelta(hours=24)


@pytest.mark.asyncio
async def test_target_can_confirm_near_end_of_the_approved_day(issuance_pipeline):
    p, c = issuance_pipeline, issuance_pipeline.c
    material = await invoke(p)
    envelope = ConstructionClientLinkRecipientContextCodec(encryption_key=p.key).issue_for_request(material=material)
    c.now = material.request.expires_at - timedelta(microseconds=1)
    if not c.sync.in_transaction():
        c.sync.begin()
    result = await p.confirm_workflow.confirm_request(
        actor=ApiActorContext(c.target, None, None, (), 'ru', None, 'active'),
        recipient_context=envelope.recipient_context, token=envelope.token,
        idempotency_key='confirm-within-24-hours')
    assert result.linked_user_id == c.target
    assert result.consumed_at == c.now


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [timedelta(0), timedelta(microseconds=1)])
async def test_confirm_at_or_after_expiry_cannot_link_or_consume(issuance_pipeline, offset):
    p, c = issuance_pipeline, issuance_pipeline.c
    material = await invoke(p)
    envelope = ConstructionClientLinkRecipientContextCodec(encryption_key=p.key).issue_for_request(material=material)
    c.now = material.request.expires_at + offset
    before = snapshot(p)
    if not c.sync.in_transaction():
        c.sync.begin()
    with pytest.raises(ConstructionClientLinkConfirmationDeniedError):
        await p.confirm_workflow.confirm_request(
            actor=ApiActorContext(c.target, None, None, (), 'ru', None, 'active'),
            recipient_context=envelope.recipient_context, token=envelope.token,
            idempotency_key='expired-confirmation')
    assert snapshot(p) == before


@pytest.mark.asyncio
async def test_issuance_replay_after_expiry_preserves_original_expiry_without_renewal(issuance_pipeline):
    p = issuance_pipeline
    first = await invoke(p)
    # Keep the test receipt longer than the token so that the replay, rather
    # than renewal of an expired idempotency reservation, is exercised.
    row = p.ledger.all()[0]
    row.expires_at = first.request.expires_at + timedelta(days=1)
    p.ledger.save(row)
    p.c.sync.commit()
    p.c.now = first.request.expires_at + timedelta(seconds=1)
    before = snapshot(p)
    second = await invoke(p)
    assert second == first and second.request.expires_at == first.request.expires_at
    assert snapshot(p) == before
