# CON-C011: planned activity reads and computed next_action

Sources: rev.12 section 5.1 and CON-C011; clarifications.txt approved activity
model, next_action definition, retained shared projects.read permission and
related planned list order/pagination. No HTTP address is introduced here.

ConstructionActivityReadService returns materialized public data for planned
non-deleted activities. get_next_action_for_lead checks that the current lead
exists in the authorized tenant, then selects the earliest planned non-deleted
activity by due_at ASC, id ASC. Overdue activities remain eligible. No candidate
returns None. No stored next_action column or task is created.

list_planned_activities supports conjunctive lead/client/project/responsible
filters, limit 20 by default and 100 maximum. The repository applies tenant,
planned/non-deleted and authorized project scope before order/limit. A project
grant cannot expose tenant CRM links. No post-query filtering changes page
meaning. One authorized lookahead row determines next_cursor.

The internal cursor carries tenant, actor, the four query filters, due_at and
activity UUID. Reusing it under another actor/tenant/filter query is rejected.
The cursor is a service value, not a newly invented HTTP encoding. Output is
an 18-field explicit projection with UTC-aware dates, excluding deleted_at and
arbitrary ORM attributes. The result is materialized before publication.

Fresh Platform membership/grants are resolved before reads and after awaited
queries/parent checks. Each returned candidate, including lookahead, must fit
its filters and shared permission/scope contract. Loss of a queried scope
prevents publishing rows/count/cursor. Responsibility/creation do not grant
permission. Existing parent links in returned activity data are preserved;
filtered parent availability is checked with ordinary reads, not locks.

The service requires an active caller transaction and shared repositories;
no_autoflush protects reads. It does not commit, rollback, add, flush or write
audit. The caller owns read transaction cleanup. There is no idempotency key
requirement for these reads.

The new ConstructionActivityReadRepository extends the existing repository and
adds a scope-limited SELECT. Existing application files are preserved. Local
tests cover SQL filtering before pagination, UTC dates, equal-deadline cursors,
overdue selection, terminal/deleted/foreign exclusions, access loss and malformed
rows. Native PostgreSQL RLS, deployed auth and physical activity DDL are not
claimed. No real Supabase, Storage or email operation occurs in this installer.

Activity HTTP create/complete/cancel paths await the customer response. This
service can be reused by the future approved HTTP boundary and client card.
Next: CON_C011_HTTP_CLARIFICATION_OR_CON_C013_CLIENT_CARD_REVIEW.
