# CON-005: area PATCH HTTP

Endpoint: PATCH /api/v1/construction/areas/{id}.
Sources: rev.12 sections 6.4, 6.4.1 and 22, the retained CON-005 mutation
clarifications, and the 07.10.2026 HTTP Stage 1 clarification.

Request:
- expected_row_version: required positive strict integer.
- name: optional string; trimmed-empty and explicit null are invalid.
- sort_order: optional signed INT32 or null. Null clears the field; omission preserves it.
- Extra fields, including tenant_id, project_id, status and transaction flags, are rejected.
- A version-only request is a version-checked no-op.
- No Idempotency-Key is required for this ordinary versioned PATCH.

The canonical actor, suite/module gates and shared construction.projects.edit
dependency are used. The workflow derives project scope on the server.
Both fields are passed together in one workflow call. The workflow validates
the public response before its single commit. Domain writes and audit remain
in the same transaction. Confirmed-area organizational edits are allowed;
completed/cancelled projects reject edits. No-op preserves audit and version.
Historical snapshots and related entities are not rewritten.

Response: the standard data/meta/request_id envelope with a public acknowledgement:
id, tenant_id, project_id, name, sort_order, row_version.

Errors documented in OpenAPI:
- 401 authentication_required / invalid_token
- 403 MODULE_DISABLED / permission_required / forbidden
- 404 not_found for unavailable area/project in allowed scope
- 409 ROW_VERSION_CONFLICT, or conflict for a terminal project
- 422 validation_error
- 500 internal_error; private exception text is excluded

Local HTTP tests use the actual workflow and service with mocked repositories,
alongside the existing SQLite transaction and area/archive regression tests.
Supabase behavioral RLS and real concurrency acceptance are separate checks.
No schema, Storage, email or external database operations are introduced.
