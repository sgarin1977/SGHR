# CON-C004 / CON-C007: tenant-local Construction block

Basis: rev.12 section 3.3 and clarifications.txt section "Блокировка и удаление сотрудника"
(lines 1697–1711 in the reviewed copy). The final 2026-10-06 grant contract
preserves immutable grant history and uses conditional revocation.

Block revokes Construction access inside the current tenant. It does not block
the global Platform account or deactivate organization membership.

ConstructionTeamAccessMutationRepository.revoke_effective_user_grants updates
all currently effective grants for the exact tenant and target user, across
tenant/project/document scopes. It changes only status, revoked_at and
revoked_by_user_id, and returns the rows changed by this statement.
Expired or already revoked grants are preserved.

The caller must check current tenant-wide authority, supply trusted identities
and an aware server timestamp, audit the returned revocations, and own the
single transaction. The repository never commits, rolls back, or writes audit.

This increment is repository infrastructure only. Block service, audit rollback,
workflow, HTTP and live tenant-isolation/concurrency acceptance remain to implement.
A bulk statement does not introduce a persistent ban or serialize future grant
issuance; these semantics must not be claimed by this repository.

Remove from organization is a separate operation. Its approved business behavior
is membership deactivation plus effective Construction grant revocation in one
business operation, without deleting the user or affecting other tenants.
The acceptance clarification at lines 1873–1899 defines creating a missing
membership through user_roles(role='client') and forbids implicit restoration.
It does not define removal of the primary users.tenant_id association or the
handling of other active Platform roles. That physical removal contract remains
pending; this increment does not change membership or invent a replacement model.
