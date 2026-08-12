from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import (
    and_,
    false,
    func,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from database.models import (
    Country,
    Language,
    RoleScope,
    UserRoleMapping,
)


class AdminScopeAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminScopeContext:
    admin_user_id: UUID
    tenant_id: UUID
    is_global: bool
    country_ids: frozenset[UUID]
    language_codes: frozenset[str]

    @property
    def has_regional_access(self) -> bool:
        return bool(
            self.country_ids
            or self.language_codes
        )

    def allows(
        self,
        *,
        country_id: UUID | None,
        language_code: str | None,
    ) -> bool:
        if self.is_global:
            return True

        if not self.has_regional_access:
            return False

        if self.country_ids:
            if country_id is None:
                return False

            if country_id not in self.country_ids:
                return False

        if self.language_codes:
            normalized_language = (
                language_code or ""
            ).strip().lower()

            if not normalized_language:
                return False

            if (
                normalized_language
                not in self.language_codes
            ):
                return False

        return True

    def sql_predicate(
        self,
        *,
        country_column=None,
        language_column=None,
    ) -> ColumnElement[bool]:
        if self.is_global:
            return true()

        if not self.has_regional_access:
            return false()

        conditions = []

        if self.country_ids:
            if country_column is None:
                return false()

            conditions.append(
                country_column.is_not(None)
            )
            conditions.append(
                country_column.in_(
                    tuple(self.country_ids)
                )
            )

        if self.language_codes:
            if language_column is None:
                return false()

            conditions.append(
                language_column.is_not(None)
            )
            conditions.append(
                func.lower(
                    language_column
                ).in_(
                    tuple(self.language_codes)
                )
            )

        if not conditions:
            return false()

        return and_(*conditions)


class AdminScopeRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_context(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
    ) -> AdminScopeContext:
        role_result = await self.session.execute(
            select(
                UserRoleMapping.id,
                UserRoleMapping.role,
            )
            .where(
                UserRoleMapping.tenant_id
                == tenant_id,
                UserRoleMapping.user_id
                == admin_user_id,
                UserRoleMapping.status
                == "active",
                UserRoleMapping.role.in_(
                    {
                        "admin",
                        "super_admin",
                    }
                ),
            )
        )
        role_rows = role_result.all()
        roles = {
            row.role
            for row in role_rows
        }

        if "super_admin" in roles:
            return AdminScopeContext(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                is_global=True,
                country_ids=frozenset(),
                language_codes=frozenset(),
            )

        if "admin" not in roles:
            raise AdminScopeAccessError(
                "Administrative access required."
            )

        admin_role_ids = {
            row.id
            for row in role_rows
            if row.role == "admin"
        }

        scope_result = await self.session.execute(
            select(
                RoleScope.scope_type,
                Country.id.label("country_id"),
                Language.code.label(
                    "language_code"
                ),
            )
            .outerjoin(
                Country,
                and_(
                    RoleScope.scope_type
                    == "country",
                    Country.id
                    == RoleScope.scope_id,
                    Country.is_active.is_(True),
                ),
            )
            .outerjoin(
                Language,
                and_(
                    RoleScope.scope_type
                    == "language",
                    Language.code
                    == RoleScope.scope_code,
                    Language.is_active.is_(True),
                ),
            )
            .where(
                RoleScope.tenant_id
                == tenant_id,
                RoleScope.user_id
                == admin_user_id,
                RoleScope.role
                == "admin",
                RoleScope.status
                == "active",
                RoleScope.user_role_id.in_(
                    admin_role_ids
                ),
                RoleScope.scope_type.in_(
                    {
                        "country",
                        "language",
                    }
                ),
            )
        )

        country_ids = set()
        language_codes = set()

        for row in scope_result.all():
            if (
                row.scope_type == "country"
                and row.country_id
            ):
                country_ids.add(
                    row.country_id
                )

            if (
                row.scope_type == "language"
                and row.language_code
            ):
                language_codes.add(
                    row.language_code.lower()
                )

        return AdminScopeContext(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            is_global=False,
            country_ids=frozenset(
                country_ids
            ),
            language_codes=frozenset(
                language_codes
            ),
        )