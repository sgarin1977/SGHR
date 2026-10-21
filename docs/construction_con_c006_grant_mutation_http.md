# CON-C006/C008: revoke and replace HTTP

Contract: customer confirmation of 2026-10-06 and the existing Platform API conventions.
REST paths and POST action names below are implementation choices for that contract.

POST /api/v1/construction/team/access-grants/{grant_id}/revoke
- Body may be omitted, null or {}. Extra fields are rejected.
- Returns the revoked historical grant; repeated authorized revoke is a successful no-op.

POST /api/v1/construction/team/access-grants/{grant_id}/replace
- Full replacement target: role, scope_type (tenant/project), scope_id, expires_at.
- expires_at must be explicitly supplied: null means no deadline, otherwise an aware ISO datetime.
- The workflow revokes the original and creates a new grant, with both audits in one transaction.
- Existing grant meaning fields are not edited in place.

Both routes require authenticated active Platform identity, enabled Construction suite/core,
and construction.access.manage. The workflow resolves current grants after locks and checks
authority for the original scope and the complete replacement target.

Idempotency-Key is optional, following the grant contract. With a key, the existing Platform
encrypted response ledger is used. Without a key, no idempotency encryption configuration is
required. Replay rechecks current authority and does not repeat business writes or audit.

200 uses the Platform data/meta/request_id envelope and the existing eight public grant fields.
No tenant/user identity input, row_version or expected_row_version is accepted.
409 ACCESS_GRANT_CONFLICT is used for an ineffective original grant.
409 IDEMPOTENCY_KEY_REUSED and idempotency_in_progress are separate idempotency conflicts.
403 covers unavailable permissions/scopes. Invalid requests return 422; internal failures
return a generic 500 without SQL, credentials or request-secret diagnostics.

Tests run the actual grant mutation workflow/service, audit, permission resolver and encrypted
ledger with local in-memory SQLite persistence. Auth/module services and PostgreSQL repositories
are controlled test dependencies. These tests do not prove live PostgreSQL RLS/concurrency or
production deployment. No Supabase, Storage or real email operations are performed.

This step connects revoke/replace only. Initial grant issuance, broader team lifecycle and
the remaining Stage 1A requirements are not declared complete.
