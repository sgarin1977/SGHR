<!-- lead-description-contract-2026-10-07 -->
> Implementation update: the original lead text is now work_type_description.
> The source whitelist and former work_type identity assumptions below are
> historical; canonical MASTER/ORGANIZATION classification remains deferred
> under the 07.10.2026 allowance. See construction_lead_work_type_description.md
> for the current local contract and physical-schema checkpoint.

# Lead source: superseding 07.10.2026 contract

Dependency for the CRM HTTP endpoints explicitly included in the customer's
HTTP Stage 1 clarification. The underlying lead domain belongs to Stage 1A.

The 07.10.2026 Lead source clarification supersedes the earlier fixed list:
source remains a required extensible code. There is no closed code set,
PostgreSQL enum, competing source registry or replacement fixed list.

This increment:
- removes CONSTRUCTION_LEAD_SOURCES and whitelist validation;
- keeps existing trim/lower normalization for new input;
- rejects non-text and trimmed-empty input;
- changes the ORM source check to length(btrim(source)) > 0, retaining TEXT NOT NULL;
- accepts extended source codes in repository creation and bound source filters;
- preserves historical rows, tenant predicates and caller-owned transactions;
- retains the complete approved CRM status pipeline and all unrelated constraints.

No additional code syntax/ASCII pattern or catalogue requirement is invented.
The former twelve source values remain valid examples, not a whitelist.

The work_type correction is a separate pending increment:
preserve original text as work_type_description and prepare later canonical
classification according to the MASTER/ORGANIZATION work type contract.
Do not interpret the currently retained work_type field as the final model.

Local tests cover normalization, unknown code acceptance, actual SQLite CHECK
behavior, repository filtering/tenant binding and rollback.
No physical Supabase state is established by an ORM edit. Before external
acceptance, inspect whether construction_leads exists and reconcile its actual
source constraint with this contract using a separately reviewed SQL step.
This installer does not connect to Supabase or alter historical database rows.
