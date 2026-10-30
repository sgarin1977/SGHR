from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from database.models import ConstructionInvitation
from database.repositories.construction import (
    ConstructionInvitationRepository,
)


class Adapter:
    def __init__(self, session):
        self.session = session

    async def flush(self):
        self.session.flush()

    async def commit(self):
        raise AssertionError("Repository must not commit.")

    async def rollback(self):
        raise AssertionError("Repository must not own rollback.")


@pytest.fixture
def invitation_store():
    metadata = MetaData()
    original = ConstructionInvitation.__table__
    table = Table(
        original.name,
        metadata,
        *[
            Column(
                column.name,
                JSON() if isinstance(column.type, JSONB) else column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in original.columns
        ],
        schema=original.schema,
    )
    engine = create_engine("sqlite:///:memory:")
    invitation_id = uuid4()
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(table.insert(), {
            "id": invitation_id,
            "tenant_id": uuid4(),
            "email": "member@example.com",
            "user_id": None,
            "role": "ADMIN",
            "status": "pending",
            "expires_at": now + timedelta(days=7),
            "grant_expires_at": now + timedelta(days=30),
            "token_hash": "a" * 64,
            "token_used_at": None,
            "delivery_metadata": {"attempt_count": 2},
            "created_by_user_id": uuid4(),
            "created_at": now,
            "updated_at": now,
            "row_version": 4,
        })

    try:
        yield engine, table, invitation_id, now
    finally:
        engine.dispose()


def stored_row(session, table, invitation_id):
    return session.execute(
        select(table).where(table.c.id == invitation_id)
    ).mappings().one()


@pytest.mark.asyncio
async def test_accept_uses_existing_record_and_orm_version(invitation_store):
    engine, table, invitation_id, now = invitation_store
    recipient = uuid4()

    with Session(engine) as session:
        before = dict(stored_row(session, table, invitation_id))
        invitation = session.get(ConstructionInvitation, invitation_id)

        result = await ConstructionInvitationRepository(
            Adapter(session)
        ).accept_invitation(
            invitation=invitation,
            user_id=recipient,
            accepted_at=now,
        )

        after = stored_row(session, table, invitation_id)
        assert result is invitation
        assert after["id"] == invitation_id
        assert after["user_id"] == recipient
        assert after["status"] == "accepted"
        assert after["token_used_at"] == now.replace(tzinfo=None)
        assert after["row_version"] == before["row_version"] + 1
        assert invitation.row_version == after["row_version"]

        for field in (
            "tenant_id", "email", "role", "expires_at",
            "grant_expires_at", "token_hash", "delivery_metadata",
            "created_by_user_id", "created_at",
        ):
            assert after[field] == before[field]

        assert len(session.execute(select(table)).all()) == 1


@pytest.mark.asyncio
async def test_caller_rollback_restores_pending_invitation(invitation_store):
    engine, table, invitation_id, now = invitation_store

    with Session(engine) as session:
        before = dict(stored_row(session, table, invitation_id))
        invitation = session.get(ConstructionInvitation, invitation_id)

        await ConstructionInvitationRepository(
            Adapter(session)
        ).accept_invitation(
            invitation=invitation,
            user_id=uuid4(),
            accepted_at=now,
        )
        assert stored_row(
            session, table, invitation_id
        )["status"] == "accepted"

        session.rollback()
        after = dict(stored_row(session, table, invitation_id))
        assert after == before


@pytest.mark.asyncio
async def test_stale_accept_cannot_overwrite_first_recipient(invitation_store):
    engine, table, invitation_id, now = invitation_store
    first_recipient, second_recipient = uuid4(), uuid4()

    with Session(engine) as first, Session(engine) as second:
        first_invitation = first.get(
            ConstructionInvitation, invitation_id
        )
        stale_invitation = second.get(
            ConstructionInvitation, invitation_id
        )
        assert first_invitation.row_version == stale_invitation.row_version

        await ConstructionInvitationRepository(
            Adapter(first)
        ).accept_invitation(
            invitation=first_invitation,
            user_id=first_recipient,
            accepted_at=now,
        )
        first.commit()

        with pytest.raises(StaleDataError):
            await ConstructionInvitationRepository(
                Adapter(second)
            ).accept_invitation(
                invitation=stale_invitation,
                user_id=second_recipient,
                accepted_at=now,
            )
        second.rollback()

        after = stored_row(first, table, invitation_id)
        assert after["status"] == "accepted"
        assert after["user_id"] == first_recipient
        assert after["row_version"] == 5
