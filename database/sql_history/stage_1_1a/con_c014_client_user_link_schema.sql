-- CON-C014: storage preparation for the approved client -> Platform user link.
-- Adds nullable linked_user_id and optimistic row_version only.
-- No client is linked, and no confirmation/membership/grant/account is created.
-- FK uses ordinary NO ACTION: deleting a user cannot silently unlink a client.
-- Existing users/client ACL, RLS and custom triggers are preserved exactly.
-- Run the WHOLE file as postgres in Supabase SQL Editor.
BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';
SET LOCAL search_path = pg_catalog, public;

DO $migration$
DECLARE
    expected jsonb := $expected${"tables":[{"kind":"r","name":"construction_clients","owner":"postgres","schema":"public","columns":[{"name":"id","type":"uuid","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"gen_random_uuid()"},{"name":"tenant_id","type":"uuid","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"display_name","type":"text","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"client_type","type":"text","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"phone","type":"text","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"email","type":"text","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"notes","type":"text","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"created_at","type":"timestamp with time zone","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"now()"},{"name":"updated_at","type":"timestamp with time zone","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"now()"},{"name":"deleted_at","type":"timestamp with time zone","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null}],"indexes":[{"name":"construction_clients_pkey","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX construction_clients_pkey ON public.construction_clients USING btree (id)"},{"name":"uq_construction_clients_tenant_id_id","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX uq_construction_clients_tenant_id_id ON public.construction_clients USING btree (tenant_id, id)"}],"policies":[{"name":"construction_tenant_isolation","roles":["construction_api"],"command":"*","permissive":true,"with_check":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)","using_expression":"(tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid)"}],"triggers":[{"name":"trg_construction_clients_set_updated_at","definition":"CREATE TRIGGER trg_construction_clients_set_updated_at BEFORE UPDATE ON construction_clients FOR EACH ROW EXECUTE FUNCTION set_updated_at()","enabled_mode":"O","function_definition":"CREATE OR REPLACE FUNCTION public.set_updated_at()\n RETURNS trigger\n LANGUAGE plpgsql\nAS $function$\nBEGIN\n    NEW.updated_at = now();\n    RETURN NEW;\nEND;\n$function$\n"}],"table_acl":"{postgres=arwdDxtm/postgres,service_role=arwdDxtm/postgres,construction_api=arw/postgres}","rls_forced":false,"constraints":[{"kind":"c","name":"ck_construction_clients_client_type","validated":true,"definition":"CHECK (client_type = ANY (ARRAY['person'::text, 'company'::text]))"},{"kind":"p","name":"construction_clients_pkey","validated":true,"definition":"PRIMARY KEY (id)"},{"kind":"f","name":"construction_clients_tenant_id_fkey","validated":true,"definition":"FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"},{"kind":"u","name":"uq_construction_clients_tenant_id_id","validated":true,"definition":"UNIQUE (tenant_id, id)"}],"rls_enabled":true},{"kind":"r","name":"users","owner":"postgres","schema":"public","columns":[{"name":"id","type":"uuid","nullable":false,"column_acl":"{construction_api=rw/postgres}","identity_kind":"","generated_kind":"","server_default":"gen_random_uuid()"},{"name":"tenant_id","type":"uuid","nullable":true,"column_acl":"{construction_api=r/postgres}","identity_kind":"","generated_kind":"","server_default":null},{"name":"active_role","type":"text","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"language_code","type":"character varying(10)","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"'ru'::character varying"},{"name":"timezone","type":"text","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"country_id","type":"uuid","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"city_id","type":"uuid","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"profile_completion_score","type":"integer","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"0"},{"name":"trust_score","type":"integer","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"0"},{"name":"risk_score","type":"integer","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"0"},{"name":"status","type":"text","nullable":false,"column_acl":"{construction_api=r/postgres}","identity_kind":"","generated_kind":"","server_default":"'active'::text"},{"name":"last_seen_at","type":"timestamp with time zone","nullable":true,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":null},{"name":"metadata","type":"jsonb","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"'{}'::jsonb"},{"name":"created_at","type":"timestamp with time zone","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"now()"},{"name":"updated_at","type":"timestamp with time zone","nullable":false,"column_acl":null,"identity_kind":"","generated_kind":"","server_default":"now()"}],"indexes":[{"name":"idx_users_active_role","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_active_role ON public.users USING btree (active_role)"},{"name":"idx_users_city_id","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_city_id ON public.users USING btree (city_id)"},{"name":"idx_users_country_id","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_country_id ON public.users USING btree (country_id)"},{"name":"idx_users_language_code","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_language_code ON public.users USING btree (language_code)"},{"name":"idx_users_last_seen_at","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_last_seen_at ON public.users USING btree (last_seen_at)"},{"name":"idx_users_status","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_status ON public.users USING btree (status)"},{"name":"idx_users_tenant_id","ready":true,"valid":true,"definition":"CREATE INDEX idx_users_tenant_id ON public.users USING btree (tenant_id)"},{"name":"users_pkey","ready":true,"valid":true,"definition":"CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)"}],"policies":[{"name":"construction_invitation_users_lock","roles":["construction_api"],"command":"w","permissive":true,"with_check":"false","using_expression":"((id = (NULLIF(current_setting('app.current_invitation_recipient_id'::text, true), ''::text))::uuid) AND (status = 'active'::text) AND ((NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid IS NOT NULL) AND ((NULLIF(current_setting('app.current_invitation_id'::text, true), ''::text))::uuid IS NOT NULL))"},{"name":"construction_invitation_users_select","roles":["construction_api"],"command":"r","permissive":true,"with_check":null,"using_expression":"((id = (NULLIF(current_setting('app.current_invitation_recipient_id'::text, true), ''::text))::uuid) AND (status = 'active'::text) AND ((NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid IS NOT NULL) AND ((NULLIF(current_setting('app.current_invitation_id'::text, true), ''::text))::uuid IS NOT NULL))"},{"name":"construction_platform_membership_users_select","roles":["construction_api"],"command":"r","permissive":true,"with_check":null,"using_expression":"((NULLIF(current_setting('app.current_invitation_id'::text, true), ''::text) IS NULL) AND (NULLIF(current_setting('app.current_invitation_recipient_id'::text, true), ''::text) IS NULL) AND (status = 'active'::text) AND ((tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid) OR (EXISTS ( SELECT 1\n   FROM user_roles membership\n  WHERE ((membership.user_id = users.id) AND (membership.tenant_id = (NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text))::uuid) AND (membership.status = 'active'::text))))))"}],"triggers":[{"name":"trg_users_updated_at","definition":"CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at()","enabled_mode":"O","function_definition":"CREATE OR REPLACE FUNCTION public.set_updated_at()\n RETURNS trigger\n LANGUAGE plpgsql\nAS $function$\nBEGIN\n    NEW.updated_at = now();\n    RETURN NEW;\nEND;\n$function$\n"}],"table_acl":"{postgres=arwdDxtm/postgres,anon=arwdDxtm/postgres,authenticated=arwdDxtm/postgres,service_role=arwdDxtm/postgres}","rls_forced":false,"constraints":[{"kind":"c","name":"chk_users_active_role","validated":true,"definition":"CHECK (active_role IS NULL OR (active_role = ANY (ARRAY['client'::text, 'seeker'::text, 'employer'::text, 'specialist'::text, 'moderator'::text, 'support'::text, 'content_manager'::text, 'agency_admin'::text, 'agency_user'::text, 'operator'::text, 'finance_admin'::text, 'advertiser'::text, 'investor'::text, 'admin'::text, 'super_admin'::text])))"},{"kind":"c","name":"chk_users_status","validated":true,"definition":"CHECK (status = ANY (ARRAY['active'::text, 'pending'::text, 'restricted'::text, 'blocked'::text, 'deleted'::text, 'pending_deletion'::text]))"},{"kind":"f","name":"users_city_id_fkey","validated":true,"definition":"FOREIGN KEY (city_id) REFERENCES cities(id)"},{"kind":"f","name":"users_country_id_fkey","validated":true,"definition":"FOREIGN KEY (country_id) REFERENCES countries(id)"},{"kind":"p","name":"users_pkey","validated":true,"definition":"PRIMARY KEY (id)"},{"kind":"f","name":"users_tenant_id_fkey","validated":true,"definition":"FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"}],"rls_enabled":true}],"role_privileges":[{"columns":[{"name":"id","insert":false,"select":false,"update":false},{"name":"tenant_id","insert":false,"select":false,"update":false},{"name":"display_name","insert":false,"select":false,"update":false},{"name":"client_type","insert":false,"select":false,"update":false},{"name":"phone","insert":false,"select":false,"update":false},{"name":"email","insert":false,"select":false,"update":false},{"name":"notes","insert":false,"select":false,"update":false},{"name":"created_at","insert":false,"select":false,"update":false},{"name":"updated_at","insert":false,"select":false,"update":false},{"name":"deleted_at","insert":false,"select":false,"update":false}],"rolsuper":false,"role_name":"anon","table_name":"construction_clients","rolbypassrls":false,"schema_usage":true,"table_delete":false,"table_insert":false,"table_select":false,"table_update":false,"table_truncate":false,"owner_membership":false},{"columns":[{"name":"id","insert":false,"select":false,"update":false},{"name":"tenant_id","insert":false,"select":false,"update":false},{"name":"display_name","insert":false,"select":false,"update":false},{"name":"client_type","insert":false,"select":false,"update":false},{"name":"phone","insert":false,"select":false,"update":false},{"name":"email","insert":false,"select":false,"update":false},{"name":"notes","insert":false,"select":false,"update":false},{"name":"created_at","insert":false,"select":false,"update":false},{"name":"updated_at","insert":false,"select":false,"update":false},{"name":"deleted_at","insert":false,"select":false,"update":false}],"rolsuper":false,"role_name":"authenticated","table_name":"construction_clients","rolbypassrls":false,"schema_usage":true,"table_delete":false,"table_insert":false,"table_select":false,"table_update":false,"table_truncate":false,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"client_type","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"notes","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true},{"name":"deleted_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"construction_api","table_name":"construction_clients","rolbypassrls":false,"schema_usage":true,"table_delete":false,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":false,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"client_type","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"notes","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true},{"name":"deleted_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"postgres","table_name":"construction_clients","rolbypassrls":true,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":true},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"display_name","insert":true,"select":true,"update":true},{"name":"client_type","insert":true,"select":true,"update":true},{"name":"phone","insert":true,"select":true,"update":true},{"name":"email","insert":true,"select":true,"update":true},{"name":"notes","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true},{"name":"deleted_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"service_role","table_name":"construction_clients","rolbypassrls":true,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"active_role","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"timezone","insert":true,"select":true,"update":true},{"name":"country_id","insert":true,"select":true,"update":true},{"name":"city_id","insert":true,"select":true,"update":true},{"name":"profile_completion_score","insert":true,"select":true,"update":true},{"name":"trust_score","insert":true,"select":true,"update":true},{"name":"risk_score","insert":true,"select":true,"update":true},{"name":"status","insert":true,"select":true,"update":true},{"name":"last_seen_at","insert":true,"select":true,"update":true},{"name":"metadata","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"anon","table_name":"users","rolbypassrls":false,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"active_role","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"timezone","insert":true,"select":true,"update":true},{"name":"country_id","insert":true,"select":true,"update":true},{"name":"city_id","insert":true,"select":true,"update":true},{"name":"profile_completion_score","insert":true,"select":true,"update":true},{"name":"trust_score","insert":true,"select":true,"update":true},{"name":"risk_score","insert":true,"select":true,"update":true},{"name":"status","insert":true,"select":true,"update":true},{"name":"last_seen_at","insert":true,"select":true,"update":true},{"name":"metadata","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"authenticated","table_name":"users","rolbypassrls":false,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false},{"columns":[{"name":"id","insert":false,"select":true,"update":true},{"name":"tenant_id","insert":false,"select":true,"update":false},{"name":"active_role","insert":false,"select":false,"update":false},{"name":"language_code","insert":false,"select":false,"update":false},{"name":"timezone","insert":false,"select":false,"update":false},{"name":"country_id","insert":false,"select":false,"update":false},{"name":"city_id","insert":false,"select":false,"update":false},{"name":"profile_completion_score","insert":false,"select":false,"update":false},{"name":"trust_score","insert":false,"select":false,"update":false},{"name":"risk_score","insert":false,"select":false,"update":false},{"name":"status","insert":false,"select":true,"update":false},{"name":"last_seen_at","insert":false,"select":false,"update":false},{"name":"metadata","insert":false,"select":false,"update":false},{"name":"created_at","insert":false,"select":false,"update":false},{"name":"updated_at","insert":false,"select":false,"update":false}],"rolsuper":false,"role_name":"construction_api","table_name":"users","rolbypassrls":false,"schema_usage":true,"table_delete":false,"table_insert":false,"table_select":false,"table_update":false,"table_truncate":false,"owner_membership":false},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"active_role","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"timezone","insert":true,"select":true,"update":true},{"name":"country_id","insert":true,"select":true,"update":true},{"name":"city_id","insert":true,"select":true,"update":true},{"name":"profile_completion_score","insert":true,"select":true,"update":true},{"name":"trust_score","insert":true,"select":true,"update":true},{"name":"risk_score","insert":true,"select":true,"update":true},{"name":"status","insert":true,"select":true,"update":true},{"name":"last_seen_at","insert":true,"select":true,"update":true},{"name":"metadata","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"postgres","table_name":"users","rolbypassrls":true,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":true},{"columns":[{"name":"id","insert":true,"select":true,"update":true},{"name":"tenant_id","insert":true,"select":true,"update":true},{"name":"active_role","insert":true,"select":true,"update":true},{"name":"language_code","insert":true,"select":true,"update":true},{"name":"timezone","insert":true,"select":true,"update":true},{"name":"country_id","insert":true,"select":true,"update":true},{"name":"city_id","insert":true,"select":true,"update":true},{"name":"profile_completion_score","insert":true,"select":true,"update":true},{"name":"trust_score","insert":true,"select":true,"update":true},{"name":"risk_score","insert":true,"select":true,"update":true},{"name":"status","insert":true,"select":true,"update":true},{"name":"last_seen_at","insert":true,"select":true,"update":true},{"name":"metadata","insert":true,"select":true,"update":true},{"name":"created_at","insert":true,"select":true,"update":true},{"name":"updated_at","insert":true,"select":true,"update":true}],"rolsuper":false,"role_name":"service_role","table_name":"users","rolbypassrls":true,"schema_usage":true,"table_delete":true,"table_insert":true,"table_select":true,"table_update":true,"table_truncate":true,"owner_membership":false}],"incoming_client_references":[{"name":"fk_construction_activities_tenant_client","validated":true,"definition":"FOREIGN KEY (tenant_id, client_id) REFERENCES construction_clients(tenant_id, id)","table_name":"construction_activities","schema_name":"public"},{"name":"fk_construction_leads_tenant_converted_client","validated":true,"definition":"FOREIGN KEY (tenant_id, converted_client_id) REFERENCES construction_clients(tenant_id, id)","table_name":"construction_leads","schema_name":"public"},{"name":"fk_construction_projects_tenant_client","validated":true,"definition":"FOREIGN KEY (tenant_id, client_id) REFERENCES construction_clients(tenant_id, id)","table_name":"construction_projects","schema_name":"public"}]}$expected$::jsonb;
    snapshot_sql text := $snapshot_query$WITH targets AS (
    SELECT c.*, n.nspname AS schema_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname IN ('construction_clients', 'users')
), columns AS (
    SELECT t.relname AS table_name, a.attnum, a.attname AS name,
        pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
        NOT a.attnotnull AS nullable,
        pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS server_default,
        a.attidentity::text AS identity_kind,
        a.attgenerated::text AS generated_kind,
        a.attacl::text AS column_acl
    FROM targets t
    JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
    LEFT JOIN pg_catalog.pg_attrdef d
        ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attnum > 0 AND NOT a.attisdropped
), constraints AS (
    SELECT t.relname AS table_name, con.conname AS name,
        con.contype::text AS kind, con.convalidated AS validated,
        pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
    FROM targets t
    JOIN pg_catalog.pg_constraint con ON con.conrelid = t.oid
), indexes AS (
    SELECT t.relname AS table_name, cls.relname AS name,
        idx.indisvalid AS valid, idx.indisready AS ready,
        pg_catalog.pg_get_indexdef(idx.indexrelid) AS definition
    FROM targets t
    JOIN pg_catalog.pg_index idx ON idx.indrelid = t.oid
    JOIN pg_catalog.pg_class cls ON cls.oid = idx.indexrelid
), policies AS (
    SELECT t.relname AS table_name, p.polname AS name,
        p.polcmd::text AS command, p.polpermissive AS permissive,
        ARRAY(
            SELECT CASE WHEN role_oid = 0 THEN 'PUBLIC'
                ELSE pg_catalog.pg_get_userbyid(role_oid) END
            FROM unnest(p.polroles) AS role_oid ORDER BY 1
        ) AS roles,
        pg_catalog.pg_get_expr(p.polqual, p.polrelid) AS using_expression,
        pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) AS with_check
    FROM targets t
    JOIN pg_catalog.pg_policy p ON p.polrelid = t.oid
), triggers AS (
    SELECT t.relname AS table_name, trg.tgname AS name,
        trg.tgenabled::text AS enabled_mode,
        pg_catalog.pg_get_triggerdef(trg.oid, true) AS definition,
        pg_catalog.pg_get_functiondef(trg.tgfoid) AS function_definition
    FROM targets t
    JOIN pg_catalog.pg_trigger trg ON trg.tgrelid = t.oid
    WHERE NOT trg.tgisinternal
), role_privileges AS (
    SELECT t.relname AS table_name, r.rolname AS role_name,
        r.rolsuper, r.rolbypassrls,
        pg_catalog.pg_has_role(r.oid, t.relowner, 'MEMBER') AS owner_membership,
        pg_catalog.has_schema_privilege(r.oid, t.relnamespace, 'USAGE') AS schema_usage,
        pg_catalog.has_table_privilege(r.oid, t.oid, 'SELECT') AS table_select,
        pg_catalog.has_table_privilege(r.oid, t.oid, 'INSERT') AS table_insert,
        pg_catalog.has_table_privilege(r.oid, t.oid, 'UPDATE') AS table_update,
        pg_catalog.has_table_privilege(r.oid, t.oid, 'DELETE') AS table_delete,
        pg_catalog.has_table_privilege(r.oid, t.oid, 'TRUNCATE') AS table_truncate,
        (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'name', a.attname,
            'select', pg_catalog.has_column_privilege(r.oid, t.oid, a.attnum, 'SELECT'),
            'insert', pg_catalog.has_column_privilege(r.oid, t.oid, a.attnum, 'INSERT'),
            'update', pg_catalog.has_column_privilege(r.oid, t.oid, a.attnum, 'UPDATE')
        ) ORDER BY a.attnum), '[]'::jsonb)
         FROM pg_catalog.pg_attribute a
         WHERE a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
        ) AS columns
    FROM targets t CROSS JOIN pg_catalog.pg_roles r
    WHERE r.rolname IN ('construction_api', 'anon', 'authenticated', 'service_role', current_user)
), client_references AS (
    SELECT ns.nspname AS schema_name, cls.relname AS table_name,
        con.conname AS name, con.convalidated AS validated,
        pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
    FROM targets t
    JOIN pg_catalog.pg_constraint con ON con.confrelid = t.oid AND con.contype = 'f'
    JOIN pg_catalog.pg_class cls ON cls.oid = con.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE t.relname = 'construction_clients'
)
SELECT jsonb_build_object(
    'state', 'READ_ONLY_CON_C014_CLIENT_USER_LINK_STORAGE_PREFLIGHT',
    'role_name', current_user,
    'read_only', current_setting('transaction_read_only'),
    'expected_tables_found', (SELECT count(*) FROM targets),
    'tables', COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'schema', t.schema_name, 'name', t.relname, 'kind', t.relkind::text,
        'owner', pg_catalog.pg_get_userbyid(t.relowner),
        'rls_enabled', t.relrowsecurity, 'rls_forced', t.relforcerowsecurity,
        'table_acl', t.relacl::text,
        'columns', COALESCE((SELECT jsonb_agg(to_jsonb(c) - 'attnum' - 'table_name' ORDER BY c.attnum)
            FROM columns c WHERE c.table_name = t.relname), '[]'::jsonb),
        'constraints', COALESCE((SELECT jsonb_agg(to_jsonb(c) - 'table_name' ORDER BY c.name)
            FROM constraints c WHERE c.table_name = t.relname), '[]'::jsonb),
        'indexes', COALESCE((SELECT jsonb_agg(to_jsonb(i) - 'table_name' ORDER BY i.name)
            FROM indexes i WHERE i.table_name = t.relname), '[]'::jsonb),
        'policies', COALESCE((SELECT jsonb_agg(to_jsonb(p) - 'table_name' ORDER BY p.name)
            FROM policies p WHERE p.table_name = t.relname), '[]'::jsonb),
        'triggers', COALESCE((SELECT jsonb_agg(to_jsonb(g) - 'table_name' ORDER BY g.name)
            FROM triggers g WHERE g.table_name = t.relname), '[]'::jsonb)
    ) ORDER BY t.relname) FROM targets t), '[]'::jsonb),
    'role_privileges', COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.table_name, r.role_name)
        FROM role_privileges r), '[]'::jsonb),
    'incoming_client_references', COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.schema_name, r.table_name, r.name)
        FROM client_references r), '[]'::jsonb),
    'client_link_fields_found', COALESCE((SELECT jsonb_agg(c.name ORDER BY c.name)
        FROM columns c WHERE c.table_name = 'construction_clients'
        AND c.name IN ('linked_user_id', 'row_version')), '[]'::jsonb),
    'business_rows_read', false,
    'database_writes', false,
    'schema_or_acl_modified', false,
    'application_files_modified', false,
    'client_link_installed_or_tested', false,
    'next_step', 'REVIEW_ACTUAL_CLIENT_LINK_COLUMNS_CONSTRAINTS_AND_PRIVILEGES'
) AS con_c014_client_user_link_storage_preflight$snapshot_query$;
    client_oid oid;
    user_oid oid;
    linked_att smallint;
    version_att smallint;
    user_id_att smallint;
    field_count integer;
    phase integer;
    fresh boolean;
    snapshot jsonb;
    normalized jsonb;
    old_values jsonb;
    current_old_values jsonb;
    lifecycle_values jsonb;
    current_lifecycle_values jsonb;
    new_columns jsonb;
    expected_columns jsonb := '[
        {"name":"linked_user_id","type":"uuid","nullable":true,"server_default":null,"identity_kind":"","generated_kind":"","column_acl":null},
        {"name":"row_version","type":"bigint","nullable":false,"server_default":"1","identity_kind":"","generated_kind":"","column_acl":null}
    ]'::jsonb;
    checks jsonb;
BEGIN
    IF current_user <> 'postgres' OR current_setting('transaction_read_only') <> 'off' THEN
        RAISE EXCEPTION 'STOP: Run the whole file as postgres in a writable SQL Editor transaction.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = current_user AND rolbypassrls) THEN
        RAISE EXCEPTION 'STOP: Full row-preservation checks require the reviewed Platform owner runtime.';
    END IF;
    SELECT c.oid INTO client_oid FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'construction_clients' AND c.relkind = 'r'
      AND pg_catalog.pg_get_userbyid(c.relowner) = 'postgres';
    SELECT c.oid INTO user_oid FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'users' AND c.relkind = 'r'
      AND pg_catalog.pg_get_userbyid(c.relowner) = 'postgres';
    IF client_oid IS NULL OR user_oid IS NULL THEN
        RAISE EXCEPTION 'STOP: Reviewed canonical client/users tables are unavailable.';
    END IF;

    -- Keep both the catalog comparison and row preservation stable during DDL.
    LOCK TABLE public.users IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.construction_clients IN ACCESS EXCLUSIVE MODE;
    SELECT count(*) INTO field_count FROM pg_catalog.pg_attribute
    WHERE attrelid = client_oid AND attnum > 0 AND NOT attisdropped
      AND attname IN ('linked_user_id', 'row_version');
    IF field_count NOT IN (0, 2) THEN
        RAISE EXCEPTION 'STOP: Client link storage is partially present; no automatic repair.';
    END IF;
    fresh := field_count = 0;

    SELECT COALESCE(jsonb_agg(to_jsonb(c) - 'linked_user_id' - 'row_version' ORDER BY c.id), '[]'::jsonb)
    INTO old_values FROM public.construction_clients c;
    IF NOT fresh THEN
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', c.id,
            'linked_user_id', to_jsonb(c)->'linked_user_id',
            'row_version', to_jsonb(c)->'row_version') ORDER BY c.id), '[]'::jsonb)
        INTO lifecycle_values FROM public.construction_clients c;
    END IF;

    FOR phase IN 0..1 LOOP
        EXECUTE snapshot_sql INTO snapshot;
        SELECT jsonb_build_object(
            'tables', (
                SELECT jsonb_agg(CASE WHEN t->>'name' = 'construction_clients' THEN
                    jsonb_set(jsonb_set(t, '{columns}', COALESCE((
                        SELECT jsonb_agg(col ORDER BY ord)
                        FROM jsonb_array_elements(t->'columns') WITH ORDINALITY AS cols(col, ord)
                        WHERE col->>'name' NOT IN ('linked_user_id', 'row_version')
                    ), '[]'::jsonb)), '{constraints}', COALESCE((
                        SELECT jsonb_agg(con ORDER BY ord)
                        FROM jsonb_array_elements(t->'constraints') WITH ORDINALITY AS cons(con, ord)
                        WHERE con->>'name' NOT IN ('fk_construction_clients_linked_user', 'ck_construction_clients_row_version')
                    ), '[]'::jsonb))
                    ELSE t END ORDER BY t->>'name')
                FROM jsonb_array_elements(snapshot->'tables') AS tables(t)
            ),
            'role_privileges', (
                SELECT jsonb_agg(CASE WHEN r->>'table_name' = 'construction_clients' THEN
                    jsonb_set(r, '{columns}', COALESCE((
                        SELECT jsonb_agg(col ORDER BY ord)
                        FROM jsonb_array_elements(r->'columns') WITH ORDINALITY AS cols(col, ord)
                        WHERE col->>'name' NOT IN ('linked_user_id', 'row_version')
                    ), '[]'::jsonb)) ELSE r END ORDER BY r->>'table_name', r->>'role_name')
                FROM jsonb_array_elements(snapshot->'role_privileges') AS roles(r)
            ),
            'incoming_client_references', snapshot->'incoming_client_references'
        ) INTO normalized;
        IF normalized IS DISTINCT FROM expected THEN
            RAISE EXCEPTION 'STOP: Existing client/users columns, ACL, policies, triggers, indexes, constraints or references differ from the reviewed preflight.';
        END IF;

        IF fresh AND phase = 0 THEN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid = client_oid
                AND conname IN ('fk_construction_clients_linked_user', 'ck_construction_clients_row_version')) THEN
                RAISE EXCEPTION 'STOP: New constraint names are already used without their columns.';
            END IF;
            ALTER TABLE public.construction_clients
                ADD COLUMN linked_user_id uuid,
                ADD COLUMN row_version bigint NOT NULL DEFAULT 1,
                ADD CONSTRAINT fk_construction_clients_linked_user
                    FOREIGN KEY (linked_user_id) REFERENCES public.users(id)
                    ON UPDATE NO ACTION ON DELETE NO ACTION,
                ADD CONSTRAINT ck_construction_clients_row_version CHECK (row_version > 0);
        END IF;

        IF NOT fresh OR phase = 1 THEN
            SELECT COALESCE(jsonb_agg(to_jsonb(col) - 'table_name' - 'attnum' ORDER BY col.attnum), '[]'::jsonb)
            INTO new_columns FROM (
                SELECT a.attnum, a.attname AS name,
                    pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
                    NOT a.attnotnull AS nullable,
                    pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS server_default,
                    a.attidentity::text AS identity_kind, a.attgenerated::text AS generated_kind,
                    a.attacl::text AS column_acl
                FROM pg_catalog.pg_attribute a
                LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE a.attrelid = client_oid AND a.attnum > 0 AND NOT a.attisdropped
                  AND a.attname IN ('linked_user_id', 'row_version')
            ) col;
            IF new_columns IS DISTINCT FROM expected_columns THEN
                RAISE EXCEPTION 'STOP: Client link columns have unexpected types, defaults, nullability or column ACL.';
            END IF;
            SELECT attnum INTO linked_att FROM pg_catalog.pg_attribute
                WHERE attrelid = client_oid AND attname = 'linked_user_id' AND NOT attisdropped;
            SELECT attnum INTO version_att FROM pg_catalog.pg_attribute
                WHERE attrelid = client_oid AND attname = 'row_version' AND NOT attisdropped;
            SELECT attnum INTO user_id_att FROM pg_catalog.pg_attribute
                WHERE attrelid = user_oid AND attname = 'id' AND NOT attisdropped;
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                WHERE conrelid = client_oid AND conname = 'fk_construction_clients_linked_user'
                  AND contype = 'f' AND convalidated AND NOT condeferrable
                  AND conkey = ARRAY[linked_att] AND confrelid = user_oid
                  AND confkey = ARRAY[user_id_att] AND confdeltype = 'a' AND confupdtype = 'a'
                  AND confmatchtype = 's') THEN
                RAISE EXCEPTION 'STOP: linked_user_id must reference canonical users.id without tenant membership or delete cascade.';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                WHERE conrelid = client_oid AND conname = 'ck_construction_clients_row_version'
                  AND contype = 'c' AND convalidated AND conkey = ARRAY[version_att]
                  AND pg_catalog.pg_get_expr(conbin, conrelid) = '(row_version > 0)') THEN
                RAISE EXCEPTION 'STOP: Positive client row_version constraint differs.';
            END IF;
            -- Old indexes/unique constraints were compared unchanged above.
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_index
                WHERE indrelid = client_oid AND indisunique AND linked_att = ANY(indkey)) THEN
                RAISE EXCEPTION 'STOP: linked_user_id must not be unique.';
            END IF;
            IF EXISTS (
                SELECT 1 FROM jsonb_array_elements(snapshot->'role_privileges') AS roles(r)
                CROSS JOIN LATERAL jsonb_array_elements(r->'columns') AS cols(col)
                WHERE r->>'table_name' = 'construction_clients'
                  AND col->>'name' IN ('linked_user_id', 'row_version')
                  AND ((col->'select') IS DISTINCT FROM (r->'table_select')
                    OR (col->'insert') IS DISTINCT FROM (r->'table_insert')
                    OR (col->'update') IS DISTINCT FROM (r->'table_update'))
            ) THEN
                RAISE EXCEPTION 'STOP: New columns unexpectedly change the reviewed role access model.';
            END IF;
        END IF;
    END LOOP;

    SELECT COALESCE(jsonb_agg(to_jsonb(c) - 'linked_user_id' - 'row_version' ORDER BY c.id), '[]'::jsonb)
    INTO current_old_values FROM public.construction_clients c;
    IF current_old_values IS DISTINCT FROM old_values THEN
        RAISE EXCEPTION 'STOP: Existing client values changed; all DDL will roll back.';
    END IF;
    IF fresh THEN
        IF EXISTS (SELECT 1 FROM public.construction_clients
            WHERE linked_user_id IS NOT NULL OR row_version <> 1) THEN
            RAISE EXCEPTION 'STOP: Fresh client links must be NULL with initial version 1.';
        END IF;
    ELSE
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', c.id,
            'linked_user_id', to_jsonb(c)->'linked_user_id',
            'row_version', to_jsonb(c)->'row_version') ORDER BY c.id), '[]'::jsonb)
        INTO current_lifecycle_values FROM public.construction_clients c;
        IF current_lifecycle_values IS DISTINCT FROM lifecycle_values THEN
            RAISE EXCEPTION 'STOP: Repeat run changed an existing client link or version.';
        END IF;
    END IF;

    checks := jsonb_build_object(
        'nullable_canonical_user_fk_without_tenant_membership', true,
        'linked_user_id_not_unique', true,
        'positive_bigint_row_version_default_1', true,
        'existing_ten_column_values_preserved', true,
        'existing_users_and_client_catalog_security_preserved', true,
        'existing_roles_and_column_permissions_preserved', true,
        'existing_indexes_triggers_and_references_preserved', true,
        'repeat_run_preserves_existing_links_and_versions', true
    );
    PERFORM pg_catalog.set_config('app.con_c014_client_link_schema_result', jsonb_build_object(
        'state', CASE WHEN fresh THEN 'APPLIED' ELSE 'ALREADY_APPLIED' END,
        'table', 'public.construction_clients',
        'added_columns', jsonb_build_array('linked_user_id', 'row_version'),
        'checks', checks,
        'existing_clients', jsonb_array_length(old_values),
        'business_updates_or_deletes', 0,
        'client_links_created', 0,
        'confirmation_flow_implemented', false,
        'application_files_modified', false,
        'stage_1a_acceptance_claimed', false,
        'next_step', 'CON_C014_CLIENT_LINK_ORM_AND_VERSION_PREPARATION'
    )::text, true);
END;
$migration$;

SELECT current_setting('app.con_c014_client_link_schema_result')::jsonb
    AS con_c014_client_user_link_schema_result;
COMMIT;
