# CON-C011: completion/cancellation service

Authority: rev.12 CON-C011 and clarifications.txt approved activity lifecycle,
shared projects.edit permission family, effective tenant/project scope, versions,
UTC timestamps and mandatory atomic audit. Latest Measurement boundary is preserved.

The service derives authorization from the shared permission engine, not the
responsible/creator identity. Tenant CRM links require tenant scope; project-only
actions may use effective project scope. Two repository helpers select only
authorized scope metadata and lock the exact previously authorized links. This
prevents a project-scoped request from loading unrelated CRM activity payloads.
Parent locks follow lead -> client -> project, then the activity row. Parent
availability, current membership/grants and unchanged links are checked around
awaited locks. A changed link/conditional state is a ROW_VERSION_CONFLICT.

Only planned -> completed or planned -> cancelled is a fresh domain mutation.
The command includes required expected_row_version and optional text result.
Server time sets the corresponding terminal timestamp and updated_at; version
advances once. Other business fields are preserved. Already terminal state is
not re-mutated; idempotent same-key completion/cancellation replay belongs to the
next workflow increment, which must not call the fresh terminal mutation again.

One construction_activity_completed/cancelled audit records status and terminal
timestamp changes. result_changed indicates a result change without duplicating
the free-form result text into audit. No required cancellation reason is imposed.
All collaborators share a session. Service neither commits nor rolls back; the
caller must roll back any failure, including audit failure after mutation.

Local tests cover scoped SQL selection, trusted links, permission/membership,
version/lifecycle, exact safe audit and actual SQLite mutation/audit commit and
rollback. SQLite adapters restore timezone metadata only for local tests. Native
PostgreSQL/RLS, concurrent transactions and deployed HTTP are not claimed. No
physical Supabase schema or endpoint is added. Next: CON_C011_ACTIVITY_TERMINAL_WORKFLOW.
