from sqlalchemy.dialects.postgresql import JSONB


def test_c005_invitation_stores_token_hash_and_delivery_metadata():
    from database.models import ConstructionInvitation

    table = ConstructionInvitation.__table__
    assert {"token_hash", "token_used_at", "delivery_metadata"} <= (
        set(table.columns.keys())
    )
    assert not {"token", "raw_token", "invitation_token"} & (
        set(table.columns.keys())
    )
    assert not table.c.token_hash.nullable
    assert table.c.token_hash.type.python_type is str
    assert table.c.token_hash.type.length == 64
    assert table.c.token_used_at.nullable
    assert table.c.token_used_at.type.timezone is True

    metadata = table.c.delivery_metadata
    assert isinstance(metadata.type, JSONB)
    assert not metadata.nullable
    assert metadata.default is not None
    assert callable(metadata.default.arg)
    first = metadata.default.arg(None)
    second = metadata.default.arg(None)
    assert first == second == {}
    assert first is not second
