# CON-C006/C008: grant mutation workflow

Contract: final customer confirmation of 2026-10-06, plus the existing
Stage 1A audit and Platform idempotency contracts.

ConstructionAccessMutationWorkflow owns one commit for grant history and
audit, together with idempotency completion when a key is supplied.
Audit, completion or commit failure rolls back the complete transaction.

Idempotency-Key is optional for grants: they are not listed among the
Stage 1A operations requiring that header. Without a key, conditional revoke
and the approved effective-grant exclusion constraint control concurrency.
With a key, the existing ApiIdempotencyService hashes the key/request,
encrypts the public response and scopes it to tenant, user and operation.

Operations:
- construction.access_grant.revoke
- construction.access_grant.replace

The workflow reads fresh actor grants after awaited idempotency, original
grant and target-project locks. Replay also rechecks authority in the
original and requested target scopes, including delegation of the new role.
A replay returns the original successful response; it does not reinstate
or modify a replacement that has subsequently expired or been revoked.
A new request/key cannot replace an already revoked original grant.

Public responses contain exactly id, user_id, role, scope_type, scope_id,
status, expires_at and granted_at. No row_version or internal audit/identity
metadata is returned. Scope, expiry, user and response shape are verified
before a cached response is returned.

Tests use an in-memory SQLite transaction ledger with the actual mutation
service, authorization resolver, audit logger and Platform encryption service.
They verify transaction boundaries and replay behavior, not live PostgreSQL
concurrency, RLS or production HTTP. No external operations are performed.

Next: HTTP revoke/replace integration and API contract. CON-C006/C008 and
Stage 1A are not declared complete by this workflow test result.
