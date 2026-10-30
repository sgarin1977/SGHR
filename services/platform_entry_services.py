"""Platform entry reads; storage collaborators remain inside services."""
from uuid import UUID

from database.repositories.platform_organizations import PlatformOrganizationsRepository
from database.repositories.platform_organization_bootstrap import PlatformOrganizationBootstrapRepository
from database.repositories.user import (
    PlatformTelegramAccountUnlinkedError, UserRepository,
)
from services.api_auth import ApiAuthAccessError


def build_bot_users(session):
    return UserRepository(session)


def build_bot_organizations(session):
    return PlatformOrganizationsRepository(session)


def build_organization_creation_repository(session):
    return PlatformOrganizationBootstrapRepository(session)


async def read_bot_organizations(*, context, users, rates, repository, after, limit):
    user = await require_canonical_bot_user(context, users, rates)
    return await repository.list_organizations(
        user_id=user.id, after=after, limit=limit,
    )


async def read_organization_creation_languages(repository):
    return await repository.list_creation_languages()


async def require_canonical_bot_user(context, users, rates):
    identity = context.verifier.verify(context.proof)
    account = await users.get_by_platform_account(
        "telegram",
        str(identity.platform_user_id),
    )
    if (
        account is None
        or account.status != "active"
        or not isinstance(account.user_id, UUID)
    ):
        raise ApiAuthAccessError(
            "Platform account is unavailable."
        )

    user = await users.get_by_id(account.user_id)
    if (
        user is None
        or user.id != account.user_id
        or user.status != "active"
    ):
        raise ApiAuthAccessError(
            "Platform account is unavailable."
        )

    await rates.ensure_platform_user_allowed(
        user_id=user.id
    )
    return user
