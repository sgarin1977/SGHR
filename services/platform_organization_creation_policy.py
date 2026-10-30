"""Current Release eligibility for CON-001 organization creation.

Platform auth owns authentication and verification of the incoming identity.
This policy rechecks canonical lifecycle in the bootstrap transaction.

No membership, role, Construction permission, entitlement or organization-count
prerequisite is introduced. Construction provisioning remains a separate
transactional collaborator and cannot replace the creation eligibility rule.
"""


class ReleaseOrganizationCreationPolicy:
    def __init__(self, *, repository, provisioner):
        session = getattr(repository, "session", None)
        if (
            session is None
            or not callable(getattr(repository, "has_active_creation_identity", None))
            or getattr(provisioner, "session", None) is not session
            or not callable(getattr(provisioner, "provision_construction", None))
        ):
            raise ValueError("Creation policy requires shared-session collaborators.")
        self.session = session
        self.repository = repository
        self.provisioner = provisioner

    async def require_creation(self, *, user_id):
        # Called only after the bootstrap requires an authenticated Platform
        # principal and locks the active canonical creator.
        return await self.repository.has_active_creation_identity(
            user_id=user_id,
        ) is True

    async def provision_construction(self, *, tenant_id, user_id, as_of):
        # No commit or external delivery is allowed in this collaborator.
        return await self.provisioner.provision_construction(
            tenant_id=tenant_id, user_id=user_id, as_of=as_of,
        )
