from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint


def test_c005_invitation_scopes_support_multiple_projects_in_same_tenant():
    from database.models import ConstructionInvitationScope

    table = ConstructionInvitationScope.__table__
    assert table.name == "construction_invitation_scopes"
    assert {"id", "tenant_id", "invitation_id", "scope_type", "scope_id"} <= (
        set(table.columns.keys())
    )
    assert table.c.id.primary_key
    for name in ("id", "tenant_id", "invitation_id", "scope_id"):
        assert table.c[name].type.python_type is UUID
        assert not table.c[name].nullable
    assert not table.c.scope_type.nullable

    foreign_keys = [
        constraint for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and tuple(constraint.columns.keys()) == ("tenant_id", "invitation_id")
    ]
    assert len(foreign_keys) == 1
    assert [element.target_fullname for element in foreign_keys[0].elements] == [
        "construction_invitations.tenant_id",
        "construction_invitations.id",
    ]

    unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "invitation_id", "scope_type", "scope_id") in unique_keys
    assert ("invitation_id",) not in unique_keys
    assert ("tenant_id", "invitation_id") not in unique_keys
