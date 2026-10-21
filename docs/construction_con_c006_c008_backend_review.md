# CON-C006/C008 backend review — 2026-10-07

Sources reviewed: rev.12 sections 3.3 and 4.3.8, roadmap CON-C006/CON-C008,
and final customer clarifications including the grant contract of 2026-10-06.
That confirmation resolves older "pending concurrency contract" notes in the
earlier access-read and role-options increment documents.

## Implemented backend contracts

| Requirement | Implementation / evidence |
| --- | --- |
| Existing employee access | Member grant GET and POST use Platform user UUID; tenant comes from authenticated context. |
| Initial grant issuance | Dedicated service/workflow and HTTP 201; one commit for grant, mandatory audit and optional idempotency. |
| Role/scope/expiry change | Conditional revoke plus a new historical grant and both audits in one transaction. |
| Revoke | Conditional update of an effective grant; authorized repeat revoke is a no-op without another audit. |
| Concurrency | Existing effective-grant exclusion constraint and ACCESS_GRANT_CONFLICT; no grant row_version. |
| Authorization | construction.access.manage plus scope-specific delegation from the fixed role policy. |
| Scope validation | Own active project must exist; document issuance remains deferred until Document Center validation. |
| Role choices | Read-only effective permission lists; no personal permission overrides or new codes. |
| Replay | Optional Platform encrypted ledger; current actor authorization is checked again; no reactivation of historical access. |
| Platform integration | Grant management does not create, restore or deactivate Platform membership. |

## Review correction

The review reproduced an authorization timing gap:
a caller grant could expire while its query or a membership check waited,
yet the old access context could still authorize replacement.

ConstructionAccessContextResolver now excludes grants expired at the time the
query result is processed. Invalid clock/deadline metadata fails closed.
Replacement resolves current access again after awaited target checks and
rechecks management of the original and target scopes plus role delegation
before revoking or issuing anything.

The regression tests reproduce those waits and verify denial without grant,
audit or idempotency changes. The existing test fixture now supplies the
repository's current-grant method. Replay assertions retain the no-duplicate
guarantee without requiring an exact internal authorization-query count.

## Remaining acceptance boundaries

- Complete CON-C008 screen and explicit confirmation of dangerous expansions
  are not verified in this backend repository.
- Live PostgreSQL RLS, exclusion-constraint races and production JWT deployment
  are not established by local mock/SQLite tests.
- Document assignment is intentionally unavailable at Stage 1A before the
  approved resource validator, rather than accepting an opaque document UUID.
- Construction block and removal from organization are separate missing team
  lifecycle operations from section 3.3 and the related customer clarification.
  Block must revoke Construction access in the tenant without globally blocking
  the Platform account. Membership removal must revoke effective grants in the
  same business operation. Those operations are not implemented by single-grant
  revoke and are not marked complete.

This review does not close CON-C006/C008 or Stage 1A solely by test counts.
Next: review the remaining team lifecycle contract before moving to CON-C009.
