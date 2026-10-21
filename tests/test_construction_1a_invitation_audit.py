from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c005_invitation_creation_uses_platform_audit():
    from services.construction_audit import ConstructionAuditLogger

    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())
    invitation_id = uuid4()
    events = []

    class EventRepository:
        async def create_event(self, **fields):
            events.append(fields)
            return SimpleNamespace(**fields)

    result = await ConstructionAuditLogger(
        event_repository=EventRepository(),
    ).record_operation(
        actor=actor,
        event_type="construction_invitation_created",
        entity_type="construction_invitation",
        entity_id=invitation_id,
        operation="create",
        details={"role": "SURVEYOR", "scope_count": 2},
        trace_id="invitation-create",
    )

    assert len(events) == 1
    event = events[0]
    assert result.entity_id == invitation_id
    assert event["event_type"] == "construction_invitation_created"
    assert event["tenant_id"] == actor.tenant_id
    assert event["user_id"] == actor.user_id
    assert event["entity_type"] == "construction_invitation"
    assert event["platform"] == "api"
    assert event["trace_id"] == "invitation-create"
    assert event["payload"] == {
        "operation": "create", "changes": [], "reason": None,
        "role": "SURVEYOR", "scope_count": 2,
    }
