# CON-004: project creation with optional idempotency

Sources: rev.12 sections 10.7, 21 and 22; retained Platform idempotency convention
and the clarification requiring domain mutation and audit in one transaction.

The customer clarification dated 08.10.2026 confirms that Idempotency-Key is
optional for POST /api/v1/construction/projects. This increment supports both
workflow paths; endpoint registration follows in the next increment.

Without a key, the workflow creates the project and audit and validates the
public response before a single commit. No idempotency record or response
ciphertext is written. The factory does not require an encryption key for this
path. Repeating an unkeyed call performs another creation.

With a key, the existing reservation/completion and encrypted replay remain.
The same key and body return the original result without another project or
audit; a changed body raises ApiIdempotencyKeyReusedError for HTTP mapping to
409 IDEMPOTENCY_KEY_REUSED. A supplied invalid key is rejected; a supplied key
without an idempotency adapter fails before business writes.

The workflow uses the existing Platform ApiIdempotencyService and
api_idempotency_records, operation construction.project.create, tenant/user
binding and the existing 24-hour retention. Production factory uses the
existing encrypted response codec. No parallel idempotency table is added.

All repositories share the Construction request session. An active server
actor, applicable construction.projects.create permission and an active
transaction are required before either creation path. Creation calls the confirmed
service with manage_transaction=False. Project, required event_logs audit and
completion of the validated response are committed once. Any failure rolls
back the shared transaction.

Response projection is injected; HTTP wiring will use the existing project
projection. The allowlist matches ProjectReadResponse. Private ORM attributes,
keys and hashes are excluded. Validation and tenant/creator/request binding
happen before completion and commit.

Replay rechecks current supplied server access context and project availability,
returns the original validated 201 snapshot even if mutable project fields
changed later, and creates no second project or audit. An unavailable/deleted
project is not recreated. Changed payload preserves IDEMPOTENCY_KEY_REUSED;
processing preserves the existing idempotency_in_progress convention.

Local tests use the real create service and Platform idempotency service.
SQLite tests persist project, audit and replay markers/results to prove shared
commit/rollback. Their test codec and reservation adapter are not production
encryption or PostgreSQL concurrency/RLS verification. Production factory uses
the existing PostgreSQL repository and Fernet codec.
