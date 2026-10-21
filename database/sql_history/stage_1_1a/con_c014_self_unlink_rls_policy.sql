-- CON-C014: add the approved read-only own-link lookup SELECT policy.
-- Run the complete file as postgres in Supabase SQL Editor.
-- No business rows are read or changed. No ACL, existing policy or role is changed.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog, public;

DO $own_link$
DECLARE
    relation_oid oid;
    runtime_oid oid;
    reference_oid oid;
    policy_name text := 'construction_clients_own_unlink_lookup_select';
    was_created boolean := false;
    policy_row record;
    expected_expression text;
    before_snapshot jsonb;
    after_snapshot jsonb;
    expected_baseline jsonb := $baseline${"table":{"kind":"r","name":"construction_clients","owner":"postgres","schema":"public","columns":[{"name":"id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":"gen_random_uuid()"},{"name":"tenant_id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"display_name","type":"text","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"client_type","type":"text","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"phone","type":"text","identity":"","nullable":true,"generated":"","column_acl":null,"server_default":null},{"name":"email","type":"text","identity":"","nullable":true,"generated":"","column_acl":null,"server_default":null},{"name":"notes","type":"text","identity":"","nullable":true,"generated":"","column_acl":null,"server_default":null},{"name":"created_at","type":"timestamp with time zone","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":"now()"},{"name":"updated_at","type":"timestamp with time zone","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":"now()"},{"name":"deleted_at","type":"timestamp with time zone","identity":"","nullable":true,"generated":"","column_acl":null,"server_default":null},{"name":"linked_user_id","type":"uuid","identity":"","nullable":true,"generated":"","column_acl":null,"server_default":null},{"name":"row_version","type":"bigint","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":"1"}],"indexes":[{"name":"construction_clients_pkey","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX construction_clients_pkey ON public.construction_clients USING btree (id)"},{"name":"uq_construction_clients_tenant_id_id","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX uq_construction_clients_tenant_id_id ON public.construction_clients USING btree (tenant_id, id)"}],"policies":[{"name":"construction_tenant_isolation","roles":["construction_api"],"command":"*","permissive":true,"with_check":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)","using_expression":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)","applies_to_construction_role":true}],"table_acl":"{postgres=arwdDxtm/postgres,service_role=arwdDxtm/postgres,construction_api=arw/postgres}","rls_forced":false,"constraints":[{"kind":"c","name":"ck_construction_clients_client_type","validated":true,"definition":"CHECK (client_type = ANY (ARRAY['person'::text, 'company'::text]))"},{"kind":"c","name":"ck_construction_clients_row_version","validated":true,"definition":"CHECK (row_version > 0)"},{"kind":"p","name":"construction_clients_pkey","validated":true,"definition":"PRIMARY KEY (id)"},{"kind":"f","name":"construction_clients_tenant_id_fkey","validated":true,"definition":"FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"},{"kind":"f","name":"fk_construction_clients_linked_user","validated":true,"definition":"FOREIGN KEY (linked_user_id) REFERENCES users(id)"},{"kind":"u","name":"uq_construction_clients_tenant_id_id","validated":true,"definition":"UNIQUE (tenant_id, id)"}],"rls_enabled":true,"custom_triggers":[{"name":"trg_construction_clients_set_updated_at","definition":"CREATE TRIGGER trg_construction_clients_set_updated_at BEFORE UPDATE ON construction_clients FOR EACH ROW EXECUTE FUNCTION set_updated_at()","enabled_mode":"O","function_name":"set_updated_at","function_owner":"postgres","function_schema":"public","security_definer":false,"function_settings":null}]},"role_access":[{"rolsuper":false,"role_name":"anon","can_delete":false,"rolinherit":true,"can_truncate":false,"rolbypassrls":false,"schema_usage":true,"table_insert":false,"table_select":false,"table_update":false,"select_columns":[],"update_columns":[],"owner_membership":false},{"rolsuper":false,"role_name":"authenticated","can_delete":false,"rolinherit":true,"can_truncate":false,"rolbypassrls":false,"schema_usage":true,"table_insert":false,"table_select":false,"table_update":false,"select_columns":[],"update_columns":[],"owner_membership":false},{"rolsuper":false,"role_name":"construction_api","can_delete":false,"rolinherit":false,"can_truncate":false,"rolbypassrls":false,"schema_usage":true,"table_insert":true,"table_select":true,"table_update":true,"select_columns":["client_type","created_at","deleted_at","display_name","email","id","linked_user_id","notes","phone","row_version","tenant_id","updated_at"],"update_columns":["client_type","created_at","deleted_at","display_name","email","id","linked_user_id","notes","phone","row_version","tenant_id","updated_at"],"owner_membership":false},{"rolsuper":false,"role_name":"postgres","can_delete":true,"rolinherit":true,"can_truncate":true,"rolbypassrls":true,"schema_usage":true,"table_insert":true,"table_select":true,"table_update":true,"select_columns":["client_type","created_at","deleted_at","display_name","email","id","linked_user_id","notes","phone","row_version","tenant_id","updated_at"],"update_columns":["client_type","created_at","deleted_at","display_name","email","id","linked_user_id","notes","phone","row_version","tenant_id","updated_at"],"owner_membership":true},{"rolsuper":false,"role_name":"service_role","can_delete":true,"rolinherit":true,"can_truncate":true,"rolbypassrls":true,"schema_usage":true,"table_insert":true,"table_select":true,"table_update":true,"select_columns":["client_type","created_at","deleted_at","display_name","email","id","linked_user_id","notes","phone","row_version","tenant_id","updated_at"],"update_columns":["client_type","created_at","deleted_at","display_name","email","id","linked_user_id","notes","phone","row_version","tenant_id","updated_at"],"owner_membership":false}],"construction_role_memberships":[],"custom_policy_functions":[],"existing_lookup_context_policies":[]}$baseline$::jsonb;
    snapshot_sql text := $catalog$
WITH relation AS (
    SELECT c.*, n.nspname AS schema_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'construction_clients'
), runtime_role AS (
    SELECT r.* FROM pg_catalog.pg_roles r WHERE r.rolname = 'construction_api'
), checked_roles AS (
    SELECT r.* FROM pg_catalog.pg_roles r
    WHERE r.rolname IN ('construction_api', 'postgres', 'anon', 'authenticated', 'service_role', current_user)
), policy_metadata AS (
    SELECT p.oid, p.polname AS name, p.polcmd::text AS command, p.polpermissive AS permissive,
        ARRAY(SELECT CASE WHEN policy_role.oid = 0 THEN 'PUBLIC'
            ELSE pg_catalog.pg_get_userbyid(policy_role.oid) END
            FROM unnest(p.polroles) AS policy_role(oid) ORDER BY 1) AS roles,
        COALESCE((SELECT EXISTS(
            SELECT 1 FROM unnest(p.polroles) AS policy_role(oid)
            WHERE CASE WHEN policy_role.oid = 0 THEN true
                ELSE pg_catalog.pg_has_role(r.oid, policy_role.oid, 'USAGE') END
        ) FROM runtime_role r), false) AS applies_to_construction_role,
        pg_catalog.pg_get_expr(p.polqual, p.polrelid) AS using_expression,
        pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) AS with_check
    FROM relation c JOIN pg_catalog.pg_policy p ON p.polrelid = c.oid
    WHERE p.polname <> 'construction_clients_own_unlink_lookup_select'
), table_metadata AS (
    SELECT jsonb_build_object(
        'schema', c.schema_name, 'name', c.relname, 'kind', c.relkind::text,
        'owner', pg_catalog.pg_get_userbyid(c.relowner),
        'rls_enabled', c.relrowsecurity, 'rls_forced', c.relforcerowsecurity,
        'table_acl', c.relacl::text,
        'columns', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', a.attname, 'type', pg_catalog.format_type(a.atttypid, a.atttypmod),
                'nullable', NOT a.attnotnull,
                'server_default', pg_catalog.pg_get_expr(d.adbin, d.adrelid),
                'identity', a.attidentity::text, 'generated', a.attgenerated::text,
                'column_acl', a.attacl::text
            ) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        ), '[]'::jsonb),
        'policies', COALESCE((SELECT jsonb_agg(to_jsonb(p) - 'oid' ORDER BY p.name)
            FROM policy_metadata p), '[]'::jsonb),
        'constraints', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', k.conname, 'kind', k.contype::text, 'validated', k.convalidated,
                'definition', pg_catalog.pg_get_constraintdef(k.oid, true)
            ) ORDER BY k.conname)
            FROM pg_catalog.pg_constraint k WHERE k.conrelid = c.oid
        ), '[]'::jsonb),
        'indexes', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', cls.relname, 'valid', idx.indisvalid, 'ready', idx.indisready,
                'definition', pg_catalog.pg_get_indexdef(idx.indexrelid)
            ) ORDER BY cls.relname)
            FROM pg_catalog.pg_index idx JOIN pg_catalog.pg_class cls ON cls.oid = idx.indexrelid
            WHERE idx.indrelid = c.oid
        ), '[]'::jsonb),
        'custom_triggers', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', t.tgname, 'enabled_mode', t.tgenabled::text,
                'definition', pg_catalog.pg_get_triggerdef(t.oid, true),
                'function_schema', ns.nspname, 'function_name', fn.proname,
                'function_owner', pg_catalog.pg_get_userbyid(fn.proowner),
                'security_definer', fn.prosecdef, 'function_settings', fn.proconfig
            ) ORDER BY t.tgname)
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_proc fn ON fn.oid = t.tgfoid
            JOIN pg_catalog.pg_namespace ns ON ns.oid = fn.pronamespace
            WHERE t.tgrelid = c.oid AND NOT t.tgisinternal
        ), '[]'::jsonb)
    ) AS metadata FROM relation c
), role_access AS (
    SELECT r.rolname AS role_name, r.rolsuper, r.rolbypassrls, r.rolinherit,
        pg_catalog.pg_has_role(r.oid, c.relowner, 'MEMBER') AS owner_membership,
        pg_catalog.has_schema_privilege(r.oid, c.relnamespace, 'USAGE') AS schema_usage,
        pg_catalog.has_table_privilege(r.oid, c.oid, 'SELECT') AS table_select,
        pg_catalog.has_table_privilege(r.oid, c.oid, 'INSERT') AS table_insert,
        pg_catalog.has_table_privilege(r.oid, c.oid, 'UPDATE') AS table_update,
        pg_catalog.has_table_privilege(r.oid, c.oid, 'DELETE') AS can_delete,
        pg_catalog.has_table_privilege(r.oid, c.oid, 'TRUNCATE') AS can_truncate,
        ARRAY(SELECT a.attname::text FROM pg_catalog.pg_attribute a
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
              AND pg_catalog.has_column_privilege(r.oid, c.oid, a.attnum, 'SELECT')
            ORDER BY a.attname) AS select_columns,
        ARRAY(SELECT a.attname::text FROM pg_catalog.pg_attribute a
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
              AND pg_catalog.has_column_privilege(r.oid, c.oid, a.attnum, 'UPDATE')
            ORDER BY a.attname) AS update_columns
    FROM relation c CROSS JOIN checked_roles r
), inherited_roles AS (
    SELECT parent.rolname AS role_name, parent.rolsuper, parent.rolbypassrls,
        pg_catalog.pg_has_role(r.oid, parent.oid, 'USAGE') AS inherited_without_set_role
    FROM runtime_role r CROSS JOIN pg_catalog.pg_roles parent
    WHERE parent.oid <> r.oid AND pg_catalog.pg_has_role(r.oid, parent.oid, 'MEMBER')
), custom_policy_functions AS (
    SELECT DISTINCT fn.oid, ns.nspname AS schema_name, fn.proname AS name,
        pg_catalog.pg_get_userbyid(fn.proowner) AS owner, fn.prosecdef AS security_definer,
        fn.proconfig AS settings,
        pg_catalog.pg_get_function_identity_arguments(fn.oid) AS arguments,
        CASE WHEN fn.prokind IN ('f', 'p') THEN pg_catalog.pg_get_functiondef(fn.oid) END AS definition
    FROM policy_metadata p
    JOIN pg_catalog.pg_depend d ON d.classid = 'pg_catalog.pg_policy'::regclass
        AND d.objid = p.oid AND d.refclassid = 'pg_catalog.pg_proc'::regclass
    JOIN pg_catalog.pg_proc fn ON fn.oid = d.refobjid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = fn.pronamespace
    WHERE ns.nspname NOT IN ('pg_catalog', 'information_schema')
), existing_lookup_context_policies AS (
    SELECT ns.nspname AS schema_name, c.relname AS table_name, p.polname AS policy_name,
        p.polcmd::text AS command, p.polpermissive AS permissive,
        pg_catalog.pg_get_expr(p.polqual, p.polrelid) AS using_expression,
        pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) AS with_check
    FROM pg_catalog.pg_policy p
    JOIN pg_catalog.pg_class c ON c.oid = p.polrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = c.relnamespace
    WHERE COALESCE(pg_catalog.pg_get_expr(p.polqual, p.polrelid), '') LIKE '%app.client_unlink_lookup_%'
       OR COALESCE(pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid), '') LIKE '%app.client_unlink_lookup_%'
)
SELECT jsonb_build_object(
    'table', (SELECT metadata FROM table_metadata),
    'role_access', COALESCE((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.role_name) FROM role_access a), '[]'::jsonb),
    'construction_role_memberships', COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.role_name) FROM inherited_roles r), '[]'::jsonb),
    'custom_policy_functions', COALESCE((SELECT jsonb_agg(to_jsonb(f) - 'oid' ORDER BY f.schema_name, f.name, f.arguments)
        FROM custom_policy_functions f), '[]'::jsonb),
    'existing_lookup_context_policies', COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.schema_name, p.table_name, p.policy_name)
        FROM existing_lookup_context_policies p
        WHERE p.schema_name NOT LIKE 'pg_temp_%'
          AND NOT (p.schema_name = 'public' AND p.table_name = 'construction_clients'
              AND p.policy_name = 'construction_clients_own_unlink_lookup_select')), '[]'::jsonb)
)

    $catalog$;
    rule_sql text := $rule$
        current_setting('transaction_read_only') = 'on'
        AND NULLIF(current_setting('app.current_tenant_id', true), '') IS NULL
        AND NULLIF(current_setting('app.current_invitation_id', true), '') IS NULL
        AND NULLIF(current_setting('app.current_invitation_recipient_id', true), '') IS NULL
        AND deleted_at IS NULL
        AND id::text = NULLIF(current_setting('app.client_unlink_lookup_client_id', true), '')
        AND linked_user_id::text = NULLIF(current_setting('app.client_unlink_lookup_user_id', true), '')
    $rule$;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: Run as postgres in SQL Editor; SET ROLE is not required.';
    END IF;
    relation_oid := pg_catalog.to_regclass('public.construction_clients');
    SELECT oid INTO runtime_oid FROM pg_catalog.pg_roles WHERE rolname = 'construction_api';
    IF relation_oid IS NULL OR runtime_oid IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = relation_oid AND c.relkind = 'r'
          AND c.relrowsecurity AND pg_catalog.pg_get_userbyid(c.relowner) = 'postgres'
    ) THEN
        RAISE EXCEPTION 'STOP: Expected Construction client table, RLS and dedicated role are required.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('con_c014_client_own_link_lookup_select_v1', 0));
    LOCK TABLE public.construction_clients IN SHARE ROW EXCLUSIVE MODE;

    -- The baseline excludes only this new policy. Any change to the supplied
    -- columns, ACL, ordinary policy, roles, constraints, indexes or triggers stops.
    EXECUTE snapshot_sql INTO before_snapshot;
    IF before_snapshot IS DISTINCT FROM expected_baseline THEN
        RAISE EXCEPTION 'STOP: Client metadata or role security differs from the supplied preflight; preserve and inspect.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid = runtime_oid
          AND (r.rolsuper OR r.rolbypassrls OR r.rolinherit))
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = relation_oid
          AND pg_catalog.pg_has_role(runtime_oid, c.relowner, 'MEMBER'))
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid <> runtime_oid
          AND pg_catalog.pg_has_role(runtime_oid, r.oid, 'MEMBER')) THEN
        RAISE EXCEPTION 'STOP: Construction role must enforce RLS without owner or inherited role authority.';
    END IF;

    -- Empty, transaction-local reference only: PostgreSQL canonicalizes the
    -- exact rule on this server. No source rows, defaults or triggers are copied.
    IF pg_catalog.to_regclass('pg_temp._con_c014_own_link_policy_reference') IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: Temporary reference name is already in use; preserve it.';
    END IF;
    CREATE TEMP TABLE _con_c014_own_link_policy_reference
        (LIKE public.construction_clients) ON COMMIT DROP;
    reference_oid := pg_catalog.to_regclass('pg_temp._con_c014_own_link_policy_reference');
    EXECUTE format('CREATE POLICY reference_own_link_select
        ON pg_temp._con_c014_own_link_policy_reference AS PERMISSIVE
        FOR SELECT TO construction_api USING (%s)', rule_sql);
    SELECT pg_catalog.pg_get_expr(polqual, polrelid) INTO expected_expression
    FROM pg_catalog.pg_policy
    WHERE polrelid = reference_oid AND polname = 'reference_own_link_select';
    IF expected_expression IS NULL THEN
        RAISE EXCEPTION 'STOP: The server did not parse the expected lookup rule.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policy
        WHERE polrelid = relation_oid AND polname = policy_name) THEN
        EXECUTE format('CREATE POLICY %I ON public.construction_clients AS PERMISSIVE
            FOR SELECT TO construction_api USING (%s)', policy_name, rule_sql);
        was_created := true;
    END IF;
    SELECT p.*, pg_catalog.pg_get_expr(p.polqual, p.polrelid) AS expression INTO policy_row
    FROM pg_catalog.pg_policy p WHERE p.polrelid = relation_oid AND p.polname = policy_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'STOP: Expected own-link policy is missing.';
    END IF;
    IF policy_row.polcmd <> 'r' OR NOT policy_row.polpermissive
       OR policy_row.polroles IS DISTINCT FROM ARRAY[runtime_oid]::oid[]
       OR policy_row.polwithcheck IS NOT NULL
       OR policy_row.expression IS DISTINCT FROM expected_expression THEN
        RAISE EXCEPTION 'STOP: Existing own-link policy differs from the exact approved rule; nothing is replaced.';
    END IF;

    EXECUTE snapshot_sql INTO after_snapshot;
    IF after_snapshot IS DISTINCT FROM before_snapshot OR after_snapshot IS DISTINCT FROM expected_baseline THEN
        RAISE EXCEPTION 'STOP: Unrelated table metadata, ACL, policies or role access changed; roll back.';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid = relation_oid) <> 2 THEN
        RAISE EXCEPTION 'STOP: Exactly the preserved tenant policy and new lookup policy are expected.';
    END IF;
    PERFORM pg_catalog.set_config('app.con_c014_own_link_policy_result', jsonb_build_object(
        'state', 'APPLIED', 'table', 'public.construction_clients',
        'policy', policy_name, 'repeat_run', NOT was_created,
        'policies_added', CASE WHEN was_created THEN 1 ELSE 0 END,
        'checks', jsonb_build_object(
            'select_only_construction_role', true,
            'read_only_lookup_transaction_required', true,
            'exact_client_and_linked_user_required', true,
            'blank_tenant_and_invitation_contexts_required', true,
            'archived_and_unlinked_rows_excluded', true,
            'server_parsed_expression_matches', true,
            'existing_tenant_policy_and_acl_unchanged', true,
            'columns_constraints_indexes_and_triggers_unchanged', true,
            'role_flags_memberships_and_access_unchanged', true
        ),
        'business_rows_read', false, 'business_rows_modified', 0,
        'privilege_grants_added', 0, 'application_files_modified', false,
        'lookup_projection_enforced_by', 'approved three-column repository path',
        'construction_runtime_connection_verified', false,
        'native_lookup_behavior_tested', false, 'stage_1a_acceptance_claimed', false,
        'next_step', 'READ_ONLY_OWN_LINK_POLICY_AND_NATIVE_QUERY_REVALIDATION'
    )::text, true);
END;
$own_link$;

SELECT current_setting('app.con_c014_own_link_policy_result')::jsonb
    AS con_c014_own_link_rls_policy_result;
COMMIT;
