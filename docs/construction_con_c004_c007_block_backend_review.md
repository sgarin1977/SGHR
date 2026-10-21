# CON-C004 / CON-C007: Construction block backend review — 2026-10-07

Reviewed sources: rev.12 section 3.3 and the customer clarification
"Блокировка и удаление сотрудника". The reviewed code includes the repository,
ConstructionTeamBlockService, ConstructionTeamBlockWorkflow and HTTP router.

The implementation revokes effective grants of one target user in the actor's
current tenant. It does not write global user status or organization membership.
Only historical grant revocation fields change. Authorization requires current
tenant-wide construction.access.manage and a refreshed, locked authority grant.
Its expiration is checked after awaited writes/audit. Project-only authority
does not permit tenant-wide block.

Each changed grant uses construction_access_grant_revoked audit in the same
transaction. The workflow commits once. An authorized repeat with no effective
grants creates no additional audit. The HTTP route uses canonical actor,
Construction Suite/Core gates and the Construction-scoped session, rejects
extra body fields and returns only user_id/revoked_grant_ids.

The user confirmed 401 local tests passed after router installation.
These tests cover local mocked/SQLite behavior, not live PostgreSQL concurrency,
production identity, RLS acceptance or complete frontend behavior.
No persistent blocked status or permanent prohibition on new grants is introduced.

This review supersedes the older CON-C006/C008 review's statement that Construction
block was missing. Remove from organization remains a separate unfinished operation:
its business behavior is agreed, but physical handling of primary users.tenant_id
and other tenant-scoped Platform roles awaits the customer's response.
The block review does not close all CON-C004/C007 or Stage 1A acceptance work.

While that clarification is pending, the next independent approved work is
CON-C009/C010 lead functionality under the final 2026-10-06 contract.
