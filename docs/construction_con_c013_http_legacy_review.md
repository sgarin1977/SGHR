# CON-C013: HTTP and legacy history review

Reviewed authority: rev.12 sections 5.1 and 22; clarifications.txt CON-C013;
new clarification.txt customer message 13.10.2026 21:01.

Approved: GET /api/v1/construction/clients/{id}; real client, projects, leads,
planned activities and domain history; independent lists (default 20/max 100)
and approved ordering. The existing GET currently places nine client fields in
data. The documents do not specify the final combined JSON shape or independent
cursor query names. Customer question: retain these client fields at data root,
add four list fields, use section_limit/section_cursor and meta.pagination with
independent next_cursor, or require data.client nesting? No route/response change
is installed while that question is pending. The independent cursor helper is
preparation and does not decide that layout.

History is attributed at event time, not from present mutable relationships.
Implemented proofs: stable canonical related_client_id; intrinsic client entity
ID; an explicit project client_id change in the event; a completed lead conversion
with valid converted_client_id/status changes and consistent creation details.
Invalid/conflicting present markers are rejected, not replaced from current rows.
Unknown legacy relations are omitted without editing old events or inventing a
marker. SQL filtering occurs before pagination, with service revalidation.

Remaining legacy work: entity/audit evidence beyond the implemented event-only
subset. Current project.client_id alone is insufficient because project updates
can replace it. Current lead.converted_client_id alone cannot retroactively prove
a client was known when a pre-conversion event occurred. Activity links are not
edited by current terminal operations, but entity-based historical proof still
requires explicit event/entity identity, tenant, timestamp and relationship
validation and repository integration before limit; no blanket current-state
join or post-limit filter should be substituted. This is an implementation gap,
not a new customer-approved business exception.

Native event_logs metadata/ACL and all five production SELECT planning probes
were reported passed after the JSONB CASE grouping fix. This proves SQL planning,
not actual history visibility, native RLS behavior or deployed authentication.
Those acceptance checks and eventual cleanup of the retained Stage 1 fixture
remain outstanding. No Stage 1A/CON-C013 acceptance is claimed.
