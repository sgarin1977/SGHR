# CON-C013: client projects read service

Authority: rev.12 section 5.1 and CON-C013; clarifications.txt client card
related list order/limit/cursor contract. new clarification.txt retains prior
contracts unless explicitly superseded. Latest shared permissions/grant scope
and no separate project_members decisions are preserved.

ConstructionClientProjectsReadService lists real projects linked to a currently
available client by tenant/client_id. It keeps the existing client read boundary:
active trusted actor and projects.read in tenant CRM scope. Project-only or
document-only access, responsibility and creation do not grant client-card read.
Fresh Platform membership and grants are checked before client lookup, again
before project query and after the final client lookup before publication.
Loss of membership/tenant scope cannot publish even an empty page/cursor.

All repositories share the caller session/transaction. Client must be non-deleted
and belong to the actor tenant. Each result, including lookahead, must retain the
requested client/tenant, be non-deleted, fit project read scope and a typed public
projection. The projection contains the same 22 fields as the existing project
GET, excluding deleted_at and arbitrary attributes. A materialized response is
returned before later ORM mutations can affect it. UTC timestamps and finite
decimal coordinates are validated. Completed/cancelled projects remain readable.

Order updated_at DESC,id DESC; default 20,max 100,one lookahead. Internal cursor
binds actor,tenant,client and the last returned updated_at/project UUID. It is
not a new HTTP encoding or route. Wrong context, malformed cursor values,
duplicates and invalid query order fail closed. No offset or contact matching.

Reads use no_autoflush and do not mutate, flush, commit, rollback or create audit.
Caller owns transaction cleanup, including failures/cancellation. No encryption
or idempotency key is required. Client GET and all existing application files
are preserved. No Supabase/Storage/email or physical schema change occurs.

Local SQLite tests cover actual list filtering/pagination/projection and refresh
without autoflush. Controlled collaborators cover current access loss, parent
availability and corrupted results. Native PostgreSQL RLS and deployed auth
remain separate acceptance checks. Full CON-C013 is not complete: leads,
activities aggregation, restricted history and the public card boundary remain.
Next: CON_C013_CLIENT_LEADS_READ_SERVICE.
