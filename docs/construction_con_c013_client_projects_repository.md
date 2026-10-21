# CON-C013: client card review and direct client projects repository

Authority: full rev.12 section 5.1 and CON-C013; clarifications.txt CON-C013
related list pagination. Latest Stage 1/Measurement deferral is retained.
This is a repository increment, not acceptance of the full client card.

Stage 1A card requires existing client, projects, leads, activities and history.
Estimates, offers, files and measurements are not emulated. Current GET client
returns its explicit minimal object; this step changes no HTTP response/route.

ConstructionClientProjectsRepository selects projects through real client_id,
tenant_id and deleted_at IS NULL. No identity/link is inferred from contact,
name or address. Completed/cancelled projects remain readable. Historical links
are not modified. The future service owns client availability/authorization.

Order: updated_at DESC,id DESC; default limit 20,max 100,one lookahead row.
Cursor binds tenant/client,UTC-aware timestamp/project UUID. Optional authorized
project IDs filter in SQL before order/limit; empty scope returns no rows.
Passing IDs alone is not authorization. The future service must require active
trusted actor/membership,shared projects.read and the applicable scopes.

Caller owns transaction. SELECT uses populate_existing and autoflush=False,
without locks, audit, mutation, commit or rollback. It returns ORM rows for the
future service to validate/materialize. Existing application files are preserved.

Review of dependencies: client read exists; direct client project list was
missing and is prepared here. Existing lead repository filters converted_client_id;
CON-C011 planned activity read supports direct client_id. Physical activity table
creation in Supabase is still pending; no schema is changed here.

EventRepository currently only creates audit; client-card history read was not
found. Last supplied native diagnostic showed INSERT and no SELECT on event_logs
for construction_api. History needs a restricted projection/query/RLS review
before ACL changes. Unrelated Platform events and sensitive audit payloads must
not be exposed. Card aggregation, public response/pagination, history and deployed
acceptance remain. Neither CON-C013 nor Stage 1A is marked complete.

Local SQLite tests verify ordering/ties/pagination, tenant/client/soft-delete
exclusions, scope before lookahead, terminal project reads and cache refresh
without autoflush. Native PostgreSQL RLS and deployed auth are not claimed.
No real Supabase, Storage, email or migration operation occurs.

Next: CON_C013_CLIENT_PROJECTS_READ_SERVICE.
Activity HTTP paths remain pending customer confirmation.
