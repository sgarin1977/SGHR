from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine

from database.models import ApiEmailAuthChallenge, EventLog, User, UserAccount
from database.repositories.user import UserRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "otp", "magic_link", "login", "unverified", "pending", "wrong_user",
    "wrong_email", "wrong_requested_user", "missing_used_at", "inactive",
])
async def test_c005_platform_email_proof_is_bound_to_active_user(scenario):
    user_id, other_user, challenge_id = uuid4(), uuid4(), uuid4()
    email = "person@example.com"
    metadata = MetaData()
    fields = {
        User: "id status",
        UserAccount: "id user_id platform platform_user_id email",
        ApiEmailAuthChallenge: "id requested_user_id email status used_at challenge_type",
        EventLog: "id user_id entity_id entity_type event_type platform",
    }
    tables = {}
    for model, names in fields.items():
        original = model.__table__
        tables[model] = Table(original.name, metadata,
            *[Column(name, original.c[name].type) for name in names.split()],
            schema=original.schema)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(tables[User].insert(), {
                "id": user_id, "status": "suspended" if scenario == "inactive" else "active"})
            connection.execute(tables[UserAccount].insert(), {
                "id": uuid4(), "user_id": user_id, "platform": "email",
                "platform_user_id": email, "email": email})
            connection.execute(tables[ApiEmailAuthChallenge].insert(), {
                "id": challenge_id,
                "requested_user_id": None if scenario == "login" else (
                    other_user if scenario == "wrong_requested_user" else user_id),
                "email": "other@example.com" if scenario == "wrong_email" else email,
                "status": "pending" if scenario == "pending" else "used",
                "used_at": None if scenario == "missing_used_at" else datetime.now(timezone.utc),
                "challenge_type": "magic_link" if scenario == "magic_link" else "otp"})
            if scenario != "unverified":
                connection.execute(tables[EventLog].insert(), {
                    "id": uuid4(), "entity_id": challenge_id,
                    "user_id": other_user if scenario == "wrong_user" else user_id,
                    "entity_type": "api_email_auth_challenge",
                    "event_type": "api_email_auth_succeeded", "platform": "api"})
            class Session:
                async def execute(self, statement):
                    return connection.execute(statement)
            verified = await UserRepository(Session()).has_verified_email(
                user_id=user_id, email=email)
            assert verified is (scenario in {"otp", "magic_link", "login"})
    finally:
        engine.dispose()
