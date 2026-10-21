# Stage 1: CON-003 client read service

Sources: rev.12 sections 5.1 and 22, the retained CRM permission clarification
of 2026-09-16, and the Stage 1 HTTP correction of 2026-10-07.

This increment provides the service needed by GET /api/v1/construction/clients/{id}.
The HTTP route is the next increment.

Client identity remains construction_clients, without a duplicate Platform user
or linked_user_id. Reads use construction.projects.read and the existing
Construction client permission boundary. Tenant identity comes from the active
server actor. The shared permission engine checks the matching user, tenant,
roles, permissions and applicable scope before querying. A project/document grant
alone is not elevated to tenant-wide CRM access.

The repository filters tenant_id, client id and deleted_at IS NULL, refreshes
cached ORM state and disables autoflush. The service rejects missing, foreign,
mismatched, deleted or malformed results and conceals database failure details.

The read performs no audit, mutation, explicit flush, commit or rollback.
Transaction lifetime belongs to the caller. Role matrix, project membership,
schema, Storage and live database data are unchanged by this increment.

Local tests exercise the real permission guard and repository SQL; SQLite
checks cover cached-state refresh, soft delete and tenant isolation.
These checks do not establish production authentication or Supabase RLS.
