from sqlalchemy import UniqueConstraint


def test_con_c001_identifier_model_matches_confirmed_contract():
    from database.models import TenantCompanyIdentifier

    table = TenantCompanyIdentifier.__table__
    assert table.name == "tenant_company_identifiers"
    expected_columns = {
        "id",
        "tenant_company_profile_id",
        "country_code",
        "identifier_type",
        "identifier_value",
        "identifier_value_normalized",
        "is_primary",
        "created_at",
        "updated_at",
    }
    assert set(table.c.keys()) == expected_columns
    assert table.c.identifier_value_normalized.computed is None
    assert table.c.country_code.type.length == 2
    assert table.c.identifier_type.type.length == 50
    assert table.c.identifier_value.type.length == 100

    unique_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert (
        "country_code",
        "identifier_type",
        "identifier_value_normalized",
    ) in unique_sets

    primary_indexes = [
        index for index in table.indexes
        if index.unique
        and {column.name for column in index.columns}
        == {
            "tenant_company_profile_id",
            "country_code",
            "identifier_type",
        }
    ]
    assert len(primary_indexes) == 1
    assert "is_primary" in str(
        primary_indexes[0].dialect_options["postgresql"]["where"]
    )
    assert any(
        fk.column.table.name == "tenant_company_profiles"
        for fk in table.c.tenant_company_profile_id.foreign_keys
    )
