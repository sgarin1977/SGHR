from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c007_resend_records_one_platform_event_without_delivery_secrets():
    from services.construction.construction_audit import (
        CONSTRUCTION_AUDIT_EVENT_TYPES,
        ConstructionAuditChange,
        ConstructionAuditLogger,
    )

    actor = SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4(), status="active",
    )
    invitation_id = uuid4()
    events = []

    class Repository:
        async def create_event(self, **fields):
            events.append(fields)
            return SimpleNamespace(**fields)

        async def commit(self):
            pytest.fail("Audit logger must not commit.")

    old_expiry = "2026-10-02T12:00:00+00:00"
    new_expiry = "2026-10-08T12:00:00+00:00"
    await ConstructionAuditLogger(event_repository=Repository()).record_operation(
        actor=actor,
        event_type="construction_invitation_resent",
        entity_type="construction_invitation", entity_id=invitation_id,
        operation="resend",
        changes=(ConstructionAuditChange(
            field="expires_at", old_value=old_expiry, new_value=new_expiry,
        ),),
        trace_id="invitation-resend",
    )

    assert "construction_invitation_resent" not in CONSTRUCTION_AUDIT_EVENT_TYPES
    assert len(events) == 1
    event = events[0]
    assert event["tenant_id"] == actor.tenant_id
    assert event["user_id"] == actor.user_id
    assert event["entity_id"] == invitation_id
    assert event["event_type"] == "construction_invitation_resent"
    assert event["platform"] == "api"
    assert event["trace_id"] == "invitation-resend"
    assert event["payload"] == {
        "operation": "resend",
        "changes": [{
            "field": "expires_at",
            "old_value": old_expiry, "new_value": new_expiry,
        }],
        "reason": None,
    }
