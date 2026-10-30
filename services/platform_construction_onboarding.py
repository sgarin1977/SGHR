"""Transactional Suite/Core provisioning for CON-001 Construction onboarding."""
from services.platform_organization_bootstrap import OrganizationBootstrapError


class PlatformConstructionOnboarding:
    def __init__(self, *, repository):
        session = getattr(repository, "session", None)
        if (
            session is None
            or not callable(getattr(repository, "provision_new_tenant", None))
            or any(not callable(getattr(session, name, None)) for name in (
                "in_transaction", "get_transaction", "get_nested_transaction",
            ))
        ):
            raise ValueError("Onboarding requires a shared transaction.")
        self.session = session
        self.repository = repository

    def _transaction(self):
        transaction = self.session.get_transaction()
        if (
            self.session.in_transaction() is not True
            or transaction is None or transaction.is_active is not True
            or self.session.get_nested_transaction() is not None
        ):
            raise OrganizationBootstrapError("An outer bootstrap transaction is required.")
        return transaction

    async def provision_construction(self, *, tenant_id, user_id, as_of):
        transaction = self._transaction()
        await self.repository.provision_new_tenant(
            tenant_id=tenant_id, user_id=user_id, as_of=as_of,
        )
        if self._transaction() is not transaction:
            raise OrganizationBootstrapError("Onboarding transaction changed.")
        # Bootstrap owns audit, idempotency completion, commit and rollback.


def build_platform_construction_onboarding(*, session):
    from database.repositories.platform_construction_onboarding import (
        PlatformConstructionOnboardingRepository,
    )
    return PlatformConstructionOnboarding(
        repository=PlatformConstructionOnboardingRepository(session),
    )
