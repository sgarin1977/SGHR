-- Fix the existing Platform membership read query for construction_api.
-- No membership records or Construction grants are created or changed.
-- Add only ordinary-context SELECT policies; preserve invitation context and ACL.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;
SET LOCAL standard_conforming_strings = on;

DO $membership_fix$
DECLARE
    users_oid oid;
    roles_oid oid;
    api_oid oid;
    owner_oid oid;
    expected_policies jsonb := '[{"table":"user_roles","name":"construction_invitation_membership_insert","command":"a","roles":["construction_api"],"permissive":true,"using":null,"with_check":"((user_id = (NULLIF(current_setting(''app.current_invitation_recipient_id''::text, true), ''''::text))::uuid) AND (tenant_id = (NULLIF(current_setting(''app.current_tenant_id''::text, true), ''''::text))::uuid) AND ((NULLIF(current_setting(''app.current_invitation_id''::text, true), ''''::text))::uuid IS NOT NULL) AND (role = ''client''::text) AND (status = ''active''::text) AND (granted_by IS NULL) AND (granted_at = transaction_timestamp()) AND (EXISTS ( SELECT 1\n   FROM users u\n  WHERE ((u.id = user_roles.user_id) AND (u.status = ''active''::text)))) AND (EXISTS ( SELECT 1\n   FROM construction_invitations i\n  WHERE ((i.id = (NULLIF(current_setting(''app.current_invitation_id''::text, true), ''''::text))::uuid) AND (i.tenant_id = user_roles.tenant_id) AND (i.status = ''pending''::text) AND (i.token_used_at IS NULL) AND (i.expires_at > statement_timestamp())))))"},{"table":"user_roles","name":"construction_invitation_membership_lock","command":"w","roles":["construction_api"],"permissive":true,"using":"((user_id = (NULLIF(current_setting(''app.current_invitation_recipient_id''::text, true), ''''::text))::uuid) AND (tenant_id = (NULLIF(current_setting(''app.current_tenant_id''::text, true), ''''::text))::uuid) AND ((NULLIF(current_setting(''app.current_invitation_id''::text, true), ''''::text))::uuid IS NOT NULL))","with_check":"false"},{"table":"user_roles","name":"construction_invitation_membership_select","command":"r","roles":["construction_api"],"permissive":true,"using":"((user_id = (NULLIF(current_setting(''app.current_invitation_recipient_id''::text, true), ''''::text))::uuid) AND (tenant_id = (NULLIF(current_setting(''app.current_tenant_id''::text, true), ''''::text))::uuid) AND ((NULLIF(current_setting(''app.current_invitation_id''::text, true), ''''::text))::uuid IS NOT NULL))","with_check":null},{"table":"users","name":"construction_invitation_users_lock","command":"w","roles":["construction_api"],"permissive":true,"using":"((id = (NULLIF(current_setting(''app.current_invitation_recipient_id''::text, true), ''''::text))::uuid) AND (status = ''active''::text) AND ((NULLIF(current_setting(''app.current_tenant_id''::text, true), ''''::text))::uuid IS NOT NULL) AND ((NULLIF(current_setting(''app.current_invitation_id''::text, true), ''''::text))::uuid IS NOT NULL))","with_check":"false"},{"table":"users","name":"construction_invitation_users_select","command":"r","roles":["construction_api"],"permissive":true,"using":"((id = (NULLIF(current_setting(''app.current_invitation_recipient_id''::text, true), ''''::text))::uuid) AND (status = ''active''::text) AND ((NULLIF(current_setting(''app.current_tenant_id''::text, true), ''''::text))::uuid IS NOT NULL) AND ((NULLIF(current_setting(''app.current_invitation_id''::text, true), ''''::text))::uuid IS NOT NULL))","with_check":null}]'::jsonb;
    policies_before jsonb;
    policies_after jsonb;
    acl_before jsonb;
    acl_after jsonb;
    existing_new_count integer;
    checks jsonb;
    required_table text;
    required_column text;
    required_oid oid;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: Run as postgres in Supabase SQL Editor.';
    END IF;
    SELECT oid INTO owner_oid FROM pg_catalog.pg_roles WHERE rolname=current_user;
    SELECT oid INTO api_oid FROM pg_catalog.pg_roles
        WHERE rolname='construction_api' AND NOT rolsuper AND NOT rolbypassrls;
    IF api_oid IS NULL OR pg_catalog.pg_has_role(api_oid,owner_oid,'MEMBER') THEN
        RAISE EXCEPTION 'STOP: Construction role security differs from diagnostic.';
    END IF;
    users_oid := pg_catalog.to_regclass('public.users');
    roles_oid := pg_catalog.to_regclass('public.user_roles');
    IF users_oid IS NULL OR roles_oid IS NULL THEN
        RAISE EXCEPTION 'STOP: Required Platform tables missing.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('construction_schema'),
        pg_catalog.hashtext('ordinary_platform_membership_select')
    );
    LOCK TABLE public.users, public.user_roles IN SHARE ROW EXCLUSIVE MODE;
    IF (SELECT count(*) FROM pg_catalog.pg_class
        WHERE oid IN (users_oid,roles_oid) AND relkind='r'
          AND relowner=owner_oid AND relrowsecurity) <> 2 THEN
        RAISE EXCEPTION 'STOP: Expected PostgreSQL-owned Platform tables with RLS enabled.';
    END IF;
    FOREACH required_table IN ARRAY ARRAY['users','user_roles'] LOOP
        required_oid := CASE WHEN required_table='users' THEN users_oid ELSE roles_oid END;
        FOREACH required_column IN ARRAY ARRAY['id','tenant_id','status'] LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute a
                WHERE a.attrelid=required_oid AND a.attname=required_column AND NOT a.attisdropped
                  AND a.atttypid=CASE WHEN required_column='status' THEN 'text'::regtype ELSE 'uuid'::regtype END
                  AND pg_catalog.has_column_privilege(api_oid,required_oid,a.attnum,'SELECT')
            ) THEN
                RAISE EXCEPTION 'STOP: Required selectable column differs: %.%', required_table,required_column;
            END IF;
        END LOOP;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid=roles_oid AND a.attname='user_id' AND NOT a.attisdropped
          AND a.atttypid='uuid'::regtype
          AND pg_catalog.has_column_privilege(api_oid,roles_oid,a.attnum,'SELECT')) THEN
        RAISE EXCEPTION 'STOP: Required user_roles.user_id access missing.';
    END IF;

    policies_before := (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'table',c.relname,'name',p.polname,'command',p.polcmd::text,
            'roles',(SELECT array_agg(CASE WHEN r=0 THEN 'PUBLIC'
                ELSE pg_catalog.pg_get_userbyid(r) END ORDER BY r) FROM unnest(p.polroles) r),
            'permissive',p.polpermissive,
            'using',pg_catalog.pg_get_expr(p.polqual,p.polrelid),
            'with_check',pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
        ) ORDER BY c.relname,p.polname),'[]'::jsonb)
        FROM pg_catalog.pg_policy p
        JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
        WHERE p.polrelid IN (users_oid,roles_oid)
          AND NOT ((p.polrelid=users_oid AND p.polname='construction_platform_membership_users_select')
                OR (p.polrelid=roles_oid AND p.polname='construction_platform_membership_roles_select')));
    IF policies_before IS DISTINCT FROM expected_policies THEN
        RAISE EXCEPTION 'STOP: Existing policies differ from the supplied diagnostic; nothing replaced.';
    END IF;
    SELECT count(*) INTO existing_new_count FROM pg_catalog.pg_policy p
        WHERE (p.polrelid=users_oid AND p.polname='construction_platform_membership_users_select')
           OR (p.polrelid=roles_oid AND p.polname='construction_platform_membership_roles_select');
    IF existing_new_count>0 THEN
        IF existing_new_count<>2 THEN
            RAISE EXCEPTION 'STOP: Partial membership policy installation; inspect before changes.';
        END IF;
        PERFORM pg_catalog.set_config('app.membership_select_fix_result',jsonb_build_object(
            'state','EXISTS_NO_CHANGES','metadata_revalidation_required',true,
            'business_rows_modified',0,'policies_added',0
        )::text,true);
        RETURN;
    END IF;
    acl_before := (SELECT jsonb_agg(jsonb_build_object(
            'table',c.relname,'owner',c.relowner,'acl',c.relacl::text,
            'rls_enabled',c.relrowsecurity,'rls_forced',c.relforcerowsecurity,
            'columns',(SELECT jsonb_agg(jsonb_build_object(
                'name',a.attname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
                'nullable',NOT a.attnotnull,'acl',a.attacl::text,
                'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid)
            ) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
        ) ORDER BY c.relname)
        FROM pg_catalog.pg_class c WHERE c.oid IN (users_oid,roles_oid));

    CREATE POLICY construction_platform_membership_roles_select
        ON public.user_roles FOR SELECT TO construction_api
        USING (
        NULLIF(current_setting('app.current_invitation_id', true), '') IS NULL
        AND NULLIF(current_setting('app.current_invitation_recipient_id', true), '') IS NULL
        AND tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        AND status = 'active'
        );
    CREATE POLICY construction_platform_membership_users_select
        ON public.users FOR SELECT TO construction_api
        USING (
        NULLIF(current_setting('app.current_invitation_id', true), '') IS NULL
        AND NULLIF(current_setting('app.current_invitation_recipient_id', true), '') IS NULL
        AND status = 'active'
        AND (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            OR EXISTS (
                SELECT 1 FROM public.user_roles AS membership
                WHERE membership.user_id = users.id
                  AND membership.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                  AND membership.status = 'active'
            )
        )
        );

    policies_after := (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'table',c.relname,'name',p.polname,'command',p.polcmd::text,
            'roles',(SELECT array_agg(CASE WHEN r=0 THEN 'PUBLIC'
                ELSE pg_catalog.pg_get_userbyid(r) END ORDER BY r) FROM unnest(p.polroles) r),
            'permissive',p.polpermissive,
            'using',pg_catalog.pg_get_expr(p.polqual,p.polrelid),
            'with_check',pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
        ) ORDER BY c.relname,p.polname),'[]'::jsonb)
        FROM pg_catalog.pg_policy p
        JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
        WHERE p.polrelid IN (users_oid,roles_oid)
          AND NOT ((p.polrelid=users_oid AND p.polname='construction_platform_membership_users_select')
                OR (p.polrelid=roles_oid AND p.polname='construction_platform_membership_roles_select')));
    acl_after := (SELECT jsonb_agg(jsonb_build_object(
            'table',c.relname,'owner',c.relowner,'acl',c.relacl::text,
            'rls_enabled',c.relrowsecurity,'rls_forced',c.relforcerowsecurity,
            'columns',(SELECT jsonb_agg(jsonb_build_object(
                'name',a.attname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
                'nullable',NOT a.attnotnull,'acl',a.attacl::text,
                'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid)
            ) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
        ) ORDER BY c.relname)
        FROM pg_catalog.pg_class c WHERE c.oid IN (users_oid,roles_oid));
    checks := jsonb_build_object(
        'existing_invitation_policies_unchanged',policies_after=policies_before,
        'schema_acl_and_rls_flags_unchanged',acl_after=acl_before,
        'two_select_only_role_scoped_policies',(SELECT count(*) FROM pg_catalog.pg_policy p
            WHERE ((p.polrelid=users_oid AND p.polname='construction_platform_membership_users_select')
                OR (p.polrelid=roles_oid AND p.polname='construction_platform_membership_roles_select'))
              AND p.polcmd='r' AND p.polroles=ARRAY[api_oid]::oid[]
              AND p.polpermissive AND p.polwithcheck IS NULL)=2,
        'ordinary_policies_exclude_invitation_context',(SELECT count(*) FROM pg_catalog.pg_policy p
            WHERE ((p.polrelid=users_oid AND p.polname='construction_platform_membership_users_select')
                OR (p.polrelid=roles_oid AND p.polname='construction_platform_membership_roles_select'))
              AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%app.current_invitation_id%'
              AND pg_catalog.pg_get_expr(p.polqual,p.polrelid) LIKE '%app.current_invitation_recipient_id%')=2,
        'no_new_table_wide_update',NOT pg_catalog.has_table_privilege(api_oid,users_oid,'UPDATE')
            AND NOT pg_catalog.has_table_privilege(api_oid,roles_oid,'UPDATE'),
        'no_delete_or_truncate',NOT pg_catalog.has_table_privilege(api_oid,users_oid,'DELETE')
            AND NOT pg_catalog.has_table_privilege(api_oid,users_oid,'TRUNCATE')
            AND NOT pg_catalog.has_table_privilege(api_oid,roles_oid,'DELETE')
            AND NOT pg_catalog.has_table_privilege(api_oid,roles_oid,'TRUNCATE')
    );
    IF EXISTS (SELECT 1 FROM jsonb_each(checks) p WHERE p.value IS DISTINCT FROM 'true'::jsonb) THEN
        RAISE EXCEPTION 'STOP: Membership policy metadata checks failed: %', checks;
    END IF;
    PERFORM pg_catalog.set_config('app.membership_select_fix_result',jsonb_build_object(
        'state','APPLIED','policies_added',2,'checks',checks,
        'business_rows_modified',0,'acl_changed',false,
        'invitation_policies_changed',false,'application_files_modified',false,
        'membership_behavior_tested',false,
        'next_step','REPEAT_READ_ONLY_DIAGNOSTIC_THEN_MEMBERSHIP_RLS_BEHAVIOR_CHECK'
    )::text,true);
END;
$membership_fix$;

SELECT current_setting('app.membership_select_fix_result')::jsonb AS membership_select_fix_result;
COMMIT;
