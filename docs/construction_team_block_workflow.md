# CON-C004 / CON-C007: block workflow

Basis: rev.12 section 3.3, the tenant-local block clarification, the fixed
Construction access policy and the final historical-grant revocation contract.

ConstructionTeamBlockWorkflow uses the existing ConstructionTeamBlockService.
Service, grant repository and audit repository must share the workflow session.
The caller supplies a Construction transaction with its tenant context established.

The service checks and locks current tenant-wide authority, conditionally revokes
effective target grants and writes their existing revocation audit events.
The workflow validates the internal result, builds an explicit public projection,
then commits once. The response is returned only after commit succeeds.
On service, projection or commit failure the workflow attempts transaction rollback.
Internal commit errors use a generic ConstructionGrantOperationError.

The public result contains only user_id and revoked_grant_ids. It contains no
persisted blocked status, row_version, identity profiles, tokens or audit payloads.
An authorized repeat with no effective grants returns an empty changed-ID list,
commits the no-op transaction and creates no new audit events. This is current-state
revocation, not replay of the first response and not a persistent prohibition
against later authorized grant issuance. Current authority is checked on each call.

Block is not in the Stage 1A mandatory Idempotency-Key list. This increment adds
no mandatory key, encrypted replay ledger, new permission or database migration.
The service already checks its locked authorization deadline after its awaited
writes and audit. Projection is synchronous and is followed immediately by commit.

Tests use the real service and SQLite in-memory transactions. They verify durable
local commits and rollback when an injected commit failure occurs before DB commit.
Live PostgreSQL concurrency/RLS, HTTP, UI and production acceptance remain pending.
Remove-from-organization membership handling still awaits customer clarification.
