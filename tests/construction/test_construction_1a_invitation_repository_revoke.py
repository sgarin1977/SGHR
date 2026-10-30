from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from datetime import timezone

from database.models import ConstructionInvitation
from database.repositories.construction import (
    ConstructionInvitationRepository,
)
from services.construction.construction_invitations import (
    ConstructionInvitationTokenDeniedError,
    hash_construction_invitation_token,
    require_construction_invitation_token,
)
from test_construction_1a_invitation_repository_accept import (
    Adapter, invitation_store, stored_row,
)


def token_record(row):
    values = dict(row)
    # SQLite повертає дату без tzinfo; production PostgreSQL — з UTC.
    values["expires_at"] = values["expires_at"].replace(
        tzinfo=timezone.utc
    )
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_revoke_invalidates_valid_token_and_uses_orm_version(
    invitation_store,
):
    engine, table, invitation_id, now = invitation_store
    token = "private-local-test-token"

    with Session(engine) as session:
        session.execute(
            table.update().where(table.c.id == invitation_id).values(
                token_hash=hash_construction_invitation_token(token)
            )
        )
        session.commit()

        before = dict(stored_row(session, table, invitation_id))
        require_construction_invitation_token(
            invitation=token_record(before), token=token, as_of=now,
        )
        invitation = session.get(ConstructionInvitation, invitation_id)

        result = await ConstructionInvitationRepository(
            Adapter(session)
        ).revoke_invitation(invitation=invitation)

        after = dict(stored_row(session, table, invitation_id))
        assert result is invitation
        assert after["status"] == "revoked"
        assert after["row_version"] == before["row_version"] + 1
        assert invitation.row_version == after["row_version"]

        for field in set(before) - {"status", "row_version", "updated_at"}:
            assert after[field] == before[field]

        assert len(session.execute(select(table)).all()) == 1
        with pytest.raises(ConstructionInvitationTokenDeniedError):
            require_construction_invitation_token(
                invitation=token_record(after), token=token, as_of=now,
            )


@pytest.mark.asyncio
async def test_revoke_is_rolled_back_by_caller(invitation_store):
    engine, table, invitation_id, now = invitation_store

    with Session(engine) as session:
        before = dict(stored_row(session, table, invitation_id))
        invitation = session.get(ConstructionInvitation, invitation_id)

        await ConstructionInvitationRepository(
            Adapter(session)
        ).revoke_invitation(invitation=invitation)

        assert stored_row(
            session, table, invitation_id
        )["status"] == "revoked"

        session.rollback()
        assert dict(stored_row(session, table, invitation_id)) == before


@pytest.mark.asyncio
async def test_stale_revoke_cannot_overwrite_newer_version(invitation_store):
    engine, table, invitation_id, now = invitation_store

    with Session(engine) as first, Session(engine) as second:
        first_invitation = first.get(
            ConstructionInvitation, invitation_id
        )
        stale_invitation = second.get(
            ConstructionInvitation, invitation_id
        )

        await ConstructionInvitationRepository(
            Adapter(first)
        ).revoke_invitation(invitation=first_invitation)
        first.commit()

        with pytest.raises(StaleDataError):
            await ConstructionInvitationRepository(
                Adapter(second)
            ).revoke_invitation(invitation=stale_invitation)
        second.rollback()

        after = stored_row(first, table, invitation_id)
        assert after["status"] == "revoked"
        assert after["row_version"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["accepted", "declined", "expired", "revoked"],
)
async def test_revoke_keeps_terminal_history_unchanged(
    invitation_store, status,
):
    engine, table, invitation_id, now = invitation_store

    with Session(engine) as session:
        session.execute(
            table.update().where(table.c.id == invitation_id).values(
                status=status
            )
        )
        session.commit()

        before = dict(stored_row(session, table, invitation_id))
        invitation = session.get(ConstructionInvitation, invitation_id)

        with pytest.raises(ValueError, match="pending"):
            await ConstructionInvitationRepository(
                Adapter(session)
            ).revoke_invitation(invitation=invitation)

        assert dict(stored_row(session, table, invitation_id)) == before
