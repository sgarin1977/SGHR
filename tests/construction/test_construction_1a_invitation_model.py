from uuid import UUID


def test_c005_invitation_model_keeps_identity_and_expirations_separate():
    from database.models import ConstructionInvitation

    table = ConstructionInvitation.__table__
    assert table.name == "construction_invitations"
    required = {
        "id", "tenant_id", "email", "user_id", "role", "status",
        "expires_at", "grant_expires_at", "created_by_user_id",
        "created_at", "updated_at", "row_version",
    }
    assert required <= set(table.columns.keys())

    for name in ("id", "tenant_id", "user_id", "created_by_user_id"):
        assert table.c[name].type.python_type is UUID
    assert table.c.id.primary_key
    assert not table.c.tenant_id.nullable
    assert table.c.user_id.nullable
    assert not table.c.email.nullable
    assert table.c.email.type.length == 320
    assert not table.c.role.nullable
    assert not table.c.status.nullable
    assert table.c.status.default.arg == "pending"

    assert not table.c.expires_at.nullable
    assert table.c.grant_expires_at.nullable
    for name in ("expires_at", "grant_expires_at", "created_at", "updated_at"):
        assert table.c[name].type.timezone is True
    assert not table.c.row_version.nullable
    assert table.c.row_version.default.arg == 1
    assert ConstructionInvitation.__mapper__.version_id_col is table.c.row_version

    assert {fk.target_fullname for fk in table.c.tenant_id.foreign_keys} == {"tenants.id"}
    assert {fk.target_fullname for fk in table.c.user_id.foreign_keys} == {"users.id"}
    assert {fk.target_fullname for fk in table.c.created_by_user_id.foreign_keys} == {"users.id"}
