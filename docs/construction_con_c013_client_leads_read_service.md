# CON-C013: canonical client-linked leads read service

Authority: full rev.12 section 5.1 and CON-C013; clarifications.txt client card
related lists, limits/order and canonical lead conversion links. Latest source
extensibility/work_type_description decisions in new clarification.txt apply.
Prior unaffected contracts remain valid; no role matrix or grant model changes.

ConstructionClientLeadsReadService reuses list_active_leads with the existing
converted_client_id filter. It never invents client_id or matches contacts/name/
address to create a relationship. Non-converted leads with matching contact data
do not enter this list. Existing recorded conversion links remain unchanged.

Active trusted actor, current Platform membership and projects.read in tenant
CRM scope are required. This retains the existing client read boundary; project/
document grants and responsibility cannot authorize the client's CRM leads.
Current grants are rechecked before the client read, before lead query and after
the final client read before publication. Client must be available in that tenant.
All repositories use the same caller-opened transaction; no_autoflush applies.

Each candidate, including lookahead, must retain tenant/client link and be
non-deleted. Public response is materialized from 16 explicit existing fields;
deleted_at, arbitrary attributes, fake work-type catalog IDs and stored next_action
are absent. The current source is returned as extensible nonblank code without
closed enum. work_type_description is preserved. Known nine-stage status values
are validated without modifying lifecycle or adding reopening. Contact and
aware UTC timestamp validity are checked. Existing project references are kept
as history and checked against the shared read permission in the tenant context;
the read does not require a new active project or mutate historical references.

Order updated_at DESC,id DESC; default 20,max 100,one lookahead. Internal cursor
binds actor,tenant,client,updated_at and lead UUID. No HTTP route/encoding is
invented. Invalid context/order/duplicates fail closed; missing client differs
from a valid client with no related leads. No write, audit, commit, rollback,
encryption, idempotency reservation or physical migration occurs.

Local SQLite tests cover actual filtered pagination, unconverted matching-contact
exclusion, tenant/soft-delete exclusion and refresh without autoflush. Controlled
collaborators cover current authorization loss, malformed records and projection.
Native PostgreSQL RLS and deployed auth are not claimed. Full card aggregation,
activity physical storage, restricted history and public boundary remain.
CON-C013 and Stage 1A are not accepted by this increment.
Next: CON_C013_CLIENT_CARD_AGGREGATION_CONTRACT_REVIEW.
