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

# CON-C009 / CON-C010: ConstructionLead ORM storage

Sources: rev.12 section 5.1, the confirmed construction_leads field contract
and final customer confirmation of 2026-10-06.

ConstructionLead adds exactly the approved fields to database/models.py.
source and status use the final fixed codes; work_type remains TEXT NOT NULL.
DB checks protect required SQL-trimmed name/work type, phone-or-email presence
and positive row_version. Full input normalization remains in ConstructionLeadInput.

Initial status is new. ORM and server row_version defaults are 1, with SQLAlchemy
version_id_col enabled. created_at/updated_at have aware UTC Python defaults and
timestamptz/server now() declarations; updated_at also has an ORM UTC onupdate.
deleted_at is nullable and represents soft deletion.

Converted client/project references are nullable composite tenant foreign keys
to existing parent (tenant_id,id) keys. They cannot refer to another tenant.
Multiple leads may reference the same existing client/project. No paired-reference
or won-requires-conversion check is added. Project-to-client ownership and all
conversion business modes remain CON-C012 service responsibilities.

responsible_user_id is a nullable FK to the Platform user, with the same SET NULL
deletion behavior as the existing project responsibility field. Active membership
in the current tenant is a service check; home-tenant equality is not substituted
for Platform membership.

The tenant/id unique key supports future tenant-scoped activity references.
A tenant/updated_at/id index supports the agreed lead list order.
No stored next_action, address normalization fields, work catalogue, account copy,
created-by field or new permission code is introduced.

Tests compile PostgreSQL DDL and inspect mapped types/defaults/constraints.
SQLite in-memory tests enforce the FK/check constraints, exercise ORM stale-write
rejection and soft deletion. SQLite timestamp readback is not PostgreSQL UTC proof.
RLS, physical Supabase schema installation and live concurrency remain pending.

The installer only appends this ORM class and creates local tests/docs. It does
not execute migrations or contact Supabase. Next: tenant-scoped lead repository,
followed by authorization, audit, idempotent creation workflow and HTTP.
