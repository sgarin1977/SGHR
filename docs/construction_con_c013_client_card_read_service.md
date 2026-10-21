# CON-C013: client card aggregation service

Authority: rev.12 section 5.1 / CON-C013; clarifications.txt CON-C013 (real
client/projects/leads/activities/history, independent lists, default 20/max 100,
approved sorting) and new clarification.txt, 13.10.2026 21:01 (planned activities
through client/converted lead/project, operation-time history, restricted RLS).

The service composes the existing client read and four related-list services.
No new table, permission, write operation, idempotency operation or HTTP route
is introduced. Estimates, offers, files and measurements are not represented
by placeholder entities or response sections. Existing application files are
preserved. This is an internal service/result contract; HTTP response shape and
cursor encoding are not introduced by this increment.

All collaborators must share the caller session. An active caller transaction
and no_autoflush are required. Sections are read sequentially, not concurrently
on AsyncSession. The service does not flush, lock, commit or roll back. Caller
owns transaction cleanup, including failures and cancellation. A failure in any
section prevents returning the entire card; no partial-success response exists.
One transaction does not by itself claim repeatable-read snapshot isolation.

Active canonical actor/context and existing construction.projects.read in tenant
CRM scope are required. Creator/responsible identity and project-only grants do
not authorize a client card. Current Platform membership/effective grants are
refreshed before reading and between sections. After the last client availability
lookup, access is refreshed again and every returned project reference retains
scope checks. Membership loss/revocation during a section or final client await
prevents publication, including empty related pages.

Client uses the same nine public fields as its existing GET. Client must remain
non-deleted in the authorized tenant. Returned client data and related records
are detached projections. Four existing page/cursor types remain independent;
limit 20 default, 100 maximum per list. Cursors bind actor/tenant/client and list
position. Page types, bounds, public schemas, order, duplicate IDs and next cursor
positions are rechecked. Projects/leads keep updated_at DESC/id DESC, activities
due_at ASC/id ASC, history created_at DESC/id DESC.

Children preserve canonical project/client and converted-lead/client links,
planned activity OR-link evidence/deduplication, and event-time history proof.
The aggregator does not reassign past history from present project links or
drop history when its entity is archived. Archive-aware history access remains
in the history read service. An archived client does not become available through
the ordinary active-client card read. A directly returned activity.client_id may
differ when a validated lead/project relation proves inclusion, as approved.

This increment is tested with real child services and controlled repositories,
and their existing local relational tests. Native SQL planning was separately
confirmed by the user; native history RLS behavior, deployed authentication and
public card HTTP acceptance remain separate. Additional unambiguous entity-based
legacy evidence beyond the implemented event-data subset still requires review.
CON-C013 and Stage 1A are not declared complete.

Next: review the public client-card HTTP contract/integration and remaining
legacy history evidence, then targeted native acceptance and fixture cleanup.
