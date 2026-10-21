# CON-C009: lead creation service

Dependency for POST /api/v1/construction/crm/leads, explicitly included in the
customer's 07.10.2026 HTTP Stage 1 clarification. The lead domain is Stage 1A.

Sources:
- rev.12 section 5.1 and the retained lead field/lifecycle contracts;
- the shared construction.projects.create permission for CRM;
- the approved work_type_description and extensible source clarifications;
- mandatory platform event_logs audit and one transaction for business/audit;
- the retained Platform membership adapter supporting primary or secondary tenant membership.

ConstructionLeadCreateService:
- requires a verified active UUID actor and matching Construction context;
- applies the common create permission and valid role/resource scopes;
- requires an active caller transaction and one shared session;
- revalidates the immutable creation input, uses actor.tenant_id as the tenant;
- checks actor membership and any explicitly assigned responsible user's active
  membership in that tenant;
- re-resolves current effective grants after the awaited membership checks,
  applying the existing role policy and expiration handling;
- creates server initial status=new, version=1, UTC timestamps and null conversion references;
- validates the returned entity binding before mandatory audit;
- writes one construction_lead_created event via the shared audit logger;
- records actor/tenant/entity/trace and the create operation without copying
  contact, description or comment text into the audit payload.

This service prepares writes and owns neither commit nor rollback.
The caller must roll back on failures. The next workflow owns the transaction,
mandatory idempotency reservation/completion, public projection and commit.
Do not wire an HTTP command directly to this service before that workflow.

The existing audit registries/logger stay intact; only construction_lead_created
is added to the Stage 1A registry. No new permission, grant, Platform user,
membership, catalog or mutable lead status operation is introduced.

Local tests cover guards, current grants, membership binding, sanitized errors,
shared-session factory and actual SQLite lead/audit commit and rollback.
They do not establish live Supabase RLS, PostgreSQL concurrency or catalog availability.
The physical lead table remains absent at the latest read-only checkpoint.
This installer performs no Supabase, Storage or real email operations.
