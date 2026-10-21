-- Create the missing lead storage required by the approved Stage 1 HTTP boundary.
-- Domain code: CON-C009/C012 (Stage 1A dependency). No Measurement domain.
-- Run in Supabase SQL Editor as postgres. One transaction; failures roll back DDL.
-- Existing tables and their ACL/RLS are not modified. No business rows inserted.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

DO $migration$
DECLARE
    role_oid oid;
    owner_oid oid;
    lead_oid oid;
    parent_oid oid;
    key_numbers smallint[];
    parent_key_names text[];
    table_name text;
    column_name text;
    acl_role text;
    current_updates text[];
    actual_columns text[];
    checks jsonb;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: Run this schema script as postgres in SQL Editor.';
    END IF;
    SELECT oid INTO owner_oid FROM pg_catalog.pg_roles WHERE rolname = current_user;
    SELECT oid INTO role_oid FROM pg_catalog.pg_roles
    WHERE rolname = 'construction_api' AND NOT rolsuper AND NOT rolbypassrls;
    IF role_oid IS NULL OR pg_catalog.pg_has_role(role_oid, owner_oid, 'MEMBER')
       OR NOT pg_catalog.has_schema_privilege(role_oid, 'public', 'USAGE') THEN
        RAISE EXCEPTION 'STOP: construction_api security prerequisites differ from preflight.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('construction_schema'),
        pg_catalog.hashtext('lead_storage_2026_10_12')
    );
    IF pg_catalog.to_regclass('public.construction_leads') IS NOT NULL THEN
        -- Repeated execution never repairs or overwrites an existing relation.
        PERFORM pg_catalog.set_config('app.lead_schema_result', jsonb_build_object(
            'state', 'EXISTS_NO_CHANGES', 'table', 'public.construction_leads',
            'metadata_revalidation_required', true, 'business_rows_modified', 0,
            'existing_tables_modified', false
        )::text, true);
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND c.relname ILIKE '%lead%'
    ) THEN
        RAISE EXCEPTION 'STOP: A lead relation appeared since preflight; inspect before creating storage.';
    END IF;
    IF pg_catalog.to_regclass('public.ix_construction_leads_tenant_updated_id') IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: Expected index name is already occupied.';
    END IF;
    FOREACH table_name IN ARRAY ARRAY['tenants', 'users', 'construction_clients', 'construction_projects']
    LOOP
        parent_oid := pg_catalog.to_regclass('public.' || table_name);
        IF parent_oid IS NULL OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = parent_oid AND c.relkind IN ('r', 'p')
        ) THEN
            RAISE EXCEPTION 'STOP: Missing parent table public.%', table_name;
        END IF;
        IF NOT pg_catalog.has_table_privilege(current_user, parent_oid, 'REFERENCES') THEN
            RAISE EXCEPTION 'STOP: REFERENCES privilege missing for public.%', table_name;
        END IF;
        parent_key_names := CASE
            WHEN table_name IN ('construction_clients', 'construction_projects') THEN ARRAY['tenant_id', 'id']
            ELSE ARRAY['id'] END;
        FOREACH column_name IN ARRAY parent_key_names
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute a
                WHERE a.attrelid = parent_oid AND a.attname = column_name
                  AND a.attnum > 0 AND NOT a.attisdropped
                  AND a.atttypid = 'uuid'::regtype AND a.attnotnull
            ) THEN
                RAISE EXCEPTION 'STOP: Parent UUID key differs: public.%.%', table_name, column_name;
            END IF;
        END LOOP;
        SELECT array_agg(a.attnum ORDER BY k.ordinality) INTO key_numbers
        FROM unnest(CASE WHEN table_name IN ('construction_clients', 'construction_projects')
            THEN ARRAY['tenant_id', 'id'] ELSE ARRAY['id'] END) WITH ORDINALITY k(name, ordinality)
        JOIN pg_catalog.pg_attribute a
          ON a.attrelid = parent_oid AND a.attname = k.name AND NOT a.attisdropped;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            WHERE c.conrelid = parent_oid AND c.contype IN ('p', 'u')
              AND c.conkey = key_numbers AND c.convalidated AND NOT c.condeferrable
        ) THEN
            RAISE EXCEPTION 'STOP: Required parent unique key missing on public.%', table_name;
        END IF;
    END LOOP;

CREATE TABLE public.construction_leads (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	source TEXT NOT NULL, 
	name TEXT NOT NULL, 
	phone TEXT, 
	email TEXT, 
	address_raw TEXT, 
	work_type_description TEXT NOT NULL, 
	responsible_user_id UUID, 
	status TEXT DEFAULT 'new' NOT NULL, 
	comment TEXT, 
	converted_client_id UUID, 
	converted_project_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	row_version BIGINT DEFAULT 1 NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_construction_leads_source CHECK (length(btrim(source)) > 0), 
	CONSTRAINT ck_construction_leads_status CHECK (status IN ('new', 'contacted', 'measurement_scheduled', 'measurement_completed', 'estimate_preparation', 'proposal_sent', 'negotiation', 'won', 'lost')), 
	CONSTRAINT ck_construction_leads_name CHECK (length(btrim(name)) > 0), 
	CONSTRAINT ck_construction_leads_work_type_description CHECK (length(btrim(work_type_description)) > 0), 
	CONSTRAINT ck_construction_leads_contact CHECK ((phone IS NOT NULL AND length(btrim(phone)) > 0) OR (email IS NOT NULL AND length(btrim(email)) > 0)), 
	CONSTRAINT ck_construction_leads_row_version CHECK (row_version > 0), 
	CONSTRAINT uq_construction_leads_tenant_id_id UNIQUE (tenant_id, id), 
	CONSTRAINT fk_construction_leads_tenant_converted_client FOREIGN KEY(tenant_id, converted_client_id) REFERENCES public.construction_clients (tenant_id, id), 
	CONSTRAINT fk_construction_leads_tenant_converted_project FOREIGN KEY(tenant_id, converted_project_id) REFERENCES public.construction_projects (tenant_id, id), 
	FOREIGN KEY(tenant_id) REFERENCES public.tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(responsible_user_id) REFERENCES public.users (id) ON DELETE SET NULL
);

CREATE INDEX ix_construction_leads_tenant_updated_id ON public.construction_leads (tenant_id, updated_at, id);

    ALTER TABLE public.construction_leads ENABLE ROW LEVEL SECURITY;
    -- Remove default grants only on the table just created, then grant its required API operations.
    REVOKE ALL PRIVILEGES ON TABLE public.construction_leads FROM PUBLIC;
    FOR acl_role IN
        SELECT DISTINCT r.rolname
        FROM pg_catalog.pg_class c
        CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) acl
        JOIN pg_catalog.pg_roles r ON r.oid = acl.grantee
        WHERE c.oid = 'public.construction_leads'::regclass AND acl.grantee <> owner_oid
    LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.construction_leads FROM %I', acl_role);
    END LOOP;
    GRANT SELECT, INSERT ON TABLE public.construction_leads TO construction_api;
    GRANT UPDATE (status, converted_client_id, converted_project_id, updated_at, row_version) ON TABLE public.construction_leads TO construction_api;
    CREATE POLICY construction_leads_select ON public.construction_leads
        FOR SELECT TO construction_api
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    CREATE POLICY construction_leads_insert ON public.construction_leads
        FOR INSERT TO construction_api
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    CREATE POLICY construction_leads_update ON public.construction_leads
        FOR UPDATE TO construction_api
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);

    lead_oid := 'public.construction_leads'::regclass;
    SELECT array_agg(a.attname::text ORDER BY a.attname::text) INTO actual_columns
    FROM pg_catalog.pg_attribute a WHERE a.attrelid = lead_oid AND a.attnum > 0 AND NOT a.attisdropped;
    SELECT array_agg(a.attname::text ORDER BY a.attname::text) INTO current_updates
    FROM pg_catalog.pg_attribute a WHERE a.attrelid = lead_oid AND a.attnum > 0 AND NOT a.attisdropped
      AND pg_catalog.has_column_privilege(role_oid, lead_oid, a.attnum, 'UPDATE');
    checks := jsonb_build_object(
        'role_enforces_rls', NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
            WHERE oid = role_oid AND (rolsuper OR rolbypassrls)),
        'no_owner_membership', NOT pg_catalog.pg_has_role(role_oid, owner_oid, 'MEMBER'),
        'table_owned_by_postgres', EXISTS (SELECT 1 FROM pg_catalog.pg_class
            WHERE oid = lead_oid AND relowner = owner_oid),
        'rls_enabled', EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE oid = lead_oid AND relrowsecurity),
        'exact_orm_columns', actual_columns = ARRAY['address_raw', 'comment', 'converted_client_id', 'converted_project_id', 'created_at', 'deleted_at', 'email', 'id', 'name', 'phone', 'responsible_user_id', 'row_version', 'source', 'status', 'tenant_id', 'updated_at', 'work_type_description']::text[],
        'named_orm_constraints_present', (SELECT count(*) FROM pg_catalog.pg_constraint
            WHERE conrelid = lead_oid AND conname IN ('ck_construction_leads_contact', 'ck_construction_leads_name', 'ck_construction_leads_row_version', 'ck_construction_leads_source', 'ck_construction_leads_status', 'ck_construction_leads_work_type_description', 'fk_construction_leads_tenant_converted_client', 'fk_construction_leads_tenant_converted_project', 'uq_construction_leads_tenant_id_id') AND convalidated) = 9,
        'exact_constraint_count', (SELECT count(*) FROM pg_catalog.pg_constraint WHERE conrelid = lead_oid) = 12,
        'index_valid', EXISTS (SELECT 1 FROM pg_catalog.pg_index
            WHERE indrelid = lead_oid AND indexrelid = 'public.ix_construction_leads_tenant_updated_id'::regclass
              AND indisvalid AND indisready),
        'select_insert_allowed', pg_catalog.has_table_privilege(role_oid, lead_oid, 'SELECT')
            AND pg_catalog.has_table_privilege(role_oid, lead_oid, 'INSERT'),
        'update_only_conversion_fields', current_updates = ARRAY['converted_client_id', 'converted_project_id', 'row_version', 'status', 'updated_at']::text[],
        'no_table_wide_update', NOT pg_catalog.has_table_privilege(role_oid, lead_oid, 'UPDATE'),
        'no_delete_or_truncate', NOT pg_catalog.has_table_privilege(role_oid, lead_oid, 'DELETE')
            AND NOT pg_catalog.has_table_privilege(role_oid, lead_oid, 'TRUNCATE'),
        'three_role_scoped_policies', (SELECT count(*) FROM pg_catalog.pg_policy
            WHERE polrelid = lead_oid AND polroles = ARRAY[role_oid]::oid[] AND polpermissive) = 3
            AND (SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid = lead_oid) = 3
    );
    IF EXISTS (SELECT 1 FROM jsonb_each(checks) item WHERE item.value IS DISTINCT FROM 'true'::jsonb) THEN
        RAISE EXCEPTION 'STOP: New table metadata check failed: %', checks;
    END IF;
    PERFORM pg_catalog.set_config('app.lead_schema_result', jsonb_build_object(
        'state', 'APPLIED', 'table', 'public.construction_leads',
        'column_count', 17, 'update_columns', current_updates, 'checks', checks,
        'business_rows_modified', 0, 'existing_tables_modified', false,
        'measurement_tables_created', false, 'canonical_work_type_catalogs_created', false,
        'tenant_isolation_behavior_tested', false,
        'next_step', 'READ_ONLY_LEAD_SCHEMA_REVALIDATION_AND_MEMBERSHIP_QUERY_REVIEW'
    )::text, true);
END;
$migration$;

SELECT current_setting('app.lead_schema_result')::jsonb AS lead_storage_schema_result;
COMMIT;
