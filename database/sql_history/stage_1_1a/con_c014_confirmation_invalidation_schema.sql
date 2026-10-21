-- CON-C014: add distinct invalidation state to the existing confirmation table.
-- Run as postgres in Supabase SQL Editor. All DDL is one guarded transaction.
-- No client link, confirmation token, session, membership or grant is changed.
-- Old request values are fingerprinted privately and compared before commit.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';

DO $migration$
DECLARE
    relation_oid oid;
    runtime_oid oid;
    owner_oid oid;
    expected_base jsonb := $baseline${"kind":"r","name":"construction_client_user_link_requests","owner":"postgres","schema":"public","columns":[{"name":"id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":"gen_random_uuid()"},{"name":"tenant_id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"client_id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"initiated_by_user_id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"target_user_id","type":"uuid","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"expected_linked_user_id","type":"uuid","identity":"","nullable":true,"generated":"","column_acl":null,"server_default":null},{"name":"expected_client_row_version","type":"bigint","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"token_hash","type":"text","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"issued_at","type":"timestamp with time zone","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"expires_at","type":"timestamp with time zone","identity":"","nullable":false,"generated":"","column_acl":null,"server_default":null},{"name":"consumed_at","type":"timestamp with time zone","identity":"","nullable":true,"generated":"","column_acl":"{construction_api=w/postgres}","server_default":null},{"name":"updated_at","type":"timestamp with time zone","identity":"","nullable":false,"generated":"","column_acl":"{construction_api=w/postgres}","server_default":"now()"},{"name":"row_version","type":"bigint","identity":"","nullable":false,"generated":"","column_acl":"{construction_api=w/postgres}","server_default":"1"}],"indexes":[{"name":"construction_client_user_link_requests_pkey","ready":true,"valid":true,"unique":true,"definition":"CREATE UNIQUE INDEX construction_client_user_link_requests_pkey ON public.construction_client_user_link_requests USING btree (id)"},{"name":"ix_con_client_link_requests_client","ready":true,"valid":true,"unique":false,"definition":"CREATE INDEX ix_con_client_link_requests_client ON public.construction_client_user_link_requests USING btree (tenant_id, client_id, issued_at, id)"},{"name":"uq_con_client_link_requests_hash","ready":true,"valid":true,"unique":true,"definition":"CREATE UNIQUE INDEX uq_con_client_link_requests_hash ON public.construction_client_user_link_requests USING btree (token_hash)"}],"policies":[{"name":"construction_client_link_requests_insert","roles":["construction_api"],"command":"a","permissive":true,"with_check":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)","using_expression":null},{"name":"construction_client_link_requests_select","roles":["construction_api"],"command":"r","permissive":true,"with_check":null,"using_expression":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)"},{"name":"construction_client_link_requests_update","roles":["construction_api"],"command":"w","permissive":true,"with_check":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)","using_expression":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)"}],"table_acl":"{postgres=arwdDxtm/postgres,construction_api=ar/postgres}","rls_forced":false,"constraints":[{"kind":"c","name":"ck_con_client_link_requests_base_version","validated":true,"deferrable":false,"definition":"CHECK (expected_client_row_version > 0)","initially_deferred":false},{"kind":"c","name":"ck_con_client_link_requests_consumed_at","validated":true,"deferrable":false,"definition":"CHECK (consumed_at IS NULL OR consumed_at >= issued_at AND consumed_at < expires_at)","initially_deferred":false},{"kind":"c","name":"ck_con_client_link_requests_expiry","validated":true,"deferrable":false,"definition":"CHECK (expires_at > issued_at)","initially_deferred":false},{"kind":"c","name":"ck_con_client_link_requests_hash","validated":true,"deferrable":false,"definition":"CHECK (length(token_hash) = 64 AND translate(token_hash, '0123456789abcdef'::text, ''::text) = ''::text)","initially_deferred":false},{"kind":"c","name":"ck_con_client_link_requests_version","validated":true,"deferrable":false,"definition":"CHECK (row_version > 0)","initially_deferred":false},{"kind":"p","name":"construction_client_user_link_requests_pkey","validated":true,"deferrable":false,"definition":"PRIMARY KEY (id)","initially_deferred":false},{"kind":"f","name":"fk_con_client_link_requests_client","validated":true,"deferrable":false,"definition":"FOREIGN KEY (tenant_id, client_id) REFERENCES construction_clients(tenant_id, id)","initially_deferred":false},{"kind":"f","name":"fk_con_client_link_requests_initiator","validated":true,"deferrable":false,"definition":"FOREIGN KEY (initiated_by_user_id) REFERENCES users(id)","initially_deferred":false},{"kind":"f","name":"fk_con_client_link_requests_target","validated":true,"deferrable":false,"definition":"FOREIGN KEY (target_user_id) REFERENCES users(id)","initially_deferred":false},{"kind":"u","name":"uq_con_client_link_requests_hash","validated":true,"deferrable":false,"definition":"UNIQUE (token_hash)","initially_deferred":false}],"rls_enabled":true,"custom_triggers":[]}$baseline$::jsonb;
    metadata_query text := $catalog$
WITH relation AS (
    SELECT c.*, n.nspname AS schema_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = 'construction_client_user_link_requests'
), roles AS (
    SELECT r.* FROM pg_catalog.pg_roles r
    WHERE r.rolname IN ('construction_api', 'postgres', 'anon', 'authenticated', 'service_role')
), details AS (
    SELECT jsonb_build_object(
        'schema', c.schema_name, 'name', c.relname, 'kind', c.relkind::text,
        'owner', pg_catalog.pg_get_userbyid(c.relowner),
        'rls_enabled', c.relrowsecurity, 'rls_forced', c.relforcerowsecurity,
        'table_acl', c.relacl::text,
        'columns', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', a.attname,
                'type', pg_catalog.format_type(a.atttypid, a.atttypmod),
                'nullable', NOT a.attnotnull,
                'server_default', pg_catalog.pg_get_expr(d.adbin, d.adrelid),
                'identity', a.attidentity::text, 'generated', a.attgenerated::text,
                'column_acl', a.attacl::text
            ) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped AND a.attname <> 'invalidated_at'
        ), '[]'::jsonb),
        'constraints', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', k.conname, 'kind', k.contype::text,
                'validated', k.convalidated, 'deferrable', k.condeferrable,
                'initially_deferred', k.condeferred,
                'definition', pg_catalog.pg_get_constraintdef(k.oid, true)
            ) ORDER BY k.conname)
            FROM pg_catalog.pg_constraint k WHERE k.conrelid = c.oid
              AND k.conname NOT IN ('ck_con_client_link_requests_invalidated_at', 'ck_con_client_link_requests_terminal')
        ), '[]'::jsonb),
        'indexes', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', idx.relname, 'valid', i.indisvalid, 'ready', i.indisready,
                'unique', i.indisunique,
                'definition', pg_catalog.pg_get_indexdef(i.indexrelid)
            ) ORDER BY idx.relname)
            FROM pg_catalog.pg_index i
            JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
            WHERE i.indrelid = c.oid
        ), '[]'::jsonb),
        'policies', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', p.polname, 'command', p.polcmd::text, 'permissive', p.polpermissive,
                'roles', ARRAY(SELECT CASE WHEN role_oid = 0 THEN 'PUBLIC'
                    ELSE pg_catalog.pg_get_userbyid(role_oid) END
                    FROM unnest(p.polroles) AS role_oid ORDER BY 1),
                'using_expression', pg_catalog.pg_get_expr(p.polqual, p.polrelid),
                'with_check', pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid)
            ) ORDER BY p.polname)
            FROM pg_catalog.pg_policy p WHERE p.polrelid = c.oid
        ), '[]'::jsonb),
        'custom_triggers', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'name', t.tgname, 'enabled_mode', t.tgenabled::text,
                'definition', pg_catalog.pg_get_triggerdef(t.oid, true),
                'function_schema', fn_ns.nspname, 'function_name', fn.proname,
                'security_definer', fn.prosecdef, 'function_settings', fn.proconfig
            ) ORDER BY t.tgname)
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_proc fn ON fn.oid = t.tgfoid
            JOIN pg_catalog.pg_namespace fn_ns ON fn_ns.oid = fn.pronamespace
            WHERE t.tgrelid = c.oid AND NOT t.tgisinternal
        ), '[]'::jsonb)
    ) AS metadata
    FROM relation c
)
SELECT metadata FROM details
    $catalog$;
    actual jsonb;
    expected_checks jsonb := jsonb_build_object(
        'ck_con_client_link_requests_invalidated_at',
        'CHECK (invalidated_at IS NULL OR invalidated_at >= issued_at AND invalidated_at < expires_at)',
        'ck_con_client_link_requests_terminal',
        'CHECK (consumed_at IS NULL OR invalidated_at IS NULL)'
    );
    rows_before bigint;
    rows_after bigint;
    values_before text;
    values_after text;
    invalidation_values_before text;
    invalidation_values_after text;
    update_columns text[];
    was_added boolean := false;
    role_record record;
BEGIN
    IF current_user <> 'postgres'
       OR current_setting('transaction_read_only') <> 'off'
       OR current_setting('session_replication_role') <> 'origin' THEN
        RAISE EXCEPTION 'STOP: Run as postgres in a writable transaction with normal triggers.';
    END IF;
    SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles WHERE rolname='postgres';
    SELECT oid INTO STRICT runtime_oid FROM pg_catalog.pg_roles
        WHERE rolname='construction_api' AND NOT rolsuper AND NOT rolbypassrls;
    IF pg_catalog.pg_has_role(runtime_oid, owner_oid, 'MEMBER') THEN
        RAISE EXCEPTION 'STOP: Construction runtime must not inherit the table owner.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
        'sghr:con-c014:client-confirmation-invalidation:v1',0));
    relation_oid := pg_catalog.to_regclass('public.construction_client_user_link_requests');
    IF relation_oid IS NULL THEN
        RAISE EXCEPTION 'STOP: Existing confirmation table is missing.';
    END IF;
    LOCK TABLE public.construction_client_user_link_requests IN ACCESS EXCLUSIVE MODE;
    EXECUTE metadata_query INTO actual;
    IF actual IS DISTINCT FROM expected_base THEN
        RAISE EXCEPTION 'STOP: Confirmation metadata differs from the reviewed preflight; no schema changes applied.';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_roles
        WHERE rolname IN ('anon','authenticated','service_role')) <> 3 THEN
        RAISE EXCEPTION 'STOP: Expected Supabase client roles are missing.';
    END IF;

    SELECT count(*),pg_catalog.md5(COALESCE(pg_catalog.string_agg(
        pg_catalog.md5((pg_catalog.to_jsonb(q)-'invalidated_at')::text),'' ORDER BY q.id),''))
    INTO rows_before,values_before
    FROM public.construction_client_user_link_requests q;

    IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid=relation_oid AND attname='invalidated_at' AND attnum>0 AND NOT attisdropped) THEN
        -- A repeat run only validates the complete, expected extension.
        -- It does not clear an existing invalidation or silently repair drift.
        SELECT pg_catalog.md5(COALESCE(pg_catalog.string_agg(
            COALESCE(q.invalidated_at::text,'NULL'),'' ORDER BY q.id),''))
        INTO invalidation_values_before FROM public.construction_client_user_link_requests q;
    ELSE
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conrelid=relation_oid AND conname IN (
                'ck_con_client_link_requests_invalidated_at','ck_con_client_link_requests_terminal')) THEN
            RAISE EXCEPTION 'STOP: Invalidation constraint name already exists without the expected column.';
        END IF;
        ALTER TABLE public.construction_client_user_link_requests
            ADD COLUMN invalidated_at timestamp with time zone;
        ALTER TABLE public.construction_client_user_link_requests
            ADD CONSTRAINT ck_con_client_link_requests_invalidated_at
                CHECK (invalidated_at IS NULL OR (invalidated_at >= issued_at AND invalidated_at < expires_at)),
            ADD CONSTRAINT ck_con_client_link_requests_terminal
                CHECK (consumed_at IS NULL OR invalidated_at IS NULL);
        GRANT UPDATE (invalidated_at) ON TABLE public.construction_client_user_link_requests TO construction_api;
        was_added := true;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
        LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid=relation_oid AND a.attname='invalidated_at' AND a.attnum>0 AND NOT a.attisdropped
          AND a.atttypid='timestamp with time zone'::regtype AND a.atttypmod=-1
          AND NOT a.attnotnull AND a.attidentity='' AND a.attgenerated='' AND d.oid IS NULL
          AND a.attacl::text='{construction_api=w/postgres}') THEN
        RAISE EXCEPTION 'STOP: Invalidation column type, nullability, default or column ACL differs.';
    END IF;
    SELECT jsonb_object_agg(k.conname,pg_catalog.pg_get_constraintdef(k.oid,true) ORDER BY k.conname)
    INTO actual FROM pg_catalog.pg_constraint k
    WHERE k.conrelid=relation_oid AND k.conname IN (
        'ck_con_client_link_requests_invalidated_at','ck_con_client_link_requests_terminal')
      AND k.contype='c' AND k.convalidated AND NOT k.condeferrable AND NOT k.condeferred;
    IF actual IS DISTINCT FROM expected_checks THEN
        RAISE EXCEPTION 'STOP: Expected validated invalidation CHECK constraints differ.'
            USING DETAIL=COALESCE(actual::text,'No matching CHECK constraints');
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_constraint WHERE conrelid=relation_oid) <> 12 THEN
        RAISE EXCEPTION 'STOP: Expected ten existing and two new constraints.';
    END IF;
    EXECUTE metadata_query INTO actual;
    IF actual IS DISTINCT FROM expected_base THEN
        RAISE EXCEPTION 'STOP: Existing columns, constraints, indexes, table ACL, RLS or triggers changed.';
    END IF;
    SELECT ARRAY(SELECT a.attname::text FROM pg_catalog.pg_attribute a
        WHERE a.attrelid=relation_oid AND a.attnum>0 AND NOT a.attisdropped
          AND pg_catalog.has_column_privilege(runtime_oid,relation_oid,a.attnum,'UPDATE')
        ORDER BY a.attname) INTO update_columns;
    IF update_columns IS DISTINCT FROM ARRAY['consumed_at','invalidated_at','row_version','updated_at']::text[]
       OR NOT pg_catalog.has_table_privilege(runtime_oid,relation_oid,'SELECT')
       OR NOT pg_catalog.has_table_privilege(runtime_oid,relation_oid,'INSERT')
       OR pg_catalog.has_table_privilege(runtime_oid,relation_oid,'UPDATE')
       OR pg_catalog.has_table_privilege(runtime_oid,relation_oid,'DELETE')
       OR pg_catalog.has_table_privilege(runtime_oid,relation_oid,'TRUNCATE')
       OR pg_catalog.has_column_privilege(runtime_oid,relation_oid,'invalidated_at','UPDATE WITH GRANT OPTION') THEN
        RAISE EXCEPTION 'STOP: Runtime privileges exceed or differ from the expected narrow extension.';
    END IF;
    FOR role_record IN SELECT oid,rolname FROM pg_catalog.pg_roles
        WHERE rolname IN ('anon','authenticated','service_role') LOOP
        IF pg_catalog.has_any_column_privilege(role_record.oid,relation_oid,'SELECT')
           OR pg_catalog.has_any_column_privilege(role_record.oid,relation_oid,'INSERT')
           OR pg_catalog.has_any_column_privilege(role_record.oid,relation_oid,'UPDATE')
           OR pg_catalog.has_table_privilege(role_record.oid,relation_oid,'DELETE')
           OR pg_catalog.has_table_privilege(role_record.oid,relation_oid,'TRUNCATE') THEN
            RAISE EXCEPTION 'STOP: Unexpected client-role privileges: %',role_record.rolname;
        END IF;
    END LOOP;

    SELECT count(*),pg_catalog.md5(COALESCE(pg_catalog.string_agg(
        pg_catalog.md5((pg_catalog.to_jsonb(q)-'invalidated_at')::text),'' ORDER BY q.id),''))
    INTO rows_after,values_after FROM public.construction_client_user_link_requests q;
    IF rows_before IS DISTINCT FROM rows_after OR values_before IS DISTINCT FROM values_after THEN
        RAISE EXCEPTION 'STOP: Existing request values changed.';
    END IF;
    SELECT pg_catalog.md5(COALESCE(pg_catalog.string_agg(
        COALESCE(q.invalidated_at::text,'NULL'),'' ORDER BY q.id),''))
    INTO invalidation_values_after FROM public.construction_client_user_link_requests q;
    IF was_added THEN
        IF EXISTS (SELECT 1 FROM public.construction_client_user_link_requests WHERE invalidated_at IS NOT NULL) THEN
            RAISE EXCEPTION 'STOP: Schema preparation must not invalidate existing requests.';
        END IF;
    ELSIF invalidation_values_before IS DISTINCT FROM invalidation_values_after THEN
        RAISE EXCEPTION 'STOP: Existing invalidation values changed on repeat run.';
    END IF;
    PERFORM pg_catalog.set_config('con_c014.confirmation_invalidation_result',jsonb_build_object(
        'state','APPLIED','table','public.construction_client_user_link_requests',
        'repeat_run',NOT was_added,'added_column','invalidated_at','column_count',14,
        'constraint_count',12,'update_columns',update_columns,'existing_requests',rows_after,
        'checks',jsonb_build_object(
            'nullable_timestamptz_without_default',true,
            'two_validated_lifecycle_checks',true,
            'consumed_and_invalidated_are_mutually_exclusive',true,
            'existing_request_values_preserved',true,
            'repeat_run_preserves_invalidation_values',true,
            'only_new_column_update_granted',true,
            'no_browser_service_or_public_access_added',true,
            'existing_columns_constraints_indexes_acl_rls_and_triggers_unchanged',true),
        'requests_invalidated_by_migration',0,'confirmation_requests_created',0,
        'client_links_changed',0,'application_files_modified',false,
        'native_supersession_workflow_tested',false,'stage_1a_acceptance_claimed',false,
        'next_step','CON_C014_INVALIDATION_ORM_AND_REPOSITORY_PREPARATION'
    )::text,true);
END;
$migration$;

SELECT current_setting('con_c014.confirmation_invalidation_result')::jsonb
    AS con_c014_confirmation_invalidation_schema_result;
COMMIT;
