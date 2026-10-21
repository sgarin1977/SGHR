# CON-C011: complete/cancel activity workflow

Sources: rev.12 CON-C011; clarifications.txt construction_activities contract and
the Stage 1A mandatory Idempotency-Key list (create/complete/cancel activity).
Retained shared projects.edit permission, current membership and tenant/project
scope checks apply. No new permissions, required result/reason or reopening.

This workflow calls the existing terminal service. Change, exact mandatory audit
and encrypted idempotency completion commit in one shared transaction. Failures,
including cancellation, roll back all three. The caller must open the transaction.
Complete and cancel have separate construction.activity operation namespaces.

expected_row_version is required. A fresh operation uses optimistic locking and
the planned -> completed|cancelled lifecycle. An identical keyed retry returns
the original materialized response without a second mutation/version/audit.
A changed raw command with the same operation/key returns key-reuse conflict.
Processing reservations produce an idempotency_in_progress conflict. A fresh
key does not bypass terminal lifecycle restrictions or stale version checks.

Stored snapshots are encrypted through the existing Platform codec and bound to
tenant, principal, operation, key hash and request hash. They contain an explicit
18-field public projection, excluding deleted_at and arbitrary ORM attributes.
The terminal result is optional text. It stays out of audit free-text payloads.
Replay rechecks current membership, scope, linked active resources and activity
availability. Being responsible or creator does not grant access. An authorized
editor may complete an activity created by another member.

Fixed parent locking order is lead, client, project, then activity; replay follows
the same order. Project-only access uses the existing limited metadata lookup
and cannot authorize linked CRM entities. Access is rechecked after reservation
and before publishing a committed response. The original response remains stable
across later allowed metadata changes; it is not rebuilt from current values.

Local tests cover real SQLite mutation/audit/idempotency commit and rollback,
replay without duplicate audit, version/lifecycle conflicts, permission loss,
scope, corrupt snapshots and failure/cancellation paths. They do not establish
native PostgreSQL RLS, concurrent request behavior or deployed authentication.
This increment adds no HTTP route and performs no real Supabase, Storage or email
operation. Physical construction_activities DDL remains a separate step.

Next: CON_C011_ACTIVITY_HTTP_CONTRACT_REVIEW.
