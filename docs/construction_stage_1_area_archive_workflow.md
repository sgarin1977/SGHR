# CON-005: atomic area archive workflow

Sources: rev.12 section 22; HTTP Stage 1 clarification dated 07.10.2026;
retained archive, scope, optimistic-locking and audit contracts.

The workflow accepts area_id and expected_row_version. Tenant and allowed
project scopes come exclusively from the verified server actor/access context.
A valid tenant-wide grant allows a tenant-bound lookup; a project-scoped grant
restricts the lookup to its verified project IDs. Document-only, forged,
foreign and inactive contexts are rejected. Unavailable scoped areas return
not found without disclosing another project's metadata.

After locating the project, the shared access guard rechecks projects.edit.
The existing archive service then locks project before area and retains all
terminal-project, confirmed-area, active-element and version restrictions.
manage_transaction=False leaves ownership with the workflow.

Archive, incremented row_version and one required event_logs audit remain in
one shared transaction. The public acknowledgement is checked before a single
commit. It contains only id, tenant_id, project_id, deleted_at (UTC) and
row_version. Failures, including response validation or commit failure, roll
back the shared transaction. There is no cascade, hard delete or implicit grant.

This increment does not register a route. The next increment implements
POST /api/v1/construction/areas/{id}/archive with the shared API envelope.
No new idempotency contract is introduced; expected_row_version remains the
approved concurrency control for this mutation.

Local tests exercise the real archive service and repository. SQLite proves
archive/audit commit and rollback; it does not establish PostgreSQL RLS or
lock/concurrency acceptance. No Supabase, Storage or email operation is run.
