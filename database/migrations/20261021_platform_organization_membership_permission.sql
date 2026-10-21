-- Platform membership management permission registry entry only.
-- Run this complete transaction explicitly in Supabase SQL Editor as postgres.
-- Does NOT assign the permission, modify memberships, or change role mappings.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog, public;

DO $permission_registration$
DECLARE
    new_permission_code constant text := 'platform.organization.memberships.manage';
    description_text constant text := 'Manage organization membership within the current tenant, subject to separate Platform authorization, ownership protection and delegation boundaries.';
    names text[];
    types oid[];
    required_flags boolean[];
    permission_count integer;
    mappings_count bigint;
    registered_now boolean := false;
    other_permissions_before jsonb;
    other_permissions_after jsonb;
    mappings_before jsonb;
    mappings_after jsonb;
    inserted_id uuid;
    inserted_count bigint;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: Run permission registration as the reviewed Platform migration role postgres.';
    END IF;
    IF to_regclass('public.permissions') IS NULL OR to_regclass('public.role_permissions') IS NULL THEN
        RAISE EXCEPTION 'STOP: Existing Platform permission tables are required.';
    END IF;
    LOCK TABLE public.permissions IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.role_permissions IN SHARE MODE;
    IF row_security_active('public.permissions'::regclass)
        OR row_security_active('public.role_permissions'::regclass) THEN
        RAISE EXCEPTION 'STOP: Registry visibility must not be restricted for this migration.';
    END IF;
    SELECT array_agg(a.attname::text ORDER BY a.attnum),
           array_agg(a.atttypid ORDER BY a.attnum),
           array_agg(a.attnotnull ORDER BY a.attnum)
    INTO names, types, required_flags
    FROM pg_attribute a
    WHERE a.attrelid='public.permissions'::regclass
      AND a.attnum>0 AND NOT a.attisdropped;
    IF names IS DISTINCT FROM ARRAY['id','code','description','created_at']::text[]
       OR types IS DISTINCT FROM ARRAY['uuid'::regtype::oid,'text'::regtype::oid,
           'text'::regtype::oid,'timestamptz'::regtype::oid]::oid[]
       OR required_flags IS DISTINCT FROM ARRAY[true,true,false,true] THEN
        RAISE EXCEPTION 'STOP: Permission columns differ from the supplied schema.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid='public.permissions'::regclass
          AND NOT tgisinternal AND tgenabled<>'D'
    ) THEN
        RAISE EXCEPTION 'STOP: Review enabled custom permission-registry triggers before inserting a new permission.';
    END IF;

    SELECT COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id),'[]'::jsonb)
    INTO other_permissions_before
    FROM public.permissions p WHERE p.code IS DISTINCT FROM new_permission_code;
    SELECT COALESCE(jsonb_agg(to_jsonb(rp) ORDER BY rp.id),'[]'::jsonb)
    INTO mappings_before FROM public.role_permissions rp;
    SELECT count(*) INTO permission_count
    FROM public.permissions p WHERE p.code=new_permission_code;
    SELECT count(*) INTO mappings_count
    FROM public.role_permissions rp WHERE rp.permission_code=new_permission_code;

    IF permission_count>1 THEN
        RAISE EXCEPTION 'STOP: The permission code is not unique.';
    ELSIF permission_count=0 THEN
        -- Registering an absent permission must not activate orphan role mappings.
        IF mappings_count<>0 THEN
            RAISE EXCEPTION 'STOP: Existing orphan mappings need an explicit authorization review.';
        END IF;
        INSERT INTO public.permissions (code,description)
        VALUES (new_permission_code,description_text) RETURNING id INTO inserted_id;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        IF inserted_count<>1 OR inserted_id IS NULL THEN
            RAISE EXCEPTION 'STOP: Permission registration receipt is invalid.';
        END IF;
        registered_now := true;
    END IF;

    SELECT COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id),'[]'::jsonb)
    INTO other_permissions_after
    FROM public.permissions p WHERE p.code IS DISTINCT FROM new_permission_code;
    SELECT COALESCE(jsonb_agg(to_jsonb(rp) ORDER BY rp.id),'[]'::jsonb)
    INTO mappings_after FROM public.role_permissions rp;
    IF other_permissions_before IS DISTINCT FROM other_permissions_after
        OR mappings_before IS DISTINCT FROM mappings_after THEN
        RAISE EXCEPTION 'STOP: Existing permission data or role mappings changed; rolling back.';
    END IF;
    SELECT count(*) INTO permission_count
    FROM public.permissions p WHERE p.code=new_permission_code;
    IF permission_count<>1 THEN
        RAISE EXCEPTION 'STOP: Permission registration postcondition failed.';
    END IF;
    PERFORM set_config('app.membership_permission_registered_now',registered_now::text,true);
END;
$permission_registration$;

SELECT jsonb_build_object(
    'state', CASE WHEN current_setting('app.membership_permission_registered_now')::boolean
        THEN 'REGISTERED' ELSE 'ALREADY_REGISTERED' END,
    'permission', p.code,
    'registry_rows_added', CASE WHEN current_setting('app.membership_permission_registered_now')::boolean THEN 1 ELSE 0 END,
    'role_assignments_added', 0,
    'existing_role_permissions_unchanged', true,
    'memberships_and_construction_grants_modified', false,
    'schema_acl_and_rls_modified', false,
    'native_or_deployed_acceptance_claimed', false
) AS platform_membership_permission_registration
FROM public.permissions p
WHERE p.code='platform.organization.memberships.manage';
COMMIT;
