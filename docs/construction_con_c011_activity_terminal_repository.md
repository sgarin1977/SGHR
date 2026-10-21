# CON-C011: conditional completion/cancellation persistence

Authority: clarifications.txt construction_activities lifecycle planned ->
completed/cancelled, result/completed_at/cancelled_at fields, UTC timestamps,
row_version and shared atomic mutation/audit requirements; retained rev.12 CON-C011.

ConstructionActivityRepository adds complete_activity/cancel_activity via a
shared conditional write. It updates only the selected tenant/activity while
non-deleted, still planned, at the expected version and with no terminal timestamps.
The write sets the requested terminal status, optional normalized text result,
matching server UTC terminal timestamp, updated_at and exactly one version advance.
All links, schedule, creator, responsible user and comment remain unchanged.
No required completion text or cancellation reason is imposed.

Missing/foreign/deleted/stale/already terminal state returns None. There is no
second transition of a completed/cancelled row, reverse/reopen or terminal no-op
UPDATE. The future service classifies conflicts, checks actual permissions and
records the mandatory audit. Idempotent repeat of complete/cancel belongs to the
future workflow using its required key, not to another repository write.

Repository never commits, rolls back or writes audit. UPDATE RETURNING refreshes
the existing identity and disables autoflush. Local tests compile PostgreSQL SQL,
exercise actual SQLite conditional writes, field preservation, next_action
exclusion after finalization, stale state and caller rollback. No live RLS,
concurrency, physical Supabase schema or HTTP behavior is claimed.
Next: CON_C011_ACTIVITY_COMPLETE_CANCEL_SERVICE.
