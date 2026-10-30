from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "own", "foreign_user", "unverified", "different_email", "inactive",
])
async def test_c005_acceptance_requires_actor_verified_invitation_email(scenario):
    from services.construction.construction_invitations import (
        ConstructionInvitationRecipientDeniedError,
        require_construction_invitation_email,
    )

    actor = SimpleNamespace(
        user_id=uuid4(),
        status="inactive" if scenario == "inactive" else "active",
    )
    proof_user = uuid4() if scenario == "foreign_user" else actor.user_id
    proof_email = (
        "other@example.com" if scenario == "different_email"
        else "person@example.com"
    )
    calls = []

    class IdentityReader:
        async def has_verified_email(self, *, user_id, email):
            calls.append((user_id, email))
            return (
                scenario != "unverified"
                and user_id == proof_user
                and email == proof_email
            )

    async def operation():
        await require_construction_invitation_email(
            actor=actor,
            invitation_email="  Person@EXAMPLE.COM  ",
            identity_reader=IdentityReader(),
        )

    if scenario == "own":
        await operation()
    else:
        with pytest.raises(ConstructionInvitationRecipientDeniedError):
            await operation()

    assert calls == (
        [] if scenario == "inactive"
        else [(actor.user_id, "person@example.com")]
    )
