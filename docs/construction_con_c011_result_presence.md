# CON-C011: optional result presence before the approved HTTP endpoints

Authority: customer confirmation 13.10.2026 21:05 in new clarification.txt:
complete/cancel require expected_row_version; result is optional and is saved
when supplied. The three activity HTTP paths are now approved, with mandatory
Idempotency-Key and the retained permission/scope/version/atomic audit rules.

This increment distinguishes omitted result from explicitly supplied nullable
text. Omission preserves the previous stored value exactly, including whitespace;
the conditional SQL UPDATE does not assign result at all. Explicit text retains
the existing trim normalization. Explicit null/blank retains the existing nullable
value contract. No new field, reason, transition, permission or route is added.

A single internal unset marker is shared by rules, command, repository and
workflow. The marker is never serialized in response/audit/hash. An omitted field
is absent from the request-hash payload; explicit null remains present. Therefore
omission versus null under the same key produces IDEMPOTENCY_KEY_REUSED. Existing
explicit-result snapshots/hashes and operation namespaces remain unchanged.
Previously stored records are not rewritten. Older domain requests which encoded
omission as explicit null may conflict on reuse under the new presence contract;
they are never silently reinterpreted as another command.

Service validates the effective value after conditional SQL; audit result_changed
uses the actual old/new value and never stores free text. Encrypted terminal
snapshot is validated before commit. Same keyed retry returns its original saved
result even after later metadata changes, without another audit/version advance.
Failure rolls back activity/audit/idempotency together. Auth/locking and version
guards remain unchanged. Caller/native transaction behavior is preserved.

Local tests include real SQLite preservation/rollback and encrypted workflow
commit/replay; they do not establish PostgreSQL RLS/deployed auth. This guarded
installer modifies only the specified imports/methods and adds tests/docs. No
Supabase, Storage, real email, schema or historical event operation occurs.

The new 21:01 CON-C013 clarification is retained: direct/transitive planned
activities, historical client ownership and stable related_client_id, restricted
Construction event_logs SELECT under RLS. Those require their own increments;
this result-presence preparation does not claim them implemented or C011/C013
accepted. Audit correlation is prepared before publishing the activity HTTP
boundary. Next: CON_C013_AUDIT_CORRELATION_PREPARATION.
