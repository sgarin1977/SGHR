# CON-C004 / CON-C007: Construction block HTTP

Basis: rev.12 section 3.3 and the approved tenant-local Construction block
clarification. This route implements that action through the existing block workflow.

POST /api/v1/construction/team/members/{user_id}/block
uses the canonical authenticated actor, Construction Suite/Core gates and
construction.access.manage. The service requires current tenant-wide authority;
project-only management does not authorize revocation across the tenant.

The path provides the target UUID. The optional body is empty (omitted, null or {});
extra body fields are rejected. Tenant, actor identity, role, scope, row_version
and tokens cannot be supplied in the body. The tenant always comes from the actor.

The endpoint calls ConstructionTeamBlockWorkflow with the Construction-scoped
session and request ID. The workflow owns one commit for grants and audit.
The 200 success envelope contains only user_id and revoked_grant_ids.
An authorized repeat can return an empty changed-ID list, with no extra audit.
No blocked status, new permission code, membership change, database migration,
mandatory Idempotency-Key or encrypted replay ledger is added.

Existing API error envelopes are reused. Invalid request data is 422, unavailable
authority is 403 and internal failures are generic 500 responses without diagnostics.
The router is registered once in api/app.py; the guarded installer preserves
unrelated app code and refuses unexpected registrations.

Local HTTP tests use the actual service/workflow and SQLite in-memory state,
with identity and Suite/Core dependencies overridden. They cover tenant binding,
authorization, input validation, rollback, public projection and route registration.
Live JWT identity, PostgreSQL RLS/concurrency, frontend and production acceptance
remain separate checks. Remove-from-organization membership handling awaits
customer clarification.
