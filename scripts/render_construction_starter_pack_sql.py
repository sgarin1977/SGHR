"""Render reviewed release data as SQL. Never connects to a database."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.construction.construction_starter_template_pack import (
    PACK_CODE, PACK_CODES, StarterTemplatePackEntry, load_starter_pack,
)


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def render_sql(entries):
    if (
        not isinstance(entries, tuple)
        or any(not isinstance(entry, StarterTemplatePackEntry) for entry in entries)
        or tuple(entry.code for entry in entries) != PACK_CODES
    ):
        raise ValueError("A complete validated release package is required.")
    payload = json.dumps(
        [entry.record() for entry in entries],
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    body = r"""
DECLARE
    source_rows jsonb := __PAYLOAD__::jsonb;
    item jsonb;
    previous_row record;
    matches integer;
    added_templates integer := 0;
    added_versions integer := 0;
    disabled_preserved integer := 0;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'STOP: reviewed Platform migration role required.';
    END IF;
    IF to_regclass('public.construction_starter_measurement_templates') IS NULL
       OR to_regclass('public.construction_starter_measurement_template_versions') IS NULL THEN
        RAISE EXCEPTION 'STOP: starter catalog storage is missing.';
    END IF;

    LOCK TABLE public.construction_starter_measurement_templates,
        public.construction_starter_measurement_template_versions
        IN SHARE ROW EXCLUSIVE MODE;

    IF (
        SELECT count(*) FROM pg_class c
        WHERE c.oid IN (
            'public.construction_starter_measurement_templates'::regclass,
            'public.construction_starter_measurement_template_versions'::regclass)
          AND c.relkind = 'r' AND c.relrowsecurity
          AND pg_get_userbyid(c.relowner) = 'postgres'
    ) <> 2 THEN
        RAISE EXCEPTION 'STOP: catalog ownership or RLS differs.';
    END IF;
    IF (
        SELECT count(*) FROM pg_trigger
        WHERE tgrelid = 'public.construction_starter_measurement_template_versions'::regclass
          AND tgname IN (
              'trg_con_starter_versions_immutable',
              'trg_con_starter_versions_no_truncate')
          AND NOT tgisinternal AND tgenabled IN ('O', 'A')
    ) <> 2 THEN
        RAISE EXCEPTION 'STOP: immutable published version protection is missing.';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(source_rows)
    LOOP
        SELECT count(*) INTO matches
        FROM public.construction_starter_measurement_templates
        WHERE id = (item->>'template_id')::uuid OR code = item->>'code';
        IF matches > 1 THEN
            RAISE EXCEPTION 'STOP: source identity collision for %.', item->>'code';
        ELSIF matches = 1 THEN
            SELECT * INTO previous_row
            FROM public.construction_starter_measurement_templates
            WHERE id = (item->>'template_id')::uuid OR code = item->>'code';
            IF previous_row.id IS DISTINCT FROM (item->>'template_id')::uuid
               OR previous_row.code IS DISTINCT FROM item->>'code' THEN
                RAISE EXCEPTION 'STOP: existing source identity differs for %.', item->>'code';
            END IF;
            IF previous_row.active IS FALSE THEN
                disabled_preserved := disabled_preserved + 1;
            END IF;
        ELSE
            INSERT INTO public.construction_starter_measurement_templates
                (id, code, active, row_version, created_at, updated_at)
            VALUES (
                (item->>'template_id')::uuid, item->>'code', true, 1,
                transaction_timestamp(), transaction_timestamp());
            added_templates := added_templates + 1;
        END IF;

        SELECT count(*) INTO matches
        FROM public.construction_starter_measurement_template_versions
        WHERE id = (item->>'version_id')::uuid
           OR (template_id = (item->>'template_id')::uuid AND version = 1);
        IF matches > 1 THEN
            RAISE EXCEPTION 'STOP: version identity collision for %.', item->>'code';
        ELSIF matches = 1 THEN
            SELECT * INTO previous_row
            FROM public.construction_starter_measurement_template_versions
            WHERE id = (item->>'version_id')::uuid
               OR (template_id = (item->>'template_id')::uuid AND version = 1);
            IF previous_row.id IS DISTINCT FROM (item->>'version_id')::uuid
               OR previous_row.template_id IS DISTINCT FROM (item->>'template_id')::uuid
               OR previous_row.version IS DISTINCT FROM 1
               OR previous_row.definition_schema_version IS DISTINCT FROM 1
               OR previous_row.source_language IS DISTINCT FROM item->>'source_language'
               OR previous_row.content_hash IS DISTINCT FROM item->>'content_hash'
               OR previous_row.definition IS DISTINCT FROM item->'definition' THEN
                RAISE EXCEPTION 'STOP: immutable source version differs for %.', item->>'code';
            END IF;
        ELSE
            INSERT INTO public.construction_starter_measurement_template_versions
                (id, template_id, version, definition_schema_version,
                 source_language, definition, content_hash, published_at, created_at)
            VALUES (
                (item->>'version_id')::uuid, (item->>'template_id')::uuid, 1, 1,
                item->>'source_language', item->'definition', item->>'content_hash',
                transaction_timestamp(), transaction_timestamp());
            added_versions := added_versions + 1;
        END IF;
    END LOOP;

    PERFORM set_config('sghr.starter_pack_result', jsonb_build_object(
        'state', 'STARTER_RELEASE_PACK_PRESENT',
        'pack_code', 'SGHR_STARTERS_V1',
        'source_templates_created', added_templates,
        'source_versions_created', added_versions,
        'disabled_templates_preserved', disabled_preserved,
        'published_versions_modified', 0,
        'organization_copies_created', 0,
        'commercial_prices_created', 0,
        'schema_acl_rls_modified', false,
        'con_o002_acceptance_claimed', false
    )::text, true);
END;
""".replace("__PAYLOAD__", _literal(payload), 1)
    tag = "$sghr_starter_pack_v1$"
    while tag in body:
        tag = tag[:-1] + "_$"
    return (
        "-- " + PACK_CODE + ": source content only; no organization copies.\n"
        "BEGIN;\n"
        "SET LOCAL standard_conforming_strings = on;\n"
        "SET LOCAL search_path = pg_catalog, public;\n"
        "SET LOCAL lock_timeout = '10s';\n"
        "SET LOCAL statement_timeout = '60s';\n"
        "DO " + tag + "\n" + body + "\n" + tag + ";\n"
        "SELECT current_setting('sghr.starter_pack_result')::jsonb AS result;\n"
        "COMMIT;\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    content = render_sql(load_starter_pack()).encode("utf-8")
    output = args.output.absolute()
    if output.suffix.lower() != ".sql" or not output.parent.is_dir() or output.is_symlink():
        raise ValueError("Choose an existing output directory and a regular .sql file.")
    if output.exists():
        if output.read_bytes() != content:
            raise ValueError("Output exists with different content; choose another path.")
        print("ALREADY_PREPARED:", output)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=".starter-release-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        # Exclusive publication: never overwrite a concurrently created output.
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("PREPARED:", output)
    print("SQL_EXECUTED: false")
    print("DATABASE_CONNECTIONS: 0")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("STOP:", str(error), file=sys.stderr)
        raise SystemExit(2)
