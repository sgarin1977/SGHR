import asyncio
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database.construction import (
    set_construction_tenant_context,
)


class ConstructionDatabaseConfigurationError(
    RuntimeError
):
    """Construction database identity is not safely configured."""


def get_construction_database_url() -> str:
    construction_url = os.getenv(
        "CONSTRUCTION_DATABASE_URL",
        "",
    ).strip()
    platform_url = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not construction_url:
        raise ConstructionDatabaseConfigurationError(
            "CONSTRUCTION_DATABASE_URL is required."
        )

    if not platform_url:
        raise ConstructionDatabaseConfigurationError(
            "DATABASE_URL is required for identity validation."
        )

    try:
        construction_identity = make_url(
            construction_url
        )
        platform_identity = make_url(platform_url)
    except Exception as exc:
        raise ConstructionDatabaseConfigurationError(
            "Database URL configuration is invalid."
        ) from exc

    construction_username = (
        construction_identity.username or ""
    )
    platform_username = platform_identity.username or ""

    construction_database_endpoint = (
        construction_identity.host,
        construction_identity.port,
        construction_identity.database,
    )
    platform_database_endpoint = (
        platform_identity.host,
        platform_identity.port,
        platform_identity.database,
    )

    if (
        construction_database_endpoint
        != platform_database_endpoint
    ):
        raise ConstructionDatabaseConfigurationError(
            "Construction must use the platform database."
        )

    if not construction_username:
        raise ConstructionDatabaseConfigurationError(
            "Construction database role is missing."
        )

    if construction_username == platform_username:
        raise ConstructionDatabaseConfigurationError(
            "Construction must use a dedicated database role."
        )

    if construction_url == platform_url:
        raise ConstructionDatabaseConfigurationError(
            "Construction database identity is not isolated."
        )

    return construction_url




async def verify_construction_database_identity(
    session,
) -> str:
    result = await session.execute(
        text(
            """
            SELECT
                current_user AS role_name,
                rolbypassrls AS bypasses_rls,
                rolsuper AS is_superuser
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
    )
    role_row = result.mappings().one_or_none()

    if role_row is None:
        raise ConstructionDatabaseConfigurationError(
            "Construction database role cannot be verified."
        )

    role_name = role_row.get("role_name")
    bypasses_rls = role_row.get("bypasses_rls")
    is_superuser = role_row.get(
        "is_superuser",
        False,
    )

    if (
        not isinstance(role_name, str)
        or not role_name
        or bypasses_rls is not False
        or is_superuser is not False
    ):
        raise ConstructionDatabaseConfigurationError(
            "Construction database role must enforce RLS."
        )

    return role_name



class ConstructionDatabaseIdentityGuard:
    def __init__(self):
        self._verified_role_name: str | None = None
        self._lock = asyncio.Lock()

    async def ensure_verified(
        self,
        session,
    ) -> str:
        if self._verified_role_name is not None:
            return self._verified_role_name

        async with self._lock:
            if self._verified_role_name is not None:
                return self._verified_role_name

            role_name = (
                await verify_construction_database_identity(
                    session
                )
            )
            self._verified_role_name = role_name
            return role_name



_construction_database_identity_guard = (
    ConstructionDatabaseIdentityGuard()
)


def build_construction_session_factory():
    database_url = get_construction_database_url()
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "statement_cache_size": 0,
        },
    )
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )



@lru_cache(maxsize=1)
def get_construction_session_factory():
    return build_construction_session_factory()


@asynccontextmanager
async def construction_transaction(
    *,
    tenant_id: UUID,
    session_factory=None,
    identity_guard=None,
):
    factory = (
        session_factory
        if session_factory is not None
        else get_construction_session_factory()
    )
    active_identity_guard = (
        identity_guard
        if identity_guard is not None
        else _construction_database_identity_guard
    )

    async with factory() as session:
        async with session.begin():
            await active_identity_guard.ensure_verified(
                session
            )
            await set_construction_tenant_context(
                session,
                tenant_id=tenant_id,
            )
            yield session
