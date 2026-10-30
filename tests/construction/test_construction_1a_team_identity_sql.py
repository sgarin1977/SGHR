from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine

from database.models import (
    ApiEmailAuthChallenge, EventLog, User, UserAccount, UserRoleMapping,
)
from database.repositories.user import UserRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "otp", "magic_link", "unverified", "pending",
    "wrong_user", "wrong_email", "wrong_requested_user",
    "used_without_timestamp", "wrong_platform",
    "foreign", "membership", "revoked", "inactive", "not_requested",
])
async def test_c004_identity_sql_verifies_email_and_tenant(scenario):
    tenant_id, other_tenant, user_id, other_user = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    challenge_id = uuid4()
    email = "team@example.com"
    metadata = MetaData()
    fields = {
        User: "id tenant_id status",
        UserAccount: (
            "id user_id platform platform_user_id email display_name source"
        ),
        UserRoleMapping: "id user_id tenant_id status",
        ApiEmailAuthChallenge: (
            "id requested_user_id email status used_at challenge_type"
        ),
        EventLog: "id user_id entity_id entity_type event_type platform",
    }
    tables = {}
    for model, names in fields.items():
        original = model.__table__
        tables[model] = Table(
            original.name, metadata,
            *[Column(name, original.c[name].type) for name in names.split()],
            schema=original.schema,
        )

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(tables[User].insert(), {
                "id": user_id,
                "tenant_id": other_tenant if scenario in (
                    "foreign", "membership", "revoked"
                ) else tenant_id,
                "status": "suspended" if scenario == "inactive" else "active",
            })
            connection.execute(tables[UserAccount].insert(), {
                "id": uuid4(), "user_id": user_id,
                "platform": "telegram" if scenario == "wrong_platform" else "email",
                "platform_user_id": email, "email": email,
                "display_name": "Member", "source": "api_email_link",
            })
            if scenario in ("membership", "revoked"):
                connection.execute(tables[UserRoleMapping].insert(), {
                    "id": uuid4(), "user_id": user_id, "tenant_id": tenant_id,
                    "status": "revoked" if scenario == "revoked" else "active",
                })
            connection.execute(tables[ApiEmailAuthChallenge].insert(), {
                "id": challenge_id,
                "requested_user_id": other_user
                if scenario == "wrong_requested_user" else user_id,
                "email": "other@example.com" if scenario == "wrong_email" else email,
                "status": "pending" if scenario == "pending" else "used",
                "used_at": None if scenario in (
                    "pending", "used_without_timestamp"
                ) else datetime.now(timezone.utc),
                "challenge_type": "magic_link" if scenario == "magic_link" else "otp",
            })
            if scenario != "unverified":
                connection.execute(tables[EventLog].insert(), {
                    "id": uuid4(), "entity_id": challenge_id,
                    "user_id": other_user if scenario == "wrong_user" else user_id,
                    "entity_type": "api_email_auth_challenge",
                    "event_type": "api_email_auth_succeeded", "platform": "api",
                })

            class Session:
                async def execute(self, statement):
                    return connection.execute(statement)

            rows = await UserRepository(Session()).list_team_identity_accounts(
                tenant_id=tenant_id,
                user_ids=(other_user if scenario == "not_requested" else user_id,),
            )
            if scenario in ("foreign", "revoked", "inactive", "not_requested"):
                assert rows == []
            else:
                assert rows == [{
                    "user_id": user_id,
                    "display_name": "Member",
                    "verified_email": email if scenario in (
                        "otp", "magic_link", "membership"
                    ) else None,
                }]
    finally:
        engine.dispose()
