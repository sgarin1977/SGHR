# CON-003: client read HTTP

Sources: rev.12 sections 5.1 and 22; CRM permission clarification of 2026-09-16;
Stage 1 HTTP correction of 2026-10-07.

GET /api/v1/construction/clients/{id} uses the confirmed client read service,
canonical Platform actor, construction suite/core module gates and
construction.projects.read. Tenant context comes from the server actor.
A query tenant_id is not a source of identity. The existing client permission
boundary is retained; project/document grants are not promoted to tenant access.

The response is the standard data/meta/request_id envelope. ClientReadResponse
explicitly includes id, tenant_id, display_name, client_type, phone, email, notes,
created_at and updated_at. Timestamps are aware and normalized to UTC.
Arbitrary ORM attributes and deleted_at are excluded. No client row_version or
linked_user_id is invented. Reads make no domain audit or mutation.

Errors: 401 authentication; 403 MODULE_DISABLED/permission_required/forbidden;
404 not_found for an unavailable client in the authorized tenant;
422 validation_error; 500 internal_error without exception details.
OpenAPI declares the GET response and error descriptions. This GET requires
no Idempotency-Key. api.app registers the new router exactly once.

Local ASGI tests use the real read service, permission/auth/module guards,
with external factories replaced by fixtures. Production authentication and
live PostgreSQL RLS remain outside these local checks.

This supersedes the service-only increment's absence of an HTTP route.
