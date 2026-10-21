# CON-005: area PATCH workflow

This increment prepares PATCH /api/v1/construction/areas/{id}, required by
rev.12 section 22 and the 07.10.2026 HTTP Stage 1 clarification.
It uses the retained CON-005 name/sort_order, version and audit contracts.

The HTTP endpoint is the next increment.

- The verified active actor, tenant and common construction.projects.edit policy
  determine eligible scopes before any area lookup.
- A shared require_area_edit_project_scope function serves archive and PATCH.
  None means verified tenant-wide permission; project-scoped access produces a
  UUID tuple. Document-only access cannot resolve an area.
- Lookup binds tenant, active area id and eligible projects. Project id is never
  accepted from a client body. The service subsequently locks parent then area.
- The workflow owns the transaction, requires one shared session and calls
  patch_area(manage_transaction=False).
- Only name and sort_order may change; omitted fields stay unchanged.
- Both changed fields share one version increment and one audit.
  No-op preserves version/audit and still requires a matching expected version.
- Public acknowledgement contains id, tenant_id, project_id, name, sort_order,
  row_version. It is validated before commit; private ORM fields are excluded.
- Changed responses have expected_row_version + 1; no-op responses keep the
  expected version. The atomic service decides whether persisted values changed.
- Failures roll back area and audit together. Typed conflicts and denials survive;
  unexpected workflow errors are sanitized.
- Archive retains its existing contract while delegating scope derivation to the
  same shared function. No new authorization model or permission is introduced.

Local tests cover scope guards, both-field edits, no-op/stale version, response
binding, shared-session wiring and real SQLite ORM commit/rollback.
Supabase behavioral RLS and actual concurrent transaction acceptance remain
separate checks. No schema changes, external writes, email or Storage are part
of this installer.
