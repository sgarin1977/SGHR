# CON-C012: conditional lead conversion repository write

Authority: clarifications.txt CON-C012 requires conversion in one database transaction, successful status won, both stored target IDs, and idempotent repeats. Stage 1A and the new clarification require optimistic locking, mandatory audit in the same transaction, and no version/audit increase for no-op. The endpoint belongs to the Stage 1A domain dependency explicitly included in the required Stage 1 HTTP surface.

ConstructionLeadRepository.complete_conversion performs one parameterized UPDATE RETURNING. Its conditions require the lead tenant/id, non-deleted lead, expected row_version, one of the seven non-terminal statuses, and absent previous conversion links. The selected client and project must exist without soft deletion in the same tenant, and project.client_id must equal the selected client ID. The statement sets status won, both target IDs, updated_at in UTC, and increments the database version by exactly one. Existing lead contact, source, description, address, comment and other fields are preserved.

The positive version must be incrementable in the existing BIGINT storage. A version of 2**63-1 cannot be incremented. The method uses the current database counter, not a potentially stale Python value. Disabled autoflush prevents unrelated pending ORM edits from being written; RETURNING with populate_existing refreshes the loaded lead.

A failed conditional write returns None without mutation. None represents a failed precondition and does not by itself distinguish a version conflict from missing/archived resources, wrong ownership, unavailable lead, or an ineligible state. The service must inspect locked state, classify the error, and ensure rollback of any conversion work. It must not continue to successful audit/completion/commit after None.

The existing get_active_lead_for_update already provides tenant-scoped FOR UPDATE, populate_existing and disabled autoflush. It is reused. The conversion service must also authorize and lock selected resources. EXISTS checks are defensive statement preconditions and do not replace resource locks, RBAC, RLS, audit or Idempotency-Key handling. Repository methods perform no commit, rollback or audit.

won/lost cannot be rewritten by this method. A successful business repeat for won must be handled before the mutation by returning the recorded links with current authorization and required relationship checks; lost is not reopened automatically. Ordinary CRM pipeline transitions remain unchanged. Clients and projects are not reassigned or updated by this repository method.

Local tests cover PostgreSQL SQL compilation, typed input/time/version validation, each eligible status, resource/tenant/ownership guards, stale ORM refresh, preserved unrelated fields, repeat-write refusal, rollback and BIGINT limits. SQLite tests do not prove production PostgreSQL locking, concurrency or RLS.

The installer updates this repository and creates its test and this document. It performs no Supabase, Storage or real email operations. Physical lead schema is not created. Next: conversion service contract review and implementation; the complete conversion endpoint and Stage 1 acceptance remain unfinished.
