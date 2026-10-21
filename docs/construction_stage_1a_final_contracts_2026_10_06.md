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

# Final Stage 1A contracts — 2026-10-06

Source: customer's explicit confirmation supplied in this chat.
This document supersedes earlier conflicting assumptions for
CON-C006/C008 and CON-C009/C010.

## Access grant history

Do not add row_version to construction_access_grants.
A grant records historical access issuance. After creation, role,
scope_type, scope_id and expires_at are not changed in place.

Changing role, scope or expiry is one atomic operation:
revoke the original effective grant, create a new grant with the required
parameters, and record both operations in audit in the same transaction.
Preserve the existing effective-grant uniqueness constraint.

Concurrency is controlled by atomic database conditions and the approved
effective-grant uniqueness. Revoke is conditional on an effective grant.
An already revoked grant is an authorized idempotent no-op without another
audit event. If a requested change no longer applies to the original
effective state, return HTTP 409 / ACCESS_GRANT_CONFLICT.
ROW_VERSION_CONFLICT does not apply to grants.

The ordinary actor, tenant, resource-scope and delegation guards continue
to apply. This contract does not authorize restoring suspended membership.

## Lead source

source is required and must use one of these machine-readable codes:

website, phone, email, whatsapp, telegram, social, referral, partner,
repeat_client, walk_in, manual, other.

Arbitrary user-defined source values are not accepted. Additional context
for other may be recorded in comment/notes. Future approved source codes
may be added without rewriting historical records.

## Lead work_type

work_type TEXT NOT NULL. It is free text at this stage.
A value empty after trim is invalid.
No UUID/catalogue binding or automatic text-to-code conversion is introduced.
A future normalized catalogue requires a separate contract and must preserve
the existing text.

## Final lead lifecycle

new -> contacted -> measurement_scheduled -> measurement_completed ->
estimate_preparation -> proposal_sent -> negotiation -> won

lost is allowed from every nonterminal state.
won and lost are terminal for the ordinary workflow.
No automatic backward transition is allowed. Reopening, if introduced
later, requires a separate domain operation.

qualified and proposal are obsolete for the new implementation.
The full rev.12 business chain replaces the earlier shortened lifecycle.

## This implementation increment

ConstructionAccessMutationRepository.revoke_if_effective performs one
tenant/ID-scoped conditional UPDATE. It changes only status,
revoked_by_user_id and revoked_at, and refreshes the returned ORM state.
It owns no commit, rollback, ORM flush or audit.

A None result means no effective row matched. The future mutation service
must distinguish an already-revoked no-op from an inapplicable change
under the original grant lock. This repository result is not itself a
successful replacement operation.

ConstructionAccessGrantConflictError supplies the approved 409 error code.
HTTP mapping, the atomic replacement service and real database concurrency
verification are the subsequent increments.

No physical model, constraint, existing service or endpoint is changed
by this installer. Tests compile PostgreSQL SQL against mocked sessions;
no Supabase, Storage or real email operations are executed.
