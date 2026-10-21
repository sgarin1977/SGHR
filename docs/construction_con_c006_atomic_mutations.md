# CON-C006/C008: atomic grant mutation service

Contract: customer confirmation of 2026-10-06 in
construction_stage_1a_final_contracts_2026_10_06.md.

ConstructionAccessMutationService prepares revoke or replacement and audit
using one Construction session. It never commits. The owning workflow must
commit the complete business operation once and roll back on its own failure.
Do not compose the legacy grant_access/revoke_access methods, which commit.

Replacement locks the original tenant-scoped grant, checks authority in both
original and target scopes, validates the target role, locks an active project
when applicable, and checks existing Platform membership. It does not create,
restore or delete membership. Server time is read after awaited preconditions.
The conditional revoke and new grant preserve the old role/scope/expiry history.
The existing temporal exclusion constraint remains the DB concurrency guard.

An authorized retry of revoke on an already revoked grant returns the existing
record without another write or audit. An ineffective original grant or a
conditional revoke miss raises 409 / ACCESS_GRANT_CONFLICT.
Only the structured diagnostic for ex_construction_access_grants_active_period
(SQLSTATE 23P01) is translated from a database integrity failure to that conflict.
No row_version is added or required for grants.

Document scope creation remains unavailable until Document Center validation.
The factory shares the session among grant, project and audit repositories.

Tests use transaction state doubles and the actual permission/audit code.
They verify preserved history, both audit events, rollback after failures,
scope/role authorization, expiry after a wait, and narrow conflict mapping.
They do not prove live PostgreSQL concurrency or production HTTP acceptance.

Next: workflow ownership of transaction, current authorization and idempotency;
then the HTTP mutation contract. This service alone does not close CON-C006/C008.
