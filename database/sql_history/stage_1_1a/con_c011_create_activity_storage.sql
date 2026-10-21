-- CON-C011 Stage 1A. Approved construction_activities storage only.
-- Run in Supabase SQL Editor as postgres. All DDL rolls back on any failure.
-- No business rows or existing application tables/ACL/policies are updated.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

DO $migration$
DECLARE
    role_oid oid;
    owner_oid oid;
    activity_oid oid;
    parent_oid oid;
    parent_keys text[];
    key_numbers smallint[];
    relation_name text;
    column_name text;
    acl_role text;
    actual_columns jsonb;
    expected_columns jsonb := '[{"name":"id","type":"uuid","nullable":false},{"name":"tenant_id","type":"uuid","nullable":false},{"name":"type","type":"text","nullable":false},{"name":"status","type":"text","nullable":false},{"name":"lead_id","type":"uuid","nullable":true},{"name":"client_id","type":"uuid","nullable":true},{"name":"project_id","type":"uuid","nullable":true},{"name":"due_at","type":"timestamp with time zone","nullable":false},{"name":"timezone","type":"text","nullable":false},{"name":"responsible_user_id","type":"uuid","nullable":true},{"name":"comment","type":"text","nullable":true},{"name":"result","type":"text","nullable":true},{"name":"completed_at","type":"timestamp with time zone","nullable":true},{"name":"cancelled_at","type":"timestamp with time zone","nullable":true},{"name":"created_by_user_id","type":"uuid","nullable":false},{"name":"created_at","type":"timestamp with time zone","nullable":false},{"name":"updated_at","type":"timestamp with time zone","nullable":false},{"name":"deleted_at","type":"timestamp with time zone","nullable":true},{"name":"row_version","type":"bigint","nullable":false}]'::jsonb;
    actual_updates text[];
    existing_before jsonb;
    existing_after jsonb;
    checks jsonb;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: Run this schema script as postgres in SQL Editor.';
    END IF;
    SELECT r.oid INTO owner_oid FROM pg_catalog.pg_roles r WHERE r.rolname = current_user;
    SELECT r.oid INTO role_oid FROM pg_catalog.pg_roles r
      WHERE r.rolname = 'construction_api' AND NOT r.rolsuper AND NOT r.rolbypassrls;
    IF role_oid IS NULL OR pg_catalog.pg_has_role(role_oid, owner_oid, 'MEMBER')
       OR NOT pg_catalog.has_schema_privilege(role_oid, 'public', 'USAGE') THEN
        RAISE EXCEPTION 'STOP: construction_api role security differs from preflight.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('construction_schema'), pg_catalog.hashtext('activity_storage_2026_10_13')
    );
    IF pg_catalog.to_regclass('public.construction_activities') IS NOT NULL THEN
        -- Never repair, alter or overwrite an existing relation on repeat.
        PERFORM pg_catalog.set_config('app.activity_schema_result', jsonb_build_object(
            'state', 'EXISTS_NO_CHANGES', 'table', 'public.construction_activities',
            'metadata_revalidation_required', true, 'business_rows_modified', 0,
            'existing_tables_modified', false
        )::text, true);
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'construction_activities' AND n.nspname <> 'public'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
    ) THEN
        RAISE EXCEPTION 'STOP: construction_activities exists in another schema; inspect it first.';
    END IF;
    FOREACH relation_name IN ARRAY ARRAY[
        'construction_activities_pkey', 'uq_construction_activities_tenant_id_id',
        'ix_construction_activities_next_action_lead'
    ] LOOP
        IF pg_catalog.to_regclass('public.' || relation_name) IS NOT NULL THEN
            RAISE EXCEPTION 'STOP: Expected index name is occupied: %', relation_name;
        END IF;
    END LOOP;

    FOREACH relation_name IN ARRAY ARRAY['tenants', 'users', 'construction_leads', 'construction_clients', 'construction_projects'] LOOP
        parent_oid := pg_catalog.to_regclass('public.' || relation_name);
        IF parent_oid IS NULL OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = parent_oid AND c.relkind = 'r'
        ) OR NOT pg_catalog.has_table_privilege(current_user, parent_oid, 'REFERENCES') THEN
            RAISE EXCEPTION 'STOP: Parent table or REFERENCES privilege differs: public.%', relation_name;
        END IF;
        parent_keys := CASE WHEN relation_name IN ('construction_leads', 'construction_clients', 'construction_projects')
            THEN ARRAY['tenant_id', 'id'] ELSE ARRAY['id'] END;
        key_numbers := ARRAY[]::smallint[];
        FOREACH column_name IN ARRAY parent_keys LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid = parent_oid
                  AND a.attname = column_name AND a.attnum > 0 AND NOT a.attisdropped
                  AND a.atttypid = 'uuid'::regtype AND a.attnotnull
            ) THEN
                RAISE EXCEPTION 'STOP: Parent UUID key differs: public.%.%', relation_name, column_name;
            END IF;
            key_numbers := array_append(key_numbers, (
                SELECT a.attnum FROM pg_catalog.pg_attribute a
                WHERE a.attrelid = parent_oid AND a.attname = column_name AND NOT a.attisdropped
            ));
        END LOOP;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_index i ON i.indexrelid = k.conindid
            WHERE k.conrelid = parent_oid AND k.contype IN ('p', 'u')
              AND k.conkey = key_numbers AND k.convalidated AND NOT k.condeferrable
              AND i.indisvalid AND i.indisready
        ) THEN
            RAISE EXCEPTION 'STOP: Valid parent unique key missing: public.%', relation_name;
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(ARRAY[
            'tenants', 'users', 'user_roles', 'construction_leads', 'construction_clients',
            'construction_projects', 'construction_access_grants', 'api_idempotency_records', 'event_logs'
        ]) AND (c.relkind <> 'r' OR NOT c.relrowsecurity OR c.relowner <> owner_oid)
    ) OR (SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' AND c.relname = ANY(ARRAY[
              'tenants', 'users', 'user_roles', 'construction_leads', 'construction_clients',
              'construction_projects', 'construction_access_grants', 'api_idempotency_records', 'event_logs'
          ])) <> 9 THEN
        RAISE EXCEPTION 'STOP: Dependency ownership/RLS/table set differs from preflight.';
    END IF;
    SELECT jsonb_agg(jsonb_build_object(
        'name', c.relname, 'owner', c.relowner, 'acl', c.relacl::text,
        'rls', c.relrowsecurity, 'forced', c.relforcerowsecurity,
        'columns', (SELECT jsonb_agg(jsonb_build_object('name', a.attname, 'type', a.atttypid,
            'typmod', a.atttypmod, 'nullable', NOT a.attnotnull, 'acl', a.attacl::text,
            'default', pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
              ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
        'policies', (SELECT jsonb_agg(to_jsonb(p) ORDER BY p.polname) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid),
        'custom_triggers', (SELECT jsonb_agg(pg_get_triggerdef(g.oid,true) ORDER BY g.tgname)
            FROM pg_catalog.pg_trigger g WHERE g.tgrelid=c.oid AND NOT g.tgisinternal)
    ) ORDER BY c.relname) INTO existing_before
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[
        'tenants','users','user_roles','construction_leads','construction_clients',
        'construction_projects','construction_access_grants','api_idempotency_records','event_logs'
    ]);

    CREATE TABLE public.construction_activities (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	type TEXT NOT NULL, 
	status TEXT DEFAULT 'planned' NOT NULL, 
	lead_id UUID, 
	client_id UUID, 
	project_id UUID, 
	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	timezone TEXT NOT NULL, 
	responsible_user_id UUID, 
	comment TEXT, 
	result TEXT, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	cancelled_at TIMESTAMP WITH TIME ZONE, 
	created_by_user_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	row_version BIGINT DEFAULT 1 NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_construction_activities_type CHECK (type IN ('call', 'message', 'meeting', 'site_visit', 'follow_up', 'task', 'other')), 
	CONSTRAINT ck_construction_activities_status CHECK (status IN ('planned', 'completed', 'cancelled')), 
	CONSTRAINT ck_construction_activities_links CHECK (lead_id IS NOT NULL OR client_id IS NOT NULL OR project_id IS NOT NULL), 
	CONSTRAINT ck_construction_activities_timezone CHECK (length(btrim(timezone)) > 0), 
	CONSTRAINT ck_construction_activities_row_version CHECK (row_version > 0), 
	CONSTRAINT ck_construction_activities_completion CHECK ((status = 'planned' AND completed_at IS NULL AND cancelled_at IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR (status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL)), 
	CONSTRAINT uq_construction_activities_tenant_id_id UNIQUE (tenant_id, id), 
	CONSTRAINT fk_construction_activities_tenant_lead FOREIGN KEY(tenant_id, lead_id) REFERENCES public.construction_leads (tenant_id, id), 
	CONSTRAINT fk_construction_activities_tenant_client FOREIGN KEY(tenant_id, client_id) REFERENCES public.construction_clients (tenant_id, id), 
	CONSTRAINT fk_construction_activities_tenant_project FOREIGN KEY(tenant_id, project_id) REFERENCES public.construction_projects (tenant_id, id), 
	FOREIGN KEY(tenant_id) REFERENCES public.tenants (id) ON DELETE CASCADE, 
	FOREIGN KEY(responsible_user_id) REFERENCES public.users (id) ON DELETE SET NULL, 
	FOREIGN KEY(created_by_user_id) REFERENCES public.users (id) ON DELETE RESTRICT
);
    CREATE INDEX ix_construction_activities_next_action_lead ON public.construction_activities (tenant_id, lead_id, due_at, id) WHERE status = 'planned' AND deleted_at IS NULL;
    ALTER TABLE public.construction_activities ENABLE ROW LEVEL SECURITY;

    -- Supabase/default ACL grants may be inherited when creating a new table.
    -- Revoke them only on this newly created table, then grant its service contract.
    REVOKE ALL PRIVILEGES ON TABLE public.construction_activities FROM PUBLIC;
    FOR acl_role IN
        SELECT DISTINCT r.rolname
        FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
        JOIN pg_catalog.pg_roles r ON r.oid = a.grantee
        WHERE c.oid = 'public.construction_activities'::regclass AND a.grantee <> owner_oid
    LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.construction_activities FROM %I', acl_role);
    END LOOP;
    GRANT SELECT, INSERT ON TABLE public.construction_activities TO construction_api;
    GRANT UPDATE (status, result, completed_at, cancelled_at, updated_at, row_version)
      ON TABLE public.construction_activities TO construction_api;
    CREATE POLICY construction_activities_select ON public.construction_activities
      FOR SELECT TO construction_api
      USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    CREATE POLICY construction_activities_insert ON public.construction_activities
      FOR INSERT TO construction_api
      WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    CREATE POLICY construction_activities_update ON public.construction_activities
      FOR UPDATE TO construction_api
      USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
      WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);

    activity_oid := 'public.construction_activities'::regclass;
    SELECT jsonb_agg(jsonb_build_object(
        'name', a.attname, 'type', format_type(a.atttypid, a.atttypmod), 'nullable', NOT a.attnotnull
    ) ORDER BY a.attnum) INTO actual_columns
    FROM pg_catalog.pg_attribute a WHERE a.attrelid = activity_oid AND a.attnum > 0 AND NOT a.attisdropped;
    SELECT array_agg(a.attname::text ORDER BY a.attname) INTO actual_updates
    FROM pg_catalog.pg_attribute a WHERE a.attrelid = activity_oid AND a.attnum > 0 AND NOT a.attisdropped
      AND pg_catalog.has_column_privilege(role_oid, activity_oid, a.attnum, 'UPDATE');
    SELECT jsonb_agg(jsonb_build_object(
        'name', c.relname, 'owner', c.relowner, 'acl', c.relacl::text,
        'rls', c.relrowsecurity, 'forced', c.relforcerowsecurity,
        'columns', (SELECT jsonb_agg(jsonb_build_object('name', a.attname, 'type', a.atttypid,
            'typmod', a.atttypmod, 'nullable', NOT a.attnotnull, 'acl', a.attacl::text,
            'default', pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
              ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
        'policies', (SELECT jsonb_agg(to_jsonb(p) ORDER BY p.polname) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid),
        'custom_triggers', (SELECT jsonb_agg(pg_get_triggerdef(g.oid,true) ORDER BY g.tgname)
            FROM pg_catalog.pg_trigger g WHERE g.tgrelid=c.oid AND NOT g.tgisinternal)
    ) ORDER BY c.relname) INTO existing_after
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname=ANY(ARRAY[
        'tenants','users','user_roles','construction_leads','construction_clients',
        'construction_projects','construction_access_grants','api_idempotency_records','event_logs'
    ]);
    checks := jsonb_build_object(
        'exact_orm_columns_types_nullability', actual_columns = expected_columns,
        'table_owned_by_postgres', (SELECT c.relowner = owner_oid FROM pg_catalog.pg_class c WHERE c.oid = activity_oid),
        'rls_enabled', (SELECT c.relrowsecurity FROM pg_catalog.pg_class c WHERE c.oid = activity_oid),
        'role_enforces_rls', NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid = role_oid AND (r.rolsuper OR r.rolbypassrls)),
        'no_owner_membership', NOT pg_catalog.pg_has_role(role_oid, owner_oid, 'MEMBER'),
        'select_insert_allowed', pg_catalog.has_table_privilege(role_oid, activity_oid, 'SELECT')
            AND pg_catalog.has_table_privilege(role_oid, activity_oid, 'INSERT'),
        'no_table_wide_update', NOT pg_catalog.has_table_privilege(role_oid, activity_oid, 'UPDATE'),
        'update_only_terminal_fields', actual_updates = ARRAY['cancelled_at','completed_at','result','row_version','status','updated_at']::text[],
        'no_delete_or_truncate', NOT pg_catalog.has_table_privilege(role_oid, activity_oid, 'DELETE')
            AND NOT pg_catalog.has_table_privilege(role_oid, activity_oid, 'TRUNCATE'),
        'no_other_non_owner_acl', NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
            WHERE c.oid = activity_oid AND a.grantee NOT IN (role_oid, owner_oid)
        ),
        'three_role_scoped_policies', (SELECT count(*) = 3 FROM pg_catalog.pg_policy p WHERE p.polrelid = activity_oid)
            AND (SELECT count(*) = 3 FROM pg_catalog.pg_policy p WHERE p.polrelid = activity_oid
                 AND p.polroles = ARRAY[role_oid]::oid[] AND p.polpermissive AND p.polcmd IN ('r','a','w')),
        'all_constraints_validated', NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k WHERE k.conrelid = activity_oid AND NOT k.convalidated),
        'exact_constraint_count', (SELECT count(*) = 14 FROM pg_catalog.pg_constraint k WHERE k.conrelid = activity_oid),
        'named_orm_constraints_present', (SELECT count(*) = 10 FROM pg_catalog.pg_constraint k
            WHERE k.conrelid = activity_oid AND k.conname = ANY(ARRAY['ck_construction_activities_completion','ck_construction_activities_links','ck_construction_activities_row_version','ck_construction_activities_status','ck_construction_activities_timezone','ck_construction_activities_type','fk_construction_activities_tenant_client','fk_construction_activities_tenant_lead','fk_construction_activities_tenant_project','uq_construction_activities_tenant_id_id']::text[])),
        'three_valid_ready_indexes', (SELECT count(*) = 3 FROM pg_catalog.pg_index i WHERE i.indrelid = activity_oid)
            AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid = activity_oid AND (NOT i.indisvalid OR NOT i.indisready)),
        'no_custom_triggers', NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger g WHERE g.tgrelid = activity_oid AND NOT g.tgisinternal),
        'existing_columns_acl_rls_policies_custom_triggers_unchanged', existing_before = existing_after
    );
    IF EXISTS (SELECT 1 FROM jsonb_each(checks) AS item(key,value) WHERE value IS DISTINCT FROM 'true'::jsonb) THEN
        RAISE EXCEPTION 'STOP: New activities table validation failed: %', checks;
    END IF;
    PERFORM pg_catalog.set_config('app.activity_schema_result', jsonb_build_object(
        'state', 'APPLIED', 'table', 'public.construction_activities', 'column_count', 19,
        'checks', checks, 'update_columns', actual_updates, 'business_rows_modified', 0,
        'existing_tables_modified', false, 'measurement_tables_created', false,
        'application_files_modified', false, 'tenant_isolation_behavior_tested', false,
        'next_step', 'READ_ONLY_ACTIVITY_SCHEMA_AND_QUERY_REVALIDATION'
    )::text, true);
END;
$migration$;

SELECT current_setting('app.activity_schema_result')::jsonb AS activity_storage_schema_result;
COMMIT;
