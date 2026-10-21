# CON-C004 / CON-C007: block service

Basis: rev.12 section 3.3, the "Блокировка и удаление сотрудника"
clarification, the fixed Construction role/permission policy, and the final
2026-10-06 historical-grant contract.

ConstructionTeamBlockService.block_access requires a verified active actor,
a UUID target, an existing transaction and tenant-wide construction.access.manage.
It selects the actor's tenant-scoped OWNER/ADMIN authority from the repository,
locks and refreshes that grant through get_grant_for_update, then checks its
current state and expiration. Project-only authority cannot authorize this action.
The authorization grant lock remains owned by the caller's transaction.

All effective grants of the target in the current tenant are conditionally
revoked through ConstructionTeamAccessMutationRepository. Each changed grant
gets the existing construction_access_grant_revoked event, with the exact three
revocation changes and team_action=block. No aggregate event or blocked status
is introduced. An authorized repeat with no effective grants creates no audit.
The operation does not write users, user_roles or other memberships.

The authorization deadline is checked after the bulk update and audit waits.
A captured pre-operation deadline supports self-revocation without treating the
operation's own changes as external loss of authority.
Failure rolls back the transaction. Success returns an immutable internal result
with the target user and the sorted UUIDs of grants changed by this invocation.
The service never commits; a workflow must own final commit.

Local tests compile the repository SQL and use an in-memory SQLite transaction
to check service rollback of grant and audit rows. PostgreSQL locks, live RLS,
concurrent grant issuance, workflow/HTTP and UI acceptance remain unverified.
This operation revokes current access and does not introduce a persistent ban.
Remove-from-organization membership semantics remain pending customer clarification.
