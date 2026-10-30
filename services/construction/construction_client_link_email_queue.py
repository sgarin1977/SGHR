"""Atomic email outbox using existing jobs and encrypted replay storage.

The job contains identifiers only. Email and the secret-free issuance command
are retained in the existing encrypted idempotency receipt. The one-time token
is never stored in the job or receipt, including in encrypted form.

This module enqueues; it does not send mail, start a worker or commit.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hmac
from uuid import UUID, uuid4

from database.models import ConstructionBackgroundJobRecord
from services.api_idempotency import hash_request_payload
from services.construction.construction_client_link_email import normalize_client_link_email
from services.construction.construction_client_link_issuance_replay import (
    ConstructionClientLinkTokenDeriver,
)
from services.construction.construction_client_link_request_issuance import (
    CLIENT_LINK_CONFIRMATION_TTL,
    ConstructionClientLinkIssuanceResult,
    hash_client_link_confirmation_token,
)
from services.construction.construction_clients import ConstructionClientOperationError

JOB_TYPE = "CLIENT_LINK_CONFIRMATION_EMAIL"
RESOURCE_TYPE = "construction_client_user_link_request"
KEY_PREFIX = "client-link-email"

def unavailable():
    return ConstructionClientOperationError(
        "Client link email queue context is invalid."
    )

def utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None or value.utcoffset() is None
    ):
        raise unavailable()
    return value.astimezone(timezone.utc)

def email_job_key(*, reservation_id, request_id):
    if not isinstance(reservation_id, UUID) or not isinstance(request_id, UUID):
        raise unavailable()
    return f"{KEY_PREFIX}:{reservation_id}:{request_id}"

def parse_email_job_key(value):
    if not isinstance(value, str):
        raise unavailable()
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise unavailable()
    try:
        reservation_id, request_id = UUID(parts[1]), UUID(parts[2])
    except (ValueError, TypeError, AttributeError):
        raise unavailable() from None
    if value != email_job_key(
        reservation_id=reservation_id, request_id=request_id,
    ):
        raise unavailable()
    return reservation_id, request_id

class ConstructionClientLinkEmailQueue:
    def __init__(self, *, session, queue, token_deriver, now_provider=None):
        if (
            session is None or getattr(queue, "session", None) is not session
            or not callable(getattr(queue, "enqueue", None))
            or not callable(getattr(session, "in_transaction", None))
            or not isinstance(token_deriver, ConstructionClientLinkTokenDeriver)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Email queue requires shared-session wiring.")
        self.session = session
        self.queue = queue
        self.deriver = token_deriver
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _bound(self, *, actor, material, reservation, payload):
        from services.construction.construction_client_link_issuance_workflow import (
            ConstructionClientLinkIssuanceWorkflow, ISSUE,
        )
        if (
            self.session.in_transaction() is not True
            or not isinstance(material, ConstructionClientLinkIssuanceResult)
            or not isinstance(payload, dict)
            or set(payload) != {
                "client_id", "target_email", "expected_row_version",
                "replacement", "previous_user_id",
            }
            or normalize_client_link_email(payload["target_email"])
                != payload["target_email"]
            or type(payload["replacement"]) is not bool
            or type(payload["expected_row_version"]) is not int
            or getattr(actor, "status", None) != "active"
        ):
            raise unavailable()
        record = getattr(reservation, "record", None)
        state = material.request
        if (
            not isinstance(getattr(record, "id", None), UUID)
            or record.tenant_id != state.tenant_id
            or record.principal_id != state.initiated_by_user_id
            or record.principal_type != "user" or record.operation != ISSUE
            or record.request_hash != hash_request_payload(payload)
            or record.status not in ("processing", "completed")
            or utc(record.created_at) > state.issued_at
            or state.expires_at - state.issued_at != CLIENT_LINK_CONFIRMATION_TTL
            or actor.user_id != state.initiated_by_user_id
            or actor.tenant_id != state.tenant_id
        ):
            raise unavailable()
        ConstructionClientLinkIssuanceWorkflow._context(
            material, actor,
            {**payload, "target_user_id": str(state.target_user_id)},
        )
        token = self.deriver.derive(
            reservation_id=record.id, reserved_at=record.created_at,
            tenant_id=state.tenant_id,
            initiated_by_user_id=state.initiated_by_user_id,
            payload=payload,
        )
        if (
            not hmac.compare_digest(token, material.token)
            or not hmac.compare_digest(
                hash_client_link_confirmation_token(token), state.token_hash,
            )
        ):
            raise unavailable()
        return record, state

    def validate_receipt(
        self, *, actor, material, reservation, payload, receipt,
    ):
        self._bound(
            actor=actor, material=material,
            reservation=reservation, payload=payload,
        )
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"version", "job_id", "command"}
            or type(receipt["version"]) is not int or receipt["version"] != 1
            or receipt["command"] != payload
            or not isinstance(receipt["job_id"], str)
        ):
            raise unavailable()
        try:
            job_id = UUID(receipt["job_id"])
        except (ValueError, TypeError, AttributeError):
            raise unavailable() from None
        if str(job_id) != receipt["job_id"]:
            raise unavailable()
        return job_id

    async def enqueue(self, *, actor, material, reservation, payload):
        record, state = self._bound(
            actor=actor, material=material,
            reservation=reservation, payload=payload,
        )
        now = utc(self.now_provider())
        if (
            getattr(reservation, "is_replay", None) is not False
            or record.status != "processing"
            or not state.issued_at <= now < state.expires_at
            or utc(record.expires_at) <= now
        ):
            raise unavailable()
        job = ConstructionBackgroundJobRecord(
            id=uuid4(), tenant_id=state.tenant_id,
            user_id=state.initiated_by_user_id,
            resource_type=RESOURCE_TYPE, resource_id=state.request_id,
            job_type=JOB_TYPE, status="QUEUED",
            idempotency_key=email_job_key(
                reservation_id=record.id, request_id=state.request_id,
            ),
            attempts=0, available_at=now, progress=0,
            lease_owner=None, lease_expires_at=None,
            error_code=None, result_reference=None,
            created_at=now, updated_at=now,
        )
        job_id = await self.queue.enqueue(job=job)
        if (
            not isinstance(job_id, UUID)
            or self.session.in_transaction() is not True
        ):
            raise unavailable()
        receipt = {
            "version": 1, "job_id": str(job_id),
            "command": deepcopy(payload),
        }
        self.validate_receipt(
            actor=actor, material=material, reservation=reservation,
            payload=payload, receipt=receipt,
        )
        return receipt

def build_construction_client_link_email_issuance_workflow(
    *, session, platform_session, encryption_key, now_provider=None,
):
    """Build the email-enabled workflow without activating a public route."""
    from services.construction.construction_client_link_issuance_workflow import (
        ConstructionClientLinkIssuanceWorkflow,
        build_construction_client_link_issuance_workflow,
    )
    from services.construction.construction_jobs import build_construction_job_queue

    base = build_construction_client_link_issuance_workflow(
        session=session, platform_session=platform_session,
        encryption_key=encryption_key, now_provider=now_provider,
    )
    delivery = ConstructionClientLinkEmailQueue(
        session=session,
        queue=build_construction_job_queue(session=session),
        token_deriver=base.deriver, now_provider=base.now_provider,
    )
    return ConstructionClientLinkIssuanceWorkflow(
        session=session, issuance_service=base.service,
        idempotency_service=base.idempotency,
        token_deriver=base.deriver, now_provider=base.now_provider,
        target_email_reader=base.target_email_reader,
        email_delivery_queue=delivery,
    )

# client_link_atomic_email_queue_v1
