# CON-C010: conditional lead status persistence

Authority: rev.12 Stage 1A CON-C010; approved full nine-state lifecycle,
confirmed 06.10.2026 and retained by the corrections of 07.10.2026;
shared tenant isolation, optimistic locking, no-op and atomic audit contracts.

The existing lifecycle validator remains authoritative. The repository adds
transition_status: a conditional UPDATE of a non-deleted lead in the selected
tenant, matching its expected row_version and current status. Only status,
updated_at and row_version change; the version advances once. Reverse, skipped
and terminal transitions are rejected. Lost is available from every non-terminal
state. Negotiation -> won does not create or change conversion references.
Conversion remains a separate business operation.

The repository returns None when database preconditions no longer match. It
neither commits nor rolls back, authorizes nor writes audit. The forthcoming
service must lock and refresh the lead, check current permissions and version,
handle an unchanged status as a successful no-op without UPDATE or audit,
classify conditional conflicts, and write the mandatory status audit in the same
transaction. No-op requests are not passed to transition_status.

Local tests exercise actual SQLite updates, unchanged business fields, foreign
tenant/deleted/stale/current-status guards, all forward stages and lost outcomes,
identity-map refresh, suppressed autoflush and caller rollback. PostgreSQL SQL is
compiled locally; live PostgreSQL, HTTP/auth and concurrency are not claimed.

No schema migration, HTTP endpoint, measurement records or provider operation
is introduced. This step does not complete CON-C010 or close Stage 1/Stage 1A.
Next: CON_C010_LEAD_STATUS_SERVICE.
