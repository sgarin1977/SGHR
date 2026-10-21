-- CON-C014: physical storage for the already prepared operator confirmation flow.
-- Run as postgres in Supabase SQL Editor. No users, links, tokens or audit rows
-- are inserted/updated/deleted. A mismatch aborts the whole transaction.
-- Repeat runs validate the existing object; they do not repair or overwrite it.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';

DO $migration$
DECLARE
    owner_oid oid;
    construction_oid oid;
    relation_oid oid;
    parent_names text[] := ARRAY['users','tenants','construction_clients',
        'event_logs','api_idempotency_records'];
    expected_columns jsonb := $columns${"client_id": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 3, "type": "uuid"}, "consumed_at": {"default": null, "generated": "", "identity": "", "nullable": true, "position": 11, "type": "timestamp with time zone"}, "expected_client_row_version": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 7, "type": "bigint"}, "expected_linked_user_id": {"default": null, "generated": "", "identity": "", "nullable": true, "position": 6, "type": "uuid"}, "expires_at": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 10, "type": "timestamp with time zone"}, "id": {"default": "gen_random_uuid()", "generated": "", "identity": "", "nullable": false, "position": 1, "type": "uuid"}, "initiated_by_user_id": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 4, "type": "uuid"}, "issued_at": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 9, "type": "timestamp with time zone"}, "row_version": {"default": "1", "generated": "", "identity": "", "nullable": false, "position": 13, "type": "bigint"}, "target_user_id": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 5, "type": "uuid"}, "tenant_id": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 2, "type": "uuid"}, "token_hash": {"default": null, "generated": "", "identity": "", "nullable": false, "position": 8, "type": "text"}, "updated_at": {"default": "now()", "generated": "", "identity": "", "nullable": false, "position": 12, "type": "timestamp with time zone"}}$columns$::jsonb;
    expected_constraints jsonb := $constraints${"ck_con_client_link_requests_base_version": {"columns": ["expected_client_row_version"], "deferrable": false, "expression": "expected_client_row_version>0", "initially_deferred": false, "kind": "c", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}, "ck_con_client_link_requests_consumed_at": {"columns": ["consumed_at", "expires_at", "issued_at"], "deferrable": false, "expression": "consumed_atISNULLORconsumed_at>=issued_atANDconsumed_at<expires_at", "initially_deferred": false, "kind": "c", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}, "ck_con_client_link_requests_expiry": {"columns": ["expires_at", "issued_at"], "deferrable": false, "expression": "expires_at>issued_at", "initially_deferred": false, "kind": "c", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}, "ck_con_client_link_requests_hash": {"columns": ["token_hash"], "deferrable": false, "expression": "lengthtoken_hash=64ANDtranslatetoken_hash,'0123456789abcdef',''=''", "initially_deferred": false, "kind": "c", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}, "ck_con_client_link_requests_version": {"columns": ["row_version"], "deferrable": false, "expression": "row_version>0", "initially_deferred": false, "kind": "c", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}, "construction_client_user_link_requests_pkey": {"columns": ["id"], "deferrable": false, "expression": null, "initially_deferred": false, "kind": "p", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}, "fk_con_client_link_requests_client": {"columns": ["tenant_id", "client_id"], "deferrable": false, "expression": null, "initially_deferred": false, "kind": "f", "match": "s", "on_delete": "a", "on_update": "a", "reference": "public.construction_clients", "reference_columns": ["tenant_id", "id"], "validated": true}, "fk_con_client_link_requests_initiator": {"columns": ["initiated_by_user_id"], "deferrable": false, "expression": null, "initially_deferred": false, "kind": "f", "match": "s", "on_delete": "a", "on_update": "a", "reference": "public.users", "reference_columns": ["id"], "validated": true}, "fk_con_client_link_requests_target": {"columns": ["target_user_id"], "deferrable": false, "expression": null, "initially_deferred": false, "kind": "f", "match": "s", "on_delete": "a", "on_update": "a", "reference": "public.users", "reference_columns": ["id"], "validated": true}, "uq_con_client_link_requests_hash": {"columns": ["token_hash"], "deferrable": false, "expression": null, "initially_deferred": false, "kind": "u", "match": null, "on_delete": null, "on_update": null, "reference": null, "reference_columns": null, "validated": true}}$constraints$::jsonb;
    expected_indexes jsonb := $indexes${"construction_client_user_link_requests_pkey": {"columns": ["id"], "method": "btree", "primary": true, "unique": true}, "ix_con_client_link_requests_client": {"columns": ["tenant_id", "client_id", "issued_at", "id"], "method": "btree", "primary": false, "unique": false}, "uq_con_client_link_requests_hash": {"columns": ["token_hash"], "method": "btree", "primary": false, "unique": true}}$indexes$::jsonb;
    actual jsonb;
    constraint_differences jsonb;
    before_metadata jsonb;
    after_metadata jsonb;
    tenant_rule text := 'tenant_id = nullif(current_setting(''app.current_tenant_id'', true), '''')::uuid';
    canonical_rule text;
    was_created boolean := false;
    policy record;
    blocked_role record;
BEGIN
    IF current_user <> 'postgres' OR current_setting('transaction_read_only') <> 'off'
       OR current_setting('session_replication_role') <> 'origin' THEN
        RAISE EXCEPTION 'STOP: Run as postgres in a writable transaction with normal triggers.';
    END IF;
    SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles WHERE rolname = 'postgres';
    SELECT oid INTO STRICT construction_oid FROM pg_catalog.pg_roles
        WHERE rolname = 'construction_api' AND NOT rolsuper AND NOT rolbypassrls;
    IF pg_catalog.pg_has_role(construction_oid, owner_oid, 'MEMBER') THEN
        RAISE EXCEPTION 'STOP: Construction runtime must not inherit the table owner.';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_roles
        WHERE rolname IN ('anon','authenticated','service_role')) <> 3 THEN
        RAISE EXCEPTION 'STOP: Expected Supabase client roles are missing.';
    END IF;
    IF NOT pg_catalog.has_schema_privilege(construction_oid, 'public', 'USAGE') THEN
        RAISE EXCEPTION 'STOP: Construction runtime cannot use public schema.';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('sghr:con-c014:client-user-link-confirmation-schema:v1', 0));
    IF (SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(parent_names) AND c.relkind='r'
          AND c.relowner=owner_oid AND c.relrowsecurity) <> cardinality(parent_names) THEN
        RAISE EXCEPTION 'STOP: Existing parent tables, owners or RLS differ from preflight.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint con
        WHERE con.conrelid='public.construction_clients'::regclass AND con.contype='u'
          AND con.convalidated AND NOT con.condeferrable
          AND ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY key(attnum,position)
              JOIN pg_catalog.pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=key.attnum
              ORDER BY key.position)=ARRAY['tenant_id','id']::text[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid='public.construction_clients'::regclass AND a.attname='row_version'
          AND a.atttypid='bigint'::regtype AND a.attnotnull AND NOT a.attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid='public.construction_clients'::regclass AND a.attname='linked_user_id'
          AND a.atttypid='uuid'::regtype AND NOT a.attnotnull AND NOT a.attisdropped
    ) THEN
        RAISE EXCEPTION 'STOP: Complete the canonical client link/version schema first.';
    END IF;

    
SELECT jsonb_build_object(
    'parents',COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'oid',c.oid,'name',c.relname,'owner',c.relowner,'acl',c.relacl::text,
        'rls',c.relrowsecurity,'forced',c.relforcerowsecurity,'options',c.reloptions,
        'columns',(SELECT jsonb_agg(jsonb_build_object('attribute',to_jsonb(a),
            'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
            ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0),
        'constraints',(SELECT jsonb_agg(to_jsonb(con) ORDER BY con.oid)
            FROM pg_catalog.pg_constraint con WHERE con.conrelid=c.oid),
        'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid)
            FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid),
        'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid)
            FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
        'indexes',(SELECT jsonb_agg(to_jsonb(i) ORDER BY i.indexrelid)
            FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid)
    ) ORDER BY c.relname) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(parent_names)),'[]'::jsonb),
    'schema',(SELECT to_jsonb(n) FROM pg_catalog.pg_namespace n WHERE n.nspname='public'),
    'roles',(SELECT jsonb_agg(jsonb_build_object('oid',r.oid,'name',r.rolname,
        'super',r.rolsuper,'bypass',r.rolbypassrls,'inherit',r.rolinherit,'login',r.rolcanlogin,
        'config',r.rolconfig) ORDER BY r.oid) FROM pg_catalog.pg_roles r),
    'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.roleid,m.member,m.grantor) FROM pg_catalog.pg_auth_members m),
    'default_privileges',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.oid) FROM pg_catalog.pg_default_acl d)
) INTO before_metadata;


    SELECT c.oid INTO relation_oid FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='construction_client_user_link_requests';
    IF relation_oid IS NULL THEN
        -- DDL is compiled directly from the current prepared ORM.
        CREATE TABLE public.construction_client_user_link_requests (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	tenant_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	initiated_by_user_id UUID NOT NULL, 
	target_user_id UUID NOT NULL, 
	expected_linked_user_id UUID, 
	expected_client_row_version BIGINT NOT NULL, 
	token_hash TEXT NOT NULL, 
	issued_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	row_version BIGINT DEFAULT 1 NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_con_client_link_requests_client FOREIGN KEY(tenant_id, client_id) REFERENCES public.construction_clients (tenant_id, id) ON DELETE NO ACTION ON UPDATE NO ACTION, 
	CONSTRAINT uq_con_client_link_requests_hash UNIQUE (token_hash), 
	CONSTRAINT ck_con_client_link_requests_hash CHECK (length(token_hash) = 64 AND translate(token_hash, '0123456789abcdef', '') = ''), 
	CONSTRAINT ck_con_client_link_requests_expiry CHECK (expires_at > issued_at), 
	CONSTRAINT ck_con_client_link_requests_consumed_at CHECK (consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at)), 
	CONSTRAINT ck_con_client_link_requests_base_version CHECK (expected_client_row_version > 0), 
	CONSTRAINT ck_con_client_link_requests_version CHECK (row_version > 0), 
	CONSTRAINT fk_con_client_link_requests_initiator FOREIGN KEY(initiated_by_user_id) REFERENCES public.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION, 
	CONSTRAINT fk_con_client_link_requests_target FOREIGN KEY(target_user_id) REFERENCES public.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION
);
        CREATE INDEX ix_con_client_link_requests_client ON public.construction_client_user_link_requests (tenant_id, client_id, issued_at, id);
        ALTER TABLE public.construction_client_user_link_requests ENABLE ROW LEVEL SECURITY;
        REVOKE ALL PRIVILEGES ON TABLE public.construction_client_user_link_requests
            FROM PUBLIC, anon, authenticated, service_role, construction_api;
        GRANT SELECT, INSERT ON TABLE public.construction_client_user_link_requests TO construction_api;
        GRANT UPDATE (consumed_at, row_version, updated_at)
            ON TABLE public.construction_client_user_link_requests TO construction_api;
        EXECUTE format('CREATE POLICY construction_client_link_requests_select ON public.construction_client_user_link_requests FOR SELECT TO construction_api USING (%s)', tenant_rule);
        EXECUTE format('CREATE POLICY construction_client_link_requests_insert ON public.construction_client_user_link_requests FOR INSERT TO construction_api WITH CHECK (%s)', tenant_rule);
        EXECUTE format('CREATE POLICY construction_client_link_requests_update ON public.construction_client_user_link_requests FOR UPDATE TO construction_api USING (%s) WITH CHECK (%s)', tenant_rule, tenant_rule);
        relation_oid := 'public.construction_client_user_link_requests'::regclass;
        was_created := true;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=relation_oid
        AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition
        AND c.relowner=owner_oid AND c.relrowsecurity AND NOT c.relforcerowsecurity
        AND c.reloptions IS NULL) THEN
        RAISE EXCEPTION 'STOP: Confirmation relation kind, owner, options or RLS differs.';
    END IF;
    SELECT jsonb_object_agg(a.attname, jsonb_build_object(
        'position',a.attnum,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
        'nullable',NOT a.attnotnull,'identity',a.attidentity::text,'generated',a.attgenerated::text,
        'default',replace(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'pg_catalog.','')) ORDER BY a.attname)
        INTO actual
    FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
        ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attrelid=relation_oid AND a.attnum>0 AND NOT a.attisdropped;
    IF actual IS DISTINCT FROM expected_columns THEN
        RAISE EXCEPTION 'STOP: Confirmation columns, defaults or nullability differ from ORM.';
    END IF;
    SELECT jsonb_object_agg(con.conname,jsonb_build_object(
        'kind',con.contype::text,'validated',con.convalidated,'deferrable',con.condeferrable,
        'initially_deferred',con.condeferred,
        'columns',ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY key(attnum,position)
            JOIN pg_catalog.pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=key.attnum
            ORDER BY CASE WHEN con.contype='c' THEN a.attname::text END, key.position),
        'expression',CASE WHEN con.contype='c' THEN regexp_replace(replace(
            pg_catalog.pg_get_expr(con.conbin,con.conrelid),'::text',''),'[[:space:]()]','','g') END,
        'reference',CASE WHEN con.contype='f' THEN ref_ns.nspname||'.'||ref.relname END,
        'reference_columns',CASE WHEN con.contype='f' THEN ARRAY(
            SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY key(attnum,position)
            JOIN pg_catalog.pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=key.attnum ORDER BY key.position) END,
        'on_update',CASE WHEN con.contype='f' THEN con.confupdtype::text END,
        'on_delete',CASE WHEN con.contype='f' THEN con.confdeltype::text END,
        'match',CASE WHEN con.contype='f' THEN con.confmatchtype::text END
    ) ORDER BY con.conname) INTO actual
    FROM pg_catalog.pg_constraint con
    LEFT JOIN pg_catalog.pg_class ref ON ref.oid=con.confrelid
    LEFT JOIN pg_catalog.pg_namespace ref_ns ON ref_ns.oid=ref.relnamespace
    WHERE con.conrelid=relation_oid;
    IF actual IS DISTINCT FROM expected_constraints THEN
        SELECT jsonb_agg(jsonb_build_object('constraint',COALESCE(observed.key,expected.key),
            'actual',observed.value,'expected',expected.value) ORDER BY COALESCE(observed.key,expected.key))
            INTO constraint_differences
        FROM jsonb_each(COALESCE(actual,'{}'::jsonb)) observed
        FULL JOIN jsonb_each(expected_constraints) expected ON observed.key=expected.key
        WHERE observed.value IS DISTINCT FROM expected.value;
        RAISE EXCEPTION 'STOP: Confirmation constraints differ from the ten ORM constraints.'
            USING DETAIL=constraint_differences::text;
    END IF;
    SELECT jsonb_object_agg(idx.relname,jsonb_build_object(
        'unique',i.indisunique,'primary',i.indisprimary,'method',am.amname,
        'columns',ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY key(attnum,position)
            LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=key.attnum ORDER BY key.position)
    ) ORDER BY idx.relname) INTO actual
    FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class idx ON idx.oid=i.indexrelid
    JOIN pg_catalog.pg_am am ON am.oid=idx.relam WHERE i.indrelid=relation_oid;
    IF actual IS DISTINCT FROM expected_indexes OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid=relation_oid
          AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indislive
               OR i.indnkeyatts<>i.indnatts OR i.indpred IS NOT NULL OR i.indexprs IS NOT NULL
               OR EXISTS(SELECT 1 FROM unnest(i.indoption) option_value WHERE option_value<>0))
    ) THEN
        RAISE EXCEPTION 'STOP: Confirmation indexes differ from the three ORM indexes.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid=relation_oid AND NOT tgisinternal)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite WHERE ev_class=relation_oid) THEN
        RAISE EXCEPTION 'STOP: Unexpected custom confirmation triggers or rules; preserve them.';
    END IF;

    -- Inspect explicit ACLs without manufacturing a zero-dimensional aclitem array.
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) permission
        WHERE c.oid=relation_oid AND permission.grantee<>owner_oid
          AND (permission.grantee<>construction_oid OR permission.grantor<>owner_oid
               OR permission.privilege_type NOT IN ('SELECT','INSERT') OR permission.is_grantable)
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) permission
        WHERE a.attrelid=relation_oid AND a.attnum>0 AND NOT a.attisdropped
          AND permission.grantee<>owner_oid
          AND (permission.grantee<>construction_oid OR permission.grantor<>owner_oid
               OR permission.privilege_type<>'UPDATE' OR permission.is_grantable
               OR a.attname NOT IN ('consumed_at','row_version','updated_at'))
    ) THEN
        RAISE EXCEPTION 'STOP: Unexpected non-owner table/column privileges or grant options.';
    END IF;
    IF NOT pg_catalog.has_table_privilege(construction_oid,relation_oid,'SELECT')
       OR NOT pg_catalog.has_table_privilege(construction_oid,relation_oid,'INSERT')
       OR pg_catalog.has_table_privilege(construction_oid,relation_oid,'UPDATE')
       OR pg_catalog.has_table_privilege(construction_oid,relation_oid,'DELETE')
       OR pg_catalog.has_table_privilege(construction_oid,relation_oid,'TRUNCATE')
       OR pg_catalog.has_table_privilege(construction_oid,relation_oid,'REFERENCES')
       OR pg_catalog.has_table_privilege(construction_oid,relation_oid,'TRIGGER')
       OR ARRAY(SELECT a.attname::text FROM pg_catalog.pg_attribute a
            WHERE a.attrelid=relation_oid AND a.attnum>0 AND NOT a.attisdropped
              AND pg_catalog.has_column_privilege(construction_oid,relation_oid,a.attnum,'UPDATE')
            ORDER BY a.attname)<>ARRAY['consumed_at','row_version','updated_at']::text[] THEN
        RAISE EXCEPTION 'STOP: Construction privileges must be SELECT/INSERT plus only three UPDATE columns.';
    END IF;
    FOR blocked_role IN SELECT oid,rolname FROM pg_catalog.pg_roles
        WHERE rolname IN ('anon','authenticated','service_role') LOOP
        IF pg_catalog.has_any_column_privilege(blocked_role.oid,relation_oid,'SELECT')
           OR pg_catalog.has_any_column_privilege(blocked_role.oid,relation_oid,'INSERT')
           OR pg_catalog.has_any_column_privilege(blocked_role.oid,relation_oid,'UPDATE')
           OR pg_catalog.has_any_column_privilege(blocked_role.oid,relation_oid,'REFERENCES')
           OR pg_catalog.has_table_privilege(blocked_role.oid,relation_oid,'DELETE')
           OR pg_catalog.has_table_privilege(blocked_role.oid,relation_oid,'TRUNCATE')
           OR pg_catalog.has_table_privilege(blocked_role.oid,relation_oid,'TRIGGER') THEN
            RAISE EXCEPTION 'STOP: Unexpected effective confirmation privilege for %.',blocked_role.rolname;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid=relation_oid)<>3 THEN
        RAISE EXCEPTION 'STOP: Exactly three role-scoped confirmation policies are required.';
    END IF;
    canonical_rule := lower(regexp_replace('tenant_id = (nullif(current_setting(''app.current_tenant_id''::text, true), ''''::text))::uuid','[[:space:]()]','','g'));
    FOR policy IN SELECT polname,polcmd,polroles,polpermissive,
        lower(regexp_replace(pg_catalog.pg_get_expr(polqual,polrelid),'[[:space:]()]','','g')) AS using_rule,
        lower(regexp_replace(pg_catalog.pg_get_expr(polwithcheck,polrelid),'[[:space:]()]','','g')) AS check_rule
        FROM pg_catalog.pg_policy WHERE polrelid=relation_oid LOOP
        IF policy.polroles IS DISTINCT FROM ARRAY[construction_oid]::oid[] OR NOT policy.polpermissive
           OR (
               (policy.polname='construction_client_link_requests_select' AND policy.polcmd='r'
                   AND policy.using_rule=canonical_rule AND policy.check_rule IS NULL)
               OR (policy.polname='construction_client_link_requests_insert' AND policy.polcmd='a'
                   AND policy.using_rule IS NULL AND policy.check_rule=canonical_rule)
               OR (policy.polname='construction_client_link_requests_update' AND policy.polcmd='w'
                   AND policy.using_rule=canonical_rule AND policy.check_rule=canonical_rule)
           ) IS NOT TRUE THEN
            RAISE EXCEPTION 'STOP: Confirmation policy role, command or tenant condition differs.';
        END IF;
    END LOOP;

    
SELECT jsonb_build_object(
    'parents',COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'oid',c.oid,'name',c.relname,'owner',c.relowner,'acl',c.relacl::text,
        'rls',c.relrowsecurity,'forced',c.relforcerowsecurity,'options',c.reloptions,
        'columns',(SELECT jsonb_agg(jsonb_build_object('attribute',to_jsonb(a),
            'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
            ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=c.oid AND a.attnum>0),
        'constraints',(SELECT jsonb_agg(to_jsonb(con) ORDER BY con.oid)
            FROM pg_catalog.pg_constraint con WHERE con.conrelid=c.oid),
        'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid)
            FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid),
        'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid)
            FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
        'indexes',(SELECT jsonb_agg(to_jsonb(i) ORDER BY i.indexrelid)
            FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid)
    ) ORDER BY c.relname) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(parent_names)),'[]'::jsonb),
    'schema',(SELECT to_jsonb(n) FROM pg_catalog.pg_namespace n WHERE n.nspname='public'),
    'roles',(SELECT jsonb_agg(jsonb_build_object('oid',r.oid,'name',r.rolname,
        'super',r.rolsuper,'bypass',r.rolbypassrls,'inherit',r.rolinherit,'login',r.rolcanlogin,
        'config',r.rolconfig) ORDER BY r.oid) FROM pg_catalog.pg_roles r),
    'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.roleid,m.member,m.grantor) FROM pg_catalog.pg_auth_members m),
    'default_privileges',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.oid) FROM pg_catalog.pg_default_acl d)
) INTO after_metadata;

    IF before_metadata IS DISTINCT FROM after_metadata THEN
        RAISE EXCEPTION 'STOP: Existing metadata changed; roll back this entire migration.';
    END IF;
    PERFORM pg_catalog.set_config('app.con_c014_client_confirmation_schema_result', jsonb_build_object(
        'state',CASE WHEN was_created THEN 'APPLIED' ELSE 'ALREADY_APPLIED' END,
        'table','public.construction_client_user_link_requests','column_count',13,
        'constraint_count',10,'index_count',3,
        'update_columns',jsonb_build_array('consumed_at','row_version','updated_at'),
        'checks',jsonb_build_object(
            'exact_orm_columns_defaults_and_constraints',true,'three_valid_ready_orm_indexes',true,
            'hash_only_token_storage',true,'expiry_explicit_without_default_lifetime',true,
            'three_current_tenant_role_policies',true,'runtime_no_superuser_bypass_or_owner_membership',true,
            'no_table_wide_update_delete_or_truncate',true,'no_browser_service_or_public_privileges',true,
            'existing_parent_columns_acl_rls_policies_triggers_indexes_unchanged',true,
            'roles_memberships_schema_and_default_privileges_unchanged',true),
        'business_rows_modified',0,'confirmation_tokens_issued',0,
        'client_links_created',0,'application_files_modified',false,
        'native_workflows_or_tenant_behavior_tested',false,'stage_1a_acceptance_claimed',false,
        'next_step','READ_ONLY_CONFIRMATION_SCHEMA_AND_REPOSITORY_QUERY_REVALIDATION'
    )::text,true);
END;
$migration$;

SELECT current_setting('app.con_c014_client_confirmation_schema_result')::jsonb
    AS con_c014_client_confirmation_schema_result;
COMMIT;
