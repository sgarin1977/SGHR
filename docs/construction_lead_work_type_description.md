# Lead work description: approved deferred classification

Sources: rev.12 section 5.1 and the customer's 07.10.2026 Lead work_type clarification.
CRM create/convert HTTP endpoints are dependencies explicitly included in HTTP Stage 1;
the underlying lead domain belongs to Stage 1A.

Confirmed Supabase read-only preflight:
construction_leads and tables matching work_type were not found.
No existing lead rows are migrated by this local increment.

Implementation:
- ConstructionLeadInput requires nonblank work_type_description instead of work_type.
- Normalization trims surrounding whitespace and retains case/internal whitespace.
- ConstructionLead maps work_type_description as TEXT NOT NULL with a nonblank CHECK.
- The repository persists the description and retains all tenant/transaction predicates.
- Legacy work_type input and arbitrary canonical UUID inputs are rejected.
- Source remains the required extensible code from the preceding increment.
- Status lifecycle, optimistic locking, soft deletion and conversion references remain intact.

Canonical classification is deferred under the explicit customer allowance:
later, a lead may refer to at most one MASTER or ORGANIZATION work type;
an ORGANIZATION target must belong to the same tenant.
That operation must use the real shared catalogs and their approved keys.
No classifier, duplicate registry, unvalidated FK placeholders or automatic
text-to-identity conversion is implemented here. The description must remain
independent of later classification and must not be discarded or overwritten by it.

The future classification fields are master_work_type_id / organization_work_type_id
according to the approved direction. They are not creation inputs or storage
references before their catalogs and validators are implemented.
This increment does not claim canonical classification is complete.

Local tests cover description preservation, invalid/legacy inputs, ORM schema,
repository round trip, tenant binding and caller rollback, together with source
and Stage 1 regression. The physical Supabase lead schema is still not created.
No SQL migration, external database write, Storage or email runs in this installer.
Next: lead creation service with shared authorization and atomic audit, followed
by idempotent workflow and the required POST /construction/crm/leads endpoint.
