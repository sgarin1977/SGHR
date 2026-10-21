# CON-005: preparation for POST /construction/areas/{id}/archive

Authority: rev.12 section 22; customer HTTP Stage 1 correction dated 07.10.2026;
retained Stage 1 archive/version/audit rules in clarifications.txt.

The existing archive service retains its guards: applicable projects.edit
permission and scope, mutable project, expected_row_version, non-confirmed area
and absence of active elements. Archive is a soft delete with one audit event.

manage_transaction defaults to True for existing callers. False is an internal
caller-owned mode requiring an active transaction; the caller commits or rolls
back archive, audit and subsequent response/workflow work together. It is not
a user-supplied request field.

find_active_area_project_id performs only a tenant-bound, nondeleted lookup.
Project-scoped callers must supply their server-verified project UUID tuple.
None is reserved for a caller whose tenant-wide permission was verified; an
empty tuple returns no result without querying. User body/query fields must
not supply these scope values. The future workflow performs the authorization
and preserves the existing project-before-area lock order.

This increment registers no HTTP endpoint or extra CRUD route. Subsequent
increments provide the archive workflow and POST endpoint specified in section 22.
SQLite checks prove commit/rollback and SQL tenant/scope filtering; they do not
prove PostgreSQL RLS or locking. No physical schema or policy is modified.
