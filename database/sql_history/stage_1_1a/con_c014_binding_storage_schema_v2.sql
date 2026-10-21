-- Revision 2: fix PostgreSQL 17 type-specific connoinherit validation.
-- CON-C014: guarded, transactional storage migration.
-- Run the entire file in Supabase SQL Editor as its existing postgres role.
-- Only public.platform_telegram_binding_tokens and its own ACL/RLS are created.
-- No role, default privilege, existing table or business row is changed.
-- No public PostgREST access; Platform runtime acceptance is a separate step.
BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';
SET LOCAL search_path = pg_catalog, public;
SET LOCAL intervalstyle = 'postgres';

DO $migration$
DECLARE
    target_oid oid;
    owner_oid oid;
    dependency_oid oid;
    created_table boolean := false;
    relation_record record;
    acl_record record;
    check_record record;
    stage integer;
    baseline jsonb;
    snapshot jsonb;
    checks jsonb;
    result jsonb;
    expected_columns jsonb := '[
        {"name":"id","type":"uuid","nullable":false,"default":"gen_random_uuid()"},
        {"name":"user_id","type":"uuid","nullable":false,"default":null},
        {"name":"tenant_id","type":"uuid","nullable":false,"default":null},
        {"name":"initiated_by_user_id","type":"uuid","nullable":false,"default":null},
        {"name":"token_hash","type":"text","nullable":false,"default":null},
        {"name":"issued_at","type":"timestamp with time zone","nullable":false,"default":null},
        {"name":"expires_at","type":"timestamp with time zone","nullable":false,"default":null},
        {"name":"consumed_at","type":"timestamp with time zone","nullable":true,"default":null},
        {"name":"revoked_at","type":"timestamp with time zone","nullable":true,"default":null},
        {"name":"updated_at","type":"timestamp with time zone","nullable":false,"default":"now()"},
        {"name":"row_version","type":"bigint","nullable":false,"default":"1"}
    ]'::jsonb;
BEGIN
    IF current_user <> 'postgres' OR current_setting('transaction_read_only') <> 'off' THEN
        RAISE EXCEPTION 'STOP: Run this schema migration as the existing SQL Editor postgres role in a writable transaction.';
    END IF;
    SELECT r.oid INTO owner_oid FROM pg_catalog.pg_roles r WHERE r.rolname = 'postgres';
    IF owner_oid IS NULL OR NOT pg_catalog.has_schema_privilege('public', 'CREATE') THEN
        RAISE EXCEPTION 'STOP: Expected existing owner or schema permission is unavailable.';
    END IF;
    IF pg_catalog.to_regprocedure('gen_random_uuid()') IS NULL THEN
        RAISE EXCEPTION 'STOP: Existing UUID generation function is unavailable.';
    END IF;

    -- Use existing canonical Platform entities, without granting new access.
    FOR relation_record IN
        SELECT name FROM (VALUES ('users'), ('tenants'), ('user_accounts'),
            ('api_idempotency_records'), ('event_logs')) AS wanted(name)
    LOOP
        SELECT c.oid INTO dependency_oid
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = relation_record.name
          AND c.relkind = 'r' AND c.relowner = owner_oid;
        IF dependency_oid IS NULL THEN
            RAISE EXCEPTION 'STOP: Expected public.% table owned by postgres is unavailable.', relation_record.name;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_attribute a
            WHERE a.attrelid = dependency_oid AND a.attname = 'id'
              AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull
              AND a.atttypid = 'uuid'::regtype
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attname = 'id'
            WHERE c.conrelid = dependency_oid AND c.contype = 'p'
              AND c.conkey = ARRAY[a.attnum]::smallint[] AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'STOP: Canonical UUID primary key changed on public.%.', relation_record.name;
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i
        WHERE i.indrelid = 'public.user_accounts'::regclass
          AND i.indisunique AND i.indisvalid AND i.indisready AND i.indimmediate
          AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts = 2
          AND (SELECT array_agg(a.attname::text ORDER BY a.attname::text)
               FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
               WHERE k.ord <= i.indnkeyatts)
              = ARRAY['platform', 'platform_user_id']::text[]
    ) THEN
        RAISE EXCEPTION 'STOP: Canonical identity ON CONFLICT uniqueness is unavailable.';
    END IF;

    -- Snapshot existing objects before/after DDL. New FK internal triggers on
    -- their parents are expected; existing custom triggers must not change.
    FOR stage IN 1..2 LOOP
        SELECT jsonb_build_object(
            'tables', (SELECT jsonb_agg(jsonb_build_object(
                'oid', c.oid, 'name', c.relname, 'kind', c.relkind::text,
                'owner', c.relowner, 'acl', c.relacl::text,
                'rls_enabled', c.relrowsecurity, 'rls_forced', c.relforcerowsecurity,
                'columns', (SELECT jsonb_agg(jsonb_build_object(
                    'attribute', to_jsonb(a),
                    'default', pg_catalog.pg_get_expr(d.adbin, d.adrelid)
                ) ORDER BY a.attnum)
                    FROM pg_catalog.pg_attribute a
                    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                    WHERE a.attrelid = c.oid AND a.attnum > 0),
                'constraints', (SELECT jsonb_agg(to_jsonb(k) ORDER BY k.oid)
                    FROM pg_catalog.pg_constraint k WHERE k.conrelid = c.oid),
                'indexes', (SELECT jsonb_agg(to_jsonb(i) ORDER BY i.indexrelid)
                    FROM pg_catalog.pg_index i WHERE i.indrelid = c.oid),
                'policies', (SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid)
                    FROM pg_catalog.pg_policy p WHERE p.polrelid = c.oid),
                'custom_triggers', (SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid)
                    FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal)
            ) ORDER BY c.relname)
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname IN (
                    'users', 'tenants', 'user_accounts', 'api_idempotency_records', 'event_logs')),
            'public_schema', (SELECT to_jsonb(n) FROM pg_catalog.pg_namespace n WHERE n.nspname = 'public'),
            'default_privileges', (SELECT jsonb_agg(to_jsonb(d) ORDER BY d.oid) FROM pg_catalog.pg_default_acl d),
            'roles', (SELECT jsonb_agg(to_jsonb(r) ORDER BY r.oid) FROM pg_catalog.pg_roles r),
            'role_memberships', (SELECT jsonb_agg(to_jsonb(m) ORDER BY m.oid) FROM pg_catalog.pg_auth_members m)
        ) INTO snapshot;
        IF stage = 1 THEN
            baseline := snapshot;
            SELECT c.oid INTO target_oid FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'platform_telegram_binding_tokens';

            IF target_oid IS NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname IN (
                        'platform_telegram_binding_tokens_pkey',
                        'uq_platform_telegram_binding_tokens_hash',
                        'ix_platform_telegram_binding_tokens_user_issued',
                        'ix_platform_telegram_binding_tokens_expiry')
                ) OR EXISTS (
                    SELECT 1 FROM pg_catalog.pg_type t
                    JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                    WHERE n.nspname = 'public' AND t.typname = 'platform_telegram_binding_tokens'
                ) THEN
                    RAISE EXCEPTION 'STOP: Binding storage object name is already occupied; no object will be replaced.';
                END IF;

                CREATE TABLE public.platform_telegram_binding_tokens (
                    id uuid NOT NULL DEFAULT gen_random_uuid(),
                    user_id uuid NOT NULL,
                    tenant_id uuid NOT NULL,
                    initiated_by_user_id uuid NOT NULL,
                    token_hash text NOT NULL,
                    issued_at timestamptz NOT NULL,
                    expires_at timestamptz NOT NULL,
                    consumed_at timestamptz,
                    revoked_at timestamptz,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    row_version bigint NOT NULL DEFAULT 1,
                    CONSTRAINT platform_telegram_binding_tokens_pkey PRIMARY KEY (id),
                    CONSTRAINT platform_telegram_binding_tokens_user_id_fkey
                        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
                    CONSTRAINT platform_telegram_binding_tokens_tenant_id_fkey
                        FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE,
                    CONSTRAINT platform_telegram_binding_tokens_initiated_by_user_id_fkey
                        FOREIGN KEY (initiated_by_user_id) REFERENCES public.users(id) ON DELETE CASCADE,
                    CONSTRAINT uq_platform_telegram_binding_tokens_hash UNIQUE (token_hash),
                    CONSTRAINT ck_platform_telegram_binding_tokens_hash
                        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
                    CONSTRAINT ck_platform_telegram_binding_tokens_ttl
                        CHECK (expires_at = issued_at + interval '15 minutes'),
                    CONSTRAINT ck_platform_telegram_binding_tokens_consumed_at
                        CHECK (consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at)),
                    CONSTRAINT ck_platform_telegram_binding_tokens_revoked_at
                        CHECK (revoked_at IS NULL OR revoked_at >= issued_at),
                    CONSTRAINT ck_platform_telegram_binding_tokens_row_version CHECK (row_version > 0)
                );
                CREATE INDEX ix_platform_telegram_binding_tokens_user_issued
                    ON public.platform_telegram_binding_tokens (user_id, issued_at, id);
                CREATE INDEX ix_platform_telegram_binding_tokens_expiry
                    ON public.platform_telegram_binding_tokens (expires_at);
                ALTER TABLE public.platform_telegram_binding_tokens ENABLE ROW LEVEL SECURITY;
                target_oid := 'public.platform_telegram_binding_tokens'::regclass;

                -- Revoke inherited creation-time default grants on THIS new
                -- table only. Do not change ALTER DEFAULT PRIVILEGES or roles.
                REVOKE ALL PRIVILEGES ON TABLE public.platform_telegram_binding_tokens FROM PUBLIC;
                FOR acl_record IN
                    SELECT DISTINCT x.grantee
                    FROM pg_catalog.pg_class c
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                        CASE WHEN cardinality(c.relacl) > 0 THEN c.relacl
                             ELSE NULL::aclitem[] END) x
                    WHERE c.oid = target_oid AND x.grantee NOT IN (0, owner_oid)
                LOOP
                    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.platform_telegram_binding_tokens FROM %I',
                        pg_catalog.pg_get_userbyid(acl_record.grantee));
                END LOOP;
                created_table := true;
            END IF;
        ELSE
            IF snapshot IS DISTINCT FROM baseline THEN
                RAISE EXCEPTION 'STOP: Existing columns, ACL, RLS, policies, custom triggers, roles or defaults changed; transaction rolled back.';
            END IF;
        END IF;
    END LOOP;

    -- An already existing table is verified, never silently repaired.
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = target_oid
          AND c.relkind = 'r' AND c.relowner = owner_oid
          AND c.relrowsecurity AND NOT c.relforcerowsecurity
          AND NOT c.relispartition AND c.reloftype = 0
    ) THEN
        RAISE EXCEPTION 'STOP: Binding relation owner, kind or RLS differs from the private Platform storage contract.';
    END IF;
    SELECT jsonb_agg(jsonb_build_object(
        'name', a.attname, 'type', pg_catalog.format_type(a.atttypid, a.atttypmod),
        'nullable', NOT a.attnotnull, 'default', pg_catalog.pg_get_expr(d.adbin, d.adrelid)
    ) ORDER BY a.attnum) INTO snapshot
    FROM pg_catalog.pg_attribute a
    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attrelid = target_oid AND a.attnum > 0 AND NOT a.attisdropped;
    IF snapshot IS DISTINCT FROM expected_columns OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid = target_oid AND a.attnum > 0
          AND (a.attisdropped OR a.attidentity <> '' OR a.attgenerated <> '' OR a.atthasmissing)
    ) THEN
        RAISE EXCEPTION 'STOP: Binding columns, types, nullability or defaults differ from the installed ORM.';
    END IF;
    -- PostgreSQL 17: ordinary local PRIMARY/UNIQUE/FOREIGN KEY constraints
    -- have connoinherit=true; the model's CHECK constraints are inheritable.
    -- Reject inherited/partition constraints and validate each type explicitly.
    IF (SELECT count(*) FROM pg_catalog.pg_constraint c WHERE c.conrelid = target_oid) <> 10
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.conrelid = target_oid
           AND (NOT c.convalidated OR c.condeferrable OR c.condeferred
               OR NOT c.conislocal OR c.coninhcount <> 0 OR c.conparentid <> 0
               OR c.contype NOT IN ('c', 'p', 'u', 'f')
               OR (c.contype = 'c' AND c.connoinherit)
               OR (c.contype IN ('p', 'u', 'f') AND NOT c.connoinherit))) THEN
        RAISE EXCEPTION USING
            MESSAGE = 'STOP: Binding constraint count or type-specific validation flags differ from the ORM.',
            DETAIL = COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'name', c.conname, 'kind', c.contype::text,
                'validated', c.convalidated, 'deferrable', c.condeferrable,
                'initially_deferred', c.condeferred, 'local', c.conislocal,
                'inheritance_count', c.coninhcount, 'parent', c.conparentid,
                'non_inheritable', c.connoinherit
            ) ORDER BY c.conname)::text FROM pg_catalog.pg_constraint c
                WHERE c.conrelid = target_oid), '[]');
    END IF;

    -- Exact native parse/deparse form, preserving expression parentheses.
    FOR check_record IN
        SELECT * FROM (VALUES
            ('ck_platform_telegram_binding_tokens_hash',
                '(token_hash ~ ''^[0-9a-f]{64}$''::text)'),
            ('ck_platform_telegram_binding_tokens_ttl',
                '(expires_at = (issued_at + ''00:15:00''::interval))'),
            ('ck_platform_telegram_binding_tokens_consumed_at',
                '((consumed_at IS NULL) OR ((consumed_at >= issued_at) AND (consumed_at < expires_at)))'),
            ('ck_platform_telegram_binding_tokens_revoked_at',
                '((revoked_at IS NULL) OR (revoked_at >= issued_at))'),
            ('ck_platform_telegram_binding_tokens_row_version', '(row_version > 0)')
        ) AS expected(name, expression)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            WHERE c.conrelid = target_oid AND c.contype = 'c' AND c.conname = check_record.name
              AND regexp_replace(pg_catalog.pg_get_expr(c.conbin, c.conrelid, false), '[[:space:]]', '', 'g')
                  = regexp_replace(check_record.expression, '[[:space:]]', '', 'g')
        ) THEN
            RAISE EXCEPTION 'STOP: Binding check constraint % differs from the ORM.', check_record.name;
        END IF;
    END LOOP;

    FOR check_record IN
        SELECT * FROM (VALUES
            ('platform_telegram_binding_tokens_pkey', 'p', 'id'),
            ('uq_platform_telegram_binding_tokens_hash', 'u', 'token_hash')
        ) AS expected(name, kind, column_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attname = check_record.column_name
            JOIN pg_catalog.pg_index i ON i.indexrelid = c.conindid
            WHERE c.conrelid = target_oid AND c.conname = check_record.name
              AND c.contype::text = check_record.kind AND c.conkey = ARRAY[a.attnum]::smallint[]
              AND i.indisunique AND i.indisvalid AND i.indisready AND i.indimmediate
              AND i.indnatts = 1 AND i.indnkeyatts = 1
              AND i.indpred IS NULL AND i.indexprs IS NULL
        ) THEN
            RAISE EXCEPTION 'STOP: Binding primary/unique constraint % differs from the ORM.', check_record.name;
        END IF;
    END LOOP;
    FOR check_record IN
        SELECT * FROM (VALUES
            ('platform_telegram_binding_tokens_user_id_fkey', 'user_id', 'users'),
            ('platform_telegram_binding_tokens_tenant_id_fkey', 'tenant_id', 'tenants'),
            ('platform_telegram_binding_tokens_initiated_by_user_id_fkey', 'initiated_by_user_id', 'users')
        ) AS expected(name, column_name, parent_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attname = check_record.column_name
            JOIN pg_catalog.pg_class parent ON parent.oid = c.confrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = parent.relnamespace
            JOIN pg_catalog.pg_attribute parent_a ON parent_a.attrelid = parent.oid AND parent_a.attname = 'id'
            WHERE c.conrelid = target_oid AND c.conname = check_record.name AND c.contype = 'f'
              AND c.conkey = ARRAY[a.attnum]::smallint[] AND c.confkey = ARRAY[parent_a.attnum]::smallint[]
              AND n.nspname = 'public' AND parent.relname = check_record.parent_name
              AND c.confdeltype = 'c' AND c.confupdtype = 'a' AND c.confmatchtype = 's'
        ) THEN
            RAISE EXCEPTION 'STOP: Binding foreign key % differs from the ORM.', check_record.name;
        END IF;
    END LOOP;

    IF (SELECT count(*) FROM pg_catalog.pg_index i WHERE i.indrelid = target_oid) <> 4 THEN
        RAISE EXCEPTION 'STOP: Unexpected binding index count.';
    END IF;
    FOR check_record IN
        SELECT * FROM (VALUES
            ('ix_platform_telegram_binding_tokens_user_issued', ARRAY['user_id','issued_at','id']::text[]),
            ('ix_platform_telegram_binding_tokens_expiry', ARRAY['expires_at']::text[])
        ) AS expected(name, columns)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_index i
            JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_catalog.pg_am am ON am.oid = ic.relam
            WHERE i.indrelid = target_oid AND ic.relname = check_record.name
              AND i.indisvalid AND i.indisready AND NOT i.indisunique
              AND i.indpred IS NULL AND i.indexprs IS NULL AND am.amname = 'btree'
              AND i.indnatts = cardinality(check_record.columns)
              AND i.indnkeyatts = cardinality(check_record.columns)
              AND (SELECT array_agg(a.attname::text ORDER BY k.ord)
                   FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS k(attnum, ord)
                   JOIN pg_catalog.pg_attribute a ON a.attrelid = target_oid AND a.attnum = k.attnum)
                  = check_record.columns
              AND NOT EXISTS (SELECT 1 FROM unnest(i.indoption::smallint[]) AS opt(value) WHERE opt.value <> 0)
        ) THEN
            RAISE EXCEPTION 'STOP: Binding index % differs from the ORM.', check_record.name;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid = target_oid)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = target_oid AND NOT t.tgisinternal)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid = target_oid OR i.inhparent = target_oid)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite r WHERE r.ev_class = target_oid) THEN
        RAISE EXCEPTION 'STOP: Unexpected binding policy, custom trigger, inheritance or rule.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            CASE WHEN c.relacl IS NULL THEN pg_catalog.acldefault('r', c.relowner)
                 WHEN cardinality(c.relacl) > 0 THEN c.relacl ELSE NULL::aclitem[] END) x
        WHERE c.oid = target_oid AND x.grantee <> owner_oid
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            CASE WHEN cardinality(a.attacl) > 0 THEN a.attacl ELSE NULL::aclitem[] END) x
        WHERE a.attrelid = target_oid AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        RAISE EXCEPTION 'STOP: Unexpected non-owner table ACL or column ACL on binding storage.';
    END IF;
    FOR relation_record IN
        SELECT r.oid, r.rolname FROM pg_catalog.pg_roles r
        WHERE r.rolname IN ('anon', 'authenticated', 'service_role', 'construction_api')
    LOOP
        IF pg_catalog.has_table_privilege(relation_record.oid, target_oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
           OR pg_catalog.has_any_column_privilege(relation_record.oid, target_oid, 'SELECT,INSERT,UPDATE,REFERENCES')
           OR pg_catalog.pg_has_role(relation_record.oid, owner_oid, 'USAGE') THEN
            RAISE EXCEPTION 'STOP: Unexpected effective binding storage access for role %.', relation_record.rolname;
        END IF;
    END LOOP;

    checks := jsonb_build_object(
        'exact_orm_columns_types_nullability_defaults', true,
        'ten_exact_validated_orm_constraints', true,
        'four_valid_ready_indexes', true,
        'hash_only_secret_storage', true,
        'persisted_user_tenant_initiator_context', true,
        'table_owned_by_postgres', true,
        'rls_enabled_no_client_policies', true,
        'no_browser_service_role_or_construction_grants', true,
        'no_custom_triggers', true,
        'existing_columns_acl_rls_policies_custom_triggers_unchanged', true,
        'roles_memberships_and_default_privileges_unchanged', true
    );
    result := jsonb_build_object(
        'state', CASE WHEN created_table THEN 'APPLIED' ELSE 'ALREADY_APPLIED' END,
        'table', 'public.platform_telegram_binding_tokens',
        'column_count', 11,
        'checks', checks,
        'business_rows_modified', 0,
        'existing_tables_modified', false,
        'application_files_modified', false,
        'canonical_identity_table', 'public.user_accounts',
        'platform_runtime_connection_verified', false,
        'native_workflows_or_rls_behavior_tested', false,
        'public_telegram_or_storage_operations', false,
        'stage_1a_acceptance_claimed', false,
        'next_step', 'READ_ONLY_PLATFORM_RUNTIME_ROLE_AND_BINDING_QUERY_REVALIDATION'
    );
    PERFORM pg_catalog.set_config('con_c014.binding_schema_result', result::text, true);
END;
$migration$;

SELECT current_setting('con_c014.binding_schema_result')::jsonb AS telegram_binding_storage_schema_result;
COMMIT;
