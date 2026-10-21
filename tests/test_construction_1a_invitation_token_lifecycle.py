from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("status,seconds,used,matching,allowed", [
    ("pending", 1, False, True, True),
    ("pending", 1, False, False, False),
    ("pending", 1, True, True, False),
    ("pending", 0, False, True, False),
    ("pending", -1, False, True, False),
    ("accepted", 1, False, True, False),
    ("declined", 1, False, True, False),
    ("revoked", 1, False, True, False),
    ("expired", 1, False, True, False),
])
def test_c007_token_requires_live_pending_unused_invitation(
    status, seconds, used, matching, allowed,
):
    from services.construction_invitations import (
        ConstructionInvitationTokenDeniedError,
        generate_construction_invitation_token,
        hash_construction_invitation_token,
        require_construction_invitation_token,
    )

    as_of = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    token = generate_construction_invitation_token()
    invitation = SimpleNamespace(
        status=status,
        expires_at=as_of + timedelta(seconds=seconds),
        token_used_at=as_of if used else None,
        token_hash=hash_construction_invitation_token(token),
    )
    arguments = {
        "invitation": invitation,
        "token": token if matching else token + "changed",
        "as_of": as_of,
    }

    if allowed:
        assert require_construction_invitation_token(**arguments) is None
    else:
        with pytest.raises(ConstructionInvitationTokenDeniedError):
            require_construction_invitation_token(**arguments)
