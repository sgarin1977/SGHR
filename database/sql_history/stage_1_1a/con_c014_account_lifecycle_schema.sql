-- CON-C014: canonical Telegram account lifecycle storage, schema only.
-- Adds status active/unlinked and row_version; no account is unlinked here.
-- Existing 17-column values, RLS, constraints, indexes and triggers are retained.
-- anon/authenticated/service_role retain INSERT/UPDATE of the old columns only.
-- New lifecycle columns are writable by the existing Platform owner runtime.
-- Run the WHOLE file as postgres in Supabase SQL Editor.
BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';
SET LOCAL search_path = pg_catalog, public;

DO $migration$
DECLARE
    expected_table jsonb := $expected_table${"kind":"r","name":"user_accounts","owner":"postgres","schema":"public","columns":[{"name":"id","type":"uuid","nullable":false,"identity_kind":"","generated_kind":"","server_default":"gen_random_uuid()"},{"name":"user_id","type":"uuid","nullable":false,"identity_kind":"","generated_kind":"","server_default":null},{"name":"platform","type":"text","nullable":false,"identity_kind":"","generated_kind":"","server_default":"'telegram'::text"},{"name":"platform_user_id","type":"text","nullable":false,"identity_kind":"","generated_kind":"","server_default":null},{"name":"username","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"first_name","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"last_name","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"display_name","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"language_code","type":"character varying(10)","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"email","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"phone","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"source","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"campaign_id","type":"uuid","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"referral_code","type":"text","nullable":true,"identity_kind":"","generated_kind":"","server_default":null},{"name":"raw_profile","type":"jsonb","nullable":false,"identity_kind":"","generated_kind":"","server_default":"'{}'::jsonb"},{"name":"created_at","type":"timestamp with time zone","nullable":false,"identity_kind":"","generated_kind":"","server_default":"now()"},{"name":"updated_at","type":"timestamp with time zone","nullable":false,"identity_kind":"","generated_kind":"","server_default":"now()"}],"indexes":[{"name":"idx_user_accounts_email","ready":true,"valid":true,"definition":"CREATE INDEX idx_user_accounts_email ON public.user_accounts USING btree (email)"},{"name":"idx_user_accounts_phone","ready":true,"valid":true,"definition":"CREATE INDEX idx_user_accounts_phone ON public.user_accounts USING btree (phone)"},{"name":"idx_user_accounts_platform_user_id","ready":true,"valid":true,"definition":"CREATE INDEX idx_user_accounts_platform_user_id ON public.user_accounts USING btree (platform, platform_user_id)"},{"name":"idx_user_accounts_user_id","ready":true,"valid":true,"definition":"CREATE INDEX idx_user_accounts_user_id ON public.user_accounts USING btree (user_id)"},{"name":"idx_user_accounts_username","ready":true,"valid":true,"definition":"CREATE INDEX idx_user_accounts_username ON public.user_accounts USING btree (username)"},{"name":"user_accounts_pkey","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX user_accounts_pkey ON public.user_accounts USING btree (id)"},{"name":"user_accounts_platform_platform_user_id_key","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX user_accounts_platform_platform_user_id_key ON public.user_accounts USING btree (platform, platform_user_id)"}],"policies":[],"triggers":[{"name":"trg_user_accounts_updated_at","definition":"CREATE TRIGGER trg_user_accounts_updated_at BEFORE UPDATE ON user_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at()","enabled_mode":"O","function_definition":"CREATE OR REPLACE FUNCTION public.set_updated_at()\n RETURNS trigger\n LANGUAGE plpgsql\nAS $function$\nBEGIN\n    NEW.updated_at = now();\n    RETURN NEW;\nEND;\n$function$\n"}],"rls_forced":false,"constraints":[{"kind":"c","name":"chk_user_accounts_platform","validated":true,"definition":"CHECK (platform = ANY (ARRAY['telegram'::text, 'email'::text, 'web'::text, 'mobile'::text, 'google'::text, 'apple'::text, 'github'::text, 'manual'::text]))"},{"kind":"p","name":"user_accounts_pkey","validated":true,"definition":"PRIMARY KEY (id)"},{"kind":"u","name":"user_accounts_platform_platform_user_id_key","validated":true,"definition":"UNIQUE (platform, platform_user_id)"},{"kind":"f","name":"user_accounts_user_id_fkey","validated":true,"definition":"FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"}],"rls_enabled":true}$expected_table$::jsonb;
    expected_roles jsonb := $expected_roles$[{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"user_id","insert":true,"select":true,"update":true},{"name":"platform","insert":true,"select":true,"update":true},{"name":"platform_user_id","insert":true,"select":true,"update":true},{"name":"username","insert":true,"select":true,"update":true},{"name":"first_name","insert":true,"select":true,"update":true},{"name":"last_name","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"source","insert":true,"select":true,"update":true},{"name":"campaign_id","insert":true,"select":true,"update":true},{"name":"referral_code","insert":true,"select":true,"update":true},{"name":"raw_profile","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"anon","rolbypassrls":false,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"user_id","insert":true,"select":true,"update":true},{"name":"platform","insert":true,"select":true,"update":true},{"name":"platform_user_id","insert":true,"select":true,"update":true},{"name":"username","insert":true,"select":true,"update":true},{"name":"first_name","insert":true,"select":true,"update":true},{"name":"last_name","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"source","insert":true,"select":true,"update":true},{"name":"campaign_id","insert":true,"select":true,"update":true},{"name":"referral_code","insert":true,"select":true,"update":true},{"name":"raw_profile","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"authenticated","rolbypassrls":false,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false},{"columns":[{"name":"id","insert":false,"select":false,"update":false},{"name":"user_id","insert":false,"select":false,"update":false},{"name":"platform","insert":false,"select":false,"update":false},{"name":"platform_user_id","insert":false,"select":false,"update":false},{"name":"username","insert":false,"select":false,"update":false},{"name":"first_name","insert":false,"select":false,"update":false},{"name":"last_name","insert":false,"select":false,"update":false},{"name":"display_name","insert":false,"select":false,"update":false},{"name":"language_code","insert":false,"select":false,"update":false},{"name":"email","insert":false,"select":false,"update":false},{"name":"phone","insert":false,"select":false,"update":false},{"name":"source","insert":false,"select":false,"update":false},{"name":"campaign_id","insert":false,"select":false,"update":false},{"name":"referral_code","insert":false,"select":false,"update":false},{"name":"raw_profile","insert":false,"select":false,"update":false},{"name":"created_at","insert":false,"select":false,"update":false},{"name":"updated_at","insert":false,"select":false,"update":false}],"rolsuper":false,"role_name":"construction_api","rolbypassrls":false,"schema_usage":true,"table_delete":false,"table_insert":false,"table_select":false,"table_update":false,"table_truncate":false,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"user_id","insert":true,"select":true,"update":true},{"name":"platform","insert":true,"select":true,"update":true},{"name":"platform_user_id","insert":true,"select":true,"update":true},{"name":"username","insert":true,"select":true,"update":true},{"name":"first_name","insert":true,"select":true,"update":true},{"name":"last_name","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"source","insert":true,"select":true,"update":true},{"name":"campaign_id","insert":true,"select":true,"update":true},{"name":"referral_code","insert":true,"select":true,"update":true},{"name":"raw_profile","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"postgres","rolbypassrls":true,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":true},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"user_id","insert":true,"select":true,"update":true},{"name":"platform","insert":true,"select":true,"update":true},{"name":"platform_user_id","insert":true,"select":true,"update":true},{"name":"username","insert":true,"select":true,"update":true},{"name":"first_name","insert":true,"select":true,"update":true},{"name":"last_name","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"source","insert":true,"select":true,"update":true},{"name":"campaign_id","insert":true,"select":true,"update":true},{"name":"referral_code","insert":true,"select":true,"update":true},{"name":"raw_profile","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"service_role","rolbypassrls":true,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false}]$expected_roles$::jsonb;
    expected_acl text := '{postgres=arwdDxtm/postgres,anon=arwdDxtm/postgres,authenticated=arwdDxtm/postgres,service_role=arwdDxtm/postgres}';
    snapshot_sql text := $snapshot_query$WITH target AS (
    SELECT c.*, n.nspname AS schema_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='user_accounts'
), columns AS (
    SELECT a.attnum, a.attname AS name,
        pg_catalog.format_type(a.atttypid,a.atttypmod) AS type,
        NOT a.attnotnull AS nullable,
        pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS server_default,
        a.attidentity::text AS identity_kind, a.attgenerated::text AS generated_kind,
        a.attacl::text AS column_acl
    FROM target t
    JOIN pg_catalog.pg_attribute a ON a.attrelid=t.oid
    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attnum>0 AND NOT a.attisdropped
), relevant_roles AS (
    SELECT r.* FROM pg_catalog.pg_roles r
    WHERE r.rolname IN ('anon','authenticated','service_role','construction_api',current_user)
), role_privileges AS (
    SELECT r.rolname AS role_name, r.rolsuper, r.rolbypassrls,
        pg_catalog.pg_has_role(r.oid,t.relowner,'MEMBER') AS owner_membership,
        pg_catalog.has_schema_privilege(r.oid,t.relnamespace,'USAGE') AS schema_usage,
        pg_catalog.has_table_privilege(r.oid,t.oid,'SELECT') AS table_select,
        pg_catalog.has_table_privilege(r.oid,t.oid,'INSERT') AS table_insert,
        pg_catalog.has_table_privilege(r.oid,t.oid,'UPDATE') AS table_update,
        pg_catalog.has_table_privilege(r.oid,t.oid,'DELETE') AS table_delete,
        pg_catalog.has_table_privilege(r.oid,t.oid,'TRUNCATE') AS table_truncate,
        (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'name',a.attname,
            'select',pg_catalog.has_column_privilege(r.oid,t.oid,a.attnum,'SELECT'),
            'insert',pg_catalog.has_column_privilege(r.oid,t.oid,a.attnum,'INSERT'),
            'update',pg_catalog.has_column_privilege(r.oid,t.oid,a.attnum,'UPDATE')
        ) ORDER BY a.attnum),'[]'::jsonb)
         FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped
        ) AS columns
    FROM target t CROSS JOIN relevant_roles r
), constraints AS (
    SELECT con.conname AS name, con.contype::text AS kind, con.convalidated AS validated,
        pg_catalog.pg_get_constraintdef(con.oid,true) AS definition
    FROM target t JOIN pg_catalog.pg_constraint con ON con.conrelid=t.oid
), indexes AS (
    SELECT cls.relname AS name, idx.indisvalid AS valid, idx.indisready AS ready,
        pg_catalog.pg_get_indexdef(idx.indexrelid) AS definition
    FROM target t JOIN pg_catalog.pg_index idx ON idx.indrelid=t.oid
    JOIN pg_catalog.pg_class cls ON cls.oid=idx.indexrelid
), policies AS (
    SELECT p.polname AS name, p.polcmd::text AS command, p.polpermissive AS permissive,
        ARRAY(SELECT CASE WHEN role_oid=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_oid) END
              FROM unnest(p.polroles) AS role_oid ORDER BY 1) AS roles,
        pg_catalog.pg_get_expr(p.polqual,p.polrelid) AS using_expression,
        pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid) AS with_check
    FROM target t JOIN pg_catalog.pg_policy p ON p.polrelid=t.oid
), triggers AS (
    SELECT trg.tgname AS name, trg.tgenabled::text AS enabled_mode,
        pg_catalog.pg_get_triggerdef(trg.oid,true) AS definition,
        pg_catalog.pg_get_functiondef(trg.tgfoid) AS function_definition
    FROM target t JOIN pg_catalog.pg_trigger trg ON trg.tgrelid=t.oid
    WHERE NOT trg.tgisinternal
)
SELECT jsonb_build_object(
    'state','READ_ONLY_CANONICAL_ACCOUNT_LIFECYCLE_PREFLIGHT',
    'role_name',current_user,
    'read_only',current_setting('transaction_read_only'),
    'table',COALESCE((SELECT jsonb_build_object(
        'schema',t.schema_name,'name',t.relname,'kind',t.relkind::text,
        'owner',pg_catalog.pg_get_userbyid(t.relowner),
        'rls_enabled',t.relrowsecurity,'rls_forced',t.relforcerowsecurity,
        'table_acl',t.relacl::text,
        'columns',COALESCE((SELECT jsonb_agg(to_jsonb(c)-'attnum' ORDER BY c.attnum) FROM columns c),'[]'::jsonb),
        'constraints',COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.name) FROM constraints c),'[]'::jsonb),
        'indexes',COALESCE((SELECT jsonb_agg(to_jsonb(i) ORDER BY i.name) FROM indexes i),'[]'::jsonb),
        'policies',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.name) FROM policies p),'[]'::jsonb),
        'triggers',COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.name) FROM triggers g),'[]'::jsonb)
    ) FROM target t),'null'::jsonb),
    'role_privileges',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.role_name) FROM role_privileges r),'[]'::jsonb),
    'lifecycle_fields_found',COALESCE((SELECT jsonb_agg(c.name ORDER BY c.name)
        FROM columns c WHERE c.name IN ('status','is_active','deactivated_at','unlinked_at','row_version')),'[]'::jsonb),
    'business_rows_read',false,
    'database_writes',false,
    'schema_or_acl_modified',false,
    'application_files_modified',false,
    'next_step','CANONICAL_ACCOUNT_LIFECYCLE_SQL_MIGRATION'
) AS con_c014_account_lifecycle_preflight$snapshot_query$;
    new_constraint_names text[] := ARRAY['ck_user_accounts_status','ck_user_accounts_unlinked_platform','ck_user_accounts_row_version'];
    protected_roles text[] := ARRAY['anon','authenticated','service_role'];
    old_names text[];
    column_list text;
    target_oid oid;
    owner_oid oid;
    fresh boolean;
    lifecycle_columns integer;
    phase integer;
    snapshot jsonb;
    stable_table jsonb;
    normalized_roles jsonb;
    role_item jsonb;
    normalized_role jsonb;
    role_name text;
    grantee_oid oid;
    protected_acl jsonb;
    original_protected_acl jsonb;
    row_count bigint;
    original_row_count bigint;
    old_values_hash text;
    original_values_hash text;
    lifecycle_hash text;
    original_lifecycle_hash text;
    constraint_row record;
    expected_definition text;
    checks jsonb;
BEGIN
    IF current_user <> 'postgres' OR current_setting('transaction_read_only') <> 'off' THEN
        RAISE EXCEPTION 'STOP: Use the existing postgres SQL Editor role in a writable transaction.';
    END IF;
    SELECT c.oid,c.relowner INTO target_oid,owner_oid FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='user_accounts' AND c.relkind='r'
      AND pg_catalog.pg_get_userbyid(c.relowner)='postgres';
    IF target_oid IS NULL THEN RAISE EXCEPTION 'STOP: Canonical public.user_accounts is unavailable.'; END IF;
    LOCK TABLE public.user_accounts IN ACCESS EXCLUSIVE MODE;
    SELECT array_agg(item->>'name' ORDER BY ord),string_agg(format('%I',item->>'name'),', ' ORDER BY ord)
    INTO old_names,column_list
    FROM jsonb_array_elements(expected_table->'columns') WITH ORDINALITY AS cols(item,ord);
    SELECT count(*) INTO lifecycle_columns FROM pg_catalog.pg_attribute
    WHERE attrelid=target_oid AND attnum>0 AND NOT attisdropped AND attname IN ('status','row_version');
    IF lifecycle_columns NOT IN (0,2) THEN RAISE EXCEPTION 'STOP: Lifecycle storage is only partially present.'; END IF;
    fresh := lifecycle_columns=0;

    FOR phase IN 0..1 LOOP
        EXECUTE snapshot_sql INTO snapshot;
        stable_table := (snapshot->'table')-'table_acl';
        stable_table := jsonb_set(stable_table,'{columns}',(
            SELECT jsonb_agg(item-'column_acl' ORDER BY ord)
            FROM jsonb_array_elements(snapshot->'table'->'columns') WITH ORDINALITY AS cols(item,ord)
            WHERE item->>'name'=ANY(old_names)
        ));
        stable_table := jsonb_set(stable_table,'{constraints}',COALESCE((
            SELECT jsonb_agg(item ORDER BY item->>'name')
            FROM jsonb_array_elements(snapshot->'table'->'constraints') AS cons(item)
            WHERE NOT(item->>'name'=ANY(new_constraint_names))
        ),'[]'::jsonb));
        IF stable_table IS DISTINCT FROM expected_table THEN
            RAISE EXCEPTION 'STOP: Existing canonical columns, constraints, indexes, RLS, policies or triggers differ from reviewed preflight.';
        END IF;
        IF phase=0 AND fresh AND (
            snapshot->'table'->>'table_acl' IS DISTINCT FROM expected_acl
            OR snapshot->'role_privileges' IS DISTINCT FROM expected_roles
            OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'table'->'columns') AS cols(item)
                      WHERE item->>'name'=ANY(old_names) AND item->'column_acl'<>'null'::jsonb)
            OR jsonb_array_length(snapshot->'table'->'constraints')<>4
        ) THEN RAISE EXCEPTION 'STOP: Original canonical grants or constraints changed; no blind ACL conversion.'; END IF;

        -- Preserve every table ACL entry except the intentional conversion of
        -- INSERT/UPDATE for these three pre-existing grantees. NULL rather than
        -- a zero-dimensional empty ACL avoids the earlier aclexplode failure.
        SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.grantor,a.grantee,a.privilege_type,a.is_grantable),'[]'::jsonb)
        INTO protected_acl FROM (
            SELECT acl.* FROM pg_catalog.pg_class c
            CROSS JOIN LATERAL pg_catalog.aclexplode(CASE
                WHEN c.relacl IS NULL THEN pg_catalog.acldefault('r',c.relowner)
                WHEN cardinality(c.relacl)>0 THEN c.relacl ELSE NULL::aclitem[] END) acl
            WHERE c.oid=target_oid AND NOT (
                acl.grantee IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=ANY(protected_roles))
                AND acl.privilege_type IN ('INSERT','UPDATE'))
        ) a;
        SELECT count(*),md5(COALESCE(string_agg(md5((to_jsonb(a)-ARRAY['status','row_version'])::text),'' ORDER BY a.id),''))
        INTO row_count,old_values_hash FROM public.user_accounts a;
        SELECT md5(COALESCE(string_agg(jsonb_build_array(a.id,to_jsonb(a)->'status',to_jsonb(a)->'row_version')::text,'' ORDER BY a.id),''))
        INTO lifecycle_hash FROM public.user_accounts a;

        IF phase=0 THEN
            original_protected_acl := protected_acl;
            original_row_count := row_count;
            original_values_hash := old_values_hash;
            original_lifecycle_hash := lifecycle_hash;
            IF fresh THEN
                IF EXISTS(SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid=target_oid AND conname=ANY(new_constraint_names)) THEN
                    RAISE EXCEPTION 'STOP: Lifecycle constraint names already exist without their columns.';
                END IF;
                ALTER TABLE public.user_accounts
                    ADD COLUMN status text NOT NULL DEFAULT 'active',
                    ADD COLUMN row_version bigint NOT NULL DEFAULT 1,
                    ADD CONSTRAINT ck_user_accounts_status CHECK (status IN ('active','unlinked')),
                    ADD CONSTRAINT ck_user_accounts_unlinked_platform CHECK (status<>'unlinked' OR platform='telegram'),
                    ADD CONSTRAINT ck_user_accounts_row_version CHECK (row_version>0);
                FOREACH role_name IN ARRAY protected_roles LOOP
                    EXECUTE format('REVOKE INSERT, UPDATE ON TABLE public.user_accounts FROM %I',role_name);
                    EXECUTE format('GRANT INSERT (%s), UPDATE (%s) ON TABLE public.user_accounts TO %I',column_list,column_list,role_name);
                END LOOP;
            END IF;
        ELSE
            IF row_count<>original_row_count OR old_values_hash IS DISTINCT FROM original_values_hash THEN
                RAISE EXCEPTION 'STOP: Existing account rows or old column values changed.';
            END IF;
            IF protected_acl IS DISTINCT FROM original_protected_acl THEN
                RAISE EXCEPTION 'STOP: Unrelated table grants changed.';
            END IF;
            IF NOT fresh AND lifecycle_hash IS DISTINCT FROM original_lifecycle_hash THEN
                RAISE EXCEPTION 'STOP: Repeated migration changed existing lifecycle values.';
            END IF;
        END IF;

        IF phase=1 OR NOT fresh THEN
            IF jsonb_array_length(snapshot->'table'->'columns')<>19
                OR jsonb_array_length(snapshot->'table'->'constraints')<>7 THEN
                RAISE EXCEPTION 'STOP: Unexpected lifecycle column or constraint count.';
            END IF;
            IF (SELECT count(*) FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
                ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=target_oid AND a.attnum>0 AND NOT a.attisdropped
                AND a.attname='status' AND a.atttypid='text'::regtype AND a.attnotnull AND a.attacl IS NULL
                AND a.attidentity='' AND a.attgenerated='' AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)='''active''::text')<>1
            OR (SELECT count(*) FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
                ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=target_oid AND a.attnum>0 AND NOT a.attisdropped
                AND a.attname='row_version' AND a.atttypid='bigint'::regtype AND a.attnotnull AND a.attacl IS NULL
                AND a.attidentity='' AND a.attgenerated='' AND pg_catalog.pg_get_expr(d.adbin,d.adrelid)='1')<>1 THEN
                RAISE EXCEPTION 'STOP: Lifecycle types, nullability, defaults or column ACL differ.';
            END IF;
            FOR constraint_row IN SELECT * FROM pg_catalog.pg_constraint
                WHERE conrelid=target_oid AND conname=ANY(new_constraint_names)
            LOOP
                expected_definition := CASE constraint_row.conname
                    WHEN 'ck_user_accounts_status' THEN 'CHECK (status = ANY (ARRAY[''active''::text, ''unlinked''::text]))'
                    WHEN 'ck_user_accounts_unlinked_platform' THEN 'CHECK (status <> ''unlinked''::text OR platform = ''telegram''::text)'
                    WHEN 'ck_user_accounts_row_version' THEN 'CHECK (row_version > 0)' END;
                IF constraint_row.contype<>'c' OR NOT constraint_row.convalidated OR constraint_row.connoinherit
                    OR regexp_replace(pg_catalog.pg_get_constraintdef(constraint_row.oid,true),'[[:space:]()]','','g')
                       IS DISTINCT FROM regexp_replace(expected_definition,'[[:space:]()]','','g') THEN
                    RAISE EXCEPTION 'STOP: Unexpected lifecycle constraint definition or validation flags.';
                END IF;
            END LOOP;
            IF (SELECT count(*) FROM pg_catalog.pg_constraint WHERE conrelid=target_oid AND conname=ANY(new_constraint_names))<>3 THEN
                RAISE EXCEPTION 'STOP: Missing lifecycle constraints.';
            END IF;
            IF EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
                CROSS JOIN LATERAL pg_catalog.aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl ELSE NULL::aclitem[] END) acl
                WHERE a.attrelid=target_oid AND a.attname=ANY(old_names) AND (
                    acl.grantor<>owner_oid OR acl.grantee NOT IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=ANY(protected_roles))
                    OR acl.privilege_type NOT IN ('INSERT','UPDATE') OR acl.is_grantable)) THEN
                RAISE EXCEPTION 'STOP: Unexpected old-column grant or grant option.';
            END IF;
            FOREACH role_name IN ARRAY protected_roles LOOP
                SELECT oid INTO grantee_oid FROM pg_catalog.pg_roles WHERE rolname=role_name;
                IF grantee_oid IS NULL OR pg_catalog.has_table_privilege(grantee_oid,target_oid,'INSERT')
                    OR pg_catalog.has_table_privilege(grantee_oid,target_oid,'UPDATE')
                    OR pg_catalog.has_column_privilege(grantee_oid,target_oid,'status','INSERT,UPDATE')
                    OR pg_catalog.has_column_privilege(grantee_oid,target_oid,'row_version','INSERT,UPDATE') THEN
                    RAISE EXCEPTION 'STOP: Lifecycle writes are not restricted for role %.',role_name;
                END IF;
            END LOOP;
            normalized_roles := '[]'::jsonb;
            FOR role_item IN SELECT value FROM jsonb_array_elements(snapshot->'role_privileges') LOOP
                normalized_role := jsonb_set(role_item,'{columns}',(
                    SELECT jsonb_agg(item ORDER BY ord) FROM jsonb_array_elements(role_item->'columns') WITH ORDINALITY AS cols(item,ord)
                    WHERE item->>'name'=ANY(old_names)));
                IF role_item->>'role_name'=ANY(protected_roles) THEN
                    normalized_role := normalized_role||jsonb_build_object('table_insert',true,'table_update',true);
                END IF;
                normalized_roles := normalized_roles||jsonb_build_array(normalized_role);
            END LOOP;
            IF normalized_roles IS DISTINCT FROM expected_roles THEN
                RAISE EXCEPTION 'STOP: Effective old-column permissions or unrelated role privileges changed.';
            END IF;
        END IF;
    END LOOP;
    IF fresh AND EXISTS(SELECT 1 FROM public.user_accounts WHERE status<>'active' OR row_version<>1) THEN
        RAISE EXCEPTION 'STOP: Existing accounts did not receive the compatibility defaults.';
    END IF;
    checks := jsonb_build_object(
        'canonical_table_extended',true,'existing_17_column_values_unchanged',true,
        'existing_rows_preserved',true,'existing_effective_column_permissions_preserved',true,
        'new_lifecycle_writes_restricted_to_platform_runtime',true,
        'rls_policies_triggers_indexes_and_old_constraints_unchanged',true,
        'unique_telegram_identity_preserved',true,'three_lifecycle_checks_validated',true);
    PERFORM set_config('con_c014.account_lifecycle_result',jsonb_build_object(
        'state',CASE WHEN fresh THEN 'APPLIED' ELSE 'ALREADY_APPLIED' END,
        'table','public.user_accounts','added_columns',jsonb_build_array('status','row_version'),
        'existing_accounts',original_row_count,'accounts_unlinked_by_this_script',0,
        'repeat_run',NOT fresh,
        'checks',checks,'auth_sessions_modified',false,'application_files_modified',false,
        'unlink_http_registered',false,'next_step','CANONICAL_ACCOUNT_ORM_AND_AUTHENTICATION_GUARDS'
    )::text,true);
END;
$migration$;

SELECT current_setting('con_c014.account_lifecycle_result')::jsonb AS con_c014_account_lifecycle_schema_result;
COMMIT;
