# CON-004: project creation transaction ownership

Source: rev.12 sections 10.7, 21 and 22; retained clarification that domain
mutation and required platform event_logs audit share one transaction.

ConstructionProjectService.create_project accepts an internal keyword-only
manage_transaction boolean, default True. Existing callers keep the existing
commit/rollback behavior. With False the caller must already own an active
transaction; the service performs its existing validation, scoped lookups,
project creation and required audit, without committing or rolling back.

The caller must complete response validation and any replay/result persistence
before committing, and must roll back on every failure. This mode is a
prerequisite for the HTTP creation workflow, not a public request parameter.
It changes no permission, role, membership, status, schema or address contract.

Local SQLite tests verify that flushed project, audit and a later result can
commit together and all roll back after audit or later workflow failure.
They do not verify live PostgreSQL RLS or the production idempotency store.

Whether a project POST without Idempotency-Key must be rejected remains
pending customer clarification. This increment does not decide that rule
or register POST /api/v1/construction/projects.
