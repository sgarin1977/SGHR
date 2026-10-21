# CON-C012: lead conversion eligibility and selection

Authority: rev.12 section 5.1 requires conversion without re-entering available data; section 22 lists POST /api/v1/construction/crm/leads/{id}/convert. The customer clarification in clarifications.txt under CON-C012 fixes one database transaction, idempotency, status won, stored converted_client_id/converted_project_id, and the three allowed scenarios. The corrected nine-state lifecycle remains authoritative.

ConstructionLeadConversionSelection represents:
- no existing IDs: create client and project;
- existing client_id only: create a project for that client;
- existing client_id and project_id: link the selected existing project of that client.

An existing project with a new client is prohibited. Typed IDs are only a syntactic prerequisite; the conversion service must verify tenant, availability, project ownership, permissions and resource scope before using them. This helper performs no database operations and grants no access.

inspect_construction_lead_conversion permits a separate conversion operation from each of the seven non-terminal stages. It does not change ordinary pipeline transitions. lost requires an explicit reopen; no reopening is implemented by this step. won with two recorded UUID links identifies a repeat that must return the existing resources, after service authorization and relationship checks. won without valid links cannot fabricate replacement resources. Non-terminal states with conversion links are rejected before planning another conversion.

Subsequent service/workflow work must lock the lead, validate and lock selected resources, apply optimistic locking for the mutation, preserve available name/phone/email and project context under the normal client/project contracts, set won and both links, perform mandatory audit and idempotency completion in one transaction. Existing resources must not be silently reassigned or overwritten. A repeat must create neither resources nor another conversion audit.

A required next dependency is removing the unconditional internal commit from client creation when it participates in an outer audited transaction. Existing standalone client creation behavior must remain supported. Project creation already supports manage_transaction=False. No mapping defaults for required client_type or project name are invented by this rule-only step.

This installer adds the pure rule module, its tests, and this document. It changes no existing application code and performs no Supabase, Storage or real email operations. Physical lead schema remains uncreated. Rule checks alone do not implement the conversion endpoint or close Stage 1.

Next step: prepare client creation for caller-owned conversion transactions, then the conversion repository/service/workflow/HTTP boundary and database acceptance.
