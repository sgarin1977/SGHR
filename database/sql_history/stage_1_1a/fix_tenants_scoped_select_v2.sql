-- v2: Expand each real ACL item through a one-element array; NULL/empty column ACL yields no items.
-- Stage 1 CON-001 / CON-009. Run as postgres in Supabase SQL Editor.
-- Only six column SELECT privileges and one ordinary-context SELECT policy.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog, public;
DO $tenant_read$
DECLARE
    relation_oid oid;
    role_oid oid;
    owner_oid oid;
    required_columns text[] := ARRAY['default_currency','default_language','id','name','slug','status'];
    selectable_columns text[];
    expected_policy_name text := 'construction_current_organization_select';
    rule_sql text := $rule$
        id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        AND status = 'active'
        AND NULLIF(current_setting('app.current_invitation_id', true), '') IS NULL
        AND NULLIF(current_setting('app.current_invitation_recipient_id', true), '') IS NULL
    $rule$;
    rule_shape text;
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
        RAISE EXCEPTION 'STOP: Run this script as postgres; SET ROLE is not required.';
    END IF;
    relation_oid := pg_catalog.to_regclass('public.tenants');
    SELECT oid INTO role_oid FROM pg_catalog.pg_roles WHERE rolname='construction_api';
    IF relation_oid IS NULL OR role_oid IS NULL THEN
        RAISE EXCEPTION 'STOP: Expected tenants table and construction_api role are required.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'construction_current_organization_select_v1',0));
    LOCK TABLE public.tenants IN SHARE ROW EXCLUSIVE MODE;
    SELECT relowner INTO owner_oid FROM pg_catalog.pg_class
        WHERE oid=relation_oid AND relkind='r' AND relrowsecurity;
    IF owner_oid IS NULL OR pg_catalog.pg_get_userbyid(owner_oid)<>'postgres'
       OR pg_catalog.pg_has_role(role_oid,owner_oid,'MEMBER')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE oid=role_oid AND (rolsuper OR rolbypassrls))
       OR NOT pg_catalog.has_schema_privilege(role_oid,'public','USAGE') THEN
        RAISE EXCEPTION 'STOP: Expected postgres-owned RLS table and restricted Construction role required.';
    END IF;
    IF (SELECT array_agg(attname::text ORDER BY attname)
        FROM pg_catalog.pg_attribute WHERE attrelid=relation_oid AND attnum>0 AND NOT attisdropped)
       IS DISTINCT FROM ARRAY['created_at','default_currency','default_language','id','metadata',
                              'name','slug','status','updated_at']::text[] THEN
        RAISE EXCEPTION 'STOP: tenants column structure differs from supplied diagnostic.';
    END IF;
    IF pg_catalog.has_table_privilege(role_oid,relation_oid,'SELECT')
       OR pg_catalog.has_table_privilege(role_oid,relation_oid,'DELETE')
       OR pg_catalog.has_table_privilege(role_oid,relation_oid,'TRUNCATE')
       OR pg_catalog.has_any_column_privilege(role_oid,relation_oid,'INSERT')
       OR pg_catalog.has_any_column_privilege(role_oid,relation_oid,'UPDATE')
       OR pg_catalog.has_any_column_privilege(role_oid,relation_oid,'REFERENCES')
       OR pg_catalog.has_table_privilege(role_oid,relation_oid,'TRIGGER') THEN
        RAISE EXCEPTION 'STOP: Existing Construction privileges exceed the supplied read-only contract; preserve and inspect.';
    END IF;
    SELECT COALESCE(array_agg(attname::text ORDER BY attname),'{}'::text[]) INTO selectable_columns
    FROM pg_catalog.pg_attribute
    WHERE attrelid=relation_oid AND attnum>0 AND NOT attisdropped
      AND pg_catalog.has_column_privilege(role_oid,relation_oid,attnum,'SELECT');
    SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_policy
        WHERE polrelid=relation_oid AND polname=expected_policy_name) INTO existing_policy;
    IF (NOT existing_policy AND cardinality(selectable_columns)<>0)
       OR (existing_policy AND selectable_columns IS DISTINCT FROM required_columns) THEN
        RAISE EXCEPTION 'STOP: Partial or unexpected tenants SELECT state; preserve it before any changes.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=relation_oid
        AND p.polname<>expected_policy_name AND p.polcmd IN ('r','*')
        AND EXISTS (SELECT 1 FROM unnest(p.polroles) grantee
            WHERE CASE WHEN grantee=0 THEN true ELSE pg_catalog.pg_has_role(role_oid,grantee,'USAGE') END)
    ) THEN
        RAISE EXCEPTION 'STOP: Another policy can grant Construction tenant visibility; preserve and review it.';
    END IF;
    rule_shape := regexp_replace(replace(rule_sql,'::text',''),'[[:space:]()]','','g');
    -- Take catalog snapshots before mutation, then repeat the same checks after it.
    FOR pass IN 1..2 LOOP
        SELECT jsonb_build_object(
            'owner',c.relowner,'rls_enabled',c.relrowsecurity,'rls_forced',c.relforcerowsecurity,
            'schema_acl',n.nspacl::text,
            'columns',(SELECT jsonb_agg(jsonb_build_object(
                'number',a.attnum,'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
                'not_null',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid)
            ) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
            'constraints',(SELECT jsonb_agg(jsonb_build_object(
                'name',k.conname,'definition',pg_get_constraintdef(k.oid,true),'validated',k.convalidated
            ) ORDER BY k.conname) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid),
            'triggers',(SELECT jsonb_agg(jsonb_build_object(
                'name',t.tgname,'enabled',t.tgenabled,'definition',pg_get_triggerdef(t.oid,true),
                'function',pg_get_functiondef(t.tgfoid)
            ) ORDER BY t.tgname) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
            'indexes',(SELECT jsonb_agg(jsonb_build_object('oid',i.indexrelid,
                'valid',i.indisvalid,'definition',pg_get_indexdef(i.indexrelid)) ORDER BY i.indexrelid)
                FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid)
        ) INTO after_guard FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=relation_oid;
        SELECT COALESCE(jsonb_agg(to_jsonb(acl) ORDER BY acl.scope,acl.column_name,
            acl.grantor,acl.grantee,acl.privilege_type,acl.is_grantable),'[]'::jsonb)
        INTO after_other_acl FROM (
            SELECT 'table'::text AS scope,''::text AS column_name,x.*
            FROM pg_catalog.pg_class c
            CROSS JOIN LATERAL unnest(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) entry(acl_item)
            CROSS JOIN LATERAL pg_catalog.aclexplode(ARRAY[entry.acl_item]) x
            WHERE c.oid=relation_oid
            UNION ALL
            SELECT 'column'::text,a.attname::text,x.*
            FROM pg_catalog.pg_attribute a
            CROSS JOIN LATERAL unnest(a.attacl) entry(acl_item)
            CROSS JOIN LATERAL pg_catalog.aclexplode(ARRAY[entry.acl_item]) x
            WHERE a.attrelid=relation_oid AND a.attnum>0 AND NOT a.attisdropped
              AND NOT(x.grantee=role_oid AND x.privilege_type='SELECT' AND a.attname::text=ANY(required_columns))
        ) acl;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'name',p.polname,'command',p.polcmd,'roles',p.polroles,'permissive',p.polpermissive,
            'using',pg_get_expr(p.polqual,p.polrelid),'with_check',pg_get_expr(p.polwithcheck,p.polrelid)
        ) ORDER BY p.polname),'[]'::jsonb) INTO after_other_policies
        FROM pg_catalog.pg_policy p WHERE p.polrelid=relation_oid AND p.polname<>expected_policy_name;
        IF pass=1 THEN
            before_guard := after_guard;
            before_other_acl := after_other_acl;
            before_other_policies := after_other_policies;
            IF NOT existing_policy THEN
                GRANT SELECT (id,name,slug,default_language,default_currency,status)
                    ON public.tenants TO construction_api;
                EXECUTE format('CREATE POLICY %I ON public.tenants AS PERMISSIVE
                    FOR SELECT TO construction_api USING (%s)',expected_policy_name,rule_sql);
            ELSE
                already_applied := true;
            END IF;
        ELSE
            IF after_guard IS DISTINCT FROM before_guard
               OR after_other_acl IS DISTINCT FROM before_other_acl
               OR after_other_policies IS DISTINCT FROM before_other_policies THEN
                RAISE EXCEPTION 'STOP: Unrelated schema, ACL, triggers or policies changed; roll back.';
            END IF;
        END IF;
    END LOOP;
    SELECT p.*,pg_get_expr(p.polqual,p.polrelid) AS using_rule INTO policy_row
    FROM pg_catalog.pg_policy p WHERE p.polrelid=relation_oid AND p.polname=expected_policy_name;
    IF NOT FOUND THEN RAISE EXCEPTION 'STOP: Expected SELECT policy is missing.'; END IF;
    IF policy_row.polcmd<>'r' OR policy_row.polroles IS DISTINCT FROM ARRAY[role_oid]::oid[]
       OR NOT policy_row.polpermissive OR policy_row.polwithcheck IS NOT NULL
       OR regexp_replace(replace(policy_row.using_rule,'::text',''),'[[:space:]()]','','g')
          IS DISTINCT FROM rule_shape THEN
        RAISE EXCEPTION 'STOP: SELECT policy differs from the exact scoped contract; roll back.';
    END IF;
    SELECT COALESCE(array_agg(attname::text ORDER BY attname),'{}'::text[]) INTO selectable_columns
    FROM pg_catalog.pg_attribute WHERE attrelid=relation_oid AND attnum>0 AND NOT attisdropped
      AND pg_catalog.has_column_privilege(role_oid,relation_oid,attnum,'SELECT');
    IF selectable_columns IS DISTINCT FROM required_columns
       OR pg_catalog.has_table_privilege(role_oid,relation_oid,'SELECT') THEN
        RAISE EXCEPTION 'STOP: SELECT must be limited to the six required columns.';
    END IF;
    PERFORM pg_catalog.set_config('app.tenants_scoped_select_fix_result',jsonb_build_object(
        'state',CASE WHEN already_applied THEN 'ALREADY_APPLIED' ELSE 'APPLIED' END,
        'table','public.tenants','select_columns',selectable_columns,
        'policy',expected_policy_name,
        'checks',jsonb_build_object('six_column_select_only',true,'no_table_wide_select',true,
            'current_active_tenant_only',true,'ordinary_context_only',true,
            'other_acl_and_policies_unchanged',true,'schema_rls_and_triggers_unchanged',true),
        'business_rows_modified',0,'application_files_modified',false,
        'tenant_isolation_behavior_tested',false,
        'next_step','REPEAT_READ_ONLY_PROJECT_CLIENT_STORAGE_CHECK'
    )::text,true);
END;
$tenant_read$;
SELECT current_setting('app.tenants_scoped_select_fix_result')::jsonb AS tenants_scoped_select_fix_result;
COMMIT;
