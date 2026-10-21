# CON-C006/C008: initial grant issuance workflow

Contract: rev.12 CON-C006 and section 4.3.8, plus final clarification of 2026-10-06.

ConstructionAccessIssuanceWorkflow.grant_access owns one commit for initial grant
issuance and mandatory audit, with Platform idempotency completion when a key is
provided. Any required audit, completion or commit failure rolls back everything.

Idempotency-Key remains optional for grants. Without a key there is no response
ledger/configuration requirement; effective duplicate grants are controlled by
the existing approved database exclusion constraint. With a key the operation is
construction.access_grant.create, scoped to tenant and authenticated user.

Role/scope codes and aware deadlines are normalized for the request hash.
A completed reservation stores HTTP 201 and only the eight public grant fields
through the existing encrypted Platform response codec.

A replay verifies current caller authority after reservation and grant-row waits.
It checks the cached response against the persisted historical grant, including
tenant, target user, issuing actor, role/scope and immutable issuance timestamps.
Replaying an expired or revoked grant returns the original successful response,
without reactivation, duplicate issuance or another audit. The original response
is history; use the existing read endpoint to obtain current effective access.
Fresh issuance validates current Platform membership and active project through
the previously tested issuance service. Neither path writes Platform membership.

No row_version, new permissions, physical model changes or migrations are added.
Processing and reused-key conflicts remain separate from ACCESS_GRANT_CONFLICT.

Tests use local in-memory SQLite transactions with the actual issuance service,
permission resolver, audit logger and Platform encrypted idempotency service,
and controlled PostgreSQL repository dependencies. They do not prove live RLS,
PostgreSQL concurrency or deployment. No Supabase, Storage or real email calls run.

Next: initial issuance HTTP integration. This workflow test result does not
declare CON-C006/C008 or Stage 1A complete.
