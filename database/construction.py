import re

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Table, text


CONSTRUCTION_TABLE_PREFIX = "construction_"
CONSTRUCTION_TENANT_COLUMN = "tenant_id"
CONSTRUCTION_RLS_REQUIRED = True


def construction_table_name(
    entity_name: str,
) -> str:
    return (
        f"{CONSTRUCTION_TABLE_PREFIX}"
        f"{entity_name}"
    )



@dataclass(frozen=True)
class ConstructionRlsContract:
    enabled: bool
    forced: bool
    policies: tuple[str, ...]
    client_grants: tuple[str, ...]


CONSTRUCTION_RLS_CONTRACT = (
    ConstructionRlsContract(
        enabled=True,
        forced=False,
        policies=(
            "construction_tenant_isolation",
        ),
        client_grants=(),
    )
)


class ConstructionTableContractError(
    ValueError,
):
    pass



def build_construction_table_security_sql(
    table_name: str,
) -> tuple[str, str, str]:
    normalized_name = (
        table_name.strip()
        if isinstance(table_name, str)
        else ""
    )

    if re.fullmatch(
        r"construction_[a-z][a-z0-9_]*",
        normalized_name,
    ) is None:
        raise ConstructionTableContractError(
            "Invalid Construction table name."
        )

    qualified_name = (
        f"public.{normalized_name}"
    )

    return (
        f"ALTER TABLE {qualified_name} "
        "ENABLE ROW LEVEL SECURITY;",
        f"REVOKE ALL ON TABLE "
        f"{qualified_name} "
        "FROM anon, authenticated;",
        "CREATE POLICY "
        "construction_tenant_isolation "
        f"ON {qualified_name} "
        "USING (tenant_id = "
        "current_setting("
        "'app.current_tenant_id'"
        ")::uuid) "
        "WITH CHECK (tenant_id = "
        "current_setting("
        "'app.current_tenant_id'"
        ")::uuid);",
    )


async def set_construction_tenant_context(
    session,
    *,
    tenant_id: UUID,
) -> None:
    if not isinstance(tenant_id, UUID):
        raise TypeError(
            "Verified tenant UUID is required."
        )

    statement = text(
        "SELECT set_config("
        "'app.current_tenant_id', "
        ":tenant_id, true"
        ")"
    )

    await session.execute(
        statement,
        {
            "tenant_id": str(tenant_id),
        },
    )


def validate_construction_table_contract(
    table: Table,
) -> Table:
    if not table.name.startswith(
        CONSTRUCTION_TABLE_PREFIX
    ):
        raise ConstructionTableContractError(
            "Construction table prefix "
            "is required."
        )

    tenant_column = table.columns.get(
        CONSTRUCTION_TENANT_COLUMN
    )

    if (
        tenant_column is None
        or tenant_column.nullable
    ):
        raise ConstructionTableContractError(
            "Construction tenant_id must "
            "be non-nullable."
        )

    foreign_key_targets = {
        foreign_key.target_fullname
        for foreign_key
        in tenant_column.foreign_keys
    }

    if "tenants.id" not in foreign_key_targets:
        raise ConstructionTableContractError(
            "Construction tenant_id must "
            "reference tenants.id."
        )

    return table
