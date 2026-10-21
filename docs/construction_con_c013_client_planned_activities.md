# CON-C013: client card planned activities

Authority: rev.12 CON-C013 and client card description; customer clarification
13.10.2026 21:01 in new clarification.txt, Planned activities.

The separate read query uses direct activity.client_id OR a same-tenant lead's
converted_client_id OR a same-tenant project's client_id. Only non-deleted,
planned activities of the current tenant are included. Parent archival does
not erase a canonical association; no unapproved active-parent filter is added.
Both joins target unique parent UUIDs, so an activity is returned once even if
all three links match. Filtering and deterministic due_at/id ordering precede
bounded keyset pagination (limit 1..100 plus one lookahead). Overdue planned
activities are included. The original general activity list retains its AND
filter contract unchanged.

Internal joined ID/tenant/client evidence is validated by the service, then
discarded from the public result. It reuses the existing planned activity
response fields, preserving original client/lead/project links. OR membership
can include an activity for each explicitly linked client; this differs from
the singular operation-time audit correlation marker and does not alter it.

The service requires an active caller transaction, the existing projects.read
permission and tenant CRM scope, active Platform membership and an available
client. Fresh grants/membership and client availability are checked before
reading and before publication, including empty pages. Project references also
retain existing scope checks. Cursor binding covers user/tenant/client.
Malformed, foreign, unrelated, duplicate, unsorted or non-planned results fail
closed without partial publication. Materialized public data contain no ORM
or correlation evidence. Caller owns transaction; no flush, lock, audit or
commit/rollback is performed by this read path.

No HTTP route, new permission, table, index, policy, historical event update,
idempotency record update, live Supabase, Storage or email operation is added.
This is a repository/service increment, not a complete client card or Stage 1A
acceptance. Restricted audit history under RLS and card aggregation remain.
Next: CON_C013_CLIENT_HISTORY_READ_CONTRACT_REVIEW.
