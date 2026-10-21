from datetime import datetime, timedelta, timezone

import pytest


def test_c005_invitation_constants_match_confirmed_contract():
    from services.construction_invitations import (
        CONSTRUCTION_INVITATION_STATUSES,
        CONSTRUCTION_INVITATION_DEFAULT_TTL,
    )

    assert CONSTRUCTION_INVITATION_STATUSES == frozenset({
        "pending", "accepted", "declined", "expired", "revoked",
    })
    assert CONSTRUCTION_INVITATION_DEFAULT_TTL == timedelta(days=7)


@pytest.mark.parametrize("status,seconds_until_expiry,expected", [
    ("pending", 1, "pending"),
    ("pending", 0, "expired"),
    ("pending", -1, "expired"),
    ("accepted", -1, "accepted"),
    ("declined", -1, "declined"),
    ("revoked", -1, "revoked"),
    ("expired", 1, "expired"),
])
def test_c005_effective_status_preserves_terminal_history(
    status, seconds_until_expiry, expected,
):
    from services.construction_invitations import (
        resolve_construction_invitation_status,
    )

    as_of = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    expires_at = as_of + timedelta(seconds=seconds_until_expiry)
    assert resolve_construction_invitation_status(
        status=status,
        expires_at=expires_at,
        as_of=as_of,
    ) == expected
