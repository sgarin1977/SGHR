<!-- lead-description-contract-2026-10-07 -->
> Implementation update: the original lead text is now work_type_description.
> The source whitelist and former work_type identity assumptions below are
> historical; canonical MASTER/ORGANIZATION classification remains deferred
> under the 07.10.2026 allowance. See construction_lead_work_type_description.md
> for the current local contract and physical-schema checkpoint.

<!-- lead-contract-2026-10-07 -->
> Contract update: the 07.10.2026 customer clarification supersedes the source
> whitelist and final free-text work_type assumptions below. Source is now a
> required extensible code; see construction_lead_source_2026_10_07.md.
> The work_type_description / later canonical classification implementation is
> a separate pending increment. The approved lead lifecycle and grant-history
> contracts remain in force. This document retains the earlier implementation
> history and must not be used to restore the superseded source/work_type rules.

# CON-C009 / CON-C010: lead input and ordinary lifecycle rules

Sources: rev.12 section 5.1 (the detailed business description, not only roadmap
titles), the Stage 1A construction_leads field contract in clarifications.txt
(lines 937–978 in the reviewed copy), and the final customer confirmation
of 2026-10-06 (source, work_type and lifecycle).

ConstructionLeadInput is an immutable creation input, not a database model.
It requires a known source code, a nonblank name, nonblank free-text work_type,
and at least one phone/email after trimming. Optional phone/email/address/comment
blank strings become None. Text case and meaningful internal whitespace are
preserved; no phone region, work catalogue, geocoding or identity is inferred.
Source whitespace/case is normalized only to one of the twelve approved codes.
responsible_user_id may be absent; a provided reference must already be a UUID.
Existence, active membership and tenant binding are later service checks.

A new input has server-controlled status=new. It accepts no tenant/id/version,
conversion references or stored next_action. Those are server/storage concerns;
next_action will be derived from planned non-deleted activities.

The ordinary lifecycle is:
new -> contacted -> measurement_scheduled -> measurement_completed ->
estimate_preparation -> proposal_sent -> negotiation -> won.
lost is reachable from every nonterminal state. won/lost have no outgoing
ordinary transitions. Same-status requests are not transitions in this helper.
The obsolete qualified/proposal codes and automatic reopening are not supported.

CON-C012 conversion is a separate approved business operation and is not
implemented or restricted through this ordinary-transition helper.

This increment validates required text and contact presence. Contact syntax/
provider validation is not established by these rules. No DNS lookup, phone
format contract or catalogue constraint is invented.

No physical lead table, migration, repository, mutable-entity version handling,
audit event, idempotency workflow, API or UI is implemented by this increment.
Subsequent storage must retain the approved row_version/default 1, UTC timestamps,
soft delete and conversion references. Creating a lead requires Idempotency-Key
under the confirmed Stage 1A contract. CRM uses the existing projects.read/create/edit
permission family; no new permission codes are introduced.
