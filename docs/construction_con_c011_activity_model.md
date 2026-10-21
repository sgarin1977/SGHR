# CON-C011: ConstructionActivity ORM model

Authority: rev.12 section 5.1 and Stage 1A CON-C011; clarifications.txt
construction_activities field, lifecycle, UTC/IANA and next_action contracts.
The existing construction_activity_rules remain the domain validators.

The model introduces the approved 19 fields. Initial state is planned/version 1.
due_at, timezone, creator and tenant are required; responsible user, comment,
result and terminal/deletion timestamps are nullable. Result is text; no mandatory
result or cancellation reason is imposed. Timestamps use timezone-aware columns.
planned has neither terminal timestamp; completed and cancelled have their own
timestamp and exclude the other outcome timestamp. No reverse lifecycle is added.

At least one lead/client/project link is required, and multiple links are allowed.
Each business-resource FK pairs tenant_id with the resource ID to prevent foreign
tenant links. Existing lead/client/project records are preserved as historical
references rather than cascading deletion through those links. The model does not
infer conversion, project ownership or user access from these references.

next_action is not stored on a lead or activity. A partial tenant/lead/due_at/id
index supports the forthcoming nearest non-deleted planned activity query.
UTC normalization and IANA validation remain in the domain layer; a text DB
constraint alone does not validate membership in the IANA database. Responsible
user membership, parent availability and permission checks belong to services.

Only ORM declaration, local tests and this document are installed. No physical
Supabase table, migration, RLS/privilege policy or endpoint is applied here.
Existing lead status/create/conversion code is preserved. Local tests compile
PostgreSQL DDL and exercise SQLite constraints, tenant links and ORM versions;
they do not establish native PostgreSQL/RLS or deployed endpoint behavior.
Next: CON_C011_ACTIVITY_REPOSITORY.
