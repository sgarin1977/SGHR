# CON-C010: atomic lead status workflow

Authority: rev.12 CON-C010; the approved nine-state CRM lifecycle retained by
new clarification.txt; clarifications.txt shared mutation/audit/version contracts.
Ordinary updates with expected_row_version do not require a separate
Idempotency-Key under the approved Stage 1A contract.

ConstructionLeadStatusWorkflow calls the status service in a caller-opened
transaction. Service, lead repository, access repository and mandatory audit
repository must all share that session. The service handles current permission,
membership, scope, refreshed row lock, expected version, no-op and transition.

The workflow materializes a minimal operation result (id, tenant_id, status,
row_version) before commit. It checks the result against the requested lead,
tenant, status and exact version advance or unchanged no-op version. Internal
changed flags and unrelated lead/contact data are not returned. This projection
does not define or add an HTTP route.

Successful change and audit commit once. Successful no-op also commits to finish
the transaction and release its lock, with no domain UPDATE or new audit.
Validation, authorization, service/audit, projection and commit failures trigger
rollback. Known domain errors retain their types; unexpected failures and rollback
diagnostics are sanitized. Cancellation during the operation attempts rollback
and propagates cancellation.

Tests include actual SQLite update/audit commit visibility and automatic rollback
after real writes, no-op, stale/reverse/terminal requests, result binding before
commit, session wiring and cancellation. Commit failure is simulated before the
database commits; this does not prove recovery from an ambiguous network outcome.
Native PostgreSQL, deployed HTTP/auth and concurrent transactions remain separate
acceptance checks. Stage 1 and Stage 1A acceptance is not claimed.

Only a new workflow, tests and this document are installed. Existing application
files, physical schema, Supabase, Storage and email are untouched.
Next: CON_C010_STATUS_HTTP_CONTRACT_REVIEW.
