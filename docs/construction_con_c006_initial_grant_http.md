# CON-C006/C008: initial grant issuance HTTP

Contract: rev.12 CON-C006/section 4.3.8 and final clarification of 2026-10-06.
The REST path and HTTP request shape below are implementation choices.

POST /api/v1/construction/team/members/{user_id}/access-grants
coexists with the existing GET on that path.

Body:
- role: one of the seven fixed Construction role codes.
- scope_type: tenant or project.
- scope_id: UUID of the requested scope.
- expires_at: optional aware ISO datetime; omitted/null means no deadline.

Identity is supplied by authenticated actor context and the path user_id.
tenant_id, user_id, target_user_id, row_version, expected_row_version and
other extra fields are rejected in the body.

The route requires active Platform identity, enabled Construction suite/core
and construction.access.manage. The tested issuance workflow checks current
scope-specific authority, delegability, active Platform membership and project
existence through the existing service. No Platform membership is written.

Idempotency-Key is optional. With a key, the existing encrypted Platform ledger
stores HTTP 201 and a public result; without a key, crypto configuration is not
required. Replay returns the original issuance response after current authority
and persisted grant binding checks. It never reactivates historical access.

201 Created returns the Platform data/meta/request_id envelope with only the
eight existing public grant fields. No grant row_version is introduced.
Effective duplicates return 409 ACCESS_GRANT_CONFLICT; reused/processing keys
have their existing separate idempotency conflict codes.
Permission/scope denial is 403, request validation is 422, internal errors
are generic 500 without SQL, credentials or request-secret diagnostics.

Tests drive the real service/workflow/audit/permission/idempotency code with
local in-memory SQLite persistence and controlled auth/module/PostgreSQL
dependencies. Created grants are tested through existing revoke/replace routes.
Live PostgreSQL concurrency/RLS and production deployment are not established.
No Supabase, Storage or real email operations are performed by this installer.

This connects initial grant issuance. It does not declare the complete team UI,
team lifecycle or Stage 1A finished. Next: CON-C006/C008 backend requirements review.
