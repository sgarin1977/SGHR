# CON-C011: idempotent activity creation workflow

Authority: rev.12 section 5.1 and CON-C011; clarifications.txt shared activity
creation/audit/transactions, permissions, UTC/IANA schedule and mandatory
Idempotency-Key for create activity. Latest new clarification.txt stage boundary
and CON-C010 HTTP confirmation remain respected.

The caller supplies activity type, aware deadline, IANA timezone, linked IDs,
nullable responsible user/comment and Idempotency-Key. Tenant/creator/initial
state are server-owned. The workflow validates the command and hashes supplied
values, retaining original whitespace and deadline offset, before normalization
affects persisted data. Changed supplied command with the same active key conflicts.

The shared api_idempotency_records mechanism is bound to tenant/user principal,
operation construction.activity.create, key hash and request hash. Its existing
24-hour retention and encrypted response codec are reused. No new storage is
introduced. Activity, required audit and completed reservation share one session
and commit. Failed audit, completion, projection, access or commit triggers rollback.
Cancellation also attempts rollback. Simulated commit errors precede actual
commit; recovery from an ambiguous network outcome is not claimed.

Creation returns the approved business fields excluding deleted_at and internal
attributes. The immutable creation snapshot has planned/version 1 and is encrypted,
including optional comment. No key/hash/ciphertext/binding is returned publicly or
duplicated into domain audit. Current permissions/membership are rechecked around
reservation and before commit.

Replay checks linked records and the original activity's tenant/creator/links,
non-deleted state, known current status and positive version. It returns the
original creation snapshot, even after later completion or metadata changes,
without another activity or audit. Reassigned links or unavailable records are
not used to broaden scope. Processing reservation yields idempotency_in_progress;
changed request uses the shared key-reused error for future HTTP mapping.

Local tests cover encrypted binding, replay, key reuse, current access loss,
unavailable records, corrupted snapshots, fixed transaction wiring and actual
SQLite activity/audit/reservation commit and rollback. Native PostgreSQL/RLS,
concurrent requests and deployed auth are not claimed. The physical Supabase
activity schema is still not created by the installer. No HTTP route is added.
Next: CON_C011_ACTIVITY_COMPLETE_CANCEL_REPOSITORY.
