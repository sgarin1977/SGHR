# CON-005: POST /api/v1/construction/areas/{id}/archive

Authority: rev.12 section 22; customer HTTP Stage 1 clarification dated
07.10.2026; retained Stage 1 archive/version/scope/audit contracts.

Request: UUID area path parameter and body expected_row_version, a required
strict positive integer. Extra body fields, including tenant_id, project_id
and manage_transaction, are rejected. Server actor/access context determines
tenant, role permissions and allowed project scopes.

Authentication, Construction suite, construction.core and projects.edit are
required. The route delegates to the confirmed atomic archive workflow.
Project lookup is tenant/scope filtered; the service preserves project-before-
area locking, terminal-project and confirmed-area restrictions, absence of
active elements, optimistic locking and required audit in the same transaction.

Success is HTTP 200 with the shared data/meta/request_id envelope. Data
contains id, tenant_id, project_id, deleted_at in UTC and incremented row_version.
Workflow validates this acknowledgement before commit; HTTP validates its schema.

Errors: 401 authentication; 403 MODULE_DISABLED/permission_required/forbidden;
404 not_found; 409 ROW_VERSION_CONFLICT for stale version, conflict for lifecycle
or archive preconditions; 422 validation_error; generic 500 internal_error.
Internal exceptions and request validation details are not echoed.

The mutation uses the approved expected_row_version concurrency contract.
No mandatory Idempotency-Key is introduced. No cascade, hard delete, implicit
grant or additional CRUD endpoint is implemented.

Local ASGI tests use the real workflow and service. Existing SQLite tests prove
archive/audit commit and rollback. Supabase RLS and PostgreSQL locking/concurrency
acceptance remain separate checks; this installer performs no real external operations.
