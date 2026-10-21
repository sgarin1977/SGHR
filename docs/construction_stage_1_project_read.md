# Stage 1: project read service

Source: rev.12 section 22, GET /api/v1/construction/projects/{id}, and the
2026-10-07 correction restoring the Stage 1 HTTP boundary.

This increment implements the read service and refreshes the existing repository
read. It does not register the HTTP endpoint yet.

An active canonical actor and construction.projects.read in the requested
tenant/project scope are required before the query. A document-only grant does
not authorize reading the whole project. Tenant context comes from the actor.

The repository filters tenant_id, project id and deleted_at IS NULL. It refreshes
cached ORM values and disables autoflush for this SELECT. The service rejects a
foreign, mismatched, deleted or malformed result. It performs no mutation,
explicit commit/rollback or audit write; transaction lifetime belongs to the caller.

No role matrix, project membership model or database schema is changed.
Local SQLite checks verify query/cache behavior; they do not prove Supabase RLS.
The next increment wires the HTTP route, response projection and CON-008 checks.
