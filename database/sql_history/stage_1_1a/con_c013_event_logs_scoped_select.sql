-- CON-C013; approved restricted Construction history SELECT only.
-- Run the complete file as postgres in Supabase SQL Editor.
-- No business rows, existing INSERT policy, other ACL or application files changed.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog, public;

DO $history_read$
DECLARE
    relation_oid oid;
    client_oid oid;
    role_oid oid;
    owner_oid oid;
    reference_oid oid;
    required_columns text[] := ARRAY['created_at','entity_id','entity_type','event_type','id','payload','tenant_id','user_id']::text[];
    selectable_columns text[];
    expected_policy_name text := 'event_logs_construction_history_select';
    rule_sql text := $rule$
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        AND NULLIF(current_setting('app.current_invitation_id', true), '') IS NULL
        AND NULLIF(current_setting('app.current_invitation_recipient_id', true), '') IS NULL
        AND (
            (entity_type = 'construction_activity' AND event_type IN ('construction_activity_cancelled','construction_activity_completed','construction_activity_created'))
            OR (entity_type = 'construction_client' AND event_type IN ('construction_client_archived','construction_client_created','construction_client_updated'))
            OR (entity_type = 'construction_lead' AND event_type IN ('construction_lead_converted','construction_lead_created','construction_lead_status_changed'))
            OR (entity_type = 'construction_project' AND event_type IN ('construction_project_created','construction_project_status_changed','construction_project_updated'))
        )
    $rule$;
    expected_expression text;
    policy_row record;
    existing_policy boolean;
    already_applied boolean := false;
    before_guard jsonb;
    after_guard jsonb;
    before_other_acl jsonb;
    after_other_acl jsonb;
    before_other_policies jsonb;
    after_other_policies jsonb;
    pass integer;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: Run as postgres in SQL Editor; SET ROLE is not required.';
    END IF;
    relation_oid := pg_catalog.to_regclass('public.event_logs');
    client_oid := pg_catalog.to_regclass('public.construction_clients');
    SELECT oid INTO role_oid FROM pg_catalog.pg_roles WHERE rolname = 'construction_api';
    IF relation_oid IS NULL OR client_oid IS NULL OR role_oid IS NULL THEN
        RAISE EXCEPTION 'STOP: Expected event_logs, construction_clients and construction_api are required.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('con_c013_event_logs_scoped_select_v1', 0));
    LOCK TABLE public.event_logs IN ACCESS EXCLUSIVE MODE;
    SELECT relowner INTO owner_oid FROM pg_catalog.pg_class
    WHERE oid = relation_oid AND relkind = 'r' AND relrowsecurity;
    IF owner_oid IS NULL OR pg_catalog.pg_get_userbyid(owner_oid) <> 'postgres'
       OR pg_catalog.pg_has_role(role_oid, owner_oid, 'MEMBER')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE oid = role_oid AND (rolsuper OR rolbypassrls OR rolinherit))
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles parent WHERE parent.oid <> role_oid
           AND pg_catalog.pg_has_role(role_oid, parent.oid, 'MEMBER'))
       OR NOT pg_catalog.has_schema_privilege(role_oid, 'public', 'USAGE') THEN
        RAISE EXCEPTION 'STOP: Table/role security differs from the supplied preflight; preserve and inspect.';
    END IF;
    IF (SELECT array_agg(attname::text ORDER BY attname) FROM pg_catalog.pg_attribute
        WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped)
       IS DISTINCT FROM ARRAY['created_at','entity_id','entity_type','event_type','id','payload','platform','scope_country_id','scope_language_code','tenant_id','trace_id','user_id']::text[] THEN
        RAISE EXCEPTION 'STOP: event_logs columns differ from supplied preflight.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements('[{"name":"id","type":"uuid","nullable":false},{"name":"tenant_id","type":"uuid","nullable":true},{"name":"user_id","type":"uuid","nullable":true},{"name":"event_type","type":"text","nullable":false},{"name":"entity_type","type":"text","nullable":true},{"name":"entity_id","type":"uuid","nullable":true},{"name":"payload","type":"jsonb","nullable":true},{"name":"created_at","type":"timestamp with time zone","nullable":false}]'::jsonb) expected
        LEFT JOIN pg_catalog.pg_attribute attribute
          ON attribute.attrelid = relation_oid AND attribute.attname = expected->>'name'
             AND attribute.attnum > 0 AND NOT attribute.attisdropped
        WHERE attribute.attnum IS NULL
           OR pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) IS DISTINCT FROM expected->>'type'
           OR (NOT attribute.attnotnull) IS DISTINCT FROM (expected->>'nullable')::boolean
    ) THEN
        RAISE EXCEPTION 'STOP: Required event_logs column types/nullability differ from preflight.';
    END IF;
    IF pg_catalog.has_table_privilege(role_oid, relation_oid, 'SELECT')
       OR pg_catalog.has_table_privilege(role_oid, relation_oid, 'DELETE')
       OR pg_catalog.has_table_privilege(role_oid, relation_oid, 'TRUNCATE')
       OR pg_catalog.has_any_column_privilege(role_oid, relation_oid, 'UPDATE')
       OR pg_catalog.has_any_column_privilege(role_oid, relation_oid, 'REFERENCES')
       OR pg_catalog.has_table_privilege(role_oid, relation_oid, 'TRIGGER')
       OR NOT pg_catalog.has_table_privilege(role_oid, relation_oid, 'INSERT') THEN
        RAISE EXCEPTION 'STOP: Construction audit privileges differ from the expected insert-only baseline.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policy
        WHERE polrelid = relation_oid AND polname = 'event_logs_construction_insert'
          AND polcmd = 'a' AND polroles = ARRAY[role_oid]::oid[] AND polpermissive
          AND polqual IS NULL AND polwithcheck IS NOT NULL) THEN
        RAISE EXCEPTION 'STOP: Expected existing Construction INSERT policy is missing or changed.';
    END IF;
    SELECT COALESCE(array_agg(attname::text ORDER BY attname), '{}'::text[]) INTO selectable_columns
    FROM pg_catalog.pg_attribute WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
      AND pg_catalog.has_column_privilege(role_oid, relation_oid, attnum, 'SELECT');
    SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_policy
        WHERE polrelid = relation_oid AND polname = expected_policy_name) INTO existing_policy;
    IF (NOT existing_policy AND cardinality(selectable_columns) <> 0)
       OR (existing_policy AND selectable_columns IS DISTINCT FROM required_columns) THEN
        RAISE EXCEPTION 'STOP: Partial or unexpected history SELECT state; preserve it before any changes.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policy policy
        WHERE policy.polrelid = relation_oid AND policy.polname <> expected_policy_name AND policy.polcmd IN ('r','*')
          AND EXISTS (SELECT 1 FROM unnest(policy.polroles) grantee
              WHERE CASE WHEN grantee = 0 THEN true ELSE pg_catalog.pg_has_role(role_oid, grantee, 'USAGE') END)
    ) THEN
        RAISE EXCEPTION 'STOP: Another applicable SELECT/ALL policy requires review before this change.';
    END IF;
    -- Temporary definition only: PostgreSQL parses the reference expression on
    -- this server. The table/policy contain no rows and disappear on COMMIT.
    IF pg_catalog.to_regclass('pg_temp._con_c013_history_policy_reference') IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: Temporary reference name is already in use; preserve it.';
    END IF;
    CREATE TEMP TABLE _con_c013_history_policy_reference (LIKE public.event_logs) ON COMMIT DROP;
    reference_oid := pg_catalog.to_regclass('pg_temp._con_c013_history_policy_reference');
    EXECUTE format('CREATE POLICY reference_history_select ON pg_temp._con_c013_history_policy_reference
        AS PERMISSIVE FOR SELECT TO construction_api USING (%s)', rule_sql);
    SELECT pg_catalog.pg_get_expr(polqual, polrelid) INTO expected_expression
    FROM pg_catalog.pg_policy WHERE polrelid = reference_oid AND polname = 'reference_history_select';
    IF expected_expression IS NULL THEN RAISE EXCEPTION 'STOP: Reference SELECT expression was not parsed.'; END IF;

    FOR pass IN 1..2 LOOP
        SELECT jsonb_build_object(
            'role', (SELECT jsonb_build_object('oid', oid, 'name', rolname, 'super', rolsuper,
                'bypass', rolbypassrls, 'inherit', rolinherit) FROM pg_catalog.pg_roles WHERE oid = role_oid),
            'memberships', (SELECT jsonb_agg(parent.oid ORDER BY parent.oid) FROM pg_catalog.pg_roles parent
                WHERE parent.oid <> role_oid AND pg_catalog.pg_has_role(role_oid, parent.oid, 'MEMBER')),
            'tables', (SELECT jsonb_agg(jsonb_build_object(
                'oid', relation.oid, 'kind', relation.relkind, 'owner', relation.relowner,
                'rls', relation.relrowsecurity, 'forced', relation.relforcerowsecurity,
                'table_acl', relation.relacl::text, 'schema_acl', namespace.nspacl::text,
                'columns', (SELECT jsonb_agg(jsonb_build_object(
                    'number', attribute.attnum, 'name', attribute.attname,
                    'type', pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                    'not_null', attribute.attnotnull, 'default', pg_catalog.pg_get_expr(default_info.adbin, default_info.adrelid),
                    'collation', attribute.attcollation, 'identity', attribute.attidentity, 'generated', attribute.attgenerated
                ) ORDER BY attribute.attnum) FROM pg_catalog.pg_attribute attribute
                LEFT JOIN pg_catalog.pg_attrdef default_info
                  ON default_info.adrelid = attribute.attrelid AND default_info.adnum = attribute.attnum
                WHERE attribute.attrelid = relation.oid AND attribute.attnum > 0 AND NOT attribute.attisdropped),
                'constraints', (SELECT jsonb_agg(jsonb_build_object(
                    'name', constraint_info.conname, 'validated', constraint_info.convalidated,
                    'definition', pg_catalog.pg_get_constraintdef(constraint_info.oid, true)
                ) ORDER BY constraint_info.conname) FROM pg_catalog.pg_constraint constraint_info
                WHERE constraint_info.conrelid = relation.oid),
                'triggers', (SELECT jsonb_agg(jsonb_build_object(
                    'name', trigger_info.tgname, 'enabled', trigger_info.tgenabled,
                    'definition', pg_catalog.pg_get_triggerdef(trigger_info.oid, true),
                    'function', pg_catalog.pg_get_functiondef(trigger_info.tgfoid)
                ) ORDER BY trigger_info.tgname) FROM pg_catalog.pg_trigger trigger_info
                WHERE trigger_info.tgrelid = relation.oid AND NOT trigger_info.tgisinternal),
                'indexes', (SELECT jsonb_agg(jsonb_build_object(
                    'oid', index_info.indexrelid, 'valid', index_info.indisvalid, 'ready', index_info.indisready,
                    'definition', pg_catalog.pg_get_indexdef(index_info.indexrelid)
                ) ORDER BY index_info.indexrelid) FROM pg_catalog.pg_index index_info WHERE index_info.indrelid = relation.oid)
            ) ORDER BY relation.oid) FROM pg_catalog.pg_class relation
                JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE relation.oid IN (relation_oid, client_oid))
        ) INTO after_guard;
        SELECT COALESCE(jsonb_agg(to_jsonb(acl) ORDER BY acl.relation_id, acl.scope, acl.column_name,
            acl.grantor, acl.grantee, acl.privilege_type, acl.is_grantable), '[]'::jsonb)
        INTO after_other_acl FROM (
            SELECT relation.oid AS relation_id, 'table'::text AS scope, ''::text AS column_name, item.*
            FROM pg_catalog.pg_class relation
            CROSS JOIN LATERAL unnest(COALESCE(relation.relacl, pg_catalog.acldefault('r', relation.relowner))) entry(acl_item)
            CROSS JOIN LATERAL pg_catalog.aclexplode(ARRAY[entry.acl_item]) item
            WHERE relation.oid IN (relation_oid, client_oid)
            UNION ALL
            SELECT attribute.attrelid, 'column'::text, attribute.attname::text, item.*
            FROM pg_catalog.pg_attribute attribute
            CROSS JOIN LATERAL unnest(attribute.attacl) entry(acl_item)
            CROSS JOIN LATERAL pg_catalog.aclexplode(ARRAY[entry.acl_item]) item
            WHERE attribute.attrelid IN (relation_oid, client_oid) AND attribute.attnum > 0 AND NOT attribute.attisdropped
              AND NOT(attribute.attrelid = relation_oid AND item.grantee = role_oid
                  AND item.privilege_type = 'SELECT' AND attribute.attname::text = ANY(required_columns))
        ) acl;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'table', policy.polrelid, 'name', policy.polname, 'command', policy.polcmd, 'roles', policy.polroles,
            'permissive', policy.polpermissive, 'using', pg_catalog.pg_get_expr(policy.polqual, policy.polrelid),
            'with_check', pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid)
        ) ORDER BY policy.polrelid, policy.polname), '[]'::jsonb) INTO after_other_policies
        FROM pg_catalog.pg_policy policy WHERE policy.polrelid IN (relation_oid, client_oid)
          AND NOT(policy.polrelid = relation_oid AND policy.polname = expected_policy_name);
        IF pass = 1 THEN
            before_guard := after_guard;
            before_other_acl := after_other_acl;
            before_other_policies := after_other_policies;
            IF NOT existing_policy THEN
                GRANT SELECT (id, tenant_id, user_id, event_type, entity_type, entity_id, payload, created_at)
                    ON public.event_logs TO construction_api;
                EXECUTE format('CREATE POLICY %I ON public.event_logs AS PERMISSIVE
                    FOR SELECT TO construction_api USING (%s)', expected_policy_name, rule_sql);
            ELSE
                already_applied := true;
            END IF;
        ELSIF after_guard IS DISTINCT FROM before_guard
           OR after_other_acl IS DISTINCT FROM before_other_acl
           OR after_other_policies IS DISTINCT FROM before_other_policies THEN
            RAISE EXCEPTION 'STOP: Unrelated ACL, INSERT policy, schema, RLS, triggers or indexes changed; roll back.';
        END IF;
    END LOOP;
    SELECT policy.*, pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) AS expression INTO policy_row
    FROM pg_catalog.pg_policy policy WHERE policy.polrelid = relation_oid AND policy.polname = expected_policy_name;
    IF NOT FOUND THEN RAISE EXCEPTION 'STOP: Expected history SELECT policy is missing.'; END IF;
    IF policy_row.polcmd <> 'r' OR policy_row.polroles IS DISTINCT FROM ARRAY[role_oid]::oid[]
       OR NOT policy_row.polpermissive OR policy_row.polwithcheck IS NOT NULL
       OR policy_row.expression IS DISTINCT FROM expected_expression THEN
        RAISE EXCEPTION 'STOP: History SELECT policy differs from the exact server-parsed contract; roll back.';
    END IF;
    SELECT COALESCE(array_agg(attname::text ORDER BY attname), '{}'::text[]) INTO selectable_columns
    FROM pg_catalog.pg_attribute WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
      AND pg_catalog.has_column_privilege(role_oid, relation_oid, attnum, 'SELECT');
    IF selectable_columns IS DISTINCT FROM required_columns
       OR pg_catalog.has_table_privilege(role_oid, relation_oid, 'SELECT')
       OR pg_catalog.has_any_column_privilege(role_oid, relation_oid, 'UPDATE')
       OR pg_catalog.has_table_privilege(role_oid, relation_oid, 'DELETE')
       OR pg_catalog.has_table_privilege(role_oid, relation_oid, 'TRUNCATE')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute attribute
           CROSS JOIN LATERAL unnest(attribute.attacl) entry(acl_item)
           CROSS JOIN LATERAL pg_catalog.aclexplode(ARRAY[entry.acl_item]) item
           WHERE attribute.attrelid = relation_oid AND attribute.attname::text = ANY(required_columns)
             AND item.grantee = role_oid AND item.privilege_type = 'SELECT' AND item.is_grantable) THEN
        RAISE EXCEPTION 'STOP: SELECT must be exactly eight non-grantable column privileges, with no audit mutation privileges.';
    END IF;
    PERFORM pg_catalog.set_config('app.con_c013_event_logs_select_result', jsonb_build_object(
        'state', CASE WHEN already_applied THEN 'ALREADY_APPLIED' ELSE 'APPLIED' END,
        'table', 'public.event_logs', 'select_columns', selectable_columns, 'policy', expected_policy_name,
        'checks', jsonb_build_object(
            'eight_column_select_only', true, 'no_table_wide_select', true, 'no_select_grant_option', true,
            'current_tenant_only', true, 'ordinary_context_only', true, 'twelve_exact_event_entity_pairs', true,
            'construction_role_only', true, 'existing_insert_policy_unchanged', true,
            'no_update_delete_truncate', true, 'other_acl_and_policies_unchanged', true,
            'schema_rls_triggers_indexes_and_client_table_unchanged', true),
        'business_rows_modified', 0, 'application_files_modified', false,
        'native_rls_behavior_tested', false, 'stage_1a_acceptance_claimed', false,
        'next_step', 'READ_ONLY_EVENT_LOGS_SELECT_AND_HISTORY_QUERY_REVALIDATION'
    )::text, true);
END;
$history_read$;
SELECT current_setting('app.con_c013_event_logs_select_result')::jsonb AS con_c013_event_logs_select_result;
COMMIT;
