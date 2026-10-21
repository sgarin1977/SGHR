# CON-C012 dependency: caller-owned client creation transaction

Authority: clarifications.txt, CON-C012, requires lead conversion to run in one database transaction with idempotency. The normal Construction client contract still requires display_name and client_type (person/company). No implicit client type or project name is introduced here.

ConstructionClientService.create_client now accepts the server-side keyword manage_transaction=True. The default retains the existing standalone behavior: normal permission checks and input normalization, repository creation, mandatory construction_client_created audit, one commit, and rollback for repository/audit/commit failures.

With manage_transaction=False, the service requires an active caller transaction and the same session for the client repository and audit repository. It performs client creation and its mandatory audit without commit or rollback. Failures propagate to the outer workflow, which must roll back all conversion work. A returned client is checked against the actor tenant and supplied creation fields before auditing. This flag neither bypasses authorization nor disables audit, and is not exposed as an HTTP body field.

Only the create_client method is changed. Other client operations, the physical model, permissions and existing HTTP routes remain unchanged. Surgical guarded insertion preserves unrelated code, comments, BOM and line endings.

Tests cover default and explicit transaction ownership, required fields, common permission/scope denial, shared-session wiring, invalid result binding, failure ownership, and legacy collaborators. SQLite tests demonstrate client/audit/later-record joint commit and rollback, including an audit failure after an actual audit insert. They do not prove PostgreSQL RLS/concurrency.

The complete conversion repository/service/workflow/HTTP boundary is subsequent work; this transaction dependency alone does not implement conversion or close Stage 1. The shared transaction design must retain the approved conversion audit/idempotency contract.

The installer creates this document and its test file and updates services/construction_clients.py. It performs no Supabase, Storage or real email operations. Physical lead schema is not created.
