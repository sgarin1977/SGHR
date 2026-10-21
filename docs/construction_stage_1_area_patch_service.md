# CON-005: atomic area PATCH service

Scope: preparation for PATCH /api/v1/construction/areas/{id}.
The HTTP route and its transaction-owning workflow are separate next steps.

Sources:
- rev.12 sections 6.4 and 6.4.1: permanent project area identity; rename
  preserves related elements, measurements and historical snapshots.
- rev.12 section 22 and the 07.10.2026 HTTP Stage 1 clarification:
  the specified area endpoint is required.
- retained CON-005 sort_order contract: nullable integer, duplicate values allowed,
  no automatic numbering or neighbor changes.
- retained CON-001–CON-009 clarification: all mutations require expected_row_version;
  no-op preserves version and creates no audit; terminal projects deny edits;
  name and sort_order may change on confirmed areas without reopening.

Implementation:
- patch_area accepts only name and sort_order in fields. Missing fields stay unchanged;
  null sort_order clears it; null or trimmed-empty name is rejected.
- Empty fields or equal normalized values are a version-checked no-op.
- The canonical actor and common construction.projects.edit scope policy are required.
- Parent project is locked and checked before locking the active tenant/project-bound area.
- The repository assigns all changed fields before a single ORM flush. The existing
  version mapper advances row_version once; stale ORM writes map to ROW_VERSION_CONFLICT.
- One construction_project_area_updated event contains only actual field changes.
- manage_transaction=True retains the service-owned transaction convention.
  False requires an active caller transaction and delegates commit/rollback to the workflow.
- No area type/status/project reassignment, cascade, measurement snapshot rewrite,
  schema migration or Idempotency-Key requirement is added.

Validation:
Mocked guards and audit tests plus local SQLite/real ORM transaction tests cover
single-version updates, no-op, stale version predicates, and area/audit rollback.
These tests do not establish Supabase behavioral RLS or production concurrency acceptance.
