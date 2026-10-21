# CON-004: project read HTTP

Source: rev.12 section 22 and the 2026-10-07 clarification restoring Stage 1 HTTP.

GET /api/v1/construction/projects/{id} uses the confirmed read service, canonical
Platform actor, construction suite/core module gates and construction.projects.read.
The service enforces the requested tenant/project scope. Query tenant_id is not
used as a source of identity. No membership is inferred from an access grant.

The response is the standard data/meta/request_id envelope with an explicit
ProjectReadResponse allowlist. UUIDs, UTC timestamps, positive row_version and
finite exact Decimal coordinates are validated. deleted_at and arbitrary ORM
attributes are excluded. Reading does not mutate the project or write domain audit.

Errors: 401 authentication; 403 MODULE_DISABLED/permission_required/forbidden;
404 not_found for an unavailable project in the authorized tenant;
422 validation_error; 500 internal_error without exception details.

OpenAPI declares the GET response and error descriptions. No Idempotency-Key
is required for this read operation. The existing role matrix is not changed.

The old CON-008 test prohibiting CRUD routes is replaced with a positive check
of this route's permission and scoped-session dependency. Other service guards
are preserved. api.app registers the new router once.

This supersedes the earlier service-only increment's absence of an HTTP route.
Local ASGI checks use the real read service, permission/module/auth guards,
but replace external dependency factories and unrelated application routers.
Production JWT sessions, live PostgreSQL RLS and the full Stage 1 acceptance
are not claimed by these local checks.
