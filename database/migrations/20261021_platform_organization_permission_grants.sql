-- Run explicitly in Supabase SQL Editor. Creates storage, never assignments.
-- The permission registry entry has already been registered separately.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog, public;

DO $storage$
DECLARE
    relation_oid oid;
    repeat_run boolean;
    column_names text[];
    column_types oid[];
    required_flags boolean[];
    constraint_names text[];
    fk record;
BEGIN
    IF current_user <> 'postgres' OR current_setting('transaction_read_only') <> 'off' THEN
        RAISE EXCEPTION 'STOP: Run as the reviewed Platform migration role postgres in a write transaction.';
    END IF;
    IF to_regclass('public.permissions') IS NULL OR to_regclass('public.users') IS NULL
       OR to_regclass('public.tenants') IS NULL THEN
        RAISE EXCEPTION 'STOP: Existing canonical Platform registry, users and tenants are required.';
    END IF;
    LOCK TABLE public.permissions IN SHARE MODE;
    IF (SELECT count(*) FROM public.permissions
        WHERE code = 'platform.organization.memberships.manage') <> 1 THEN
        RAISE EXCEPTION 'STOP: The existing membership permission must be registered exactly once.';
    END IF;
    relation_oid := to_regclass('public.platform_organization_permission_grants');
    repeat_run := relation_oid IS NOT NULL;
    IF NOT repeat_run THEN
        CREATE TABLE public.platform_organization_permission_grants (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            permission_code text NOT NULL,
            status text NOT NULL DEFAULT 'active',
            row_version bigint NOT NULL DEFAULT 1,
            granted_by_user_id uuid NOT NULL,
            granted_at timestamptz NOT NULL,
            revoked_by_user_id uuid,
            revoked_at timestamptz,
            CONSTRAINT platform_organization_permission_grants_pkey PRIMARY KEY (id),
            CONSTRAINT pog_tenant_fk FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT,
            CONSTRAINT pog_user_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT,
            CONSTRAINT pog_permission_fk FOREIGN KEY (permission_code) REFERENCES public.permissions(code) ON DELETE RESTRICT,
            CONSTRAINT pog_granter_fk FOREIGN KEY (granted_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT,
            CONSTRAINT pog_revoker_fk FOREIGN KEY (revoked_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT,
            CONSTRAINT pog_code_ck CHECK (permission_code = 'platform.organization.memberships.manage'),
            CONSTRAINT pog_version_ck CHECK (row_version > 0),
            CONSTRAINT pog_lifecycle_ck CHECK (
                (status = 'active' AND revoked_by_user_id IS NULL AND revoked_at IS NULL)
                OR (status = 'revoked' AND revoked_by_user_id IS NOT NULL
                    AND revoked_at IS NOT NULL AND revoked_at >= granted_at AND row_version >= 2)
            )
        );
        ALTER TABLE public.platform_organization_permission_grants OWNER TO postgres;
        CREATE UNIQUE INDEX uq_pog_active_user_permission
            ON public.platform_organization_permission_grants (tenant_id, user_id, permission_code)
            WHERE status = 'active';
        CREATE INDEX ix_pog_tenant_user ON public.platform_organization_permission_grants (tenant_id, user_id, id);
        ALTER TABLE public.platform_organization_permission_grants ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON public.platform_organization_permission_grants FROM PUBLIC, anon, authenticated, service_role, construction_api;
        relation_oid := 'public.platform_organization_permission_grants'::regclass;
    END IF;
    LOCK TABLE public.platform_organization_permission_grants IN SHARE ROW EXCLUSIVE MODE;
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid = relation_oid AND relkind = 'r'
        AND relowner = (SELECT oid FROM pg_roles WHERE rolname = 'postgres')
        AND relrowsecurity AND NOT relforcerowsecurity) THEN
        RAISE EXCEPTION 'STOP: Unexpected permission storage ownership, relation kind or RLS flags.';
    END IF;
    SELECT array_agg(a.attname::text ORDER BY a.attnum), array_agg(a.atttypid ORDER BY a.attnum),
           array_agg(a.attnotnull ORDER BY a.attnum)
    INTO column_names, column_types, required_flags
    FROM pg_attribute a WHERE a.attrelid = relation_oid AND a.attnum > 0 AND NOT a.attisdropped;
    IF column_names IS DISTINCT FROM ARRAY['id','tenant_id','user_id','permission_code','status','row_version',
            'granted_by_user_id','granted_at','revoked_by_user_id','revoked_at']::text[]
       OR column_types IS DISTINCT FROM ARRAY['uuid'::regtype::oid,'uuid'::regtype::oid,'uuid'::regtype::oid,
            'text'::regtype::oid,'text'::regtype::oid,'int8'::regtype::oid,'uuid'::regtype::oid,
            'timestamptz'::regtype::oid,'uuid'::regtype::oid,'timestamptz'::regtype::oid]::oid[]
       OR required_flags IS DISTINCT FROM ARRAY[true,true,true,true,true,true,true,true,false,false] THEN
        RAISE EXCEPTION 'STOP: Explicit permission columns differ from the reviewed repository contract.';
    END IF;
    SELECT array_agg(conname::text ORDER BY conname) INTO constraint_names
    FROM pg_constraint WHERE conrelid = relation_oid;
    IF constraint_names IS DISTINCT FROM ARRAY[
        'platform_organization_permission_grants_pkey','pog_code_ck','pog_granter_fk','pog_lifecycle_ck',
        'pog_permission_fk','pog_revoker_fk','pog_tenant_fk','pog_user_fk','pog_version_ck']::text[]
       OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = relation_oid AND NOT convalidated) THEN
        RAISE EXCEPTION 'STOP: Expected nine validated permission constraints.';
    END IF;
    -- Have PostgreSQL parse the expected checks, avoiding fragile formatting or
    -- hand-counted constraint expressions on repeat runs. This table is empty
    -- and disappears at transaction end; it is not a domain entity.
    CREATE TEMP TABLE _sghr_pog_expected_checks (
        permission_code text, status text, row_version bigint,
        revoked_by_user_id uuid, revoked_at timestamptz, granted_at timestamptz,
        CONSTRAINT pog_code_ck CHECK (permission_code = 'platform.organization.memberships.manage'),
        CONSTRAINT pog_version_ck CHECK (row_version > 0),
        CONSTRAINT pog_lifecycle_ck CHECK (
            (status = 'active' AND revoked_by_user_id IS NULL AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_by_user_id IS NOT NULL
                AND revoked_at IS NOT NULL AND revoked_at >= granted_at AND row_version >= 2)
        )
    ) ON COMMIT DROP;
    IF EXISTS (
        SELECT 1 FROM pg_constraint expected
        LEFT JOIN pg_constraint actual ON actual.conrelid = relation_oid
            AND actual.conname = expected.conname AND actual.contype = 'c'
        WHERE expected.conrelid = 'pg_temp._sghr_pog_expected_checks'::regclass
          AND pg_get_constraintdef(actual.oid) IS DISTINCT FROM pg_get_constraintdef(expected.oid)
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint k WHERE k.conrelid = relation_oid AND k.contype = 'p'
            AND k.conname = 'platform_organization_permission_grants_pkey'
            AND k.conkey = ARRAY[1]::smallint[] AND NOT k.condeferrable
    ) THEN
        RAISE EXCEPTION 'STOP: Permission primary key or lifecycle check definitions differ.';
    END IF;
    IF (SELECT count(*) FROM pg_attrdef WHERE adrelid = relation_oid) <> 3
       OR NOT EXISTS (SELECT 1 FROM pg_attrdef WHERE adrelid = relation_oid AND adnum = 1
            AND pg_get_expr(adbin, adrelid) = 'gen_random_uuid()')
       OR NOT EXISTS (SELECT 1 FROM pg_attrdef WHERE adrelid = relation_oid AND adnum = 5
            AND pg_get_expr(adbin, adrelid) = '''active''::text')
       OR NOT EXISTS (SELECT 1 FROM pg_attrdef WHERE adrelid = relation_oid AND adnum = 6
            AND pg_get_expr(adbin, adrelid) = '1')
       OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = relation_oid AND attnum > 0
            AND NOT attisdropped AND (attidentity <> '' OR attgenerated <> '')) THEN
        RAISE EXCEPTION 'STOP: Unexpected explicit permission defaults or generated columns.';
    END IF;
    FOR fk IN SELECT * FROM (VALUES
        ('pog_tenant_fk','tenant_id','public.tenants'::regclass::oid,'id'),
        ('pog_user_fk','user_id','public.users'::regclass::oid,'id'),
        ('pog_permission_fk','permission_code','public.permissions'::regclass::oid,'code'),
        ('pog_granter_fk','granted_by_user_id','public.users'::regclass::oid,'id'),
        ('pog_revoker_fk','revoked_by_user_id','public.users'::regclass::oid,'id')
    ) AS expected(name, local_column, parent_oid, parent_column)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint k
            JOIN pg_attribute a ON a.attrelid = relation_oid AND a.attname = fk.local_column
            JOIN pg_attribute p ON p.attrelid = fk.parent_oid AND p.attname = fk.parent_column
            WHERE k.conrelid = relation_oid AND k.conname = fk.name AND k.contype = 'f'
              AND k.confrelid = fk.parent_oid AND k.conkey = ARRAY[a.attnum]::smallint[]
              AND k.confkey = ARRAY[p.attnum]::smallint[] AND k.confdeltype = 'r' AND k.confupdtype = 'a'
              AND NOT k.condeferrable) THEN
            RAISE EXCEPTION 'STOP: Unexpected canonical permission FK: %.', fk.name;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = relation_oid)
       OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = relation_oid AND NOT tgisinternal)
       OR EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(
            COALESCE(c.relacl, acldefault('r', c.relowner))) x
            WHERE c.oid = relation_oid AND x.grantee <> c.relowner)
       OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = relation_oid
            AND a.attnum > 0 AND NOT a.attisdropped AND cardinality(a.attacl) > 0) THEN
        RAISE EXCEPTION 'STOP: Only the existing trusted Platform owner may access explicit permission storage.';
    END IF;
    IF (SELECT count(*) FROM pg_index WHERE indrelid = relation_oid) <> 3
       OR EXISTS (SELECT 1 FROM pg_index WHERE indrelid = relation_oid
            AND (NOT indisvalid OR NOT indisready))
       OR NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = relation_oid AND c.relname = 'uq_pog_active_user_permission'
              AND i.indisunique AND NOT i.indisprimary AND i.indkey::text = '2 3 4'
              AND i.indexprs IS NULL AND i.indnkeyatts = 3 AND i.indnatts = 3
              AND pg_get_expr(i.indpred, i.indrelid) = '(status = ''active''::text)')
       OR NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = relation_oid AND c.relname = 'ix_pog_tenant_user'
              AND NOT i.indisunique AND i.indkey::text = '2 3 1' AND i.indpred IS NULL
              AND i.indexprs IS NULL AND i.indnkeyatts = 3 AND i.indnatts = 3) THEN
        RAISE EXCEPTION 'STOP: Expected primary, active-unique and tenant/user indexes.';
    END IF;
    IF NOT repeat_run AND EXISTS (SELECT 1 FROM public.platform_organization_permission_grants) THEN
        RAISE EXCEPTION 'STOP: Newly created permission storage must be empty.';
    END IF;
    RAISE NOTICE 'Explicit tenant permission storage verified; no assignments were created. Repeat: %', repeat_run;
END
$storage$;

SELECT jsonb_build_object(
    'state', 'STORAGE_READY',
    'table', 'public.platform_organization_permission_grants',
    'permission_assignments_created', 0,
    'shared_role_permissions_modified', false,
    'existing_memberships_or_construction_grants_modified', false,
    'rls_enabled_without_client_policies', true,
    'native_business_or_concurrency_acceptance_claimed', false,
    'next_step', 'EXPLICIT_PERMISSION_RUNTIME_AND_REMOVAL_INTEGRATION'
) AS platform_organization_permission_storage_result;
COMMIT;
