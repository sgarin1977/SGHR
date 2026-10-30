from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.parametrize("status,target,seconds,allowed", [
    ("pending", "accepted", 1, True),
    ("pending", "declined", 1, True),
    ("pending", "revoked", 1, True),
    ("pending", "accepted", 0, False),
    ("pending", "declined", -1, False),
    ("pending", "revoked", 0, False),
    ("accepted", "revoked", 1, False),
    ("declined", "pending", 1, False),
    ("expired", "pending", 1, False),
    ("revoked", "pending", 1, False),
    ("pending", "expired", 1, False),
    ("pending", "expired", -1, False),
])
def test_c007_user_transition_requires_live_pending_invitation(
    status, target, seconds, allowed,
):
    from services.construction.construction_invitations import (
        ConstructionInvitationTransitionError,
        require_construction_invitation_transition,
    )

    as_of = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    arguments = {
        "status": status,
        "target_status": target,
        "expires_at": as_of + timedelta(seconds=seconds),
        "as_of": as_of,
    }
    if allowed:
        require_construction_invitation_transition(**arguments)
    else:
        with pytest.raises(ConstructionInvitationTransitionError):
            require_construction_invitation_transition(**arguments)
