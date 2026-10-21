# CON-004: POST /api/v1/construction/projects

Sources: rev.12 sections 10.7 and 22; final customer clarification dated
08.10.2026 in new clarification.txt, projects.create contract.

The request contains name and address_raw (required nonblank strings), optional
client_id, responsible_user_id and comment. Server-owned fields, including
tenant_id, created_by, status and row_version, are rejected in the body.
Tenant and creator come from the canonical authenticated Platform actor.

Authentication, Construction suite, construction.core and the shared
construction.projects.create permission are required. The existing service
enforces applicable tenant scope, tenant-bound active client and active
Platform membership for a responsible user. Creating or assigning a project
does not create implicit access grants or a separate project membership.

Idempotency-Key is optional. A supplied key uses the existing Platform
idempotency implementation, encrypted response storage and 24-hour retention.
The same key and body return the original 201 snapshot; a changed body returns
409 IDEMPOTENCY_KEY_REUSED. An in-progress reservation returns 409
idempotency_in_progress. Missing, deleted or unavailable original project on
replay returns 404 without recreation.

Without a key, no encryption configuration or idempotency reservation is
required. Each successful unkeyed request creates a project and its audit.
The existing workflow owns the shared transaction and validates the public
response before commit; failures roll back project, audit and keyed replay
state together. HTTP reuses ProjectReadResponse and the common response envelope.

Invalid requests return 422, denied access 403, and unexpected failures a
generic 500 without internal exception details. OpenAPI documents the optional
header, request schema, 201 response and error codes.

Local ASGI tests exercise the real workflow/create/idempotency services with
controlled repositories. Existing SQLite workflow tests verify actual shared
commit/rollback. These checks do not establish PostgreSQL RLS or concurrency
acceptance. This installer performs no real Supabase, Storage or email operations.
